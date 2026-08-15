"""Real Sync work reports handled cards without making extra provider reads."""

from __future__ import annotations

import tempfile
import unittest
from collections.abc import Iterator
from pathlib import Path
from unittest.mock import patch

from pykantui.sync.provider import ProviderBackend
from pykantui.tracker.models import RemoteColumn, RemoteComment, RemoteIssue
from pykantui.workspace import markdown
from pykantui.workspace.progress import SyncPhase, SyncProgressUpdate
from pykantui.workspace.sync import sync
from tests.integration.sync.test_push import PROJECT, TODO, RecordingProvider, issue


class CountingProvider(RecordingProvider):
    """The ordinary test provider with observable collection requests."""

    def __init__(self, issues: list[RemoteIssue]) -> None:
        super().__init__(issues)
        self.issue_list_calls = 0
        self.column_list_calls = 0

    def iter_issues(self, project_id: str) -> Iterator[RemoteIssue]:
        self.issue_list_calls += 1
        yield from super().iter_issues(project_id)

    def list_columns(self, project_id: str) -> list[RemoteColumn]:
        self.column_list_calls += 1
        return super().list_columns(project_id)


class BrokenPullProvider(CountingProvider):
    def iter_issues(self, project_id: str) -> Iterator[RemoteIssue]:
        self.issue_list_calls += 1
        yield self._issues[0]
        raise RuntimeError("provider page failed")


class CommentCountingProvider(CountingProvider):
    """A comment-capable provider whose existing reads are observable."""

    spec = RecordingProvider.spec.model_copy(
        update={
            "capabilities": RecordingProvider.spec.capabilities.model_copy(
                update={"read_comments": True}
            )
        }
    )

    def __init__(self, issues: list[RemoteIssue]) -> None:
        super().__init__(issues)
        self.comment_reads: list[str] = []

    def iter_comments(self, project_id: str, issue: RemoteIssue) -> Iterator[RemoteComment]:
        del project_id
        self.comment_reads.append(issue.issue_id)
        return iter(())


class SyncProgressIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temporary = tempfile.TemporaryDirectory()
        self.workspace = Path(self._temporary.name)

    def tearDown(self) -> None:
        self._temporary.cleanup()

    @staticmethod
    def _opt_in_comment_threads(workspace: Path, keys: tuple[str, ...]) -> None:
        for key in keys:
            path = next(workspace.rglob(f"{key}.md"))
            text = path.read_text(encoding="utf-8")
            region = (
                f"{markdown.COMMENTS_MARKER}\n\n"
                f"{markdown.COMMENT_DRAFTS_MARKER}\n\n"
                f"{markdown.NOTES_MARKER}"
            )
            path.write_text(text.replace(markdown.NOTES_MARKER, region), encoding="utf-8")

    def test_pull_reports_unknown_length_fetch_then_exact_markdown_fraction(self) -> None:
        provider = CountingProvider([issue(f"K-{number}", TODO) for number in range(1, 6)])
        seen: list[SyncProgressUpdate] = []

        report = sync(
            self.workspace,
            provider,
            PROJECT,
            push_edits=False,
            commit=False,
            progress=seen.append,
        )

        fetching = [update for update in seen if update.phase is SyncPhase.FETCHING]
        reconciling = [update for update in seen if update.phase is SyncPhase.RECONCILING]
        self.assertEqual([0, 1, 2, 3, 4, 5], [update.completed for update in fetching])
        self.assertTrue(all(update.total is None for update in fetching))
        self.assertEqual([0, 1, 2, 3, 4, 5], [update.completed for update in reconciling])
        self.assertTrue(all(update.total == 5 for update in reconciling))
        self.assertEqual("K-1", reconciling[0].item)
        self.assertEqual("K-5", reconciling[-1].item)
        self.assertEqual(SyncPhase.PREPARING, seen[0].phase)
        self.assertEqual(SyncPhase.COMPLETE, seen[-1].phase)
        self.assertFalse(seen[-1].active)
        self.assertEqual(report.summary(), seen[-1].summary)

    def test_completed_means_handled_so_each_outbound_item_has_before_and_after_updates(self) -> None:
        provider = CountingProvider([issue("K-1", TODO), issue("K-2", TODO)])
        sync(self.workspace, provider, PROJECT, push_edits=False, commit=False)
        for key in ("K-1", "K-2"):
            path = next(self.workspace.rglob(f"{key}.md"))
            path.write_text(
                path.read_text(encoding="utf-8").replace(f"title: Title {key}", f"title: Edited {key}"),
                encoding="utf-8",
            )
        seen: list[SyncProgressUpdate] = []

        sync(
            self.workspace,
            provider,
            PROJECT,
            confirm=lambda _plan: True,
            commit=False,
            progress=seen.append,
        )

        applying = [update for update in seen if update.phase is SyncPhase.APPLYING]
        self.assertEqual([0, 1, 1, 2], [update.completed for update in applying])
        self.assertTrue(all(update.total == 2 for update in applying))
        self.assertEqual(["K-1", "K-1", "K-2", "K-2"], [update.item for update in applying])
        self.assertEqual(["K-1", "K-2"], [key for key, _edit in provider.updates])

    def test_progress_observation_does_not_add_provider_collection_or_item_reads(self) -> None:
        def reads(*, observe: bool) -> tuple[int, int, list[str]]:
            with tempfile.TemporaryDirectory() as directory:
                provider = CountingProvider([issue(f"K-{number}", TODO) for number in range(1, 4)])
                sync(
                    Path(directory),
                    provider,
                    PROJECT,
                    push_edits=False,
                    commit=False,
                    progress=(lambda _update: None) if observe else None,
                )
                return provider.issue_list_calls, provider.column_list_calls, list(provider.fetches)

        self.assertEqual(reads(observe=False), reads(observe=True))

    def test_backend_forwards_the_same_progress_callback_contract(self) -> None:
        provider = CountingProvider([issue("K-1", TODO)])
        sync(self.workspace, provider, PROJECT, push_edits=False, commit=False)
        backend = ProviderBackend(self.workspace, provider, PROJECT)
        seen: list[SyncProgressUpdate] = []

        report = backend.sync_now(
            confirm=lambda _plan: True,
            commit=False,
            progress=seen.append,
        )

        self.assertEqual(SyncPhase.PREPARING, seen[0].phase)
        self.assertEqual(SyncPhase.COMPLETE, seen[-1].phase)
        self.assertEqual(report.summary(), seen[-1].summary)

    def test_backend_terminal_success_retains_the_exact_final_reconciliation_fraction(self) -> None:
        provider = CountingProvider([issue(f"K-{number}", TODO) for number in range(1, 6)])
        sync(self.workspace, provider, PROJECT, push_edits=False, commit=False)
        backend = ProviderBackend(self.workspace, provider, PROJECT)
        seen: list[SyncProgressUpdate] = []

        backend.sync_now(confirm=lambda _plan: True, commit=False, progress=seen.append)

        terminal = seen[-1]
        self.assertEqual(SyncPhase.COMPLETE, terminal.phase)
        self.assertEqual((5, 5), (terminal.completed, terminal.total))
        self.assertEqual("K-5", terminal.item)

    def test_uncaught_provider_failure_emits_a_terminal_error_without_claiming_more_work(self) -> None:
        provider = BrokenPullProvider([issue("K-1", TODO), issue("K-2", TODO)])
        seen: list[SyncProgressUpdate] = []

        with self.assertRaisesRegex(RuntimeError, "provider page failed"):
            sync(
                self.workspace,
                provider,
                PROJECT,
                push_edits=False,
                commit=False,
                progress=seen.append,
            )

        self.assertEqual(SyncPhase.FAILED, seen[-1].phase)
        self.assertFalse(seen[-1].active)
        self.assertTrue(seen[-1].error)
        self.assertNotIn("provider page failed", seen[-1].item)
        fetching = [update for update in seen if update.phase is SyncPhase.FETCHING]
        self.assertEqual(1, fetching[-1].completed)
        self.assertEqual(
            (fetching[-1].completed, fetching[-1].total, fetching[-1].item),
            (seen[-1].completed, seen[-1].total, seen[-1].item),
        )

    def test_zero_comment_targets_still_report_a_truthful_zero_fraction(self) -> None:
        provider = CountingProvider([issue("K-1", TODO)])
        seen: list[SyncProgressUpdate] = []

        sync(
            self.workspace,
            provider,
            PROJECT,
            push_edits=False,
            commit=False,
            progress=seen.append,
        )

        comments = [update for update in seen if update.phase.value == "comments"]
        self.assertEqual([(0, 0)], [(update.completed, update.total) for update in comments])

    def test_comment_hydration_reports_each_existing_read_without_adding_requests(self) -> None:
        def run(*, observe: bool) -> tuple[tuple[int, int, list[str], list[str]], list[SyncProgressUpdate]]:
            with tempfile.TemporaryDirectory() as directory:
                workspace = Path(directory)
                provider = CommentCountingProvider(
                    [issue(f"K-{number}", TODO) for number in range(1, 4)]
                )
                sync(workspace, provider, PROJECT, push_edits=False, commit=False)
                self._opt_in_comment_threads(workspace, ("K-1", "K-2", "K-3"))
                provider.issue_list_calls = 0
                provider.column_list_calls = 0
                provider.fetches.clear()
                provider.comment_reads.clear()
                seen: list[SyncProgressUpdate] = []

                sync(
                    workspace,
                    provider,
                    PROJECT,
                    push_edits=False,
                    commit=False,
                    progress=seen.append if observe else None,
                )
                calls = (
                    provider.issue_list_calls,
                    provider.column_list_calls,
                    list(provider.fetches),
                    list(provider.comment_reads),
                )
                return calls, seen

        baseline_calls, _baseline_seen = run(observe=False)
        observed_calls, seen = run(observe=True)

        self.assertEqual(baseline_calls, observed_calls)
        self.assertEqual(
            ["id-K-1", "id-K-2", "id-K-3"],
            observed_calls[-1],
        )
        comments = [update for update in seen if update.phase.value == "comments"]
        self.assertEqual((0, 3), (comments[0].completed, comments[0].total))
        self.assertEqual((3, 3), (comments[-1].completed, comments[-1].total))
        self.assertEqual("K-3", comments[-1].item)

    def test_prune_probes_report_each_missing_card_once_without_hidden_retries(self) -> None:
        provider = CountingProvider([issue(f"K-{number}", TODO) for number in range(1, 5)])
        sync(self.workspace, provider, PROJECT, push_edits=False, commit=False)
        provider._issues.clear()
        provider.fetches.clear()
        seen: list[SyncProgressUpdate] = []

        sync(
            self.workspace,
            provider,
            PROJECT,
            push_edits=False,
            commit=False,
            progress=seen.append,
        )

        self.assertEqual(
            ["id-K-1", "id-K-2", "id-K-3", "id-K-4"],
            provider.fetches,
        )
        verifying = [update for update in seen if update.phase.value == "verifying"]
        self.assertEqual((0, 4), (verifying[0].completed, verifying[0].total))
        self.assertEqual((4, 4), (verifying[-1].completed, verifying[-1].total))
        self.assertEqual("K-4", verifying[-1].item)

    def test_backend_reports_that_provider_completed_when_local_reload_then_fails(self) -> None:
        provider = CountingProvider([issue("K-1", TODO)])
        sync(self.workspace, provider, PROJECT, push_edits=False, commit=False)
        backend = ProviderBackend(self.workspace, provider, PROJECT)
        path = next(self.workspace.rglob("K-1.md"))
        path.write_text(
            path.read_text(encoding="utf-8").replace("title: Title K-1", "title: Edited once"),
            encoding="utf-8",
        )
        provider.updates.clear()

        with (
            patch.object(backend, "reload_local", side_effect=OSError("local index unreadable")),
            self.assertRaises(Exception) as raised,
        ):
            backend.sync_now(confirm=lambda _plan: True, commit=False)

        error = raised.exception
        self.assertEqual("PostSyncReloadError", type(error).__name__)
        self.assertEqual(1, len(provider.updates), "the confirmed provider write must run exactly once")
        self.assertEqual("K-1", provider.updates[0][0])
        self.assertIn("wrote", error.report.summary())  # type: ignore[attr-defined]
        self.assertIn("local index unreadable", str(error))


if __name__ == "__main__":
    unittest.main()

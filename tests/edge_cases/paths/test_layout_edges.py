"""Turning tracker names into paths, when the names are hostile.

Column and project names come from other people's boards, so they contain
whatever those people typed: slashes, emoji, trailing dots, names Windows has
reserved since DOS. Every one of them becomes a directory here, and a name that
cannot be created is a sync that fails on somebody else's board and not on
ours.

The rule throughout: never produce a path that cannot exist, and never let two
different columns collapse onto the same one.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from pykantui.api import ProviderError
from pykantui.tracker.models import RemoteColumn, RemoteIssue, RemoteProject, safe_name, slugify
from pykantui.workspace import layout
from pykantui.workspace.layout import ColumnStyle
from pykantui.workspace.paths import ensure_workspace_path

PROJECT = RemoteProject(project_id="P1", key="ACME", name="widgets")


def column(name: str) -> RemoteColumn:
    return RemoteColumn(column_id="c1", name=name)


class SafeNameTests(unittest.TestCase):
    """Anything that reaches a filesystem goes through this."""

    def test_a_slash_cannot_create_a_directory_level(self) -> None:
        """ "Done / Shipped" must be one folder, not two."""
        found = safe_name("Done / Shipped")

        self.assertNotIn("/", found)
        self.assertNotIn("\\", found)

    def test_a_backslash_is_handled_too(self) -> None:
        self.assertNotIn("\\", safe_name(r"Done \ Shipped"))

    def test_windows_reserved_characters(self) -> None:
        for ch in '<>:"|?*':
            self.assertNotIn(ch, safe_name(f"a{ch}b"), f"{ch!r} survived")

    def test_control_characters_are_removed(self) -> None:
        found = safe_name("a\x00b\x1fc\nd")

        self.assertTrue(all(ord(c) >= 32 for c in found), repr(found))

    def test_a_trailing_dot_or_space_is_trimmed(self) -> None:
        """Windows silently drops both, so two columns could collide."""
        self.assertFalse(safe_name("In Progress.").endswith("."))
        self.assertFalse(safe_name("In Progress ").endswith(" "))

    def test_an_empty_name_still_produces_something(self) -> None:
        self.assertTrue(safe_name(""))
        self.assertTrue(safe_name("   "))
        self.assertTrue(safe_name("///"))

    def test_a_very_long_name_is_bounded(self) -> None:
        """260 characters is still a real limit on plenty of Windows setups."""
        found = safe_name("x" * 500)

        self.assertLessEqual(len(found), 120, f"{len(found)} characters is too long for a path")

    def test_unicode_is_preserved(self) -> None:
        """A Japanese column name is not an error."""
        self.assertIn("進行中", safe_name("進行中"))

    def test_emoji_survive(self) -> None:
        self.assertTrue(safe_name("Done ✅"))


class SlugifyTests(unittest.TestCase):
    def test_spaces_become_dashes(self) -> None:
        self.assertEqual("in-progress", slugify("In Progress"))

    def test_case_is_flattened(self) -> None:
        self.assertEqual("done", slugify("DONE"))

    def test_runs_of_separators_collapse(self) -> None:
        self.assertEqual("a-b", slugify("a   ---   b"))

    def test_leading_and_trailing_separators_go(self) -> None:
        found = slugify("  -- In Review --  ")
        self.assertFalse(found.startswith("-"))
        self.assertFalse(found.endswith("-"))

    def test_slugify_may_return_nothing(self) -> None:
        """It is a fragment helper, not a path helper.

        Empty is the honest answer for a name with no ASCII word characters in
        it; making one up here would invent a folder name from nothing. The
        guarantee that a *path* is never empty belongs to `column_folder`,
        which falls back to `safe_name` -- see the test below.
        """
        for hostile in ("", "   ", "---", "///", "!!!"):
            self.assertEqual("", slugify(hostile), hostile)

    def test_two_different_columns_do_not_collide(self) -> None:
        self.assertNotEqual(slugify("In Progress"), slugify("In Review"))


class ColumnFolderTests(unittest.TestCase):
    def test_slug_style_is_lowercase_and_dashed(self) -> None:
        self.assertEqual("in-progress", layout.column_folder(column("In Progress"), ColumnStyle.SLUG))

    def test_name_style_keeps_the_tracker_wording(self) -> None:
        self.assertEqual("In Progress", layout.column_folder(column("In Progress"), ColumnStyle.NAME))

    def test_name_style_is_still_path_safe(self) -> None:
        """Keeping the wording must not mean keeping a slash."""
        found = layout.column_folder(column("Done / Shipped"), ColumnStyle.NAME)

        self.assertNotIn("/", found)

    def test_a_folder_name_is_never_empty(self) -> None:
        """An empty segment would drop issues into the project root, beside
        PROJECT.md, where the column could no longer be read back from the path."""
        for hostile in ("", "   ", "---", "///", "!!!", "進行中", "✅"):
            for style in ColumnStyle:
                found = layout.column_folder(column(hostile), style)
                self.assertTrue(found.strip(), f"{hostile!r} ({style.value}) produced no folder")

    def test_a_column_with_no_name_falls_back_to_its_id(self) -> None:
        nameless = RemoteColumn(column_id="c-42", name="")

        for style in ColumnStyle:
            self.assertIn("42", layout.column_folder(nameless, style))

    def test_a_windows_reserved_name_is_not_used_bare(self) -> None:
        """CON, PRN, AUX and NUL cannot be directories on Windows at all."""
        for reserved in ("CON", "PRN", "AUX", "NUL", "COM1", "LPT1"):
            found = layout.column_folder(column(reserved), ColumnStyle.NAME)
            self.assertNotEqual(reserved.upper(), found.upper(), f"{reserved} is unusable as a folder")

    def test_the_folder_can_actually_be_created(self) -> None:
        """The test that matters: does the filesystem accept it?"""
        hostile = [
            "Done / Shipped",
            "In Progress.",
            "CON",
            "  ",
            "a" * 300,
            "進行中",
            "Done ✅",
            'we"ird',
            "a<b>c",
            "x|y",
            "q?r",
            "s*t",
        ]
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            for index, raw in enumerate(hostile):
                for style in ColumnStyle:
                    folder = root / f"{index}-{style.value}" / layout.column_folder(column(raw), style)
                    folder.mkdir(parents=True, exist_ok=True)
                    self.assertTrue(folder.is_dir(), f"{raw!r} ({style.value}) could not be created")


class FolderIndexTests(unittest.TestCase):
    def test_every_column_is_findable_by_its_folder(self) -> None:
        columns = [column("To Do"), RemoteColumn(column_id="c2", name="In Progress")]

        index = layout.folder_index(columns, ColumnStyle.SLUG)

        self.assertEqual("c1", index["to-do"].column_id)
        self.assertEqual("c2", index["in-progress"].column_id)

    def test_columns_that_would_share_a_folder(self) -> None:
        """ "To Do" and "TO-DO" both slug to "to-do"; the index must not lose one silently."""
        columns = [column("To Do"), RemoteColumn(column_id="c2", name="TO-DO")]

        with self.assertRaisesRegex(ProviderError, "To Do.*TO-DO.*to-do"):
            layout.folder_index(columns, ColumnStyle.SLUG)

    def test_name_style_fails_closed_on_case_insensitive_filesystem_collision(self) -> None:
        columns = [column("Review"), RemoteColumn(column_id="c2", name="review")]

        with self.assertRaisesRegex(ProviderError, "Review.*review"):
            layout.folder_index(columns, ColumnStyle.NAME)


class IssuePathTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.ws = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def issue(self, key: str = "ACME-1", issue_id: str = "1") -> RemoteIssue:
        return RemoteIssue(issue_id=issue_id, key=key, title="t")

    def test_the_file_is_named_after_the_key(self) -> None:
        path = layout.issue_path(self.ws, "jira", PROJECT, column("To Do"), self.issue(), ColumnStyle.SLUG)

        self.assertEqual("ACME-1.md", path.name)

    def test_a_key_with_a_slash_does_not_escape_the_column(self) -> None:
        """GitHub keys look like "owner/repo#4"."""
        path = layout.issue_path(
            self.ws, "github", PROJECT, column("To Do"), self.issue(key="acme/widgets#4"), ColumnStyle.SLUG
        )

        self.assertNotIn("/", path.name)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("x", encoding="utf-8")
        self.assertTrue(path.is_file())

    def test_an_issue_with_no_key_falls_back_to_its_id(self) -> None:
        path = layout.issue_path(
            self.ws, "monday", PROJECT, column("To Do"), self.issue(key="", issue_id="12345"), ColumnStyle.SLUG
        )

        self.assertIn("12345", path.name)

    def test_two_issues_never_share_a_path(self) -> None:
        first = layout.issue_path(self.ws, "j", PROJECT, column("To Do"), self.issue("A-1", "1"), ColumnStyle.SLUG)
        second = layout.issue_path(self.ws, "j", PROJECT, column("To Do"), self.issue("A-2", "2"), ColumnStyle.SLUG)

        self.assertNotEqual(first, second)

    def test_the_path_sits_under_the_workspace(self) -> None:
        """A key full of ../ must not write outside the tree."""
        path = layout.issue_path(
            self.ws, "j", PROJECT, column("To Do"), self.issue(key="../../etc/passwd"), ColumnStyle.SLUG
        )

        self.assertTrue(
            self.ws.resolve() in path.resolve().parents,
            f"{path} escaped the workspace",
        )


class IssueDiscoveryTests(unittest.TestCase):
    """Only real files physically contained by the project are sync inputs."""

    def test_a_symlinked_column_cannot_import_markdown_from_outside(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            workspace = Path(name) / "workspace"
            outside = Path(name) / "outside"
            outside.mkdir()
            (outside / "stolen.md").write_text("secret", encoding="utf-8")
            project_root = layout.project_dir(workspace, "jira", PROJECT)
            project_root.mkdir(parents=True)
            try:
                (project_root / "to-do").symlink_to(outside, target_is_directory=True)
            except OSError as error:
                self.skipTest(f"symlinks are unavailable: {error}")

            self.assertEqual([], layout.iter_issue_files(workspace, "jira", PROJECT))

    def test_a_symlinked_markdown_file_is_not_a_sync_input(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            workspace = Path(name) / "workspace"
            outside = Path(name) / "outside.md"
            outside.write_text("secret", encoding="utf-8")
            column_root = layout.project_dir(workspace, "jira", PROJECT) / "to-do"
            column_root.mkdir(parents=True)
            try:
                (column_root / "ACME-1.md").symlink_to(outside)
            except OSError as error:
                self.skipTest(f"symlinks are unavailable: {error}")

            self.assertEqual([], layout.iter_issue_files(workspace, "jira", PROJECT))


class WorkspaceContainmentTests(unittest.TestCase):
    def test_a_path_outside_the_workspace_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            workspace = root / "workspace"
            workspace.mkdir()

            with self.assertRaisesRegex(ProviderError, "refusing workspace path"):
                ensure_workspace_path(workspace, root / "outside.md")

    def test_an_output_path_through_a_symlink_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            workspace = root / "workspace"
            workspace.mkdir()
            outside = root / "outside"
            outside.mkdir()
            linked = workspace / "linked"
            try:
                linked.symlink_to(outside, target_is_directory=True)
            except OSError as error:
                self.skipTest(f"symlinks are unavailable: {error}")

            with self.assertRaisesRegex(ProviderError, "refusing workspace path"):
                ensure_workspace_path(workspace, linked / "card.md")

    def test_a_draft_cannot_be_written_through_a_symlinked_column(self) -> None:
        from pykantui.commands.new import write_draft
        from pykantui.tracker.models import IssueDraft
        from pykantui.workspace.project import Project

        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            workspace = root / "workspace"
            outside = root / "outside"
            workspace.mkdir()
            outside.mkdir()
            project = Project(provider="jira", project_id="P1", key="ACME")
            target = layout.column_dir(workspace, "jira", project.remote(), column("To Do"), project.column_style)
            target.parent.mkdir(parents=True)
            try:
                target.symlink_to(outside, target_is_directory=True)
            except OSError as error:
                self.skipTest(f"symlinks are unavailable: {error}")

            with self.assertRaisesRegex(ProviderError, "refusing workspace path"):
                write_draft(workspace, project, column("To Do"), IssueDraft(title="do not escape"))

            self.assertEqual([], list(outside.iterdir()))


class LinkTests(unittest.TestCase):
    def test_a_space_is_encoded(self) -> None:
        """`[A](In Progress/A.md)` is not a link in CommonMark."""
        link = layout.link_to("In Progress", "ACME-1.md")

        self.assertNotIn(" ", link)

    def test_the_link_round_trips_to_the_same_file(self) -> None:
        from urllib.parse import unquote

        link = layout.link_to("In Progress", "ACME-1.md")

        self.assertEqual("In Progress/ACME-1.md", unquote(link))


if __name__ == "__main__":
    unittest.main()

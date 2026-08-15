"""Security and width contracts for provider issue links."""

from __future__ import annotations

import inspect
import unittest
from typing import Any, cast
from unittest.mock import Mock, patch

from rich.cells import cell_len
from rich.style import Style

from pykantui.models import Task
from pykantui.providers.asana.mapper import task_to_remote as asana_task
from pykantui.providers.clickup.mapper import task_to_remote as clickup_task
from pykantui.providers.github.mapper import issue_to_remote as github_issue
from pykantui.providers.jira.mapper import issue_to_remote as jira_issue
from pykantui.providers.linear.mapper import issue_to_remote as linear_issue
from pykantui.providers.monday.mapper import item_to_remote as monday_item
from pykantui.providers.plane.mapper import work_item_to_remote as plane_item
from pykantui.providers.shortcut.mapper import story_to_remote as shortcut_story
from pykantui.providers.trello.mapper import card_to_remote as trello_card
from pykantui.tui import provider_links
from pykantui.tui.provider_links import (
    ISSUE_LINK_GLYPH,
    ProviderIssueLink,
    open_provider_url,
    provider_issue_url,
    safe_https_url,
)


def external_url_launcher() -> Any:
    """Return the host-aware launcher contract under test.

    Looking it up at runtime lets the focused suite report each missing
    behavior independently during the RED phase instead of failing collection
    on the first missing import.
    """
    launcher = getattr(provider_links, "launch_external_url", None)
    if launcher is None:
        raise AssertionError("provider links need a host-aware launch_external_url helper")
    return launcher


class ProviderIssueUrlTests(unittest.TestCase):
    def test_all_nine_builtin_mappers_populate_remote_issue_url(self) -> None:
        sources = {
            "asana": (asana_task, "url=task.permalink_url"),
            "clickup": (clickup_task, "url=task.url"),
            "github": (github_issue, "url=issue.html_url"),
            "jira": (jira_issue, 'url=f"{base_url}/browse/'),
            "linear": (linear_issue, "url=issue.url"),
            "monday": (monday_item, "url=item.url or"),
            "plane": (plane_item, "url=_plane_web_url("),
            "shortcut": (shortcut_story, "url=story.app_url"),
            "trello": (trello_card, "url=card.url"),
        }
        self.assertEqual(9, len(sources))
        for provider, (mapper, marker) in sources.items():
            with self.subTest(provider=provider):
                self.assertIn(marker, inspect.getsource(cast(Any, mapper)))

    def test_exact_north_east_arrow_is_one_terminal_cell(self) -> None:
        self.assertEqual("↗", ISSUE_LINK_GLYPH)
        self.assertEqual(1, cell_len(ISSUE_LINK_GLYPH))

    def test_https_provider_url_is_preserved(self) -> None:
        url = "https://acme.atlassian.net/browse/JPT-42?focused=true#comments"
        self.assertEqual(url, safe_https_url(url))

    def test_surrounding_space_is_removed(self) -> None:
        self.assertEqual("https://example.test/i/1", safe_https_url("  https://example.test/i/1  "))

    def test_missing_and_non_string_values_have_no_action(self) -> None:
        for value in (None, "", "   ", 42, object()):
            with self.subTest(value=value):
                self.assertEqual("", safe_https_url(value))

    def test_widget_is_absent_from_navigation_when_url_is_unsafe(self) -> None:
        link = ProviderIssueLink("javascript:alert(1)")

        self.assertFalse(link.display)
        self.assertTrue(link.disabled)
        self.assertEqual("", link.provider_url)
        self.assertIsNone(link.tooltip)

    def test_available_widget_has_localized_accessible_tooltip(self) -> None:
        with (
            patch("pykantui.tui.provider_links._", return_value="Localized link help") as translate,
            patch("pykantui.tui.provider_links._running_in_container", return_value=False),
        ):
            link = ProviderIssueLink("https://example.test/i/1")

        self.assertEqual("Localized link help", link.tooltip)
        translate.assert_called_once_with("Open provider issue in browser")

    def test_container_tooltip_distinguishes_terminal_link_from_copy_action(self) -> None:
        with patch("pykantui.tui.provider_links._running_in_container", return_value=True):
            link = ProviderIssueLink("https://example.test/i/1")

        self.assertEqual("Ctrl+click to open · click to copy", link.tooltip)

    def test_widget_renders_one_cell_osc8_link_target(self) -> None:
        url = "https://example.test/i/1"
        link = ProviderIssueLink(url)
        rendered = link._link_text()

        self.assertEqual(1, rendered.cell_len)
        self.assertEqual(url, cast(Style, rendered.style).link)

    def test_unsafe_or_ambiguous_urls_are_rejected(self) -> None:
        for url in (
            "http://example.test/i/1",
            "javascript:alert(1)",
            "file:///etc/passwd",
            "//example.test/i/1",
            "https:///missing-host",
            "https://user:secret@example.test/i/1",
            "https://example.test\\@attacker.test/i/1",
            "https://example.test\n.attacker.test/i/1",
            "https://example.test:invalid/i/1",
            "https://example.test:65536/i/1",
        ):
            with self.subTest(url=url):
                self.assertEqual("", safe_https_url(url))

    def test_task_url_comes_only_from_cached_metadata(self) -> None:
        task = Task(
            task_id=1,
            title="JPT-42\nProvider issue",
            column_id=1,
            metadata={"url": "https://acme.atlassian.net/browse/JPT-42"},
        )
        self.assertEqual("https://acme.atlassian.net/browse/JPT-42", provider_issue_url(task))

    def test_task_without_a_safe_cached_url_has_no_action(self) -> None:
        task = Task(task_id=1, title="Local card", column_id=1, metadata={"url": "http://unsafe.test"})
        self.assertEqual("", provider_issue_url(task))

    def test_browser_exception_is_contained_and_reported_without_success_notice(self) -> None:
        app = Mock()

        with (
            patch("pykantui.tui.provider_links._", return_value="Localized browser error") as translate,
            patch("pykantui.tui.provider_links.launch_external_url", return_value=False),
        ):
            opened = open_provider_url(app, "https://example.test/i/1")

        self.assertFalse(opened)
        app.notify.assert_called_once_with(
            "Localized browser error",
            severity="error",
            timeout=4,
        )
        translate.assert_called_once_with("Could not open the provider link")

    def test_explicit_false_browser_result_is_reported(self) -> None:
        app = Mock()

        with patch("pykantui.tui.provider_links.launch_external_url", return_value=False):
            opened = open_provider_url(app, "https://example.test/i/1")

        self.assertFalse(opened)
        app.notify.assert_called_once()

    def test_invalid_url_never_reaches_browser_or_notification(self) -> None:
        app = Mock()

        opened = open_provider_url(app, "javascript:alert(1)")

        self.assertFalse(opened)
        app.open_url.assert_not_called()
        app.notify.assert_not_called()


class ExternalUrlLauncherTests(unittest.TestCase):
    """Host-browser behavior, including a Docker-safe clipboard fallback."""

    URL = "https://example.test/issues/JPT-1"

    def test_provider_action_delegates_to_the_testable_launcher(self) -> None:
        app = Mock()
        with patch.object(
            provider_links,
            "launch_external_url",
            create=True,
            return_value=True,
        ) as launcher:
            opened = open_provider_url(app, self.URL)

        self.assertTrue(opened)
        launcher.assert_called_once_with(app, self.URL)
        app.open_url.assert_not_called()

    def test_native_host_opens_with_the_browser_callback(self) -> None:
        app = Mock()
        browser_open = Mock(return_value=True)

        opened = external_url_launcher()(
            app,
            self.URL,
            browser_open=browser_open,
            in_container=False,
        )

        self.assertTrue(opened)
        browser_open.assert_called_once_with(self.URL)
        app.copy_to_clipboard.assert_not_called()

    def test_container_copies_url_for_the_host_without_launching_inside_docker(self) -> None:
        app = Mock()
        browser_open = Mock(return_value=True)

        opened = external_url_launcher()(
            app,
            self.URL,
            browser_open=browser_open,
            in_container=True,
        )

        self.assertTrue(opened)
        browser_open.assert_not_called()
        app.copy_to_clipboard.assert_called_once_with(self.URL)
        notice = str(app.notify.call_args.args[0])
        self.assertIn("link copied", notice)
        self.assertNotIn(self.URL, notice)

    def test_failed_native_browser_falls_back_to_terminal_clipboard(self) -> None:
        app = Mock()
        browser_open = Mock(return_value=False)

        opened = external_url_launcher()(
            app,
            self.URL,
            browser_open=browser_open,
            in_container=False,
        )

        self.assertTrue(opened)
        browser_open.assert_called_once_with(self.URL)
        app.copy_to_clipboard.assert_called_once_with(self.URL)

    def test_browser_and_clipboard_failures_are_reported_as_failure(self) -> None:
        app = Mock()
        app.copy_to_clipboard.side_effect = OSError("clipboard unavailable")
        browser_open = Mock(side_effect=OSError("browser unavailable"))

        opened = external_url_launcher()(
            app,
            self.URL,
            browser_open=browser_open,
            in_container=False,
        )

        self.assertFalse(opened)


if __name__ == "__main__":
    unittest.main()

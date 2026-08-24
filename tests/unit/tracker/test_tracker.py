"""The provider layer.

Fixtures here are trimmed copies of real responses -- Jira and Plane were
recorded from live instances, Trello is from the published API shape. Recording
them means CI needs neither network nor a token, and it means a provider
changing its wire format shows up as a failing test rather than as an empty
export.
"""

from __future__ import annotations

import json
import unittest
from datetime import date, datetime
from typing import Any, cast
from unittest.mock import Mock, PropertyMock, patch

import httpx
from pydantic import ValidationError

from pykantui.api.errors import PaginationError, PayloadError
from pykantui.providers import builtin_providers, verified_providers
from pykantui.providers.asana import AsanaProvider
from pykantui.providers.asana.schemas import ProjectWire as AsanaProjectWire
from pykantui.providers.asana.schemas import ReferenceWire as AsanaReferenceWire
from pykantui.providers.asana.schemas import UserWire as AsanaUserWire
from pykantui.providers.clickup import ClickUpProvider
from pykantui.providers.clickup import _epoch as _clickup_epoch
from pykantui.providers.clickup import _group_for as clickup_group
from pykantui.providers.github import GitHubProvider, is_pull_request
from pykantui.providers.jira import JiraProvider, _group_for
from pykantui.providers.jira.client import JiraApi, JiraClient
from pykantui.providers.linear import LinearProvider
from pykantui.providers.linear import _group_for as linear_group
from pykantui.providers.linear import _next_cursor as _linear_cursor
from pykantui.providers.monday import MondayProvider, _labels_from
from pykantui.providers.monday import _group_for as monday_group
from pykantui.providers.plane import PlaneProvider
from pykantui.providers.shortcut import ShortcutProvider
from pykantui.providers.shortcut import _group_for as shortcut_group
from pykantui.providers.trello import TrelloProvider
from pykantui.tracker import (
    COLUMN_BACKLOG,
    COLUMN_CANCELLED,
    COLUMN_DONE,
    COLUMN_REVIEW,
    COLUMN_STARTED,
    COLUMN_TODO,
    COLUMN_UNKNOWN,
    AuthError,
    IssueDraft,
    IssueEdit,
    NotFoundError,
    ProviderError,
    RemoteColumn,
    RemoteIssue,
    UnsupportedError,
    build,
    get,
    names,
    register,
    specs,
    unregister,
)
from pykantui.tracker.base import Provider
from pykantui.tracker.cache import TTL_ISSUES, TTL_STRUCTURE, ResponseCache
from pykantui.tracker.columns import group_from_name, resolve_group
from pykantui.tracker.http import JsonHttp, page_by_cursor, page_by_offset, page_by_token
from pykantui.tracker.markup import adf_to_markdown, html_to_markdown, to_markdown, wiki_to_markdown
from pykantui.tracker.models import RemoteProject, safe_name, slugify
from pykantui.tracker.spec import Capabilities, CredentialSetupKind, FieldKind, ProviderField, ProviderSpec
from pykantui.tracker.util import parse_date, parse_datetime, sort_key


class RegistryTests(unittest.TestCase):
    def test_the_builtins_are_registered(self) -> None:
        self.assertEqual(set(builtin_providers()), set(names()))

    def test_specs_are_sorted_and_complete(self) -> None:
        found = {spec.name: spec for spec in specs()}
        self.assertEqual(set(builtin_providers()), set(found))
        for spec in found.values():
            self.assertTrue(spec.label)
            self.assertTrue(spec.auth_fields, f"{spec.name} declares no credentials")

    def test_unknown_provider_names_what_is_available(self) -> None:
        with self.assertRaises(ProviderError) as caught:
            build("jra", {}, {})
        message = str(caught.exception)
        self.assertIn("jra", message)
        self.assertIn("jira", message)  # the hint lists the real ones

    def test_register_refuses_to_shadow_silently(self) -> None:
        register("temp", lambda: JiraProvider)
        try:
            with self.assertRaises(ValueError):
                register("temp", lambda: JiraProvider)
            register("temp", lambda: JiraProvider, replace=True)  # explicit is fine
        finally:
            unregister("temp")
        self.assertNotIn("temp", names())


class SpecTests(unittest.TestCase):
    def test_secret_is_derived_from_kind(self) -> None:
        self.assertTrue(ProviderField(name="t", label="T", kind=FieldKind.SECRET).secret)
        self.assertFalse(ProviderField(name="t", label="T").secret)

    def test_cli_flag_uses_dashes(self) -> None:
        self.assertEqual("--project-key", ProviderField(name="project_key", label="K").cli_flag)

    def test_secrets_cannot_be_declared_as_config(self) -> None:
        """The guard that stops a token being written to project.json."""
        with self.assertRaises(ValueError):
            ProviderSpec(
                name="x",
                label="X",
                config_fields=(ProviderField(name="token", label="Token", kind=FieldKind.SECRET),),
            )

    def test_every_builtin_keeps_secrets_out_of_config(self) -> None:
        for spec in specs():
            for field in spec.config_fields:
                self.assertFalse(field.secret, f"{spec.name}.{field.name} would be written in the clear")

    def test_env_lookup_takes_the_first_that_is_set(self) -> None:
        import os

        field = ProviderField(name="token", label="T", env_vars=("PYKANTUI_TEST_A", "PYKANTUI_TEST_B"))
        os.environ.pop("PYKANTUI_TEST_A", None)
        os.environ["PYKANTUI_TEST_B"] = "second"
        try:
            self.assertEqual("second", field.from_env())
        finally:
            os.environ.pop("PYKANTUI_TEST_B", None)


class ModelTests(unittest.TestCase):
    def test_safe_name_strips_characters_windows_rejects(self) -> None:
        self.assertEqual("a-b-c", safe_name("a<b>c"))
        self.assertEqual("PROJ-1", safe_name("PROJ-1"))
        self.assertEqual("a-b", safe_name("a/b"))
        self.assertEqual("a-b", safe_name("a|b"))

    def test_safe_name_escapes_windows_device_names(self) -> None:
        """CON.md is not a creatable file on Windows, whatever the extension."""
        self.assertEqual("_CON", safe_name("CON"))
        self.assertEqual("_NUL", safe_name("NUL"))
        self.assertEqual("CONTROL", safe_name("CONTROL"))  # only the exact names

    def test_safe_name_never_returns_empty(self) -> None:
        self.assertEqual("untitled", safe_name("..."))
        self.assertEqual("untitled", safe_name(""))

    def test_title_is_collapsed_to_one_line(self) -> None:
        """A newline in a title would break the YAML frontmatter it lands in."""
        issue = RemoteIssue(issue_id="1", title="first\nsecond")
        self.assertEqual("first second", issue.title)

    def test_unknown_column_group_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            RemoteColumn(column_id="1", name="X", group="sideways")  # type: ignore[arg-type]

    def test_filename_is_just_the_key_when_the_key_is_human(self) -> None:
        """Appending a title would churn the filename on every rename."""
        issue = RemoteIssue(issue_id="10", key="JPT-4", title="Task 1")
        self.assertEqual("JPT-4.md", issue.filename())
        self.assertEqual("sc-77.md", RemoteIssue(issue_id="7", key="sc-77", title="x").filename())

    def test_a_digits_only_key_gains_a_title_slug(self) -> None:
        """Asana, Monday and Trello have no human key; 1201234567.md is unreadable."""
        issue = RemoteIssue(issue_id="1201234567", key="1201234567", title="Ship the thing")
        self.assertEqual("1201234567-ship-the-thing.md", issue.filename())

    def test_the_id_stays_in_front_of_the_slug(self) -> None:
        """So files still sort by age and stay unique when titles collide."""
        a = RemoteIssue(issue_id="1", key="100", title="Same title").filename()
        b = RemoteIssue(issue_id="2", key="200", title="Same title").filename()
        self.assertNotEqual(a, b)
        self.assertTrue(a.startswith("100-"))

    def test_an_untitled_numeric_issue_still_gets_a_filename(self) -> None:
        self.assertEqual("998877.md", RemoteIssue(issue_id="998877", key="998877").filename())

    def test_slugify_truncates_on_a_word_boundary(self) -> None:
        self.assertEqual("ship-the-thing", slugify("Ship the thing"))
        self.assertEqual("customize-your-settings", slugify("Customize your settings ⚙️"))
        # cut short rather than ending mid-word
        self.assertEqual("one-two", slugify("one two threeeeeeee", limit=10))

    def test_slugify_drops_non_ascii_rather_than_transliterating(self) -> None:
        """These land in git paths and shell commands."""
        self.assertEqual("welcome-to-plane", slugify("Welcome to Plane 👋"))
        self.assertEqual("", slugify("👋🎯"))

    def test_project_path_is_one_segment_by_default(self) -> None:
        self.assertEqual(("JPT",), RemoteProject(project_id="1", key="JPT").path_parts())

    def test_an_owner_becomes_a_parent_directory(self) -> None:
        """owner/repo cannot be one directory, and flattening loses a distinction."""
        project = RemoteProject(project_id="acme/widgets", key="widgets", owner="acme")
        self.assertEqual(("acme", "widgets"), project.path_parts())

    def test_a_slash_in_an_owner_is_still_sanitised(self) -> None:
        """Nested owner names remain one safe parent-directory segment."""
        project = RemoteProject(project_id="1", key="app", owner="group/subgroup")
        self.assertEqual(("group-subgroup", "app"), project.path_parts())

    def test_blank_labels_are_dropped(self) -> None:
        issue = RemoteIssue.model_validate({"issue_id": "1", "labels": ["a", "", "  ", "b"]})
        self.assertEqual(("a", "b"), issue.labels)

    def test_issues_are_frozen(self) -> None:
        """A provider's answer describes one moment; mutating it in place would
        change what another part of the sync is still reading."""
        issue = RemoteIssue(issue_id="1", title="x")
        with self.assertRaises(ValidationError):
            issue.title = "y"


class PagingTests(unittest.TestCase):
    def test_token_paging_stops_on_is_last(self) -> None:
        pages = [
            {"issues": [1, 2], "nextPageToken": "t1", "isLast": False},
            {"issues": [3], "isLast": True},
        ]
        seen: list[str | None] = []

        def fetch(token: str | None) -> Any:
            seen.append(token)
            return pages[len(seen) - 1]

        self.assertEqual([1, 2, 3], list(page_by_token(fetch, items_key="issues")))
        self.assertEqual([None, "t1"], seen)

    def test_cursor_paging_ignores_a_cursor_on_the_last_page(self) -> None:
        """Plane sends next_cursor on every page, including the last one.

        Ending the loop on "there is a cursor" therefore never ends it. This is
        the regression test for that: page two says next_page_results=False but
        still carries a cursor, and the loop must stop anyway.
        """
        pages = [
            {"results": [1], "next_cursor": "c1", "next_page_results": True},
            {"results": [2], "next_cursor": "c2", "next_page_results": False},
        ]
        calls: list[str | None] = []

        def fetch(cursor: str | None) -> Any:
            calls.append(cursor)
            return pages[len(calls) - 1]

        self.assertEqual([1, 2], list(page_by_cursor(fetch)))
        self.assertEqual(2, len(calls), "kept paging past the last page")

    def test_offset_paging_stops_on_a_short_page(self) -> None:
        """Ends on a short page rather than on `total`, which Jira stopped sending."""
        pages = [{"values": [1, 2]}, {"values": [3]}]
        calls: list[int] = []

        def fetch(start: int, size: int) -> Any:
            calls.append(start)
            return pages[len(calls) - 1]

        self.assertEqual([1, 2, 3], list(page_by_offset(fetch, page_size=2)))
        self.assertEqual([0, 2], calls)

    def test_a_repeated_cursor_is_an_incomplete_sync_not_success(self) -> None:
        def fetch(cursor: str | None) -> Any:
            return {"results": [cursor or "first"], "next_cursor": "same", "next_page_results": True}

        with self.assertRaisesRegex(PaginationError, "repeated cursor"):
            list(page_by_cursor(fetch))

    def test_cursor_paging_fails_closed_when_more_is_true_without_a_cursor(self) -> None:
        def fetch(cursor: str | None) -> Any:
            return {"results": [1], "next_cursor": "", "next_page_results": True}

        with self.assertRaisesRegex(PaginationError, "missing next cursor"):
            list(page_by_cursor(fetch))

    def test_hitting_the_page_safety_limit_is_not_silent_truncation(self) -> None:
        def fetch(start: int, size: int) -> Any:
            return {"values": list(range(start, start + size))}

        with self.assertRaisesRegex(PaginationError, "safety limit"):
            list(page_by_offset(fetch, page_size=2, max_pages=2))

    def test_non_positive_pagination_limits_are_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "max_pages"):
            list(page_by_token(lambda token: None, max_pages=0))
        with self.assertRaisesRegex(ValueError, "page_size"):
            list(page_by_offset(lambda start, size: None, page_size=0))


class MarkupTests(unittest.TestCase):
    def test_adf_headings_lists_and_marks(self) -> None:
        document = {
            "type": "doc",
            "content": [
                {"type": "heading", "attrs": {"level": 2}, "content": [{"type": "text", "text": "Title"}]},
                {
                    "type": "paragraph",
                    "content": [
                        {"type": "text", "text": "bold", "marks": [{"type": "strong"}]},
                        {"type": "text", "text": " and "},
                        {
                            "type": "text",
                            "text": "link",
                            "marks": [{"type": "link", "attrs": {"href": "https://example.com"}}],
                        },
                    ],
                },
                {
                    "type": "bulletList",
                    "content": [
                        {
                            "type": "listItem",
                            "content": [{"type": "paragraph", "content": [{"type": "text", "text": "one"}]}],
                        },
                    ],
                },
            ],
        }
        result = adf_to_markdown(document)
        self.assertIn("## Title", result)
        self.assertIn("**bold**", result)
        self.assertIn("[link](https://example.com)", result)
        self.assertIn("- one", result)

    def test_adf_code_block_keeps_its_language(self) -> None:
        document = {
            "type": "doc",
            "content": [
                {"type": "codeBlock", "attrs": {"language": "python"}, "content": [{"type": "text", "text": "x = 1"}]}
            ],
        }
        self.assertIn("```python\nx = 1\n```", adf_to_markdown(document))

    def test_unknown_adf_nodes_keep_their_text(self) -> None:
        """An exotic node should cost its formatting, never its content."""
        document = {
            "type": "doc",
            "content": [
                {
                    "type": "someFutureThing",
                    "content": [{"type": "paragraph", "content": [{"type": "text", "text": "kept"}]}],
                }
            ],
        }
        self.assertIn("kept", adf_to_markdown(document))

    def test_html_conversion(self) -> None:
        html = "<h2>Head</h2><p>a <strong>b</strong> <a href='http://x.dev'>c</a></p><ul><li>one</li><li>two</li></ul>"
        result = html_to_markdown(html)
        self.assertIn("## Head", result)
        self.assertIn("**b**", result)
        self.assertIn("[c](http://x.dev)", result)
        self.assertIn("- one", result)
        self.assertIn("- two", result)

    def test_wiki_conversion(self) -> None:
        wiki = "h1. Title\n\n*bold* and _italic_\n\n* one\n* two\n\n{code:python}\nx = 1\n{code}"
        result = wiki_to_markdown(wiki)
        self.assertIn("# Title", result)
        self.assertIn("**bold**", result)
        self.assertIn("*italic*", result)
        self.assertIn("- one", result)
        self.assertIn("```python", result)

    def test_wiki_code_blocks_are_not_mangled_by_inline_rules(self) -> None:
        """An asterisk inside a code block is a glob, not emphasis."""
        result = wiki_to_markdown("{code}\nrm *.py\n{code}")
        self.assertIn("rm *.py", result)
        self.assertNotIn("**", result)

    def test_to_markdown_sniffs_the_format(self) -> None:
        self.assertEqual("", to_markdown(None))
        self.assertEqual("plain text", to_markdown("plain text"))
        self.assertIn("## H", to_markdown("<h2>H</h2>"))
        self.assertIn("# H", to_markdown("h1. H"))
        self.assertIn(
            "hello",
            to_markdown(
                {"type": "doc", "content": [{"type": "paragraph", "content": [{"type": "text", "text": "hello"}]}]}
            ),
        )

    def test_a_dict_never_leaks_into_the_output(self) -> None:
        """The failure this guards: `{'type': 'doc', ...}` written into a file."""
        result = to_markdown(
            {"type": "doc", "content": [{"type": "paragraph", "content": [{"type": "text", "text": "real text"}]}]}
        )
        self.assertNotIn("'type'", result)
        self.assertEqual("real text", result)


class UtilTests(unittest.TestCase):
    def test_parses_each_providers_timestamp_format(self) -> None:
        cases = {
            "2026-08-07T20:56:03.206-0500": "jira, colon-less offset",
            "2026-07-20T10:39:50.910392Z": "plane, Z suffix",
            "2026-08-07T21:03:11.000Z": "trello",
        }
        for value, why in cases.items():
            self.assertIsInstance(parse_datetime(value), datetime, why)

    def test_unreadable_values_become_none_not_an_exception(self) -> None:
        values: tuple[object, ...] = (None, "", "not a date", 42, {})
        for value in values:
            self.assertIsNone(parse_datetime(value))
            self.assertIsNone(parse_date(value))

    def test_parse_date_narrows_a_timestamp(self) -> None:
        self.assertEqual(date(2026, 8, 18), parse_date("2026-08-18"))
        self.assertEqual(date(2026, 8, 7), parse_date("2026-08-07T20:56:03.206-0500"))

    def test_sort_key_is_always_comparable(self) -> None:
        """The bug this prevents: sorting a list where one position is null."""
        values: list[dict[str, object]] = [{"p": 2.0}, {"p": None}, {"p": "1"}]
        ordered = sorted(values, key=lambda item: sort_key(item["p"]))
        self.assertEqual([None, "1", 2.0], [item["p"] for item in ordered])


#: Trimmed from a real response, board 3 of a live Cloud site.
JIRA_ISSUE = {
    "id": "10018",
    "key": "JPT-4",
    "fields": {
        "summary": "Task 1",
        "description": None,
        "status": {"id": "10009", "name": "In Progress", "statusCategory": {"key": "indeterminate"}},
        "issuetype": {"name": "Task"},
        "assignee": {"displayName": "alex"},
        "reporter": {"displayName": "alex"},
        "labels": ["backend"],
        "components": [{"id": "10001", "name": "API"}, {"id": "10002", "name": "Platform"}],
        "priority": {"name": "Medium"},
        "created": "2026-08-07T20:56:05.516-0500",
        "updated": "2026-08-07T21:00:00.000-0500",
        "duedate": "2026-08-09",
        "parent": {"key": "JPT-1"},
        "resolutiondate": None,
    },
}


class JiraProviderTests(unittest.TestCase):
    def setUp(self) -> None:
        self.provider = JiraProvider(
            {"base_url": "https://example.atlassian.net", "project_key": "JPT"},
            {"email": "a@b.c", "token": "x"},
        )

    def test_issue_mapping(self) -> None:
        issue = self.provider._to_issue(JIRA_ISSUE, "https://example.atlassian.net")
        self.assertEqual("JPT-4", issue.key)
        self.assertEqual("Task 1", issue.title)
        self.assertEqual("10009", issue.column_id)
        self.assertEqual("In Progress", issue.status)
        self.assertEqual("Task", issue.issue_type)
        self.assertEqual("alex", issue.assignee)
        self.assertEqual(("backend",), issue.labels)
        self.assertEqual(("API", "Platform"), issue.components)
        self.assertEqual("JPT-1", issue.parent_key)
        self.assertEqual(date(2026, 8, 9), issue.due_date)
        self.assertEqual("https://example.atlassian.net/browse/JPT-4", issue.url)
        self.assertEqual("JPT-4.md", issue.filename())

    def test_missing_nested_fields_do_not_raise(self) -> None:
        """An unassigned issue has assignee: null, which is ordinary."""
        bare = {"id": "1", "key": "X-1", "fields": {"summary": "s", "status": {}}}
        issue = self.provider._to_issue(bare, "https://example.atlassian.net")
        self.assertEqual("", issue.assignee)
        self.assertEqual("", issue.priority)
        self.assertIsNone(issue.due_date)

    def test_jql_is_deterministically_ordered(self) -> None:
        """Without ORDER BY, a re-export produces a diff made of reshuffling."""
        self.assertEqual("project = JPT ORDER BY key ASC", self.provider._jql("JPT"))

    def test_extra_jql_is_parenthesised(self) -> None:
        """Unbracketed, `a OR b` would swallow the project filter."""
        provider = JiraProvider({"base_url": "https://x", "jql": "a OR b"}, {"email": "e", "token": "t"})
        self.assertEqual("project = JPT AND (a OR b) ORDER BY key ASC", provider._jql("JPT"))

    def test_column_grouping_by_name_and_category(self) -> None:
        # Board path: names only, no category available.
        self.assertEqual(COLUMN_TODO, _group_for("To Do", ""))
        self.assertEqual(COLUMN_STARTED, _group_for("In Progress", ""))
        self.assertEqual(COLUMN_DONE, _group_for("Done", ""))
        # Status path: category available.
        self.assertEqual(COLUMN_TODO, _group_for("To Do", "new"))
        self.assertEqual(COLUMN_DONE, _group_for("Done", "done"))
        # Names that beat the category they were given.
        self.assertEqual(COLUMN_REVIEW, _group_for("In Review", "indeterminate"))
        self.assertEqual(COLUMN_BACKLOG, _group_for("Backlog", "new"))

    def test_move_needs_a_transition_that_exists(self) -> None:
        self.provider._transitions["JPT-4"] = {"10011": "41"}
        with self.assertRaises(ProviderError) as caught:
            self.provider.move_issue(
                RemoteIssue(issue_id="1", key="JPT-4"),
                RemoteColumn(column_id="99999", name="Nowhere"),
            )
        self.assertIn("no transition", str(caught.exception))


#: Trimmed from a real response, a live workspace.
PLANE_ISSUE = {
    "id": "3f1c",
    "sequence_id": 7,
    "name": "6. Customize your settings",
    "description_html": "<p>Now that you're <strong>familiar</strong>...</p><h2>Workspace settings</h2>",
    "state": "state-uuid",
    "state_group": "started",
    "priority": "none",
    "assignees": ["member-uuid"],
    "labels": [],
    "created_at": "2026-07-20T10:39:50.910392Z",
    "updated_at": "2026-07-21T10:39:50.910392Z",
    "target_date": "2026-08-30",
    "completed_at": None,
    "sort_order": 65535.0,
    "parent": None,
}


class PlaneProviderTests(unittest.TestCase):
    def setUp(self) -> None:
        self.provider = PlaneProvider({"workspace": "acme", "project_id": "p1"}, {"token": "t"})

    def test_issue_mapping_builds_a_human_key_and_markdown_body(self) -> None:
        issue = self.provider._to_issue(
            PLANE_ISSUE, "acme", "p1", "ACME", {"state-uuid": "In Progress"}, {"member-uuid": "alex"}
        )
        self.assertEqual("ACME-7", issue.key)
        self.assertEqual("In Progress", issue.status)
        self.assertEqual("alex", issue.assignee, "assignee UUID was not resolved to a name")
        self.assertIn("**familiar**", issue.body)
        self.assertIn("## Workspace settings", issue.body)
        self.assertEqual(date(2026, 8, 30), issue.due_date)
        self.assertEqual(65535.0, issue.position)

    def test_priority_none_is_treated_as_empty(self) -> None:
        self.assertEqual("", self.provider._to_issue(PLANE_ISSUE, "w", "p", "K", {}, {}).priority)

    def test_falls_back_to_the_uuid_when_there_is_no_identifier(self) -> None:
        issue = self.provider._to_issue(PLANE_ISSUE, "w", "p", "", {}, {})
        self.assertEqual("3f1c", issue.key)


#: From Trello's published card shape. Unlike the two above, not yet observed.
TRELLO_CARD = {
    "id": "abc123",
    "idShort": 4,
    "name": "Ship the thing",
    "desc": "Already **markdown**.",
    "idList": "list1",
    "due": "2026-08-20T12:00:00.000Z",
    "dueComplete": True,
    "pos": 65535,
    "url": "https://trello.com/c/abc123",
    "labels": [{"name": "urgent", "color": "red"}, {"name": "", "color": "blue"}],
    "dateLastActivity": "2026-08-07T21:03:11.000Z",
}


class TrelloProviderTests(unittest.TestCase):
    def setUp(self) -> None:
        self.provider = TrelloProvider({"board_id": "b1"}, {"key": "k", "token": "t"})

    def test_card_mapping(self) -> None:
        issue = self.provider._to_issue(TRELLO_CARD, {"list1": "Done"})
        self.assertEqual("CARD-4", issue.key)
        self.assertEqual("Ship the thing", issue.title)
        self.assertEqual("Already **markdown**.", issue.body, "Trello desc is markdown; do not convert it")
        self.assertEqual("Done", issue.status)
        self.assertEqual(("urgent", "blue"), issue.labels)
        self.assertEqual(date(2026, 8, 20), issue.due_date)

    def test_live_member_shape_allows_a_hidden_email(self) -> None:
        from pykantui.providers.trello.mapper import member_to_remote
        from pykantui.providers.trello.schemas import MemberWire

        member = MemberWire.model_validate(
            {"id": "member-1", "fullName": "Alex", "username": "alex", "email": None}
        )

        self.assertEqual("", member_to_remote(member).email)

    def test_due_complete_drives_finished_at(self) -> None:
        self.assertIsNotNone(self.provider._to_issue(TRELLO_CARD, {}).finished_at)
        open_card = {**TRELLO_CARD, "dueComplete": False}
        self.assertIsNone(self.provider._to_issue(open_card, {}).finished_at)

    def test_list_names_are_grouped(self) -> None:
        columns = {
            "Backlog": COLUMN_BACKLOG,
            "To Do": COLUMN_TODO,
            "In Progress": COLUMN_STARTED,
            "Code Review": COLUMN_REVIEW,
            "Done": COLUMN_DONE,
        }
        for name, expected in columns.items():
            with self.subTest(name=name):
                from pykantui.providers.trello import _group_for as trello_group

                self.assertEqual(expected, trello_group(name))


#: From Monday's published GraphQL shape. Unverified, like Trello.
MONDAY_ITEM = {
    "id": "998877",
    "name": "Ship the thing",
    "created_at": "2026-08-01T09:00:00Z",
    "updated_at": "2026-08-07T09:00:00Z",
    "group": {"id": "topics", "title": "This week"},
    "column_values": [
        {"id": "status", "type": "status", "text": "Working on it", "value": '{"index":0}'},
        {"id": "date4", "type": "date", "text": "2026-08-20", "value": '{"date":"2026-08-20"}'},
        {"id": "long_text", "type": "long_text", "text": "Some detail.", "value": None},
        {"id": "person", "type": "people", "text": "alex", "value": None},
    ],
}

#: A status column's settings arrive as a JSON string nested in the JSON body.
MONDAY_SETTINGS = '{"labels":{"0":"Working on it","1":"Done","5":"Stuck"}}'


class MondayProviderTests(unittest.TestCase):
    def setUp(self) -> None:
        self.provider = MondayProvider(
            {"board_id": "123", "description_column": "long_text", "assignee_column": "person", "due_column": "date4"},
            {"token": "t"},
        )

    def test_labels_are_decoded_from_the_nested_json_string(self) -> None:
        self.assertEqual(
            {"0": "Working on it", "1": "Done", "5": "Stuck"},
            _labels_from(MONDAY_SETTINGS),
        )

    def test_malformed_settings_do_not_raise(self) -> None:
        for value in (None, "", "not json", "[]", 42):
            self.assertEqual({}, _labels_from(value))

    def test_item_maps_onto_the_status_axis(self) -> None:
        labels = _labels_from(MONDAY_SETTINGS)
        issue = self.provider._to_issue(MONDAY_ITEM, "123", "status", labels)
        self.assertEqual("0", issue.column_id, "should key on the index, not the label text")
        self.assertEqual("Working on it", issue.status)
        self.assertEqual("Some detail.", issue.body)
        self.assertEqual("alex", issue.assignee)
        self.assertEqual(date(2026, 8, 20), issue.due_date)

    def test_falls_back_to_the_group_without_a_status_column(self) -> None:
        issue = self.provider._to_issue(MONDAY_ITEM, "123", "", {})
        self.assertEqual("topics", issue.column_id)
        self.assertEqual("This week", issue.status)

    def test_status_labels_are_grouped(self) -> None:
        self.assertEqual(COLUMN_STARTED, monday_group("Working on it"))
        self.assertEqual(COLUMN_STARTED, monday_group("Stuck"))
        self.assertEqual(COLUMN_DONE, monday_group("Done"))
        self.assertEqual(COLUMN_TODO, monday_group("Not Started"))


class GraphQLTests(unittest.TestCase):
    """GraphQL reports failure at HTTP 200, so the payload must be checked."""

    def _client(self, payload: dict[str, Any]) -> JsonHttp:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=payload)

        return JsonHttp(
            "https://api.example.com",
            client=httpx.Client(base_url="https://api.example.com", transport=httpx.MockTransport(handler)),
        )

    def test_data_is_returned_on_success(self) -> None:
        client = self._client({"data": {"me": {"id": "1"}}})
        self.assertEqual({"me": {"id": "1"}}, client.graphql("query { me { id } }"))

    def test_errors_at_status_200_still_raise(self) -> None:
        client = self._client({"data": None, "errors": [{"message": "Field 'nope' doesn't exist"}]})
        with self.assertRaises(ProviderError) as caught:
            client.graphql("query { nope }")
        self.assertIn("doesn't exist", str(caught.exception))

    def test_an_auth_error_in_the_body_is_recovered_as_autherror(self) -> None:
        """Without this, a bad Monday token looks like a generic failure."""
        client = self._client({"errors": [{"message": "Not authenticated"}]})
        with self.assertRaises(AuthError):
            client.graphql("query { me { id } }")

    def test_partial_graphql_data_with_errors_is_an_ambiguous_payload(self) -> None:
        client = self._client({
            "data": {"commentCreate": {"success": True, "comment": None}},
            "errors": [{"message": "comment record unavailable"}],
        })

        with self.assertRaises(PayloadError):
            client.graphql("mutation { commentCreate { success } }")

    def test_partial_graphql_data_stays_ambiguous_even_for_an_auth_word(self) -> None:
        client = self._client({
            "data": {"commentCreate": {"success": True, "comment": None}},
            "errors": [{"message": "forbidden nested field"}],
        })

        with self.assertRaises(PayloadError):
            client.graphql("mutation { commentCreate { success } }")


LINEAR_ISSUE = {
    "id": "uuid-1",
    "identifier": "ENG-42",
    "title": "Ship the thing",
    "description": "Already **markdown**.",
    "url": "https://linear.app/acme/issue/ENG-42",
    "priorityLabel": "High",
    "sortOrder": 12.5,
    "createdAt": "2026-08-01T09:00:00.000Z",
    "updatedAt": "2026-08-07T09:00:00.000Z",
    "startedAt": "2026-08-02T09:00:00.000Z",
    "completedAt": None,
    "dueDate": "2026-08-20",
    "state": {"id": "state-1", "name": "In Progress"},
    "assignee": {"displayName": "alex"},
    "creator": {"displayName": "sam"},
    "parent": {"identifier": "ENG-1"},
    "labels": {"nodes": [{"name": "backend"}, {"name": "urgent"}]},
}


class LinearProviderTests(unittest.TestCase):
    def setUp(self) -> None:
        self.provider = LinearProvider({"team_id": "t1"}, {"token": "k"})

    def test_issue_mapping(self) -> None:
        issue = self.provider._to_issue(LINEAR_ISSUE)
        self.assertEqual("ENG-42", issue.key)
        self.assertEqual("state-1", issue.column_id)
        self.assertEqual("In Progress", issue.status)
        self.assertEqual("alex", issue.assignee)
        self.assertEqual("sam", issue.reporter)
        self.assertEqual(("backend", "urgent"), issue.labels)
        self.assertEqual("ENG-1", issue.parent_key)
        self.assertEqual(date(2026, 8, 20), issue.due_date)
        self.assertEqual(12.5, issue.position)

    def test_state_types_map_without_guesswork(self) -> None:
        self.assertEqual(COLUMN_BACKLOG, linear_group("Triage", "triage"))
        self.assertEqual(COLUMN_TODO, linear_group("Todo", "unstarted"))
        self.assertEqual(COLUMN_STARTED, linear_group("In Progress", "started"))
        self.assertEqual(COLUMN_DONE, linear_group("Done", "completed"))

    def test_review_is_promoted_out_of_started(self) -> None:
        """Linear has no review type, so the name has to rescue that one."""
        self.assertEqual(COLUMN_REVIEW, linear_group("In Review", "started"))

    def test_paging_stops_when_has_next_page_is_false(self) -> None:
        """endCursor is present on the last page too; hasNextPage is the signal."""
        self.assertIsNone(_linear_cursor({"pageInfo": {"hasNextPage": False, "endCursor": "c1"}}))
        self.assertEqual("c1", _linear_cursor({"pageInfo": {"hasNextPage": True, "endCursor": "c1"}}))


GITHUB_ISSUE = {
    "id": 900,
    "number": 42,
    "title": "Ship the thing",
    "body": "Already **markdown**.",
    "state": "open",
    "type": {"id": 1, "name": "Task"},
    "labels": [{"name": "status:in progress"}, {"name": "bug"}],
    "assignees": [{"login": "alex"}],
    "user": {"login": "sam"},
    "created_at": "2026-08-01T09:00:00Z",
    "updated_at": "2026-08-07T09:00:00Z",
    "closed_at": None,
    "html_url": "https://github.com/acme/app/issues/42",
}


class GitHubProviderTests(unittest.TestCase):
    def setUp(self) -> None:
        self.provider = GitHubProvider({"repo": "acme/app"}, {"token": "t"})

    def test_status_label_becomes_the_column(self) -> None:
        issue = self.provider._to_issue(GITHUB_ISSUE, "acme/app", "status:")
        self.assertEqual("status:in progress", issue.column_id)
        self.assertEqual("in progress", issue.status)
        self.assertEqual("app#42", issue.key)
        self.assertEqual("Task", issue.issue_type)

    def test_the_status_label_is_not_also_listed_as_a_label(self) -> None:
        """Listing it twice would put it in the frontmatter under two meanings."""
        issue = self.provider._to_issue(GITHUB_ISSUE, "acme/app", "status:")
        self.assertEqual(("bug",), issue.labels)

    def test_falls_back_to_open_and_closed(self) -> None:
        bare = {**GITHUB_ISSUE, "labels": [{"name": "bug"}]}
        self.assertEqual("state:open", self.provider._to_issue(bare, "acme/app", "status:").column_id)
        closed = {**bare, "state": "closed"}
        self.assertEqual("state:closed", self.provider._to_issue(closed, "acme/app", "status:").column_id)

    def test_pull_requests_are_not_board_cards(self) -> None:
        """GitHub returns PRs from the issues endpoint, marked only by one key."""
        self.assertFalse(is_pull_request(GITHUB_ISSUE))
        self.assertTrue(is_pull_request({**GITHUB_ISSUE, "pull_request": {"url": "..."}}))

    def test_single_issue_refresh_uses_the_issue_endpoint(self) -> None:
        requested: list[str] = []

        class FakeHttp:
            def get(self, path: str, params: Any = None, **_: Any) -> Any:
                requested.append(path)
                return GITHUB_ISSUE

        self.provider._http = cast(JsonHttp, FakeHttp())
        refreshed = self.provider.get_issue(
            "acme/app",
            RemoteIssue(issue_id="900", extra={"number": 42}),
        )

        self.assertEqual(["/repos/acme/app/issues/42"], requested)
        self.assertIsNotNone(refreshed)
        assert refreshed is not None
        self.assertEqual("Ship the thing", refreshed.title)

    def test_single_issue_refresh_returns_none_when_the_issue_is_gone(self) -> None:
        class MissingHttp:
            def get(self, path: str, params: Any = None, **_: Any) -> Any:
                raise NotFoundError(path)

        self.provider._http = cast(JsonHttp, MissingHttp())

        refreshed = self.provider.get_issue(
            "acme/app",
            RemoteIssue(issue_id="900", extra={"number": 42}),
        )

        self.assertIsNone(refreshed)

    def test_repository_issue_types_are_discovered_and_mapped(self) -> None:
        class FakeHttp:
            def get(self, path: str, params: Any = None, **_: Any) -> Any:
                self.path = path
                return [{"id": 1, "name": "Task"}, {"id": 2, "name": "Bug"}]

        fake = FakeHttp()
        self.provider._http = cast(JsonHttp, fake)

        issue_types = self.provider.list_issue_types("acme/app")

        self.assertEqual("/repos/acme/app/issue-types", fake.path)
        self.assertEqual(["Task", "Bug"], [item.name for item in issue_types])

    def test_type_is_hidden_when_the_repository_has_no_issue_types(self) -> None:
        class FakeHttp:
            def get(self, path: str, params: Any = None, **_: Any) -> Any:
                return []

        self.provider._http = cast(JsonHttp, FakeHttp())

        self.assertNotIn("issue_type", self.provider.editable_card_fields())
        self.assertNotIn("issue_type", self.provider.creatable_card_fields())


ASANA_TASK = {
    "gid": "1001",
    "name": "Ship the thing",
    "notes": "Plain text.",
    "completed": False,
    "created_at": "2026-08-01T09:00:00.000Z",
    "modified_at": "2026-08-07T09:00:00.000Z",
    "due_on": "2026-08-20",
    "assignee": {"name": "alex"},
    "tags": [{"name": "urgent"}],
    "permalink_url": "https://app.asana.com/0/1/1001",
    "memberships": [
        {"project": {"gid": "OTHER"}, "section": {"gid": "sec-x", "name": "Wrong project"}},
        {"project": {"gid": "PROJ"}, "section": {"gid": "sec-1", "name": "In Progress"}},
    ],
}


class AsanaProviderTests(unittest.TestCase):
    def setUp(self) -> None:
        self.provider = AsanaProvider({"project_id": "PROJ"}, {"token": "t"})

    def test_section_is_taken_from_the_matching_project(self) -> None:
        """A task can sit in several projects; membership[0] is the wrong answer."""
        issue = self.provider._to_issue(ASANA_TASK, "PROJ")
        self.assertEqual("sec-1", issue.column_id)
        self.assertEqual("In Progress", issue.status)

    def test_a_task_with_no_memberships_does_not_raise(self) -> None:
        issue = self.provider._to_issue({**ASANA_TASK, "memberships": []}, "PROJ")
        self.assertEqual("", issue.column_id)

    def test_task_mapping(self) -> None:
        issue = self.provider._to_issue(ASANA_TASK, "PROJ")
        self.assertEqual("alex", issue.assignee)
        self.assertEqual(("urgent",), issue.labels)
        self.assertEqual(date(2026, 8, 20), issue.due_date)

    def test_unscoped_discovery_lists_projects_from_every_accessible_workspace(self) -> None:
        """An account with five workspaces must not silently use the first one."""
        api = Mock()
        api.workspaces.return_value = [
            AsanaUserWire(gid="W1", name="Engineering"),
            AsanaUserWire(gid="W2", name="Operations"),
        ]
        api.projects.side_effect = lambda params: iter(
            [
                AsanaProjectWire(
                    gid=f"P-{params['workspace']}",
                    name="Roadmap",
                    workspace=AsanaReferenceWire(
                        gid=str(params["workspace"]),
                        name="Engineering" if params["workspace"] == "W1" else "Operations",
                    ),
                )
            ]
        )
        provider = AsanaProvider({"project_id": "PROJ"}, {"token": "t"})

        with patch.object(AsanaProvider, "api", new_callable=PropertyMock, return_value=api):
            projects = provider.list_projects()

        self.assertEqual(["P-W1", "P-W2"], [project.project_id for project in projects])
        self.assertEqual(["Engineering", "Operations"], [project.extra["workspace_name"] for project in projects])
        self.assertEqual(["W1", "W2"], [call.args[0]["workspace"] for call in api.projects.call_args_list])

    def test_configured_asana_workspace_remains_an_explicit_discovery_scope(self) -> None:
        api = Mock()
        api.projects.return_value = iter([])
        provider = AsanaProvider(
            {"project_id": "PROJ", "workspace": "W2"},
            {"token": "t"},
        )

        with patch.object(AsanaProvider, "api", new_callable=PropertyMock, return_value=api):
            provider.list_projects()

        api.workspaces.assert_not_called()
        self.assertEqual("W2", api.projects.call_args.args[0]["workspace"])


CLICKUP_TASK = {
    "id": "abc",
    "custom_id": "DEV-9",
    "name": "Ship the thing",
    "text_content": "Plain body.",
    "status": {"status": "in progress", "type": "custom"},
    "priority": {"priority": "high"},
    "assignees": [{"username": "alex"}],
    "creator": {"username": "sam"},
    "tags": [{"name": "urgent"}],
    "date_created": "1785000000000",
    "date_updated": "1785600000000",
    "start_date": None,
    "date_closed": None,
    "due_date": "1786000000000",
    "orderindex": "3.5",
    "url": "https://app.clickup.com/t/abc",
}


class ClickUpProviderTests(unittest.TestCase):
    def setUp(self) -> None:
        self.provider = ClickUpProvider({"list_id": "l1"}, {"token": "t"})

    def test_task_mapping_prefers_the_custom_id(self) -> None:
        issue = self.provider._to_issue(CLICKUP_TASK)
        self.assertEqual("DEV-9", issue.key)
        self.assertEqual("in progress", issue.column_id)
        self.assertEqual("alex", issue.assignee)
        self.assertEqual(3.5, issue.position)

    def test_epoch_milliseconds_are_decoded(self) -> None:
        issue = self.provider._to_issue(CLICKUP_TASK)
        self.assertIsNotNone(issue.created_at)
        self.assertIsNotNone(issue.due_date)

    def test_a_missing_date_does_not_become_1970(self) -> None:
        """The bug this guards: an unstarted task reported as started at the epoch."""
        issue = self.provider._to_issue(CLICKUP_TASK)
        self.assertIsNone(issue.started_at)
        self.assertIsNone(issue.finished_at)
        for empty in (None, "", 0, "0"):
            self.assertIsNone(_clickup_epoch(empty))

    def test_status_types_and_names_both_contribute(self) -> None:
        self.assertEqual(COLUMN_DONE, clickup_group("complete", "closed"))
        self.assertEqual(COLUMN_REVIEW, clickup_group("in review", "custom"))
        self.assertEqual(COLUMN_TODO, clickup_group("anything", "open"))


SHORTCUT_STORY = {
    "id": 77,
    "name": "Ship the thing",
    "description": "Already **markdown**.",
    "workflow_state_id": 500,
    "story_type": "feature",
    "owner_ids": ["alex"],
    "labels": [{"name": "urgent"}],
    "created_at": "2026-08-01T09:00:00Z",
    "updated_at": "2026-08-07T09:00:00Z",
    "started_at": "2026-08-02T09:00:00Z",
    "completed_at": None,
    "deadline": "2026-08-20T00:00:00Z",
    "epic_id": 3,
    "position": 100,
    "app_url": "https://app.shortcut.com/acme/story/77",
}


class ShortcutProviderTests(unittest.TestCase):
    def setUp(self) -> None:
        self.provider = ShortcutProvider({"workflow_id": "1"}, {"token": "t"})

    def test_story_mapping(self) -> None:
        issue = self.provider._to_issue(SHORTCUT_STORY, {"500": "In Progress"})
        self.assertEqual("sc-77", issue.key)
        self.assertEqual("500", issue.column_id)
        self.assertEqual("In Progress", issue.status)
        self.assertEqual("feature", issue.issue_type)
        self.assertEqual(date(2026, 8, 20), issue.due_date)
        self.assertEqual("3", issue.parent_key)

    def test_owner_ids_are_ids_not_names(self) -> None:
        """They were being rendered as the assignee, so cards showed raw ids.

        A directory is what turns one into a name; without it the field is
        empty, which is honest, where a UUID in the assignee line is not.
        """
        issue = self.provider._to_issue(SHORTCUT_STORY, {"500": "In Progress"})

        self.assertEqual(("alex",), issue.assignee_ids)
        self.assertEqual("", issue.assignee)

    def test_a_directory_resolves_them_to_people(self) -> None:
        issue = self.provider._to_issue(SHORTCUT_STORY, {"500": "In Progress"}, {"alex": "Alex Kim"})

        self.assertEqual("Alex Kim", issue.assignee)
        self.assertEqual(("alex",), issue.assignee_ids)

    def test_state_types_map(self) -> None:
        self.assertEqual(COLUMN_TODO, shortcut_group("Ready", "unstarted"))
        self.assertEqual(COLUMN_STARTED, shortcut_group("Doing", "started"))
        self.assertEqual(COLUMN_DONE, shortcut_group("Shipped", "done"))
        self.assertEqual(COLUMN_REVIEW, shortcut_group("In Review", "started"))


class IssueEditTests(unittest.TestCase):
    """The diff that decides what a markdown edit actually sends back."""

    def _issue(self, **kw: Any) -> RemoteIssue:
        base = {
            "issue_id": "1",
            "key": "K-1",
            "title": "Ship it",
            "body": "before",
            "column_id": "c1",
            "labels": ("a",),
            "due_date": date(2026, 8, 20),
            "priority": "High",
            "assignee": "alex",
            "issue_type": "Task",
            "components": ("API",),
        }
        return RemoteIssue(**{**base, **kw})

    def test_no_change_sends_nothing(self) -> None:
        """The guard against every sync rewriting every field of every issue."""
        same = self._issue()
        edit = IssueEdit.changed(same, self._issue())
        self.assertTrue(edit.is_empty())
        self.assertEqual((), edit.touched())

    def test_only_the_changed_field_is_carried(self) -> None:
        edit = IssueEdit.changed(self._issue(), self._issue(title="Ship it faster"))
        self.assertEqual(("title",), edit.touched())
        self.assertEqual("Ship it faster", edit.title)
        self.assertIsNone(edit.body, "an untouched field must stay None, not empty string")

    def test_clearing_is_distinct_from_leaving_alone(self) -> None:
        """The distinction that stops a sync silently wiping fields.

        `None` means "not touched" and must never be sent. A field the user
        actually emptied has to be sent as an explicit null instead, which is
        what `cleared` records.
        """
        untouched = IssueEdit.changed(self._issue(), self._issue())
        self.assertNotIn("due_date", untouched.touched())

        emptied = IssueEdit.changed(self._issue(), self._issue(due_date=None))
        self.assertIn("due_date", emptied.touched())
        self.assertIn("due_date", emptied.cleared)
        self.assertIsNone(emptied.due_date)

    def test_empty_collections_count_as_cleared(self) -> None:
        edit = IssueEdit.changed(self._issue(), self._issue(labels=()))
        self.assertIn("labels", edit.cleared)

    def test_issue_type_changes_are_not_lost(self) -> None:
        edit = IssueEdit.changed(self._issue(), self._issue(issue_type="Bug"))

        self.assertEqual(("issue_type",), edit.touched())
        self.assertEqual("Bug", edit.issue_type)

    def test_component_changes_are_not_lost(self) -> None:
        edit = IssueEdit.changed(self._issue(), self._issue(components=("API", "Platform")))

        self.assertEqual(("components",), edit.touched())
        self.assertEqual(("API", "Platform"), edit.components)

    def test_unsupported_reports_what_a_provider_cannot_take(self) -> None:
        edit = IssueEdit(title="x", priority="High")
        self.assertEqual(("priority",), edit.unsupported(("title", "body")))
        self.assertEqual((), edit.unsupported(("title", "priority")))


class WriteBackTests(unittest.TestCase):
    """Every provider's PUT/PATCH body, captured without a network."""

    def _capture(self, provider: Any) -> list[tuple[str, str, Any, Any]]:
        sent: list[tuple[str, str, Any, Any]] = []

        class Fake:
            def request(self, method: str, path: str, *, params: Any = None, body: Any = None) -> Any:
                sent.append((method, path, params, body))
                if path == "/rest/api/3/user/assignable/search":
                    return [{"accountId": "acct-alex", "displayName": "Alex"}]
                return {}

            def get(self, path: str, params: Any = None, **_: Any) -> Any:
                if path.endswith("/issue-types"):
                    return [{"id": 1, "name": "Task"}, {"id": 2, "name": "Bug"}]
                return self.request("GET", path, params=params)

            def put(self, path: str, body: Any = None, params: Any = None) -> Any:
                return self.request("PUT", path, params=params, body=body)

            def post(self, path: str, body: Any = None, params: Any = None) -> Any:
                return self.request("POST", path, params=params, body=body)

            def patch(self, path: str, body: Any = None, params: Any = None) -> Any:
                return self.request("PATCH", path, params=params, body=body)

            def delete(self, path: str, params: Any = None) -> Any:
                return self.request("DELETE", path, params=params)

            def graphql(self, query: str, variables: Any = None, *, path: str = "") -> Any:
                sent.append(("GRAPHQL", query.strip().split("\n")[0], variables, None))
                return {}

        provider._http = Fake()
        return sent

    def test_jira_splits_the_transition_out(self) -> None:
        """Status is not a Jira field; it only moves through a transition."""
        p = JiraProvider({"base_url": "https://x", "project_key": "JPT"}, {"email": "e", "token": "t"})
        sent = self._capture(p)
        p._transitions["JPT-4"] = {"10011": "41"}
        p.update_issue(
            RemoteIssue(issue_id="1", key="JPT-4"),
            IssueEdit(title="New title", column_id="10011"),
        )
        methods = [(m, path) for m, path, _, _ in sent]
        self.assertIn(("PUT", "/rest/api/2/issue/JPT-4"), methods)
        self.assertTrue(any("transitions" in path for _, path in methods), "no transition call was made")
        field_call = next(b for m, path, _, b in sent if m == "PUT")
        self.assertEqual({"summary": "New title"}, field_call["fields"])

    def test_jira_sends_every_supported_card_field(self) -> None:
        p = JiraProvider({"base_url": "https://x"}, {"email": "e", "token": "t"})
        sent = self._capture(p)

        p.update_issue(
            RemoteIssue(issue_id="1", key="K"),
            IssueEdit(assignee="Alex", issue_type="Bug", priority="High", components=("API", "Platform")),
        )

        lookup = next(call for call in sent if call[1] == "/rest/api/3/user/assignable/search")
        self.assertEqual({"issueKey": "K", "query": "Alex", "maxResults": 20}, lookup[2])
        update = next(body for method, path, _, body in sent if method == "PUT" and path.endswith("/K"))
        self.assertEqual(
            {
                "assignee": {"accountId": "acct-alex"},
                "issuetype": {"name": "Bug"},
                "priority": {"name": "High"},
                "components": [{"name": "API"}, {"name": "Platform"}],
            },
            update["fields"],
        )

    def test_jira_can_clear_assignee_and_priority(self) -> None:
        p = JiraProvider({"base_url": "https://x"}, {"email": "e", "token": "t"})
        sent = self._capture(p)

        p.update_issue(
            RemoteIssue(issue_id="1", key="K"),
            IssueEdit(cleared=("assignee", "priority")),
        )

        update = next(body for method, path, _, body in sent if method == "PUT" and path.endswith("/K"))
        self.assertEqual({"assignee": None, "priority": None}, update["fields"])

    def test_jira_can_clear_components(self) -> None:
        p = JiraProvider({"base_url": "https://x"}, {"email": "e", "token": "t"})
        sent = self._capture(p)

        p.update_issue(RemoteIssue(issue_id="1", key="K"), IssueEdit(cleared=("components",)))

        update = next(body for method, path, _, body in sent if method == "PUT" and path.endswith("/K"))
        self.assertEqual({"components": []}, update["fields"])

    def test_jira_create_payload_includes_components(self) -> None:
        p = JiraProvider({"base_url": "https://x"}, {"email": "e", "token": "t"})
        p.list_issue_types = lambda project_id: []  # type: ignore[method-assign]

        fields = p.build_create_payload(
            "10000",
            IssueDraft(title="Ship it", components=("API", "Platform")),
        )

        self.assertEqual([{"name": "API"}, {"name": "Platform"}], fields["components"])

    def test_jira_refuses_to_clear_required_issue_type_before_sending(self) -> None:
        p = JiraProvider({"base_url": "https://x"}, {"email": "e", "token": "t"})
        sent = self._capture(p)

        with self.assertRaises(ProviderError) as caught:
            p.update_issue(
                RemoteIssue(issue_id="1", key="K"),
                IssueEdit(title="Must not be sent", cleared=("issue_type",)),
            )

        self.assertIn("issue type", str(caught.exception).lower())
        self.assertEqual([], sent)

    def test_nothing_is_sent_before_an_unsupported_edit_is_rejected(self) -> None:
        """All-or-nothing: a partial write must be impossible."""
        p = MondayProvider({"board_id": "1"}, {"token": "t"})
        sent = self._capture(p)
        with self.assertRaises(UnsupportedError):
            p.update_issue(RemoteIssue(issue_id="1"), IssueEdit(title="ok", body="not supported"))
        self.assertEqual([], sent, "the title was sent before the edit was rejected")

    def test_plane_sends_one_patch_for_everything(self) -> None:
        p = PlaneProvider({"workspace": "w", "project_id": "p"}, {"token": "t"})
        sent = self._capture(p)
        p.update_issue(
            RemoteIssue(issue_id="i1"),
            IssueEdit(title="T", body="<p>B</p>", column_id="s2", due_date=date(2026, 9, 1)),
        )
        self.assertEqual(1, len(sent), "Plane should not need a second request")
        method, _, _, body = sent[0]
        self.assertEqual("PATCH", method)
        self.assertEqual(
            {"name": "T", "description_html": "<p>B</p>", "state": "s2", "target_date": "2026-09-01"}, body
        )

    def test_github_swaps_the_column_label_in_one_patch(self) -> None:
        """Column and labels must go together, or the issue briefly has no column."""
        p = GitHubProvider({"repo": "acme/app"}, {"token": "t"})
        sent = self._capture(p)
        issue = RemoteIssue(issue_id="1", labels=("bug", "status:todo"), extra={"number": 42})
        p.update_issue(issue, IssueEdit(column_id="status:done"))
        self.assertEqual(1, len(sent))
        body = sent[0][3]
        self.assertEqual(["bug", "status:done"], body["labels"])

    def test_github_open_close_uses_state_not_a_label(self) -> None:
        p = GitHubProvider({"repo": "acme/app"}, {"token": "t"})
        sent = self._capture(p)
        p.update_issue(RemoteIssue(issue_id="1", extra={"number": 7}), IssueEdit(column_id="state:closed"))
        self.assertEqual({"state": "closed"}, sent[0][3])

    def test_asana_moves_sections_with_a_second_call(self) -> None:
        p = AsanaProvider({"project_id": "P"}, {"token": "t"})
        sent = self._capture(p)
        p.update_issue(RemoteIssue(issue_id="t1"), IssueEdit(title="T", column_id="sec-2"))
        self.assertEqual(2, len(sent), "Asana needs addTask for the section")
        self.assertTrue(sent[1][1].endswith("/addTask"))

    def test_clickup_encodes_the_due_date_back_to_epoch_ms(self) -> None:
        p = ClickUpProvider({"list_id": "l"}, {"token": "t"})
        sent = self._capture(p)
        p.update_issue(RemoteIssue(issue_id="c1"), IssueEdit(due_date=date(2026, 8, 20)))
        value = sent[0][3]["due_date"]
        self.assertIsInstance(value, int)
        self.assertEqual(date(2026, 8, 20), _clickup_to_ms_roundtrip(value))

    def test_asana_sends_a_resolved_assignee_gid(self) -> None:
        p = AsanaProvider({"project_id": "P"}, {"token": "t"})
        sent = self._capture(p)
        p._resolve_assignee_id = lambda value: "user-1"  # type: ignore[method-assign]

        p.update_issue(RemoteIssue(issue_id="t1"), IssueEdit(assignee="Alex"))

        self.assertEqual("user-1", sent[0][3]["data"]["assignee"])

    def test_clickup_sends_assignee_type_and_tag_changes(self) -> None:
        p = ClickUpProvider({"list_id": "l"}, {"token": "t"})
        sent = self._capture(p)
        p._resolve_assignee_ids = lambda value: [7]  # type: ignore[method-assign]
        issue = RemoteIssue(issue_id="c1", labels=("old",))

        p.update_issue(
            issue,
            IssueEdit(assignee="Alex", issue_type="3", labels=("new",)),
        )

        update = next(body for method, path, _, body in sent if method == "PUT" and path == "/task/c1")
        self.assertEqual({"add": [7], "rem": []}, update["assignees"])
        self.assertEqual(3, update["custom_item_id"])
        calls = {(method, path) for method, path, _, _ in sent}
        self.assertIn(("DELETE", "/task/c1/tag/old"), calls)
        self.assertIn(("POST", "/task/c1/tag/new"), calls)

    def test_monday_sends_configured_typed_column_values(self) -> None:
        p = MondayProvider(
            {
                "board_id": "1",
                "description_column": "desc",
                "assignee_column": "people",
                "type_column": "type",
                "priority_column": "priority",
                "labels_column": "tags",
                "due_column": "due",
            },
            {"token": "t"},
        )
        sent = self._capture(p)
        p._resolve_people_ids = lambda value: [7]  # type: ignore[method-assign]

        p.update_issue(
            RemoteIssue(issue_id="i1"),
            IssueEdit(
                body="Details",
                assignee="Alex",
                issue_type="Bug",
                priority="High",
                labels=("backend",),
                due_date=date(2026, 8, 20),
            ),
        )

        values = json.loads(sent[0][2]["values"])
        self.assertEqual("Details", values["desc"])
        self.assertEqual({"personsAndTeams": [{"id": 7, "kind": "person"}]}, values["people"])
        self.assertEqual({"labels": ["Bug"]}, values["type"])
        self.assertEqual({"label": "High"}, values["priority"])
        self.assertEqual({"labels": ["backend"]}, values["tags"])
        self.assertEqual({"date": "2026-08-20"}, values["due"])

    def test_shortcut_sends_labels_as_objects(self) -> None:
        p = ShortcutProvider({"workflow_id": "1"}, {"token": "t"})
        sent = self._capture(p)
        p.update_issue(RemoteIssue(issue_id="9"), IssueEdit(labels=("urgent",)))
        self.assertEqual([{"name": "urgent"}], sent[0][3]["labels"])

    def test_linear_uses_one_mutation(self) -> None:
        p = LinearProvider({"team_id": "t"}, {"token": "k"})
        sent = self._capture(p)
        p.update_issue(RemoteIssue(issue_id="u1"), IssueEdit(title="T", column_id="s9"))
        self.assertEqual(1, len(sent))
        self.assertEqual({"title": "T", "stateId": "s9"}, sent[0][2]["input"])

    def test_linear_sends_assignee_labels_and_priority(self) -> None:
        p = LinearProvider({"team_id": "team"}, {"token": "t"})
        sent = self._capture(p)
        p._resolve_user_id = lambda value: "user-1"  # type: ignore[method-assign]
        p._resolve_label_ids = lambda values: ["label-1"]  # type: ignore[method-assign]

        p.update_issue(
            RemoteIssue(issue_id="i1"),
            IssueEdit(assignee="Alex", labels=("bug",), priority="High"),
        )

        variables = sent[0][2]
        self.assertEqual("user-1", variables["input"]["assigneeId"])
        self.assertEqual(["label-1"], variables["input"]["labelIds"])
        self.assertEqual(2, variables["input"]["priority"])

    def test_plane_sends_assignee_and_label_ids_but_not_unsupported_type(self) -> None:
        p = PlaneProvider({"workspace": "w", "project_id": "p"}, {"token": "t"})
        sent = self._capture(p)
        p._resolve_member_ids = lambda value: ["member-1"]  # type: ignore[method-assign]
        p._resolve_label_ids = lambda values: ["label-1"]  # type: ignore[method-assign]

        p.update_issue(
            RemoteIssue(issue_id="i1"),
            IssueEdit(assignee="Alex", labels=("bug",)),
        )

        self.assertEqual(
            {"assignees": ["member-1"], "labels": ["label-1"]},
            sent[0][3],
        )

        with self.assertRaises(UnsupportedError):
            p.update_issue(
                RemoteIssue(issue_id="i1"),
                IssueEdit(issue_type="type-1"),
            )

    def test_github_sends_assignee_and_issue_type(self) -> None:
        p = GitHubProvider({"repo": "acme/app"}, {"token": "t"})
        sent = self._capture(p)

        p.update_issue(
            RemoteIssue(issue_id="1", extra={"number": 7}),
            IssueEdit(assignee="alex, sam", issue_type="bug"),
        )

        self.assertEqual(["alex", "sam"], sent[0][3]["assignees"])
        self.assertEqual("Bug", sent[0][3]["type"])

    def test_github_rejects_a_type_the_repository_does_not_offer(self) -> None:
        p = GitHubProvider({"repo": "acme/app"}, {"token": "t"})
        sent = self._capture(p)
        p.list_issue_types = lambda project_id: []  # type: ignore[method-assign]

        with self.assertRaises(UnsupportedError):
            p.update_issue(
                RemoteIssue(issue_id="1", extra={"number": 7}),
                IssueEdit(issue_type="Bug"),
            )

        self.assertEqual([], sent)

    def test_shortcut_sends_owner_ids_and_story_type(self) -> None:
        p = ShortcutProvider({"workflow_id": "1"}, {"token": "t"})
        sent = self._capture(p)
        p._resolve_owner_ids = lambda value: ["owner-1"]  # type: ignore[method-assign]

        p.update_issue(
            RemoteIssue(issue_id="7"),
            IssueEdit(assignee="Alex", issue_type="bug"),
        )

        self.assertEqual(["owner-1"], sent[0][3]["owner_ids"])
        self.assertEqual("bug", sent[0][3]["story_type"])

    def test_trello_sends_member_and_label_ids(self) -> None:
        p = TrelloProvider({"board_id": "b"}, {"key": "k", "token": "t"})
        sent = self._capture(p)
        p._resolve_member_ids = lambda value: ["member-1"]  # type: ignore[method-assign]
        p._resolve_label_ids = lambda values: ["label-1"]  # type: ignore[method-assign]

        p.update_issue(
            RemoteIssue(issue_id="c1"),
            IssueEdit(assignee="Alex", labels=("bug",)),
        )

        params = sent[0][2]
        self.assertEqual("member-1", params["idMembers"])
        self.assertEqual("label-1", params["idLabels"])

    def test_an_empty_edit_sends_no_request_at_all(self) -> None:
        for provider in (
            PlaneProvider({"workspace": "w", "project_id": "p"}, {"token": "t"}),
            GitHubProvider({"repo": "a/b"}, {"token": "t"}),
            LinearProvider({"team_id": "t"}, {"token": "k"}),
        ):
            with self.subTest(provider=provider.spec.name):
                sent = self._capture(provider)
                provider.update_issue(RemoteIssue(issue_id="1", extra={"number": 1, "iid": 1}), IssueEdit())
                self.assertEqual([], sent)


def _clickup_to_ms_roundtrip(ms: int) -> date:
    from datetime import UTC, datetime

    return datetime.fromtimestamp(ms / 1000, tz=UTC).date()


class DiscoveryTests(unittest.TestCase):
    """Nothing about the provider set may be written down twice."""

    def test_a_new_provider_module_needs_no_registration(self) -> None:
        """Dropping a file into pykantui/providers is the whole job.

        This is the test that would fail if someone reintroduced a hand-written
        `_BUILTINS` dict: the scan and the registry would stop agreeing.
        """
        from pykantui.tracker.registry import builtin_names

        self.assertEqual(set(builtin_providers()), set(builtin_names()))

    def test_discovery_does_not_import_the_providers(self) -> None:
        """Listing what exists must not drag in every tracker's dependencies."""
        import subprocess
        import sys

        code = (
            "import sys;"
            "from pykantui.providers import builtin_providers, verified_providers;"
            "names = builtin_providers();"
            "loaded = [m for m in sys.modules if m.startswith('pykantui.providers.')];"
            "print(len(names), loaded)"
        )
        result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
        count, loaded = result.stdout.strip().split(" ", 1)
        self.assertEqual("10", count)
        self.assertEqual("[]", loaded, "listing providers imported them")

    def test_verified_is_declared_by_the_provider_not_a_list(self) -> None:
        """Derived from each spec, so verifying one is a one-line change.

        Deliberately asserts the *relationship* rather than today's set. The
        previous version hardcoded ("jira", "plane") -- which is exactly the
        hand-maintained list this function exists to avoid, and it failed the
        moment a provider was verified rather than telling anyone anything.
        """
        declared = tuple(sorted(spec.name for spec in specs() if spec.verified))

        self.assertEqual(declared, verified_providers())
        for spec in specs():
            self.assertEqual(spec.verified, spec.name in verified_providers())

    def test_at_least_the_two_original_providers_stay_verified(self) -> None:
        """A guard against a spec losing its flag by accident."""
        for name in ("jira", "plane"):
            self.assertIn(name, verified_providers())

    def test_editable_fields_track_the_model(self) -> None:
        """EDITABLE_FIELDS is derived, so adding a field to IssueEdit is enough."""
        from pykantui.tracker.models import EDITABLE_FIELDS

        self.assertEqual(set(EDITABLE_FIELDS), set(IssueEdit.model_fields) - {"cleared"})

    def test_a_provider_class_is_found_by_inspection(self) -> None:
        """The class may be named anything; only the module name is a convention."""
        from pykantui.providers import jira as jira_module
        from pykantui.tracker.registry import _provider_class_in

        self.assertIs(JiraProvider, _provider_class_in(jira_module))


class ColumnGroupingTests(unittest.TestCase):
    """The shared heuristic that replaced provider-local copies of one table."""

    def test_specific_names_beat_the_tracker_type(self) -> None:
        """ "In Review" is `indeterminate` to Jira and `started` to Linear.

        Both lose the distinction a board cares about, so the name wins.
        """
        self.assertEqual(
            COLUMN_REVIEW,
            resolve_group("In Review", type_key="indeterminate", type_map={"indeterminate": COLUMN_STARTED}),
        )
        self.assertEqual(COLUMN_BACKLOG, resolve_group("Backlog", type_key="new", type_map={"new": COLUMN_TODO}))

    def test_the_type_beats_a_general_name(self) -> None:
        """A renamed column still classifies correctly if the tracker typed it."""
        self.assertEqual(
            COLUMN_DONE, resolve_group("Shipped to prod", type_key="completed", type_map={"completed": COLUMN_DONE})
        )

    def test_names_carry_it_when_there_is_no_type(self) -> None:
        """Trello, GitHub, Asana, Monday — and Jira's board path."""
        for name, expected in (
            ("To Do", COLUMN_TODO),
            ("In Progress", COLUMN_STARTED),
            ("Done", COLUMN_DONE),
            ("Cancelled", COLUMN_CANCELLED),
            ("Backlog", COLUMN_BACKLOG),
        ):
            with self.subTest(name=name):
                self.assertEqual(expected, group_from_name(name))

    def test_unknown_is_returned_rather_than_guessed(self) -> None:
        self.assertEqual(COLUMN_UNKNOWN, group_from_name("Zzyzx"))

    def test_every_provider_now_shares_one_table(self) -> None:
        """The regression this guards: a name added to one provider only.

        All providers used to carry their own copy, and a phrase added to one
        was missing from the others -- showing up as a card in the wrong
        column on one tracker and nowhere else.
        """
        self.assertEqual(COLUMN_STARTED, monday_group("Working on it"))
        self.assertEqual(COLUMN_STARTED, group_from_name("Working on it"))
        self.assertEqual(COLUMN_REVIEW, clickup_group("in review", "custom"))
        self.assertEqual(COLUMN_REVIEW, linear_group("In Review", "started"))
        self.assertEqual(COLUMN_REVIEW, shortcut_group("In Review", "started"))
        self.assertEqual(COLUMN_REVIEW, _group_for("In Review", "indeterminate"))


class EveryProviderTests(unittest.TestCase):
    """Invariants that must hold for every provider, so a new one cannot skip them."""

    def test_every_provider_declares_credentials_and_a_token_url(self) -> None:
        for spec in specs():
            with self.subTest(provider=spec.name):
                self.assertTrue(spec.auth_fields, "declares no credentials")
                self.assertTrue(spec.token_url, "gives the user nowhere to get a token")
                self.assertIsNot(
                    CredentialSetupKind.GENERIC,
                    spec.credential_setup,
                    "does not disclose whether provider registration is required",
                )
                self.assertTrue(spec.description)

    def test_no_provider_leaks_a_secret_into_config(self) -> None:
        for spec in specs():
            for field in spec.config_fields:
                with self.subTest(provider=spec.name, field=field.name):
                    self.assertFalse(field.secret, "would be written to project.json in the clear")

    def test_every_provider_can_be_constructed_without_touching_the_network(self) -> None:
        """Building must be lazy -- the wizard builds one before it has credentials."""
        for name in names():
            with self.subTest(provider=name):
                self.assertIsNotNone(build(name, {}, {}))

    def test_registry_and_manifest_agree(self) -> None:
        """BUILTIN_PROVIDERS is documentation; drift makes it a lie."""
        self.assertEqual(set(builtin_providers()), set(names()))

    def test_secret_fields_are_never_given_a_default(self) -> None:
        for spec in specs():
            for field in spec.auth_fields:
                if field.secret:
                    with self.subTest(provider=spec.name, field=field.name):
                        self.assertEqual("", field.default)

    def test_every_advertised_provider_operation_has_a_real_implementation(self) -> None:
        for name in names():
            provider_type = get(name)
            capabilities = provider_type.spec.capabilities
            with self.subTest(provider=name, operation="refresh one card"):
                self.assertIsNot(provider_type.get_issue, Provider.get_issue)
            if capabilities.create_issues:
                with self.subTest(provider=name, operation="create"):
                    self.assertIsNot(provider_type.create_issue, Provider.create_issue)
            if capabilities.move_issues:
                with self.subTest(provider=name, operation="move"):
                    self.assertIsNot(provider_type.move_issue, Provider.move_issue)
            if capabilities.writable_fields:
                with self.subTest(provider=name, operation="edit"):
                    self.assertIsNot(provider_type.update_issue, Provider.update_issue)

    def test_components_are_exposed_only_by_jira(self) -> None:
        for spec in specs():
            editable = set(spec.editable_card_fields({}))
            creatable = set(spec.creatable_card_fields({}))
            with self.subTest(provider=spec.name):
                if spec.name == "jira":
                    self.assertIn("components", editable)
                    self.assertIn("components", creatable)
                else:
                    self.assertNotIn("components", editable)
                    self.assertNotIn("components", creatable)


class CapabilityTests(unittest.TestCase):
    def test_unsupported_actions_raise_rather_than_failing_quietly(self) -> None:
        provider = TrelloProvider({}, {})
        provider_spec = TrelloProvider.spec
        self.assertTrue(provider_spec.capabilities.move_issues)
        self.assertTrue(provider_spec.capabilities.create_issues)
        self.assertIsNot(TrelloProvider.create_issue, Provider.create_issue)
        self.assertIsInstance(provider, TrelloProvider)

    def test_jira_declares_no_reorder(self) -> None:
        """Jira has no client-side row order, so J/K must stay hidden."""
        self.assertFalse(JiraProvider.spec.capabilities.reorder_issues)
        self.assertTrue(PlaneProvider.spec.capabilities.reorder_issues)

    def test_capabilities_default_to_off(self) -> None:
        """A provider that declares nothing is a read-only mirror."""
        blank = Capabilities()
        self.assertFalse(blank.move_issues)
        self.assertEqual((), blank.writable_fields)

    def test_a_typo_in_writable_fields_is_caught_at_import(self) -> None:
        """Otherwise it fails much later, as an edit the editor offers and the
        provider then refuses."""
        with self.assertRaises(ValueError):
            ProviderSpec(name="x", label="X", capabilities=Capabilities(writable_fields=("titel",)))


if __name__ == "__main__":
    unittest.main()


class CacheTests(unittest.TestCase):
    """The layer that stops a sync re-asking for what it already knows."""

    def setUp(self) -> None:
        import tempfile
        from pathlib import Path

        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_in_process_entries_are_isolated_by_provider_and_project_scope(self) -> None:
        cache = ResponseCache(self.root)
        jira_app = cache.scope("jira", "APP")
        jira_ops = cache.scope("jira", "OPS")
        github_app = cache.scope("github", "APP")

        jira_app.put("columns-shared", [{"name": "Jira App"}])

        self.assertIsNone(jira_ops.get("columns-shared"))
        self.assertIsNone(github_app.get("columns-shared"))

    def _client(self, cache: Any, responses: list[httpx.Response]) -> tuple[JsonHttp, list[httpx.Request]]:
        seen: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            return responses[min(len(seen) - 1, len(responses) - 1)]

        client = JsonHttp(
            "https://api.example.com",
            cache=cache,
            client=httpx.Client(base_url="https://api.example.com", transport=httpx.MockTransport(handler)),
        )
        return client, seen

    def test_a_fresh_entry_costs_no_request_at_all(self) -> None:
        cache = ResponseCache(self.root, provider="jira", project="JPT")
        client, seen = self._client(cache, [httpx.Response(200, json={"v": 1})])

        self.assertEqual({"v": 1}, client.get("/thing", ttl=60, label="thing"))
        self.assertEqual({"v": 1}, client.get("/thing", ttl=60, label="thing"))
        self.assertEqual(1, len(seen), "the second call went to the network")
        self.assertEqual(1, cache.hits)

    def test_ttl_zero_never_caches(self) -> None:
        """Caching is opt-in, so a call that says nothing always fetches."""
        cache = ResponseCache(self.root)
        client, seen = self._client(cache, [httpx.Response(200, json={"v": 1})])
        client.get("/thing")
        client.get("/thing")
        self.assertEqual(2, len(seen))

    def test_a_304_returns_the_cached_body(self) -> None:
        """Past its TTL but unchanged: one round trip, no payload."""
        cache = ResponseCache(self.root, provider="jira", project="JPT")
        client, seen = self._client(
            cache,
            [
                httpx.Response(200, json={"v": 1}, headers={"ETag": 'W/"abc"'}),
                httpx.Response(304),
            ],
        )
        self.assertEqual({"v": 1}, client.get("/thing", ttl=60, label="thing"))
        # expire it, so the next call has to revalidate rather than hit
        cache._memory.clear()
        entry = cache.get(cache.key_for("GET", "/thing", {}, "thing"))
        assert entry is not None
        cache.put(
            cache.key_for("GET", "/thing", {}, "thing"), entry.body, etag=entry.etag, last_modified=entry.last_modified
        )
        cache._memory.clear()
        import time as _time

        stored = cache.path_for(cache.key_for("GET", "/thing", {}, "thing"))
        import json as _json

        doc = _json.loads(stored.read_text(encoding="utf-8"))
        doc["fetched_at"] = _time.time() - 9999
        stored.write_text(_json.dumps(doc), encoding="utf-8")
        cache._memory.clear()

        self.assertEqual({"v": 1}, client.get("/thing", ttl=60, label="thing"))
        self.assertEqual(2, len(seen))
        self.assertIn("if-none-match", {k.lower() for k in seen[1].headers})
        self.assertEqual(1, cache.revalidations)

    def test_jira_components_are_project_scoped_cached_and_paginated(self) -> None:
        seen: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            start = int(request.url.params.get("startAt", "0"))
            values = (
                [{"id": "1", "name": "API"}, {"id": "2", "name": "Platform"}]
                if start == 0
                else [{"id": "3", "name": "Web"}]
            )
            return httpx.Response(
                200,
                json={
                    "startAt": start,
                    "maxResults": 2,
                    "total": 3,
                    "isLast": start > 0,
                    "values": values,
                },
            )

        cache = ResponseCache(self.root).scope("jira", "JPT")
        transport = httpx.MockTransport(handler)
        client = JiraClient(
            "https://example.atlassian.net",
            cache=cache,
            client=httpx.Client(base_url="https://example.atlassian.net", transport=transport),
        )
        api = JiraApi(client)

        first = list(api.components("JPT", ttl=TTL_STRUCTURE))
        second = list(api.components("JPT", ttl=TTL_STRUCTURE))

        self.assertEqual(["API", "Platform", "Web"], [item.name for item in first])
        self.assertEqual([item.id for item in first], [item.id for item in second])
        self.assertEqual(2, len(seen), "the second listing did not use the six-hour cache")
        self.assertTrue(all(request.url.path == "/rest/api/3/project/JPT/component" for request in seen))

    def test_different_params_are_different_entries(self) -> None:
        cache = ResponseCache(self.root)
        client, seen = self._client(cache, [httpx.Response(200, json={"v": 1})])
        client.get("/thing", {"page": 1}, ttl=60)
        client.get("/thing", {"page": 2}, ttl=60)
        self.assertEqual(2, len(seen), "two pages shared one cache entry")

    def test_a_corrupt_cache_file_is_ignored_not_fatal(self) -> None:
        cache = ResponseCache(self.root, provider="jira", project="JPT")
        key = cache.key_for("GET", "/thing", {}, "thing")
        cache.path_for(key).parent.mkdir(parents=True, exist_ok=True)
        cache.path_for(key).write_text("{not json", encoding="utf-8")
        cache._memory.clear()
        self.assertIsNone(cache.get(key))

    def test_the_cache_mirrors_the_project_tree(self) -> None:
        cache = ResponseCache(self.root).scope("jira", "JPT")
        self.assertEqual(self.root / "jira" / "JPT", cache.directory())

    def test_clearing_issue_responses_keeps_the_structural_cache(self) -> None:
        cache = ResponseCache(self.root).scope("jira", "JPT")
        issue_key = cache.key_for("GET", "/issues", {}, "issues")
        column_key = cache.key_for("GET", "/columns", {}, "columns")
        cache.put(issue_key, [{"key": "JIR-1"}])
        cache.put(column_key, [{"name": "To Do"}])

        removed = cache.clear_label("issues")

        self.assertEqual(1, removed)
        self.assertIsNone(cache.get(issue_key))
        self.assertIsNotNone(cache.get(column_key))

    def test_refresh_invalidates_issue_responses_then_restores_normal_ttl(self) -> None:
        cache = ResponseCache(self.root).scope("jira", "JPT")
        issue_key = cache.key_for("GET", "/issues", {}, "issues")
        cache.put(issue_key, [{"key": "JIR-1"}])
        provider = JiraProvider({"base_url": "https://x"}, {"email": "e", "token": "t"})
        provider.use_cache(cache)

        provider.refresh()

        self.assertIsNone(cache.get(issue_key))
        self.assertEqual(TTL_ISSUES, provider.issue_ttl)

    def test_columns_are_fetched_once_per_provider_instance(self) -> None:
        """The duplicate that was costing a real request on every sync."""
        calls = {"n": 0}

        class Counting(JiraProvider):
            def list_columns(self, project_id: str) -> Any:
                calls["n"] += 1
                return []

        provider = Counting({"base_url": "https://x"}, {"email": "e", "token": "t"})
        provider.columns("JPT")
        provider.columns("JPT")
        self.assertEqual(1, calls["n"])
        provider.forget_columns()
        provider.columns("JPT")
        self.assertEqual(2, calls["n"])

    def test_a_subclass_may_use_the_name_columns_for_its_own_purposes(self) -> None:
        """Regression: the base class used to claim `_columns` and broke any
        provider that kept its own list under that name."""

        class OwnsTheName(JiraProvider):
            def __init__(self) -> None:
                super().__init__({"base_url": "https://x"}, {"email": "e", "token": "t"})
                self._columns = ["not a dict"]

            def list_columns(self, project_id: str) -> Any:
                return [RemoteColumn(column_id="1", name="To Do")]

        provider = OwnsTheName()
        self.assertEqual(1, len(provider.columns("JPT")))
        self.assertEqual(["not a dict"], provider._columns)

    def test_refresh_drops_issue_caching_but_keeps_structure(self) -> None:
        provider = JiraProvider({"base_url": "https://x"}, {"email": "e", "token": "t"})
        self.assertGreater(provider.issue_ttl, 0)
        provider.refresh()
        self.assertEqual(0.0, provider.issue_ttl)


class PlaneShapeTests(unittest.TestCase):
    """Plane returns the same field in two shapes from two endpoints.

    Found by running the full flow against a live Plane project: listing issues
    gives bare label UUIDs, fetching one gives label objects. Compared against
    each other, every labelled issue looked changed -- so the pre-push conflict
    check reported a conflict on an issue nobody had touched.
    """

    def setUp(self) -> None:
        self.provider = PlaneProvider({"workspace": "w", "project_id": "p"}, {"token": "t"})
        # pretend the label directory has already been fetched
        self.provider._label_cache = {"uuid-1": "concepts", "uuid-2": "admin"}

    def test_bare_uuids_resolve_to_names(self) -> None:
        """What the list endpoint sends."""
        self.assertEqual(["concepts"], self.provider._label_names("p", ["uuid-1"]))

    def test_label_objects_resolve_to_names(self) -> None:
        """What the single-issue endpoint sends."""
        self.assertEqual(
            ["concepts"],
            self.provider._label_names("p", [{"id": "uuid-1", "name": "concepts"}]),
        )

    def test_both_shapes_agree(self) -> None:
        """The regression: they must compare equal, or every sync sees a conflict."""
        from_list = self.provider._label_names("p", ["uuid-1", "uuid-2"])
        from_one = self.provider._label_names(
            "p", [{"id": "uuid-1", "name": "concepts"}, {"id": "uuid-2", "name": "admin"}]
        )
        self.assertEqual(from_list, from_one)

    def test_an_unknown_id_falls_back_to_the_id(self) -> None:
        """Better a UUID in the file than a silently dropped label."""
        self.assertEqual(["uuid-9"], self.provider._label_names("p", ["uuid-9"]))

    def test_no_labels_is_empty_not_an_error(self) -> None:
        values: tuple[object, ...] = (None, [], "not a list")
        for value in values:
            self.assertEqual([], self.provider._label_names("p", value))

    def test_the_members_endpoint_returns_a_bare_list(self) -> None:
        """The other shape surprise: unlike every other Plane collection,
        members has no `results` wrapper. Running it through the paged helper
        returned nothing, and every assignee came back blank."""
        from pykantui.api import JsonClient
        from pykantui.providers.plane.client import PlaneApi

        class BareListClient:
            def get(self, *_args: object, **_kwargs: object) -> object:
                return [{"id": "member-1", "email": "alex@example.test"}]

        api = PlaneApi(cast(JsonClient, BareListClient()), "w")
        members = api.members("p", ttl=0)

        self.assertEqual(["member-1"], [member.id for member in members])

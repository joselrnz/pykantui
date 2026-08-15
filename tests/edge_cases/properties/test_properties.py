"""Property checks for untrusted text, JSON, and cache-path boundaries."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from hypothesis import given, settings
from hypothesis import strategies as st

from pykantui.api import ResponseCache, ensure_json
from pykantui.tracker.models import RemoteIssue
from pykantui.workspace import markdown

_VISIBLE_TEXT = st.text(
    alphabet=st.characters(blacklist_categories=("Cc", "Cs")),
    max_size=200,
)
_PATHISH_TEXT = st.text(
    alphabet=st.sampled_from(tuple("abcXYZ019 ./\\:_-")),
    min_size=1,
    max_size=60,
)
_JSON = st.recursive(
    st.none() | st.booleans() | st.integers() | st.floats(allow_nan=False) | st.text(),
    lambda children: st.lists(children, max_size=5)
    | st.dictionaries(st.text(max_size=20), children, max_size=5),
    max_leaves=20,
)


def _issue(title: str = "Title") -> RemoteIssue:
    return RemoteIssue(
        issue_id="issue-1",
        key="ACME-1",
        title=title,
        column_id="todo",
        status="To Do",
    )


class MarkdownPropertyTests(unittest.TestCase):
    @settings(max_examples=100, deadline=None)
    @given(title=_VISIBLE_TEXT)
    def test_arbitrary_provider_titles_survive_the_document_boundary(self, title: str) -> None:
        rendered = markdown.render(
            _issue(title),
            column_name="to-do",
            provider="edge",
        )
        parsed = markdown.parse(rendered)

        self.assertEqual(" ".join(title.split()), parsed.front.get("title", ""))

    @settings(max_examples=100, deadline=None)
    @given(notes=_VISIBLE_TEXT)
    def test_private_notes_survive_without_entering_provider_source(self, notes: str) -> None:
        rendered = markdown.render(
            _issue(),
            column_name="to-do",
            notes=notes,
            provider="edge",
        )
        parsed = markdown.parse(rendered)

        self.assertEqual(notes.strip(), parsed.notes)
        if notes.strip():
            self.assertNotIn(notes.strip(), parsed.source)


class CachePathPropertyTests(unittest.TestCase):
    def setUp(self) -> None:
        # Windows search/indexing can briefly retain a generated deep
        # directory after the cache write closes. That is unrelated to the
        # containment assertion and must not make a randomized run flaky.
        self._temporary = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.root = Path(self._temporary.name).resolve()

    def tearDown(self) -> None:
        self._temporary.cleanup()

    @settings(max_examples=100, deadline=None)
    @given(provider=_PATHISH_TEXT, project=_PATHISH_TEXT, label=_PATHISH_TEXT)
    def test_arbitrary_cache_names_cannot_escape_the_cache_root(
        self,
        provider: str,
        project: str,
        label: str,
    ) -> None:
        cache = ResponseCache(self.root).scope(provider, project)
        cache.put(label, {"safe": True})

        for path in self.root.rglob("*"):
            self.assertTrue(path.resolve().is_relative_to(self.root))


class JsonBoundaryPropertyTests(unittest.TestCase):
    @settings(max_examples=150, deadline=None)
    @given(value=_JSON)
    def test_every_generated_json_value_passes_the_shared_boundary(self, value: object) -> None:
        self.assertEqual(value, ensure_json(value))


if __name__ == "__main__":
    unittest.main()

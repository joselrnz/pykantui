"""Two places where tracker text becomes something executable-ish.

Issue titles come from other people. They reach an HTML file that gets opened
in a browser, and they reach cache keys that become filenames. Both are places
where "whatever the tracker said" must not be taken literally.
"""

from __future__ import annotations

import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from pykantui.api import expect_object
from pykantui.tracker.cache import ResponseCache
from pykantui.tracker.models import RemoteColumn, RemoteIssue, RemoteProject
from pykantui.workspace import graph, layout

TODO = RemoteColumn(column_id="c1", name="To Do", position=0, group="todo")
DOING = RemoteColumn(column_id="c2", name="In Progress", position=1, group="started")
PROJECT = RemoteProject(project_id="P1", key="ACME", name="widgets")

INJECTIONS = [
    '<script>alert("xss")</script>',
    '" onmouseover="alert(1)',
    "</text></svg><script>bad()</script>",
    "<img src=x onerror=alert(1)>",
    "javascript:alert(1)",
    "Tom & Jerry <3",
    "'; DROP TABLE cards; --",
    "</style><style>body{display:none}",
]


class GraphEscapingTests(unittest.TestCase):
    """`kbn graph` writes an HTML file people open in a browser."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.ws = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def write(self, issues: list[RemoteIssue]) -> str:
        folder = layout.column_dir(self.ws, "edge", PROJECT, TODO, layout.DEFAULT_COLUMN_STYLE)
        folder.mkdir(parents=True, exist_ok=True)
        from pykantui.workspace import markdown

        for issue in issues:
            path = folder / f"{issue.display_key()}.md"
            path.write_text(markdown.render(issue, column_name="to-do", provider="edge"), encoding="utf-8")
        found = graph.read(self.ws, "edge", PROJECT, [TODO, DOING])
        return graph.render(found, generated=datetime(2026, 1, 1))

    def issue(self, key: str, title: str) -> RemoteIssue:
        return RemoteIssue(issue_id=f"id-{key}", key=key, title=title, column_id=TODO.column_id, status="To Do")

    def test_no_injection_appears_verbatim(self) -> None:
        """Escaped is enough: "onerror=alert(1)" as *text* is inert.

        What matters is that the tag and attribute delimiters are gone, so no
        user string can open an element or an attribute. Checking for the whole
        injection verbatim tests exactly that -- grepping for a fragment like
        "onerror=" flags harmless text and proves nothing.
        """
        html = self.write([self.issue(f"ACME-{n}", t) for n, t in enumerate(INJECTIONS)])

        for injection in INJECTIONS:
            if not any(ch in injection for ch in "<>&\"'"):
                # Nothing to escape. "javascript:alert(1)" is inert as text and
                # only dangerous in an href -- covered separately below.
                continue
            self.assertNotIn(injection, html, f"{injection!r} reached the page unescaped")

    def test_user_content_cannot_open_an_element(self) -> None:
        """The page has exactly the one <script> and one <style> it ships with."""
        clean = self.write([self.issue("ACME-1", "Plain")])
        hostile = self.write([self.issue(f"ACME-{n}", t) for n, t in enumerate(INJECTIONS)])

        for tag in ("<script", "<style", "<img", "<svg"):
            self.assertEqual(
                clean.count(tag),
                hostile.count(tag),
                f"user content changed the number of {tag} elements",
            )

    def test_user_content_cannot_open_an_attribute(self) -> None:
        """A bare quote from a title would break out of the attribute it sits in."""
        html = self.write([self.issue("ACME-1", '" onmouseover="alert(1)')])

        self.assertIn("&quot;", html)
        self.assertNotIn('" onmouseover=', html)

    def test_the_page_has_no_links_built_from_tracker_text(self) -> None:
        """A title is inert as text; it would not be inert in an href.

        "javascript:alert(1)" needs no escaping where it sits now, and this is
        the assumption that makes that true -- so it is asserted rather than
        remembered.
        """
        html = self.write([self.issue("ACME-1", "javascript:alert(1)")])

        for probe in ("href=", "xlink:href", "<a "):
            self.assertNotIn(probe, html, f"the graph grew a link ({probe}); re-check escaping")

    def test_an_ampersand_is_escaped(self) -> None:
        """Otherwise the SVG is not well-formed and may not render at all."""
        html = self.write([self.issue("ACME-1", "Tom & Jerry")])

        self.assertIn("&amp;", html)
        self.assertNotIn("Tom & Jerry", html)

    def test_the_text_is_still_readable(self) -> None:
        """Escaping must not mean losing the title."""
        html = self.write([self.issue("ACME-1", "Tom & Jerry <3")])

        self.assertIn("Tom", html)
        self.assertIn("Jerry", html)

    def test_a_unicode_title_survives(self) -> None:
        html = self.write([self.issue("ACME-1", "進行中 ✅")])

        self.assertIn("進行中", html)

    def test_the_page_has_no_external_references(self) -> None:
        """Self-contained by construction: no CDN, no fonts, no tracking."""
        html = self.write([self.issue("ACME-1", "Plain")])

        for probe in ("http://", 'src="//', "cdn.", "googleapis"):
            self.assertNotIn(probe, html, f"{probe!r} makes the page depend on the network")

    def test_an_empty_workspace_still_renders(self) -> None:
        html = self.write([])

        self.assertIn("<svg", html)


class CacheKeyTests(unittest.TestCase):
    """A key collision serves one request's answer to a different request."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.cache = ResponseCache(Path(self._tmp.name))
        self.root = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_different_parameters_differ(self) -> None:
        first = self.cache.key_for("GET", "/issues", {"page": 1}, "issues")
        second = self.cache.key_for("GET", "/issues", {"page": 2}, "issues")

        self.assertNotEqual(first, second)

    def test_different_labels_differ(self) -> None:
        first = self.cache.key_for("GET", "/x", {}, "issues")
        second = self.cache.key_for("GET", "/x", {}, "board")

        self.assertNotEqual(first, second)

    def test_a_split_parameter_cannot_alias(self) -> None:
        """{a:1, b:2} must not hash the same as {ab:12}."""
        first = self.cache.key_for("GET", "/i", {"a": "1", "b": "2"}, "x")
        second = self.cache.key_for("GET", "/i", {"ab": "12"}, "x")

        self.assertNotEqual(first, second)

    def test_parameter_order_does_not_matter(self) -> None:
        """Otherwise the same request misses the cache half the time."""
        first = self.cache.key_for("GET", "/i", {"a": "1", "b": "2"}, "x")
        second = self.cache.key_for("GET", "/i", {"b": "2", "a": "1"}, "x")

        self.assertEqual(first, second)

    def test_a_zero_ttl_is_always_stale(self) -> None:
        """`--refresh` sets 0; treating it as "no expiry" would break the bypass."""
        self.cache.put("k", {"v": 1})
        entry = self.cache.get("k")

        assert entry is not None
        self.assertTrue(entry.is_fresh(60))
        self.assertFalse(entry.is_fresh(0))
        self.assertFalse(entry.is_fresh(-1))

    def test_a_hostile_key_stays_inside_the_cache_directory(self) -> None:
        key = self.cache.key_for("GET", "/a b/c:d?e=f", {"q": "../../etc/passwd"}, "lbl")
        path = self.cache.path_for(key)

        self.assertIn(self.root.resolve(), path.resolve().parents)

    def test_a_symlinked_cache_scope_cannot_write_outside_the_cache(self) -> None:
        cache_root = self.root / "cache"
        outside = self.root / "outside"
        outside.mkdir()
        cache_root.mkdir()
        try:
            (cache_root / "provider").symlink_to(outside, target_is_directory=True)
        except OSError as error:
            self.skipTest(f"symlinks are unavailable: {error}")

        cache = ResponseCache(cache_root).scope("provider", "project")
        cache.put("secret", {"token": "must-not-escape"})

        self.assertEqual([], list(outside.rglob("*.json")))

    def test_a_stored_entry_reads_back(self) -> None:
        self.cache.put("k", {"v": [1, 2, 3]})

        entry = self.cache.get("k")

        assert entry is not None
        self.assertEqual({"v": [1, 2, 3]}, entry.body)

    def test_a_missing_entry_is_none(self) -> None:
        self.assertIsNone(self.cache.get("never-written"))

    def test_a_corrupt_entry_is_none_not_an_exception(self) -> None:
        """A half-written file after a crash must not break the next run."""
        key = self.cache.key_for("GET", "/x", {}, "l")
        path = self.cache.path_for(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{not json", encoding="utf-8")

        self.assertIsNone(self.cache.get(key))

    def test_unicode_survives_a_round_trip(self) -> None:
        self.cache.put("k", {"title": "進行中 ✅"})

        entry = self.cache.get("k")

        assert entry is not None
        self.assertEqual("進行中 ✅", expect_object(entry.body)["title"])


if __name__ == "__main__":
    unittest.main()

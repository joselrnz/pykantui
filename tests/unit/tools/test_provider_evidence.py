"""Contracts for deterministic, secret-free provider evidence bundles."""

from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tools.provider_evidence import (
    EVIDENCE_PHASES,
    build_action_manifest,
    build_edge_cases,
    build_enterprise_fixture,
    capture_evidence,
    record_artifact,
    validate_manifest,
    validate_png,
    validate_svg,
    write_index,
)

PROVIDERS = {
    "asana",
    "clickup",
    "github",
    "jira",
    "linear",
    "monday",
    "plane",
    "shortcut",
    "trello",
}


class EdgeManifestTests(unittest.TestCase):
    def test_readme_capture_fixtures_are_enterprise_like_and_provider_specific(self) -> None:
        fixtures = {provider: build_enterprise_fixture(provider) for provider in PROVIDERS}

        self.assertEqual(PROVIDERS, set(fixtures))
        self.assertEqual(9, len({fixture.project_name for fixture in fixtures.values()}))
        for fixture in fixtures.values():
            self.assertEqual(27, len(fixture.card_titles))
            self.assertEqual(27, len(set(fixture.card_titles)))
            visible = " ".join((fixture.project_key, fixture.project_name, *fixture.card_titles)).casefold()
            self.assertNotIn("evidence", visible)
            self.assertNotIn("example", visible)
            self.assertNotIn("live", visible)

    def test_manifest_has_100_plus_deterministic_cases_across_all_providers(self) -> None:
        first = build_edge_cases()
        second = build_edge_cases()

        self.assertEqual(first, second)
        self.assertGreaterEqual(len(first), 100)
        self.assertEqual(PROVIDERS, {item["provider"] for item in first})
        self.assertEqual(len(first), len({item["case_id"] for item in first}))
        self.assertTrue(all(item["network"] == "forbidden" for item in first))

    def test_cases_cover_provider_fields_types_and_hostile_markdown(self) -> None:
        cases = build_edge_cases()
        categories = {str(item["category"]) for item in cases}

        self.assertTrue(
            {
                "provider-field",
                "provider-type",
                "unicode",
                "markdown-marker",
                "comments-20-plus",
                "cards-20-plus",
                "conflict",
            }.issubset(categories)
        )
        for provider in PROVIDERS:
            provider_cases = [item for item in cases if item["provider"] == provider]
            self.assertTrue(any(item["category"] == "provider-field" for item in provider_cases))
            self.assertTrue(any(item["category"] == "provider-type" for item in provider_cases))


class ActionManifestTests(unittest.TestCase):
    def test_every_provider_has_each_required_screenshot_phase(self) -> None:
        manifest = build_action_manifest("run-20260814T120000Z")
        actions = manifest["actions"]

        self.assertEqual(len(PROVIDERS) * len(EVIDENCE_PHASES), len(actions))
        self.assertEqual("offline-simulated", manifest["evidence_kind"])
        self.assertEqual([], manifest["live_api_receipts"])
        for provider in PROVIDERS:
            self.assertEqual(
                set(EVIDENCE_PHASES),
                {item["phase"] for item in actions if item["provider"] == provider},
            )
        validate_manifest(manifest)
        self.assertTrue(all(set(item["screenshots"]) == {"svg", "png"} for item in actions))

    def test_manifest_rejects_secrets_absolute_paths_and_unhashed_artifacts(self) -> None:
        manifest = build_action_manifest("safe-run")
        manifest["actions"][0]["workspace"] = "C:/" + "Users/example/private"
        with self.assertRaisesRegex(ValueError, "absolute workspace"):
            validate_manifest(manifest)

        manifest = build_action_manifest("safe-run")
        manifest["actions"][0]["token"] = "secret-value"
        with self.assertRaisesRegex(ValueError, "secret"):
            validate_manifest(manifest)

        manifest = build_action_manifest("safe-run")
        manifest["actions"][0]["artifacts"] = {
            "svg": {
                "path": "safe-run/jira/before.svg",
                "bytes": 12,
                "sha256": "",
            }
        }
        with self.assertRaisesRegex(ValueError, "sha256"):
            validate_manifest(manifest)

    def test_recorded_artifact_has_relative_path_size_and_full_sha256(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            artifact = root / "safe-run" / "jira" / "before.svg"
            artifact.parent.mkdir(parents=True)
            artifact.write_text('<svg width="80" height="24"><text>board</text></svg>', encoding="utf-8")

            record = record_artifact(root, artifact)

        self.assertEqual("safe-run/jira/before.svg", record["path"])
        self.assertEqual(64, len(str(record["sha256"])))
        recorded_bytes = record["bytes"]
        assert isinstance(recorded_bytes, int)
        self.assertGreater(recorded_bytes, 0)
        json.dumps(record)


class GeometryTests(unittest.TestCase):
    def test_svg_geometry_requires_positive_dimensions_and_visible_content(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            valid = root / "valid.svg"
            valid.write_text(
                '<svg xmlns="http://www.w3.org/2000/svg" width="800" height="480" '
                'viewBox="0 0 800 480"><text x="2" y="12">Kanban</text></svg>',
                encoding="utf-8",
            )
            empty = root / "empty.svg"
            empty.write_text('<svg width="0" height="0"></svg>', encoding="utf-8")

            metrics = validate_svg(valid)
            self.assertEqual([800.0, 480.0], metrics["pixels"])
            visible_nodes = metrics["visible_nodes"]
            assert isinstance(visible_nodes, int)
            self.assertGreater(visible_nodes, 0)
            with self.assertRaisesRegex(ValueError, "positive"):
                validate_svg(empty)

    def test_png_geometry_reports_dimensions_and_nonempty_pixels(self) -> None:
        from PIL import Image

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "capture.png"
            image = Image.new("RGB", (80, 24), "black")
            image.putpixel((2, 2), (255, 255, 255))
            image.save(path)

            metrics = validate_png(path)

        self.assertEqual([80, 24], metrics["pixels"])
        colors = metrics["colors"]
        assert isinstance(colors, int)
        self.assertGreaterEqual(colors, 2)

    def test_index_is_a_relative_png_gallery_without_live_claims(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = build_action_manifest("safe-run")
            action = manifest["actions"][0]
            action["status"] = "captured"
            action["artifacts"] = {
                "svg": {"path": "safe-run/asana/01-before.svg", "bytes": 10, "sha256": "a" * 64},
                "png": {"path": "safe-run/asana/01-before.png", "bytes": 20, "sha256": "b" * 64},
            }

            path = write_index(root, manifest)
            text = path.read_text(encoding="utf-8")

        self.assertIn("![Asana before](asana/01-before.png)", text)
        self.assertIn("Offline simulated evidence", text)
        self.assertNotIn("live API validated", text.lower())


class CaptureOrchestrationTests(unittest.TestCase):
    def test_one_provider_capture_records_both_formats_and_leaves_others_planned(self) -> None:
        from PIL import Image

        async def fake_journey(
            root: Path,
            run_tag: str,
            provider_name: str,
            actions: list[dict[str, object]],
        ) -> None:
            self.assertEqual("jira", provider_name)
            for action in actions:
                screenshots = action["screenshots"]
                assert isinstance(screenshots, dict)
                path = root / str(screenshots["svg"])
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(
                    f'<svg xmlns="http://www.w3.org/2000/svg" width="80" height="24" ><text>{run_tag}</text></svg>',
                    encoding="utf-8",
                )

        def fake_raster(svg_path: Path) -> Path:
            png = svg_path.with_suffix(".png")
            image = Image.new("RGB", (80, 24), "black")
            image.putpixel((1, 1), (255, 255, 255))
            image.save(png)
            return png

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with (
                patch("tools.provider_evidence._capture_provider_journey", side_effect=fake_journey),
                patch("tools.provider_evidence.rasterise_svg", side_effect=fake_raster),
            ):
                manifest_path = asyncio.run(capture_evidence(root, "orchestration", provider_names={"jira"}))
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

        self.assertEqual(18, manifest["counts"]["artifacts"])
        self.assertEqual(
            9,
            len([item for item in manifest["actions"] if item["status"] == "captured"]),
        )
        self.assertEqual(
            72,
            len([item for item in manifest["actions"] if item["status"] == "planned"]),
        )
        self.assertTrue(
            all(
                set(item["artifacts"]) == {"svg", "png"} for item in manifest["actions"] if item["status"] == "captured"
            )
        )


if __name__ == "__main__":
    unittest.main()

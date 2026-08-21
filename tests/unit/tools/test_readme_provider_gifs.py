"""Tests for privacy-safe provider GIF assembly."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from PIL import Image
from tools.readme_provider_gifs import FRAME_PHASES, build_provider_gif


class ReadmeProviderGifTests(unittest.TestCase):
    def test_builds_a_long_metadata_free_gif_from_every_workflow_phase(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            source = root / "privacy-safe" / "jira"
            source.mkdir(parents=True)
            for number, phase in enumerate(FRAME_PHASES, start=1):
                Image.new("RGB", (80, 40), (number * 20, 30, 50)).save(source / f"{number:02d}-{phase}.png")
            target = root / "assets" / "jira.gif"

            result = build_provider_gif(root, "privacy-safe", "jira", target)

            self.assertEqual(target, result)
            with Image.open(target) as image:
                frame_count = int(getattr(image, "n_frames", 1))
                durations: list[int] = []
                for index in range(frame_count):
                    image.seek(index)
                    durations.append(int(image.info.get("duration", 0)))
                self.assertEqual(len(FRAME_PHASES), frame_count)
                self.assertGreaterEqual(sum(durations), 10_000)
                self.assertNotIn("author", {str(key).casefold() for key in image.info})

    def test_rejects_missing_frames_without_partial_output(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            source = root / "privacy-safe" / "asana"
            source.mkdir(parents=True)
            for number, phase in enumerate(FRAME_PHASES[:-1], start=1):
                Image.new("RGB", (80, 40), "navy").save(source / f"{number:02d}-{phase}.png")
            target = root / "asana.gif"

            with self.assertRaisesRegex(ValueError, "missing frame"):
                build_provider_gif(root, "privacy-safe", "asana", target)

            self.assertFalse(target.exists())

    def test_rejects_mismatched_frame_geometry_without_partial_output(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            source = root / "privacy-safe" / "trello"
            source.mkdir(parents=True)
            for number, phase in enumerate(FRAME_PHASES, start=1):
                size = (81, 40) if number == 2 else (80, 40)
                Image.new("RGB", size, "navy").save(source / f"{number:02d}-{phase}.png")
            target = root / "trello.gif"

            with self.assertRaisesRegex(ValueError, "geometry differs"):
                build_provider_gif(root, "privacy-safe", "trello", target)

            self.assertFalse(target.exists())


if __name__ == "__main__":
    unittest.main()

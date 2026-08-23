"""Interactive README banner generation."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from PIL import Image
from tools.banner_gif import (
    HIGHLIGHT_ORDER,
    REVIEW_LABEL_MAX_WIDTH,
    build_banner_gif,
    build_timeline,
    fit_label_font,
    project_version,
)

ROOT = Path(__file__).parents[3]


class BannerGifTests(unittest.TestCase):
    def test_release_label_is_fitted_inside_the_review_card(self) -> None:
        label = "[ ] Release 999.999.999"

        font = fit_label_font(label, max_width=REVIEW_LABEL_MAX_WIDTH)

        self.assertLessEqual(font.getlength(label), REVIEW_LABEL_MAX_WIDTH)

    def test_banner_release_label_matches_the_package(self) -> None:
        self.assertEqual("1.2.1", project_version())

    def test_timeline_types_command_and_walks_task_across_board(self) -> None:
        timeline = build_timeline()

        typed = [frame.command for frame in timeline]
        highlighted = [frame.highlight for frame in timeline if frame.highlight]

        self.assertEqual("pykantui sync", max(typed, key=len))
        self.assertEqual(list(HIGHLIGHT_ORDER), highlighted)
        self.assertEqual("synced 1 task", timeline[-1].status)

    def test_generated_gif_loops_and_contains_the_full_timeline(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "banner.gif"

            build_banner_gif(
                ROOT / "assets" / "pykantui-banner-v2.png",
                output,
                width=700,
            )

            with Image.open(output) as image:
                self.assertEqual((700, 280), image.size)
                self.assertEqual(len(build_timeline()), getattr(image, "n_frames", 1))
                self.assertEqual(0, image.info["loop"])


if __name__ == "__main__":
    unittest.main()

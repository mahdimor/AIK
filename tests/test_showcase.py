from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from build_showcase import build  # noqa: E402


class ShowcaseBuildTests(unittest.TestCase):
    def test_showcase_build_is_complete_and_portable(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            destination = build(Path(temporary) / "public")
            expected = [
                "index.html",
                "style.css",
                "assets/screenshots/01-overview.png",
                "assets/screenshots/05-history-dashboard.png",
                "demo/review.html",
                "demo/history/dashboard.html",
                ".nojekyll",
            ]
            for relative in expected:
                self.assertTrue((destination / relative).exists(), relative)

            self.assertNotIn(
                str(ROOT.resolve()).lower(),
                (destination / "demo" / "history" / "dashboard.json").read_text(encoding="utf-8").lower(),
            )

    def test_showcase_build_refuses_unmarked_output_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary) / "existing"
            destination.mkdir()
            (destination / "keep.txt").write_text("keep", encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "unmarked directory"):
                build(destination)

            self.assertEqual((destination / "keep.txt").read_text(encoding="utf-8"), "keep")


if __name__ == "__main__":
    unittest.main()

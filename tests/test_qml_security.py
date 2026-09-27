from __future__ import annotations

from pathlib import Path
import unittest


OVERLAY_QML = Path(__file__).resolve().parents[1] / "HintOverlay.qml"


class QmlSecurityTests(unittest.TestCase):
    def test_every_text_item_forces_plain_text(self) -> None:
        source = OVERLAY_QML.read_text(encoding="utf-8")
        text_item_count = source.count("Text {")

        self.assertGreater(text_item_count, 0)
        self.assertEqual(
            text_item_count,
            source.count("textFormat: Text.PlainText"),
            "Every Text item must explicitly disable rich-text interpretation",
        )


if __name__ == "__main__":
    unittest.main()

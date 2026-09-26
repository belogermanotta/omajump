from __future__ import annotations

import json
import pathlib
import unittest

from omajump.geometry import CoordinateMapper, deduplicate_candidates, frame_match_score
from omajump.model import Candidate, Client, Monitor, Rect


FIXTURE = pathlib.Path(__file__).parent / "fixtures" / "mixed_scale.json"


def candidate(rect: Rect, role: str = "button", action: str = "press") -> Candidate:
    return Candidate(object(), rect, role, 0, action)


class CoordinateMapperTests(unittest.TestCase):
    def setUp(self) -> None:
        data = json.loads(FIXTURE.read_text(encoding="utf-8"))
        client = data["client"]
        monitor = data["monitor"]
        self.data = data
        self.client = Client(
            client["pid"],
            Rect(client["x"], client["y"], client["width"], client["height"]),
            client["monitorId"],
        )
        self.monitor = Monitor(
            monitor["name"],
            Rect(monitor["x"], monitor["y"], monitor["width"], monitor["height"]),
            monitor["scale"],
            monitor["id"],
        )

    def test_window_local_logical_frame(self) -> None:
        frame = Rect(*self.data["frame"])
        mapper = CoordinateMapper.infer(self.client, self.monitor, frame)
        self.assertEqual(mapper.mode, "local")
        self.assertEqual(mapper.base_scale, 1.0)
        self.assertEqual(mapper.to_global(Rect(10, 20, 30, 40)), Rect(3124, 52, 30, 40))

    def test_electron_web_subtree_uses_output_scale(self) -> None:
        mapper = CoordinateMapper.infer(self.client, self.monitor, Rect(*self.data["frame"]))
        web = Rect(*self.data["webDocument"])
        scale = mapper.derive_scale(web, mapper.base_scale, "document web")
        self.assertEqual(scale, 2.0)

        visible = mapper.visible_rect(Rect(*self.data["target"]), scale)
        self.assertIsNotNone(visible)
        overlay = mapper.overlay_rect(visible)  # type: ignore[arg-type]
        expected = Rect(*self.data["expectedOverlay"])
        self.assertEqual(overlay, expected)

    def test_global_logical_frame_is_preserved(self) -> None:
        mapper = CoordinateMapper.infer(self.client, self.monitor, self.client.rect)
        self.assertEqual(mapper.mode, "global")
        target = Rect(3200, 100, 60, 20)
        self.assertEqual(mapper.to_global(target), target)

    def test_visible_rect_is_clipped_to_window_and_monitor(self) -> None:
        mapper = CoordinateMapper.infer(self.client, self.monitor, Rect(*self.data["frame"]))
        visible = mapper.visible_rect(Rect(-50, -50, 100, 100), 1.0)
        self.assertEqual(visible, Rect(3114, 32, 50, 50))

    def test_frame_score_accepts_logical_and_scaled_frames(self) -> None:
        logical = frame_match_score(Rect(0, 0, 931, 1042), self.client, self.monitor)
        scaled = frame_match_score(Rect(0, 0, 1862, 2084), self.client, self.monitor)
        wrong = frame_match_score(Rect(0, 0, 400, 400), self.client, self.monitor)
        self.assertLess(logical, wrong)
        self.assertLess(scaled, wrong)


class DeduplicationTests(unittest.TestCase):
    def test_prefers_semantic_smaller_duplicate(self) -> None:
        generic = candidate(Rect(10, 10, 100, 30), "section", "click")
        semantic = candidate(Rect(11, 10, 98, 30), "button", "press")
        result = deduplicate_candidates([generic, semantic])
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].role, "button")

    def test_keeps_distinct_targets_and_sorts_spatially(self) -> None:
        bottom = candidate(Rect(5, 100, 20, 20))
        top_right = candidate(Rect(80, 10, 20, 20))
        top_left = candidate(Rect(10, 10, 20, 20))
        result = deduplicate_candidates([bottom, top_right, top_left])
        self.assertEqual([item.rect for item in result], [top_left.rect, top_right.rect, bottom.rect])


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import unittest
from unittest.mock import patch

from omajump.backend import BackendError, BackendSession, ScanResult, _active_geometry, choose_action
from omajump.model import Candidate, Monitor, Rect


class ActionSelectionTests(unittest.TestCase):
    def test_prefers_direct_press_over_context_menu(self) -> None:
        self.assertEqual(
            choose_action("button", ["showcontextmenu", "press"], {"focusable"}),
            (1, "press"),
        )

    def test_rejects_click_ancestor_noise(self) -> None:
        self.assertIsNone(
            choose_action("section", ["clickancestor", "showcontextmenu"], {"focusable"})
        )

    def test_generic_default_action_is_not_a_target(self) -> None:
        self.assertIsNone(choose_action("panel", ["dodefault"], set()))

    def test_structural_click_is_not_a_target(self) -> None:
        self.assertIsNone(choose_action("separator", ["click"], set()))

    def test_default_action_is_allowed_for_semantic_role(self) -> None:
        self.assertEqual(choose_action("list item", ["dodefault"], set()), (0, "dodefault"))

    def test_focusable_editor_uses_focus_fallback(self) -> None:
        self.assertEqual(choose_action("entry", [], {"focusable"}), (-1, "focus"))


class _ActionNode:
    def __init__(self, result: bool = True) -> None:
        self.calls = 0
        self.result = result

    def get_action_iface(self) -> "_ActionNode":
        return self

    def do_action(self, index: int) -> bool:
        self.calls += 1
        return self.result and index == 0


class _FocusNode:
    def __init__(self) -> None:
        self.calls = 0

    def get_component_iface(self) -> "_FocusNode":
        return self

    def grab_focus(self) -> bool:
        self.calls += 1
        return True


class ActivationTests(unittest.TestCase):
    def session_with(self, candidate: Candidate) -> BackendSession:
        session = BackendSession()
        monitor = Monitor("test", Rect(0, 0, 100, 100))
        session.result = ScanResult(monitor, [candidate], 1, False)
        return session

    def test_primary_action_runs_exactly_once(self) -> None:
        node = _ActionNode()
        session = self.session_with(Candidate(node, Rect(1, 1, 10, 10), "button", 0, "press"))
        self.assertTrue(session.activate(0)["ok"])
        self.assertEqual(node.calls, 1)

    def test_focus_fallback_runs_exactly_once(self) -> None:
        node = _FocusNode()
        session = self.session_with(Candidate(node, Rect(1, 1, 10, 10), "entry", -1, "focus"))
        self.assertTrue(session.activate(0)["ok"])
        self.assertEqual(node.calls, 1)

    def test_invalid_target_never_invokes_an_action(self) -> None:
        node = _ActionNode()
        session = self.session_with(Candidate(node, Rect(1, 1, 10, 10), "button", 0, "press"))
        with self.assertRaises(BackendError):
            session.activate(4)
        self.assertEqual(node.calls, 0)


class HyprlandGeometryTests(unittest.TestCase):
    def test_physical_monitor_dimensions_become_logical(self) -> None:
        active = {"pid": 42, "at": [3114, 32], "size": [931, 1042], "monitor": 1}
        monitors = [
            {
                "id": 1,
                "name": "HDMI-A-2",
                "x": 2150,
                "y": 0,
                "width": 3840,
                "height": 2160,
                "scale": 2.0,
                "transform": 0,
                "focused": True,
            }
        ]
        with patch("omajump.backend._json_command", side_effect=[active, monitors]):
            client, monitor = _active_geometry()
        self.assertEqual(client.rect, Rect(3114, 32, 931, 1042))
        self.assertEqual(monitor.rect, Rect(2150, 0, 1920, 1080))


if __name__ == "__main__":
    unittest.main()

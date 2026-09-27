from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from omajump.backend import (
    BackendError,
    BackendSession,
    ScanResult,
    ScrollTarget,
    _prime_accessibility,
    _screen_geometry,
    _screens_geometry,
    _title_match_score,
    choose_action,
    process_distance,
)
from omajump.model import Candidate, Client, Monitor, Rect


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

    def test_grid_click_is_scoped_to_fallback_window(self) -> None:
        session = BackendSession()
        monitor = Monitor("test", Rect(100, 50, 1000, 800), monitor_id=2)
        client = Client(
            42,
            Rect(200, 100, 600, 500),
            2,
            address="0xabc123",
        )
        session.result = ScanResult(monitor, [], 1, False, [client])
        with (
            patch("omajump.backend.time.sleep"),
            patch(
                "omajump.backend.subprocess.run",
                return_value=SimpleNamespace(stdout="ok\n"),
            ) as run,
        ):
            response = session.click(0, 200, 150)
        self.assertTrue(response["ok"])
        self.assertEqual(run.call_count, 3)
        commands = [call.args[0][-1] for call in run.call_args_list]
        self.assertIn('window = "address:0xabc123"', commands[0])
        self.assertIn("x = 300", commands[1])
        self.assertIn("y = 200", commands[1])
        self.assertIn('key = "mouse:272"', commands[2])

    def test_grid_click_rejects_points_outside_window(self) -> None:
        session = BackendSession()
        monitor = Monitor("test", Rect(0, 0, 1000, 800), monitor_id=2)
        client = Client(42, Rect(100, 100, 500, 400), 2, address="0xabc123")
        session.result = ScanResult(monitor, [], 1, False, [client])
        with patch("omajump.backend.subprocess.run") as run:
            with self.assertRaises(BackendError):
                session.click(0, 900, 700)
        run.assert_not_called()

    def test_terminal_scroll_uses_shift_page_down_on_exact_window(self) -> None:
        session = BackendSession()
        monitor = Monitor("test", Rect(0, 0, 1000, 800), monitor_id=2)
        client = Client(
            42, Rect(100, 100, 500, 400), 2,
            address="0xabc123", class_name="org.wezfurlong.wezterm",
        )
        target = ScrollTarget(None, client, monitor, Rect(100, 100, 500, 400))
        session.result = ScanResult(
            monitor, [], 1, False, monitors=[monitor], scroll_targets=[target]
        )
        with patch(
            "omajump.backend.subprocess.run",
            return_value=SimpleNamespace(stdout="ok\n"),
        ) as run:
            response = session.scroll(0, 1)
        self.assertTrue(response["ok"])
        command = run.call_args.args[0][-1]
        self.assertIn('mods = "SHIFT"', command)
        self.assertIn('key = "PAGE_DOWN"', command)
        self.assertIn('window = "address:0xabc123"', command)


class HyprlandGeometryTests(unittest.TestCase):
    def test_selects_active_workspace_on_every_monitor(self) -> None:
        monitors = [
            {"id": 0, "name": "DP-2", "x": 0, "y": 0, "width": 1920,
             "height": 1080, "scale": 1, "focused": False,
             "activeWorkspace": {"id": 1}},
            {"id": 1, "name": "HDMI-A-2", "x": 1920, "y": 0, "width": 3840,
             "height": 2160, "scale": 2, "focused": True,
             "activeWorkspace": {"id": 4}},
        ]
        clients = [
            {"pid": 10, "at": [0, 0], "size": [1920, 1080], "monitor": 0,
             "workspace": {"id": 1}, "mapped": True},
            {"pid": 11, "at": [1920, 0], "size": [1920, 1080], "monitor": 1,
             "workspace": {"id": 4}, "mapped": True},
            {"pid": 12, "at": [0, 0], "size": [1920, 1080], "monitor": 0,
             "workspace": {"id": 2}, "mapped": True},
        ]
        with patch("omajump.backend._json_command", side_effect=[monitors, clients]):
            contexts, focused = _screens_geometry()
        self.assertEqual(focused, "HDMI-A-2")
        self.assertEqual([monitor.name for _clients, monitor in contexts], ["DP-2", "HDMI-A-2"])
        self.assertEqual([[client.pid for client in visible] for visible, _monitor in contexts], [[10], [11]])
        self.assertEqual(contexts[1][1].rect, Rect(1920, 0, 1920, 1080))

    def test_selects_visible_clients_on_focused_monitor_workspace(self) -> None:
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
                "activeWorkspace": {"id": 4, "name": "4"},
            }
        ]
        clients = [
            {"pid": 42, "at": [3114, 32], "size": [931, 1042], "monitor": 1,
             "workspace": {"id": 4}, "mapped": True, "hidden": False,
             "focusHistoryID": 0},
            {"pid": 43, "at": [2175, 32], "size": [931, 1042], "monitor": 1,
             "workspace": {"id": 4}, "mapped": True, "hidden": False,
             "focusHistoryID": 1},
            {"pid": 44, "at": [2150, 0], "size": [1920, 1080], "monitor": 1,
             "workspace": {"id": 5}, "mapped": True, "hidden": False},
            {"pid": 45, "at": [0, 0], "size": [1000, 800], "monitor": 0,
             "workspace": {"id": 4}, "mapped": True, "hidden": False},
        ]
        with patch("omajump.backend._json_command", side_effect=[monitors, clients]):
            visible, monitor = _screen_geometry()
        self.assertEqual([client.pid for client in visible], [42, 43])
        self.assertEqual(visible[0].rect, Rect(3114, 32, 931, 1042))
        self.assertEqual(monitor.rect, Rect(2150, 0, 1920, 1080))

    def test_fullscreen_client_hides_other_workspace_clients(self) -> None:
        monitors = [{
            "id": 1, "name": "test", "x": 0, "y": 0, "width": 1000,
            "height": 800, "scale": 1, "focused": True,
            "activeWorkspace": {"id": 2},
        }]
        clients = [
            {"pid": 10, "at": [0, 0], "size": [500, 800], "monitor": 1,
             "workspace": {"id": 2}, "mapped": True, "fullscreen": 0},
            {"pid": 11, "at": [0, 0], "size": [1000, 800], "monitor": 1,
             "workspace": {"id": 2}, "mapped": True, "fullscreen": 2},
        ]
        with patch("omajump.backend._json_command", side_effect=[monitors, clients]):
            visible, _monitor = _screen_geometry()
        self.assertEqual([client.pid for client in visible], [11])


class ProcessMatchingTests(unittest.TestCase):
    def test_related_parent_and_child_match(self) -> None:
        with patch("omajump.backend._ancestry", side_effect=[[30, 20, 10], [20, 10]]):
            self.assertEqual(process_distance(30, 20), 1)

    def test_sibling_processes_do_not_match(self) -> None:
        with patch("omajump.backend._ancestry", side_effect=[[30, 10], [40, 10]]):
            self.assertIsNone(process_distance(30, 40))

    def test_accessible_title_disambiguates_same_sized_windows(self) -> None:
        class NamedNode:
            def get_name(self) -> str:
                return "Inbox - Vivaldi"

        self.assertEqual(_title_match_score(NamedNode(), "Inbox - Vivaldi"), 0.0)
        self.assertGreater(_title_match_score(NamedNode(), "Music - Vivaldi"), 0.0)


class _LazyNode:
    def __init__(self) -> None:
        self.primed = False

    def get_child_count(self) -> int:
        return 1

    def get_child_at_index(self, index: int) -> object | None:
        return object() if self.primed and index == 0 else None

    def get_attributes(self) -> list[str]:
        self.primed = True
        return []

    def get_relation_set(self) -> list[object]:
        return []


class AccessibilityPrimingTests(unittest.TestCase):
    def test_lazy_tree_is_woken_before_scan(self) -> None:
        node = _LazyNode()
        _prime_accessibility(node, timeout=0)
        self.assertTrue(node.primed)


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from omajump.backend import (
    BackendError,
    BackendSession,
    ParagraphTarget,
    ScanResult,
    SearchTarget,
    ScrollTarget,
    _prime_accessibility,
    _approximate_range_rect,
    _child_snapshot,
    _omajump_layers_visible,
    _is_paragraph_like,
    _node_text_range,
    _screen_geometry,
    _screens_geometry,
    _paragraph_ranges,
    _title_match_score,
    _wezterm_paragraph_targets,
    assemble_paragraph_targets,
    choose_action,
    deduplicate_paragraph_targets,
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

    def test_page_tab_without_direct_action_uses_pointer_fallback(self) -> None:
        self.assertEqual(
            choose_action("page tab", ["clickAncestor", "showContextMenu"], {"enabled"}),
            (-2, "pointer"),
        )

    def test_page_tab_prefers_direct_action_over_pointer_fallback(self) -> None:
        self.assertEqual(choose_action("page tab", ["press"], {"enabled"}), (0, "press"))

    def test_focusable_editor_uses_focus_fallback(self) -> None:
        self.assertEqual(choose_action("entry", [], {"focusable"}), (-1, "focus"))

    def test_multiline_static_text_is_a_paragraph_but_short_labels_are_not(self) -> None:
        self.assertTrue(
            _is_paragraph_like(
                "static",
                "This is a complete block of accessible prose with several useful words.",
                Rect(0, 0, 500, 40),
            )
        )
        self.assertFalse(
            _is_paragraph_like("static", "Open settings and preferences", Rect(0, 0, 300, 18))
        )

    def test_blank_line_makes_one_sentence_a_paragraph(self) -> None:
        ranges = _paragraph_ranges("Prompt output\n\nOne sentence.\n\nNext block")
        self.assertEqual([item[2] for item in ranges], ["Prompt output", "One sentence.", "Next block"])
        self.assertTrue(
            _is_paragraph_like("terminal", ranges[1][2], Rect(0, 0, 300, 18), ranges[1][3])
        )
        self.assertEqual(
            _approximate_range_rect(Rect(10, 20, 300, 60), "First\n\nSecond", 7, 13),
            Rect(10, 60, 300, 20),
        )

    def test_terminal_text_reads_bounded_tail_even_when_node_has_a_name(self) -> None:
        class TextNode:
            def get_name(self) -> str:
                return "Terminal title"

            def get_text_iface(self) -> "TextNode":
                return self

            def get_character_count(self) -> int:
                return 30_000

            def get_text(self, start: int, end: int) -> str:
                return f"{start}:{end}\n\nRecent paragraph."

        text, offset, iface = _node_text_range(TextNode(), limit=20_000, prefer_tail=True)
        self.assertEqual(offset, 10_000)
        self.assertEqual(text, "10000:30000\n\nRecent paragraph.")
        self.assertIsNotNone(iface)

    def test_wezterm_fallback_maps_blank_line_blocks_to_rows(self) -> None:
        monitor = Monitor("test", Rect(0, 0, 1000, 800), monitor_id=2)
        client = Client(
            42, Rect(100, 100, 500, 400), 2,
            title="notes", class_name="org.wezfurlong.wezterm",
        )
        panes = [{"pane_id": 7, "title": "notes", "size": {"rows": 40}}]
        with (
            patch("omajump.backend.os.readlink", return_value="/usr/bin/wezterm-gui"),
            patch(
                "omajump.backend.subprocess.run",
                return_value=SimpleNamespace(stdout="First sentence.\n\nSecond sentence.\n"),
            ) as run,
        ):
            targets = _wezterm_paragraph_targets(client, monitor, panes)
        self.assertEqual([target.text for target in targets], ["First sentence.", "Second sentence."])
        self.assertEqual(targets[0].rect, Rect(100, 100, 500, 10))
        self.assertEqual(targets[1].rect, Rect(100, 120, 500, 10))
        self.assertEqual(
            run.call_args.args[0],
            ["wezterm", "cli", "get-text", "--pane-id", "7", "--start-line", "0"],
        )

    def test_wezterm_fallback_does_not_guess_between_duplicate_titles(self) -> None:
        monitor = Monitor("test", Rect(0, 0, 1000, 800), monitor_id=2)
        client = Client(
            42, Rect(0, 0, 500, 400), 2,
            title="shell", class_name="org.wezfurlong.wezterm",
        )
        panes = [
            {"pane_id": 1, "title": "shell", "size": {"rows": 40}},
            {"pane_id": 2, "title": "shell", "size": {"rows": 40}},
        ]
        with (
            patch("omajump.backend.os.readlink", return_value="/usr/bin/wezterm-gui"),
            patch("omajump.backend.subprocess.run") as run,
        ):
            self.assertEqual(_wezterm_paragraph_targets(client, monitor, panes), [])
        run.assert_not_called()

    def test_wezterm_fallback_rejects_spoofed_window_class(self) -> None:
        monitor = Monitor("test", Rect(0, 0, 1000, 800), monitor_id=2)
        client = Client(
            42, Rect(0, 0, 500, 400), 2,
            title="notes", class_name="org.wezfurlong.wezterm",
        )
        panes = [{"pane_id": 7, "title": "notes", "size": {"rows": 40}}]
        with (
            patch("omajump.backend.os.readlink", return_value="/tmp/not-wezterm"),
            patch("omajump.backend.subprocess.run") as run,
        ):
            self.assertEqual(_wezterm_paragraph_targets(client, monitor, panes), [])
        run.assert_not_called()

    def test_nested_paragraph_subtext_is_deduplicated(self) -> None:
        monitor = Monitor("test", Rect(0, 0, 1200, 800), monitor_id=2)
        client = Client(42, Rect(0, 0, 1200, 800), 2)
        complete = ParagraphTarget(
            "I updated notes/HOME.md to make the homepage more useful: it now shows "
            "projects from the Werlion Board's In Progress column, filters blank "
            "placeholder tasks from today's actions, and puts the seven-day check-in "
            "ahead of recent notes.",
            object(), client, monitor, Rect(20, 19, 1169, 123),
        )
        nested_tail = ParagraphTarget(
            "from the Werlion Board's In Progress column, filters blank placeholder "
            "tasks from today's actions, and puts the seven-day check-in ahead of "
            "recent notes.",
            object(), client, monitor, Rect(24, 64, 1117, 78),
        )
        weekly = ParagraphTarget(
            "The weekly view now uses the last seven calendar days, counts only "
            "recorded habit scores, and shows missing days instead of zero.",
            object(), client, monitor, Rect(20, 175, 1175, 167),
        )

        self.assertEqual(
            deduplicate_paragraph_targets([complete, nested_tail, weekly]),
            [complete, weekly],
        )

    def test_equal_paragraph_text_prefers_tighter_geometry(self) -> None:
        monitor = Monitor("test", Rect(0, 0, 1200, 800), monitor_id=2)
        client = Client(42, Rect(0, 0, 1200, 800), 2)
        loose = ParagraphTarget(
            "The same accessible paragraph text.",
            object(), client, monitor, Rect(10, 10, 600, 100),
        )
        precise = ParagraphTarget(
            "The same accessible paragraph text.",
            object(), client, monitor, Rect(20, 20, 580, 80),
        )
        self.assertEqual(deduplicate_paragraph_targets([loose, precise]), [precise])

    def test_wrapped_list_fragments_are_assembled_into_two_paragraphs(self) -> None:
        monitor = Monitor("test", Rect(0, 0, 1000, 800), monitor_id=2)
        client = Client(42, Rect(0, 0, 1000, 800), 2)
        fragments = [
            ParagraphTarget(
                "**Two-way Obsidian sync** — reads today's daily note and renders every",
                object(), client, monitor, Rect(48, 16, 800, 78), "static",
            ),
            ParagraphTarget(
                "- [ ] ↔ - [x] ... ✅ YYYY-MM-DD toggles",
                object(), client, monitor, Rect(370, 94, 390, 18), "static",
            ),
            ParagraphTarget(
                "checkbox, and writes back without touching anything else in the file.",
                object(), client, monitor, Rect(48, 94, 847, 73), "static",
            ),
            ParagraphTarget(
                "**Inline task entry** — add a checklist item directly below TODAY with the",
                object(), client, monitor, Rect(48, 184, 850, 20), "static",
            ),
            ParagraphTarget(
                "button; press Return to write it into the daily note. Selecting text and "
                "pasting an HTTP(S) URL turns it into a Markdown link automatically.",
                object(), client, monitor, Rect(48, 215, 806, 112), "static",
            ),
        ]

        assembled = assemble_paragraph_targets(fragments)
        self.assertEqual(len(assembled), 2)
        self.assertIn("renders every - [ ] ↔ - [x] ... ✅ YYYY-MM-DD toggles checkbox", assembled[0].text)
        self.assertTrue(assembled[1].text.startswith("**Inline task entry**"))
        self.assertIn("with the button; press Return", assembled[1].text)

    def test_blank_line_boundaries_are_never_reassembled(self) -> None:
        monitor = Monitor("test", Rect(0, 0, 1000, 800), monitor_id=2)
        client = Client(42, Rect(0, 0, 1000, 800), 2)
        first = ParagraphTarget(
            "One short sentence without final punctuation",
            object(), client, monitor, Rect(20, 20, 500, 30), "static", True,
        )
        second = ParagraphTarget(
            "another short sentence",
            object(), client, monitor, Rect(20, 51, 500, 30), "static", True,
        )
        self.assertEqual(assemble_paragraph_targets([first, second]), [first, second])

    def test_overlapping_fragment_with_leading_period_is_reassembled(self) -> None:
        monitor = Monitor("test", Rect(0, 0, 1000, 800), monitor_id=2)
        client = Client(42, Rect(0, 0, 1000, 800), 2)
        first = ParagraphTarget(
            "There are no scores in the seven-day window; the last recorded score is",
            object(), client, monitor, Rect(20, 20, 600, 40), "static",
        )
        second = ParagraphTarget(
            ". Open a daily note and fill its Properties to populate the averages.",
            object(), client, monitor, Rect(20, 42, 580, 40), "static",
        )
        assembled = assemble_paragraph_targets([first, second])
        self.assertEqual(len(assembled), 1)
        self.assertIn("score is. Open a daily note", assembled[0].text)


class OverlayReleaseTests(unittest.TestCase):
    def test_detects_omajump_layer_on_any_monitor(self) -> None:
        layers = {
            "DP-2": {"levels": {"3": [{"namespace": "omajump-DP-2"}]}},
            "HDMI-A-2": {"levels": {"3": [{"namespace": "notifications"}]}},
        }
        self.assertTrue(_omajump_layers_visible(layers))

    def test_ignores_unrelated_layers(self) -> None:
        layers = {"DP-2": {"levels": {"3": [{"namespace": "notifications"}]}}}
        self.assertFalse(_omajump_layers_visible(layers))


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

    def test_page_tab_pointer_fallback_clicks_exact_window(self) -> None:
        monitor = Monitor("test", Rect(100, 50, 1000, 800), monitor_id=2)
        client = Client(42, Rect(200, 100, 600, 500), 2, address="0xabc123")
        candidate = Candidate(
            object(), Rect(120, 70, 80, 30), "page tab", -2, "pointer",
            "test", client, monitor,
        )
        session = BackendSession()
        session.result = ScanResult(monitor, [candidate], 1, False)
        with (
            patch("omajump.backend._wait_for_overlay_release"),
            patch(
                "omajump.backend.subprocess.run",
                return_value=SimpleNamespace(stdout="ok\n"),
            ) as run,
        ):
            response = session.activate(0)
        self.assertTrue(response["ok"])
        commands = [call.args[0][-1] for call in run.call_args_list]
        self.assertIn('window = "address:0xabc123"', commands[0])
        self.assertIn("x = 260", commands[1])
        self.assertIn("y = 135", commands[1])
        self.assertIn('key = "mouse:272"', commands[2])

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
            patch("omajump.backend._wait_for_overlay_release"),
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
        self.assertEqual(run.call_count, 2)
        commands = [call.args[0][-1] for call in run.call_args_list]
        self.assertIn('mods = "SHIFT"', commands[0])
        self.assertIn('key = "PAGE_DOWN"', commands[0])
        self.assertIn('state = "down"', commands[0])
        self.assertIn('window = "address:0xabc123"', commands[0])
        self.assertIn('state = "up"', commands[1])

    def test_search_enter_clicks_match_center_in_exact_window(self) -> None:
        session = BackendSession()
        monitor = Monitor("test", Rect(100, 50, 1000, 800), monitor_id=2)
        client = Client(42, Rect(200, 100, 600, 500), 2, address="0xabc123")
        target = SearchTarget(
            "Matching text", object(), client, monitor, Rect(200, 150, 80, 20)
        )
        session.result = ScanResult(
            monitor, [], 1, False, monitors=[monitor], search_targets=[target]
        )
        with (
            patch("omajump.backend._wait_for_overlay_release"),
            patch(
                "omajump.backend.subprocess.run",
                return_value=SimpleNamespace(stdout="ok\n"),
            ) as run,
        ):
            response = session.search_click(0)
        self.assertTrue(response["ok"])
        commands = [call.args[0][-1] for call in run.call_args_list]
        self.assertIn("x = 340", commands[1])
        self.assertIn("y = 210", commands[1])
        self.assertIn('window = "address:0xabc123"', commands[2])

    def test_selected_paragraph_is_copied_verbatim(self) -> None:
        session = BackendSession()
        monitor = Monitor("test", Rect(0, 0, 1000, 800), monitor_id=2)
        client = Client(42, Rect(0, 0, 500, 400), 2)
        target = ParagraphTarget(
            "First line\nSecond line", object(), client, monitor, Rect(2, 2, 100, 40)
        )
        session.result = ScanResult(
            monitor, [], 1, False, monitors=[monitor], paragraph_targets=[target]
        )
        with patch("omajump.backend.subprocess.run") as run:
            response = session.copy_paragraph(0)
        self.assertTrue(response["ok"])
        self.assertEqual(run.call_args.args[0], ["wl-copy"])
        self.assertEqual(run.call_args.kwargs["input"], "First line\nSecond line")


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

    def test_child_snapshot_retains_declared_lazy_count(self) -> None:
        node = _LazyNode()
        children, declared = _child_snapshot(node)
        self.assertEqual(children, [])
        self.assertEqual(declared, 1)


if __name__ == "__main__":
    unittest.main()

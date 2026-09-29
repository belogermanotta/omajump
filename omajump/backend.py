"""Live AT-SPI session used by the OmaJump Quickshell overlay."""

from __future__ import annotations

import argparse
import difflib
import json
import math
import os
import re
import subprocess
import sys
import time
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any, Callable, Iterable

from .geometry import CoordinateMapper, deduplicate_candidates, frame_match_score
from .labels import generate_labels
from .model import Candidate, Client, Monitor, Rect


PRIMARY_ACTIONS = (
    "press",
    "click",
    "activate",
    "jump",
    "open",
    "check",
    "toggle",
    "dodefault",
)

EXPLICIT_ACTIONS = set(PRIMARY_ACTIONS) - {"dodefault"}
EDITABLE_ROLES = {"entry", "password text", "text", "edit bar", "terminal"}
DEFAULT_ACTION_ROLES = {
    "button",
    "check box",
    "combo box",
    "link",
    "list item",
    "menu item",
    "page tab",
    "radio button",
    "slider",
    "spin button",
    "toggle button",
}
STRUCTURAL_ROLES = {
    "application",
    "document frame",
    "document web",
    "frame",
    "header",
    "landmark",
    "list",
    "panel",
    "paragraph",
    "progress bar",
    "separator",
    "static",
    "status bar",
}

SCROLLABLE_ROLES = {
    "document frame",
    "document web",
    "list",
    "scroll pane",
    "table",
    "terminal",
    "tree",
    "viewport",
}

TEXT_ROLES = {
    "button",
    "caption",
    "check box",
    "document frame",
    "document web",
    "entry",
    "heading",
    "label",
    "link",
    "list item",
    "menu item",
    "paragraph",
    "radio button",
    "static",
    "text",
}


class BackendError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def choose_action(role: str, actions: Iterable[str], states: set[str]) -> tuple[int, str] | None:
    """Select a direct action, pointer fallback, or editable-control focus."""

    if role in STRUCTURAL_ROLES:
        return None

    normalized = [str(action or "").replace("-", "").replace("_", "").lower() for action in actions]
    for wanted in PRIMARY_ACTIONS:
        if wanted not in normalized:
            continue
        if wanted in EXPLICIT_ACTIONS or role in DEFAULT_ACTION_ROLES:
            return normalized.index(wanted), wanted

    # Chromium-family browser chrome (including Vivaldi) commonly exposes
    # tabs with only "clickAncestor". Calling that action is noisy and often
    # reports success without changing tabs, so click the semantic tab bounds
    # through Hyprland instead. Direct AT-SPI actions above remain preferred.
    if role == "page tab":
        return -2, "pointer"

    if role in EDITABLE_ROLES and "focusable" in states:
        return -1, "focus"
    return None


def _json_command(*args: str) -> Any:
    try:
        completed = subprocess.run(args, check=True, text=True, capture_output=True, timeout=1.5)
        return json.loads(completed.stdout)
    except (FileNotFoundError, subprocess.SubprocessError, json.JSONDecodeError) as error:
        raise BackendError("hyprland-unavailable", f"Could not query Hyprland: {error}") from error


def _omajump_layers_visible(layers: Any) -> bool:
    if not isinstance(layers, dict):
        return False
    for monitor in layers.values():
        if not isinstance(monitor, dict):
            continue
        levels = monitor.get("levels", {})
        if not isinstance(levels, dict):
            continue
        for surfaces in levels.values():
            if not isinstance(surfaces, list):
                continue
            if any(
                isinstance(surface, dict)
                and str(surface.get("namespace", "")).startswith("omajump-")
                for surface in surfaces
            ):
                return True
    return False


def _wait_for_overlay_release(timeout: float = 0.75) -> None:
    """Wait until Hyprland has unmapped OmaJump before sending a pointer event."""

    deadline = time.monotonic() + timeout
    while True:
        try:
            if not _omajump_layers_visible(_json_command("hyprctl", "-j", "layers")):
                break
        except BackendError:
            break
        if time.monotonic() >= deadline:
            break
        time.sleep(0.025)
    # Allow normal pointer focus to settle after the final layer disappears.
    time.sleep(0.04)


def _screens_geometry() -> tuple[list[tuple[list[Client], Monitor]], str]:
    """Return visible clients for every monitor's active workspace."""

    monitors_json = _json_command("hyprctl", "-j", "monitors")
    clients_json = _json_command("hyprctl", "-j", "clients")
    focused_name = next(
        (str(item.get("name", "")) for item in monitors_json if item.get("focused")), ""
    )
    if not monitors_json or not focused_name:
        raise BackendError("no-monitor", "Could not identify the connected monitors")

    contexts: list[tuple[list[Client], Monitor]] = []
    for monitor_data in monitors_json:
        scale = max(0.1, float(monitor_data.get("scale", 1.0)))
        width = float(monitor_data.get("width", 0))
        height = float(monitor_data.get("height", 0))
        if int(monitor_data.get("transform", 0)) % 2:
            width, height = height, width
        monitor = Monitor(
            name=str(monitor_data.get("name", "")),
            rect=Rect(
                float(monitor_data.get("x", 0)),
                float(monitor_data.get("y", 0)),
                width / scale,
                height / scale,
            ),
            scale=scale,
            monitor_id=int(monitor_data.get("id", -1)),
        )
        workspace_id = int(monitor_data.get("activeWorkspace", {}).get("id", -1))
        visible: list[Client] = []
        for item in clients_json:
            at = item.get("at", [0, 0])
            size = item.get("size", [0, 0])
            client = Client(
                pid=int(item.get("pid", 0)),
                rect=Rect(float(at[0]), float(at[1]), float(size[0]), float(size[1])),
                monitor_id=int(item.get("monitor", -2)),
                workspace_id=int(item.get("workspace", {}).get("id", -2)),
                focus_history_id=int(item.get("focusHistoryID", -1)),
                fullscreen=int(item.get("fullscreen", 0)),
                title=str(item.get("title", "")),
                address=str(item.get("address", "")),
                class_name=str(item.get("class", "")),
            )
            if (
                client.pid > 0
                and client.monitor_id == monitor.monitor_id
                and client.workspace_id == workspace_id
                and bool(item.get("mapped", True))
                and not bool(item.get("hidden", False))
                and client.rect.width > 1
                and client.rect.height > 1
                and client.rect.intersection(monitor.rect) is not None
            ):
                visible.append(client)
        fullscreen_clients = [client for client in visible if client.fullscreen == 2]
        if fullscreen_clients:
            visible = fullscreen_clients
        visible.sort(
            key=lambda client: (
                client.focus_history_id < 0,
                client.focus_history_id if client.focus_history_id >= 0 else 1_000_000,
            )
        )
        contexts.append((visible, monitor))
    return contexts, focused_name


def _screen_geometry() -> tuple[list[Client], Monitor]:
    """Compatibility wrapper returning the focused monitor context."""

    contexts, focused_name = _screens_geometry()
    for clients, monitor in contexts:
        if monitor.name == focused_name:
            return clients, monitor
    raise BackendError("no-monitor", "Could not identify the focused monitor")


def _parent_pid(pid: int) -> int:
    try:
        # Field two may contain spaces and parentheses; split after its final ')'.
        with open(f"/proc/{pid}/stat", encoding="utf-8") as stat_file:
            tail = stat_file.read().rsplit(")", 1)[1].split()
        return int(tail[1])
    except (OSError, ValueError, IndexError):
        return 0


@lru_cache(maxsize=512)
def _ancestry(pid: int, limit: int = 24) -> tuple[int, ...]:
    result: list[int] = []
    current = pid
    while current > 1 and current not in result and len(result) < limit:
        result.append(current)
        current = _parent_pid(current)
    return tuple(result)


def process_distance(left: int, right: int) -> int | None:
    if left <= 0 or right <= 0:
        return None
    left_chain = _ancestry(left)
    right_chain = _ancestry(right)
    if right in left_chain:
        return left_chain.index(right)
    if left in right_chain:
        return right_chain.index(left)
    return None


def _rect_for(node: Any, Atspi: Any) -> Rect | None:
    try:
        component = node.get_component_iface()
        extent = component.get_extents(Atspi.CoordType.SCREEN)
        return Rect(float(extent.x), float(extent.y), float(extent.width), float(extent.height))
    except Exception:
        return None


def _role(node: Any) -> str:
    try:
        return str(node.get_role_name() or "").lower()
    except Exception:
        return ""


def _title_match_score(node: Any, title: str) -> float:
    """Return a small penalty that disambiguates same-sized app windows."""

    try:
        accessible_name = str(node.get_name() or "")
    except Exception:
        accessible_name = ""
    left = " ".join(accessible_name.casefold().split())
    right = " ".join(title.casefold().split())
    if not left or not right:
        return 0.5
    if left == right:
        return 0.0
    if left in right or right in left:
        return 0.1
    return 1.0 - difflib.SequenceMatcher(None, left, right).ratio()


def _child_snapshot(node: Any, limit: int = 2000) -> tuple[list[Any], int]:
    children: list[Any] = []
    try:
        declared = max(0, int(node.get_child_count()))
        count = min(declared, limit)
        for index in range(count):
            try:
                child = node.get_child_at_index(index)
                if child is not None:
                    children.append(child)
            except Exception:
                continue
    except Exception:
        return [], 0
    return children, declared


def _children(node: Any, limit: int = 2000) -> list[Any]:
    return _child_snapshot(node, limit)[0]


def _prime_accessibility(
    node: Any,
    timeout: float = 0.3,
    children: list[Any] | None = None,
    declared_children: int | None = None,
) -> list[Any]:
    """Wake lazy accessibility trees, notably Chromium/Electron renderers."""

    if children is None:
        children, declared_children = _child_snapshot(node)
    if children:
        return children
    if not declared_children:
        return []
    for method_name in ("get_attributes", "get_relation_set"):
        try:
            getattr(node, method_name)()
        except Exception:
            pass
    deadline = time.monotonic() + max(0.0, timeout)
    while time.monotonic() < deadline:
        children = _children(node)
        if children:
            return children
        time.sleep(0.02)
    return []


def _top_level_frames(application: Any, Atspi: Any) -> list[tuple[Any, Rect]]:
    frames: list[tuple[Any, Rect]] = []
    queue = [(application, 0)]
    while queue:
        node, depth = queue.pop(0)
        role = _role(node)
        rect = _rect_for(node, Atspi)
        if node is not application and rect is not None and role in {"frame", "dialog", "window", "alert"}:
            frames.append((node, rect))
            continue
        if depth < 3:
            queue.extend((child, depth + 1) for child in _children(node, 100))
    if not frames:
        rect = _rect_for(application, Atspi)
        if rect is not None:
            frames.append((application, rect))
    return frames


def _node_states(node: Any, Atspi: Any) -> set[str]:
    result: set[str] = set()
    try:
        state_set = node.get_state_set()
        for name, enum_value in (
            ("enabled", Atspi.StateType.ENABLED),
            ("focusable", Atspi.StateType.FOCUSABLE),
            ("focused", Atspi.StateType.FOCUSED),
            ("showing", Atspi.StateType.SHOWING),
            ("visible", Atspi.StateType.VISIBLE),
        ):
            if state_set.contains(enum_value):
                result.add(name)
    except Exception:
        pass
    return result


def _has_focused_descendant(application: Any, Atspi: Any, budget: int = 2500) -> bool:
    stack = [(application, 0)]
    seen = 0
    while stack and seen < budget:
        node, depth = stack.pop()
        seen += 1
        if "focused" in _node_states(node, Atspi):
            return True
        if depth < 40:
            stack.extend((child, depth + 1) for child in reversed(_children(node)))
    return False


def _window_inventory(desktop: Any, Atspi: Any) -> list[tuple[int, Any, Any, Rect]]:
    inventory: list[tuple[int, Any, Any, Rect]] = []
    for application in _children(desktop, 200):
        try:
            app_pid = int(application.get_process_id())
        except Exception:
            app_pid = 0
        inventory.extend(
            (app_pid, application, frame, rect)
            for frame, rect in _top_level_frames(application, Atspi)
        )
    return inventory


def _match_windows(
    desktop: Any,
    clients: list[Client],
    monitor: Monitor,
    Atspi: Any,
    inventory: list[tuple[int, Any, Any, Rect]] | None = None,
    used: set[int] | None = None,
) -> list[tuple[Client, Any, Rect]]:
    if inventory is None:
        inventory = _window_inventory(desktop, Atspi)
    matches: list[tuple[Client, Any, Rect]] = []
    used = used if used is not None else set()
    for client in clients:
        choices: list[tuple[float, int]] = []
        for index, (app_pid, _application, _frame, rect) in enumerate(inventory):
            if index in used:
                continue
            distance = process_distance(client.pid, app_pid)
            if distance is not None:
                score = (
                    distance * 5
                    + frame_match_score(rect, client, monitor)
                    + _title_match_score(_frame, client.title) * 2
                )
                choices.append((score, index))

        # Some toolkits broker accessibility through a separate process. The
        # focused-state fallback is safe only when there is one visible client.
        if not choices and len(clients) == 1:
            for index, (_app_pid, application, _frame, rect) in enumerate(inventory):
                if index in used:
                    continue
                geometry_score = frame_match_score(rect, client, monitor)
                if geometry_score < 0.25 and _has_focused_descendant(application, Atspi):
                    choices.append((geometry_score, index))
        if not choices:
            continue
        _, index = min(choices, key=lambda item: item[0])
        used.add(index)
        _app_pid, _application, frame, rect = inventory[index]
        matches.append((client, frame, rect))
    return matches


def _action_names(node: Any) -> list[str]:
    try:
        interface = node.get_action_iface()
        return [str(interface.get_action_name(index) or "") for index in range(interface.get_n_actions())]
    except Exception:
        return []


def _node_text(node: Any, limit: int = 240) -> str:
    """Return a compact accessible label suitable for live text search."""

    values: list[str] = []
    try:
        values.append(str(node.get_name() or ""))
    except Exception:
        pass
    if not any(value.strip() for value in values):
        try:
            text_iface = node.get_text_iface()
            values.append(
                str(text_iface.get_text(0, min(limit, text_iface.get_character_count())) or "")
            )
        except Exception:
            pass
    return " ".join(" ".join(values).split())[:limit]


def _node_text_range(
    node: Any, limit: int = 20_000, prefer_tail: bool = False
) -> tuple[str, int, Any | None]:
    """Return paragraph text, its AT-SPI offset, and the text interface.

    Paragraph mode must preserve line breaks. Terminals can expose a very large
    scrollback buffer, so read its most recent bounded tail instead of the
    oldest text.
    """

    try:
        text_iface = node.get_text_iface()
        count = max(0, int(text_iface.get_character_count()))
        start = max(0, count - limit) if prefer_tail else 0
        end = min(count, start + limit)
        value = str(text_iface.get_text(start, end) or "")
        if value.strip():
            return value, start, text_iface
    except Exception:
        pass
    try:
        value = str(node.get_name() or "")[:limit]
    except Exception:
        value = ""
    return value, 0, None


def _paragraph_ranges(text: str) -> list[tuple[int, int, str, bool]]:
    """Split text at blank lines while retaining source offsets."""

    breaks = list(re.finditer(r"(?:\r?\n)[^\S\r\n]*(?:\r?\n)+", text))
    bounds: list[tuple[int, int]] = []
    start = 0
    for match in breaks:
        bounds.append((start, match.start()))
        start = match.end()
    bounds.append((start, len(text)))

    ranges: list[tuple[int, int, str, bool]] = []
    for raw_start, raw_end in bounds:
        while raw_start < raw_end and text[raw_start].isspace():
            raw_start += 1
        while raw_end > raw_start and text[raw_end - 1].isspace():
            raw_end -= 1
        if raw_start < raw_end:
            ranges.append((raw_start, raw_end, text[raw_start:raw_end], bool(breaks)))
    return ranges


def _range_rect(text_iface: Any, start: int, end: int, Atspi: Any) -> Rect | None:
    try:
        extent = text_iface.get_range_extents(start, end, Atspi.CoordType.SCREEN)
        rect = Rect(float(extent.x), float(extent.y), float(extent.width), float(extent.height))
        return rect if rect.width > 0 and rect.height > 0 else None
    except Exception:
        return None


def _approximate_range_rect(container: Rect, text: str, start: int, end: int) -> Rect:
    """Estimate line geometry when a toolkit omits AT-SPI range extents."""

    line_count = max(1, text.count("\n") + 1)
    first_line = min(line_count - 1, text.count("\n", 0, start))
    last_line = min(line_count - 1, text.count("\n", 0, end))
    line_height = container.height / line_count
    return Rect(
        container.x,
        container.y + first_line * line_height,
        container.width,
        max(line_height, (last_line - first_line + 1) * line_height),
    )


def _wezterm_panes() -> list[dict[str, Any]]:
    """Return bounded local WezTerm pane metadata, when available."""

    try:
        completed = subprocess.run(
            ["wezterm", "cli", "list", "--format", "json"],
            check=True,
            text=True,
            capture_output=True,
            timeout=1.5,
        )
        payload = json.loads(completed.stdout)
    except (FileNotFoundError, subprocess.SubprocessError, json.JSONDecodeError):
        return []
    if not isinstance(payload, list):
        return []
    return [item for item in payload[:100] if isinstance(item, dict)]


def _is_wezterm_client(client: Client) -> bool:
    if client.class_name.casefold() not in {
        "org.wezfurlong.wezterm",
        "wezterm",
        "wezterm-gui",
    }:
        return False
    try:
        executable = os.path.basename(os.readlink(f"/proc/{client.pid}/exe"))
    except OSError:
        return False
    return executable in {"wezterm", "wezterm-gui"}


def _wezterm_paragraph_targets(
    client: Client, monitor: Monitor, panes: list[dict[str, Any]]
) -> list["ParagraphTarget"]:
    """Read visible WezTerm text when the terminal publishes no AT-SPI tree."""

    if not _is_wezterm_client(client):
        return []
    title = " ".join(client.title.casefold().split())
    matches = [
        pane
        for pane in panes
        if title
        and title
        in {
            " ".join(str(pane.get("title", "")).casefold().split()),
            " ".join(str(pane.get("window_title", "")).casefold().split()),
        }
    ]
    if len(matches) != 1:
        return []
    try:
        pane_id = int(matches[0]["pane_id"])
        rows = max(1, min(1000, int(matches[0].get("size", {}).get("rows", 0))))
        if pane_id < 0:
            return []
        completed = subprocess.run(
            [
                "wezterm", "cli", "get-text", "--pane-id", str(pane_id),
                "--start-line", "0",
            ],
            check=True,
            text=True,
            capture_output=True,
            timeout=1.5,
        )
    except (KeyError, TypeError, ValueError, FileNotFoundError, subprocess.SubprocessError):
        return []

    # A visible pane is small, but cap untrusted terminal output defensively.
    text = completed.stdout[:100_000]
    ranges = _paragraph_ranges(text)
    if not any(separated for _start, _end, _text, separated in ranges):
        return []

    local_x = client.rect.x - monitor.rect.x
    local_y = client.rect.y - monitor.rect.y
    line_height = client.rect.height / rows
    targets: list[ParagraphTarget] = []
    for start, end, paragraph, separated in ranges:
        if not _is_paragraph_like("terminal", paragraph, client.rect, separated):
            continue
        first_row = min(rows - 1, text.count("\n", 0, start))
        last_row = min(rows - 1, text.count("\n", 0, end))
        rect = Rect(
            local_x,
            local_y + first_row * line_height,
            client.rect.width,
            max(line_height, (last_row - first_row + 1) * line_height),
        )
        targets.append(ParagraphTarget(paragraph, None, client, monitor, rect))
    return targets


def _is_scrollable(node: Any, role: str) -> bool:
    if role in SCROLLABLE_ROLES:
        return True
    try:
        attributes = node.get_attributes() or []
        if isinstance(attributes, dict):
            values = (f"{key}:{value}" for key, value in attributes.items())
        else:
            values = (str(value) for value in attributes)
        return any(value.casefold() in {"scrollable:true", "scrollable:1"} for value in values)
    except Exception:
        return False


def _is_paragraph_like(
    role: str, text: str, rect: Rect, separated_by_gap: bool = False
) -> bool:
    """Recognize prose blocks in Chromium trees that expose them as static text."""

    if role == "paragraph":
        return bool(text.strip())
    if role not in {"section", "static", "terminal", "text"}:
        return False
    # A blank line is an explicit author/toolkit paragraph boundary. Respect it
    # even when the resulting block contains only one short sentence.
    if separated_by_gap:
        return bool(text.strip())
    # A terminal without blank-line boundaries is usually prompts and command
    # output rather than a selectable prose block.
    if role == "terminal":
        return False
    words = text.split()
    if len(words) < 7 or len(text) < 40 or text.endswith("…"):
        return False
    return rect.height >= 24 or len(text) >= 100


def deduplicate_paragraph_targets(
    targets: list["ParagraphTarget"],
) -> list["ParagraphTarget"]:
    """Collapse nested AT-SPI fragments that describe one prose block.

    Browser accessibility trees often expose a complete paragraph and one or
    more child static-text nodes containing wrapped subsets of the same text.
    Prefer the most complete text; for equal text, prefer tighter geometry.
    """

    normalized = [" ".join(target.text.casefold().split()) for target in targets]
    ranked = sorted(
        range(len(targets)),
        key=lambda index: (-len(normalized[index]), targets[index].rect.area, index),
    )
    kept: list[int] = []
    for index in ranked:
        candidate = targets[index]
        candidate_text = normalized[index]
        duplicate = False
        for kept_index in kept:
            existing = targets[kept_index]
            existing_text = normalized[kept_index]
            if (
                candidate.monitor.name != existing.monitor.name
                or candidate.client != existing.client
                or not candidate_text
                or not existing_text
                or (
                    candidate_text not in existing_text
                    and existing_text not in candidate_text
                )
            ):
                continue
            overlap = candidate.rect.intersection(existing.rect)
            smaller_area = min(candidate.rect.area, existing.rect.area)
            if overlap is not None and smaller_area > 0 and overlap.area / smaller_area >= 0.8:
                duplicate = True
                break
        if not duplicate:
            kept.append(index)

    # Retain discovery order so target IDs remain stable across scans.
    return [targets[index] for index in sorted(kept)]


@dataclass(slots=True)
class FallbackTarget:
    client: Client
    monitor: Monitor


@dataclass(slots=True)
class SearchTarget:
    text: str
    node: object
    client: Client
    monitor: Monitor
    rect: Rect


@dataclass(slots=True)
class ScrollTarget:
    node: object | None
    client: Client
    monitor: Monitor
    rect: Rect


@dataclass(slots=True)
class ParagraphTarget:
    text: str
    node: object | None
    client: Client
    monitor: Monitor
    rect: Rect


@dataclass(slots=True)
class ScanResult:
    monitor: Monitor
    candidates: list[Candidate]
    nodes_scanned: int
    truncated: bool
    fallback_clients: list[Client] = field(default_factory=list)
    monitors: list[Monitor] = field(default_factory=list)
    fallback_targets: list[FallbackTarget] = field(default_factory=list)
    search_targets: list[SearchTarget] = field(default_factory=list)
    scroll_targets: list[ScrollTarget] = field(default_factory=list)
    paragraph_targets: list[ParagraphTarget] = field(default_factory=list)
    input_monitor: str = ""


class BackendSession:
    def __init__(self, max_nodes: int = 6000, max_targets: int = 500) -> None:
        self.max_nodes = max_nodes
        self.max_targets = max_targets
        self.Atspi: Any = None
        self.result: ScanResult | None = None

    def _load_atspi(self) -> Any:
        if self.Atspi is not None:
            return self.Atspi
        try:
            import gi

            gi.require_version("Atspi", "2.0")
            from gi.repository import Atspi

            Atspi.init()
        except (ImportError, ValueError, RuntimeError) as error:
            raise BackendError("atspi-unavailable", f"AT-SPI is unavailable: {error}") from error
        self.Atspi = Atspi
        return Atspi

    def scan(
        self,
        context_callback: Callable[[dict[str, Any]], None] | None = None,
        mode: str = "hints",
    ) -> dict[str, Any]:
        if mode not in {"hints", "search", "scroll", "paragraph"}:
            raise BackendError("invalid-mode", f"Unknown OmaJump mode: {mode}")
        Atspi = self._load_atspi()
        contexts, input_monitor = _screens_geometry()
        monitor = next(
            (item for _clients, item in contexts if item.name == input_monitor),
            contexts[0][1],
        )
        if context_callback is not None:
            context_callback(
                {
                    "type": "context",
                    "inputMonitor": input_monitor,
                    "monitors": [item.name for _clients, item in contexts],
                }
            )
        desktop = Atspi.get_desktop(0)
        if desktop is None:
            raise BackendError("atspi-unavailable", "The AT-SPI desktop could not be opened")

        candidates: list[Candidate] = []
        search_targets: list[SearchTarget] = []
        scroll_targets: list[ScrollTarget] = []
        paragraph_targets: list[ParagraphTarget] = []
        fallback_targets: list[FallbackTarget] = []
        nodes_scanned = 0
        truncated = False
        inventory = _window_inventory(desktop, Atspi)
        used_inventory: set[int] = set()
        windows_scanned = 0
        windows_considered = sum(len(clients) for clients, _monitor in contexts)
        wezterm_panes = (
            _wezterm_panes()
            if mode == "paragraph"
            and any(
                _is_wezterm_client(client)
                for clients, _monitor in contexts
                for client in clients
            )
            else []
        )

        for clients, current_monitor in contexts:
            matches = _match_windows(
                desktop, clients, current_monitor, Atspi, inventory, used_inventory
            )
            windows_scanned += len(matches)
            matched_client_ids = {id(client) for client, _frame, _rect in matches}
            for client, frame, frame_rect in matches:
                window_candidates: list[Candidate] = []
                window_scroll_targets: list[ScrollTarget] = []
                frame_children = _prime_accessibility(frame)
                mapper = CoordinateMapper.infer(client, current_monitor, frame_rect)
                stack: list[tuple[Any, int, float]] = [(frame, 0, mapper.base_scale)]
                window_nodes = 0

                while stack and window_nodes < self.max_nodes:
                    node, depth, inherited_scale = stack.pop()
                    window_nodes += 1
                    nodes_scanned += 1
                    role = _role(node)
                    raw_rect = _rect_for(node, Atspi)
                    node_scale = (
                        mapper.derive_scale(raw_rect, inherited_scale, role)
                        if raw_rect is not None
                        else inherited_scale
                    )
                    visible = mapper.visible_rect(raw_rect, node_scale) if raw_rect is not None else None

                    if mode == "hints":
                        actionable_role = role not in STRUCTURAL_ROLES
                        states = (
                            _node_states(node, Atspi)
                            if visible is not None and actionable_role
                            else set()
                        )
                        selected = (
                            None
                            if not actionable_role
                            else choose_action(role, _action_names(node), states)
                        )
                        covers_window = (
                            visible is not None
                            and visible.area / max(1.0, client.rect.area) >= 0.65
                            and role not in DEFAULT_ACTION_ROLES
                            and role not in EDITABLE_ROLES
                        )
                        if (
                            visible is not None
                            and selected is not None
                            and {"enabled", "showing", "visible"}.issubset(states)
                            and not covers_window
                            and visible.width >= 2
                            and visible.height >= 2
                        ):
                            action_index, action_name = selected
                            window_candidates.append(
                                Candidate(
                                    node=node,
                                    rect=mapper.overlay_rect(visible),
                                    role=role,
                                    action_index=action_index,
                                    action_name=action_name,
                                    monitor_name=current_monitor.name,
                                    client=client,
                                    monitor=current_monitor,
                                )
                            )
                    elif mode == "search":
                        states = (
                            _node_states(node, Atspi)
                            if visible is not None and role in TEXT_ROLES
                            else set()
                        )
                        if (
                            visible is not None
                            and role in TEXT_ROLES
                            and {"showing", "visible"}.issubset(states)
                            and visible.width >= 2
                            and visible.height >= 2
                        ):
                            text = _node_text(node)
                            if text:
                                search_targets.append(
                                    SearchTarget(
                                        text, node, client, current_monitor,
                                        mapper.overlay_rect(visible),
                                    )
                                )
                    elif mode == "paragraph":
                        paragraph_role = role in {
                            "paragraph", "section", "static", "terminal", "text"
                        }
                        states = (
                            _node_states(node, Atspi)
                            if visible is not None and paragraph_role
                            else set()
                        )
                        if (
                            visible is not None
                            and paragraph_role
                            and {"showing", "visible"}.issubset(states)
                            and visible.width >= 2
                            and visible.height >= 2
                        ):
                            source, source_offset, text_iface = _node_text_range(
                                node, prefer_tail=role == "terminal"
                            )
                            ranges = _paragraph_ranges(source)
                            for start, end, text, separated in ranges:
                                block_raw = (
                                    _range_rect(
                                        text_iface,
                                        source_offset + start,
                                        source_offset + end,
                                        Atspi,
                                    )
                                    if text_iface is not None
                                    else None
                                )
                                block_visible = (
                                    mapper.visible_rect(block_raw, node_scale)
                                    if block_raw is not None
                                    else visible
                                    if len(ranges) == 1
                                    else _approximate_range_rect(visible, source, start, end)
                                )
                                if block_visible is None or not _is_paragraph_like(
                                    role,
                                    text,
                                    mapper.overlay_rect(block_visible),
                                    separated,
                                ):
                                    continue
                                paragraph_targets.append(
                                    ParagraphTarget(
                                        text, node, client, current_monitor,
                                        mapper.overlay_rect(block_visible),
                                    )
                                )
                    elif (
                        visible is not None
                        and _is_scrollable(node, role)
                        and {"showing", "visible"}.issubset(_node_states(node, Atspi))
                        and visible.width >= 64
                        and visible.height >= 64
                    ):
                        window_scroll_targets.append(
                            ScrollTarget(node, client, current_monitor, mapper.overlay_rect(visible))
                        )

                    if depth < 48:
                        if node is frame:
                            children = frame_children
                            declared_children = len(frame_children)
                        else:
                            children, declared_children = _child_snapshot(node)
                        if not children and declared_children:
                            children = _prime_accessibility(
                                node,
                                timeout=0.12,
                                children=children,
                                declared_children=declared_children,
                            )
                        stack.extend(
                            (child, depth + 1, node_scale) for child in reversed(children)
                        )
                truncated = truncated or bool(stack)
                if mode == "hints":
                    window_candidates = deduplicate_candidates(window_candidates)
                    candidates.extend(window_candidates)
                    if not window_candidates:
                        fallback_targets.append(FallbackTarget(client, current_monitor))
                elif mode == "scroll":
                    if window_scroll_targets:
                        # Prefer the most specific region when nested containers overlap.
                        for item in sorted(window_scroll_targets, key=lambda target: target.rect.area):
                            if not any(
                                item.monitor.name == kept.monitor.name
                                and item.rect.iou(kept.rect) >= 0.86
                                for kept in scroll_targets
                            ):
                                scroll_targets.append(item)
                    else:
                        scroll_targets.append(
                            ScrollTarget(
                                None,
                                client,
                                current_monitor,
                                Rect(
                                    client.rect.x - current_monitor.rect.x,
                                    client.rect.y - current_monitor.rect.y,
                                    client.rect.width,
                                    client.rect.height,
                                ),
                            )
                        )

            if mode == "hints":
                fallback_targets.extend(
                    FallbackTarget(client, current_monitor)
                    for client in clients
                    if id(client) not in matched_client_ids
                )
            elif mode == "scroll":
                scroll_targets.extend(
                    ScrollTarget(
                        None,
                        client,
                        current_monitor,
                        Rect(
                            client.rect.x - current_monitor.rect.x,
                            client.rect.y - current_monitor.rect.y,
                            client.rect.width,
                            client.rect.height,
                        ),
                    )
                    for client in clients
                    if id(client) not in matched_client_ids
                )
            elif mode == "paragraph" and wezterm_panes:
                for client in clients:
                    if _is_wezterm_client(client):
                        paragraph_targets.extend(
                            _wezterm_paragraph_targets(client, current_monitor, wezterm_panes)
                        )

        # Remove repeated accessible text fragments and nested duplicate regions.
        unique_search: list[SearchTarget] = []
        search_keys: set[tuple[Any, ...]] = set()
        for item in search_targets:
            key = (
                item.monitor.name,
                item.text.casefold(),
                round(item.rect.x),
                round(item.rect.y),
                round(item.rect.width),
                round(item.rect.height),
            )
            if key not in search_keys:
                search_keys.add(key)
                unique_search.append(item)
        search_targets = unique_search[:1500]
        paragraph_targets = deduplicate_paragraph_targets(paragraph_targets)[: self.max_targets]
        if len(candidates) > self.max_targets:
            candidates = candidates[: self.max_targets]
            truncated = True
        if len(scroll_targets) > self.max_targets:
            scroll_targets = scroll_targets[: self.max_targets]
            truncated = True
        fallback_clients = [item.client for item in fallback_targets]
        self.result = ScanResult(
            monitor,
            candidates,
            nodes_scanned,
            truncated,
            fallback_clients,
            [item for _clients, item in contexts],
            fallback_targets,
            search_targets,
            scroll_targets,
            paragraph_targets,
            input_monitor,
        )
        labels = generate_labels(len(candidates))

        return {
            "type": "targets",
            "mode": mode,
            "inputMonitor": input_monitor,
            "monitors": [item.name for _clients, item in contexts],
            "targets": [
                {
                    "id": index,
                    "label": labels[index],
                    "monitor": candidate.monitor_name,
                    "x": round(candidate.rect.x, 2),
                    "y": round(candidate.rect.y, 2),
                    "width": round(candidate.rect.width, 2),
                    "height": round(candidate.rect.height, 2),
                }
                for index, candidate in enumerate(candidates)
            ],
            "fallbackWindows": [
                {
                    "id": index,
                    "monitor": target.monitor.name,
                    "x": round(target.client.rect.x - target.monitor.rect.x, 2),
                    "y": round(target.client.rect.y - target.monitor.rect.y, 2),
                    "width": round(target.client.rect.width, 2),
                    "height": round(target.client.rect.height, 2),
                }
                for index, target in enumerate(fallback_targets)
            ],
            "searchTargets": [
                {
                    "id": index,
                    "text": target.text,
                    "monitor": target.monitor.name,
                    "x": round(target.rect.x, 2),
                    "y": round(target.rect.y, 2),
                    "width": round(target.rect.width, 2),
                    "height": round(target.rect.height, 2),
                }
                for index, target in enumerate(search_targets)
            ],
            "scrollTargets": [
                {
                    "id": index,
                    "monitor": target.monitor.name,
                    "x": round(target.rect.x, 2),
                    "y": round(target.rect.y, 2),
                    "width": round(target.rect.width, 2),
                    "height": round(target.rect.height, 2),
                }
                for index, target in enumerate(scroll_targets)
            ],
            "paragraphTargets": [
                {
                    "id": index,
                    "monitor": target.monitor.name,
                    "x": round(target.rect.x, 2),
                    "y": round(target.rect.y, 2),
                    "width": round(target.rect.width, 2),
                    "height": round(target.rect.height, 2),
                }
                for index, target in enumerate(paragraph_targets)
            ],
            "meta": {
                "nodesScanned": nodes_scanned,
                "truncated": truncated,
                "windowsConsidered": windows_considered,
                "windowsScanned": windows_scanned,
                "unsupportedWindows": windows_considered - windows_scanned,
            },
        }

    def _pointer_click(
        self, client: Client, monitor: Monitor, x: float, y: float
    ) -> None:
        if not math.isfinite(x) or not math.isfinite(y):
            raise BackendError("invalid-target", "The selected point is invalid")
        global_x = monitor.rect.x + x
        global_y = monitor.rect.y + y
        point = Rect(global_x, global_y, 1, 1)
        if point.intersection(client.rect) is None or point.intersection(monitor.rect) is None:
            raise BackendError("invalid-target", "The selected point is outside the target window")

        if re.fullmatch(r"0x[0-9a-fA-F]+", client.address):
            selector = f"address:{client.address}"
        else:
            selector = f"pid:{client.pid}"

        # QML hides the exclusive overlay before sending this command. Wait
        # for Hyprland to confirm that its surfaces are actually unmapped.
        _wait_for_overlay_release()
        commands = (
            f'hl.dsp.focus({{ window = "{selector}" }})',
            f"hl.dsp.cursor.move({{ x = {round(global_x)}, y = {round(global_y)} }})",
            f'hl.dsp.send_shortcut({{ mods = "", key = "mouse:272", window = "{selector}" }})',
        )
        try:
            for command in commands:
                completed = subprocess.run(
                    ["hyprctl", "dispatch", command],
                    check=True,
                    text=True,
                    capture_output=True,
                    timeout=1.5,
                )
                if completed.stdout.strip() != "ok":
                    raise BackendError(
                        "activation-failed",
                        completed.stdout.strip() or "Hyprland rejected the pointer action",
                    )
        except (FileNotFoundError, subprocess.SubprocessError) as error:
            raise BackendError(
                "activation-failed", f"Could not send the pointer action: {error}"
            ) from error

    def click(self, fallback_id: int, x: float, y: float) -> dict[str, Any]:
        if self.result is None:
            raise BackendError("not-scanned", "No accessibility scan is active")
        if fallback_id < 0 or fallback_id >= len(self.result.fallback_clients):
            raise BackendError("invalid-target", "The selected fallback window no longer exists")

        if self.result.fallback_targets:
            fallback = self.result.fallback_targets[fallback_id]
            client = fallback.client
            monitor = fallback.monitor
        else:
            # Preserve the small direct-construction API used by integrations.
            client = self.result.fallback_clients[fallback_id]
            monitor = self.result.monitor
        self._pointer_click(client, monitor, x, y)
        return {"type": "activated", "id": fallback_id, "ok": True}

    def search_click(self, target_id: int) -> dict[str, Any]:
        if self.result is None:
            raise BackendError("not-scanned", "No accessibility scan is active")
        if target_id < 0 or target_id >= len(self.result.search_targets):
            raise BackendError("invalid-target", "The selected text no longer exists")
        target = self.result.search_targets[target_id]
        x, y = target.rect.center
        self._pointer_click(target.client, target.monitor, x, y)
        return {"type": "activated", "id": target_id, "ok": True}

    def copy_paragraph(self, target_id: int) -> dict[str, Any]:
        if self.result is None:
            raise BackendError("not-scanned", "No accessibility scan is active")
        if target_id < 0 or target_id >= len(self.result.paragraph_targets):
            raise BackendError("invalid-target", "The selected paragraph no longer exists")
        text = self.result.paragraph_targets[target_id].text
        try:
            subprocess.run(
                ["wl-copy"], input=text, check=True, text=True,
                capture_output=True, timeout=2.0,
            )
        except (FileNotFoundError, subprocess.SubprocessError) as error:
            raise BackendError("copy-failed", f"Could not copy the paragraph: {error}") from error
        return {"type": "copied", "id": target_id, "ok": True}

    def scroll(self, target_id: int, direction: int) -> dict[str, Any]:
        if self.result is None:
            raise BackendError("not-scanned", "No accessibility scan is active")
        if target_id < 0 or target_id >= len(self.result.scroll_targets):
            raise BackendError("invalid-target", "The selected scroll region no longer exists")
        if direction not in {-1, 1}:
            raise BackendError("invalid-command", "Scroll direction must be up or down")

        target = self.result.scroll_targets[target_id]
        if target.node is not None:
            try:
                target.node.get_component_iface().grab_focus()
            except Exception:
                pass
        client = target.client
        selector = (
            f"address:{client.address}"
            if re.fullmatch(r"0x[0-9a-fA-F]+", client.address)
            else f"pid:{client.pid}"
        )
        terminal = any(
            name in client.class_name.casefold()
            for name in ("wezterm", "kitty", "alacritty", "foot", "ghostty", "terminal")
        )
        mods = "SHIFT" if terminal else ""
        key = "PAGE_UP" if direction < 0 else "PAGE_DOWN"
        commands = (
            f'hl.dsp.send_key_state({{ mods = "{mods}", key = "{key}", '
            f'state = "down", window = "{selector}" }})',
            f'hl.dsp.send_key_state({{ mods = "{mods}", key = "{key}", '
            f'state = "up", window = "{selector}" }})',
        )
        try:
            for index, command in enumerate(commands):
                completed = subprocess.run(
                    ["hyprctl", "dispatch", command],
                    check=True,
                    text=True,
                    capture_output=True,
                    timeout=1.5,
                )
                if completed.stdout.strip() != "ok":
                    raise BackendError(
                        "scroll-failed",
                        completed.stdout.strip() or "Hyprland rejected the scroll action",
                    )
                if index == 0:
                    time.sleep(0.05)
        except (FileNotFoundError, subprocess.SubprocessError) as error:
            raise BackendError("scroll-failed", f"Could not send the scroll action: {error}") from error
        return {"type": "scrolled", "id": target_id, "direction": direction, "ok": True}

    def activate(self, target_id: int) -> dict[str, Any]:
        if self.result is None:
            raise BackendError("not-scanned", "No accessibility scan is active")
        if target_id < 0 or target_id >= len(self.result.candidates):
            raise BackendError("invalid-target", "The selected target no longer exists")

        candidate = self.result.candidates[target_id]
        try:
            if candidate.action_name == "pointer":
                if candidate.client is None or candidate.monitor is None:
                    raise BackendError(
                        "activation-failed", "The selected tab is missing window geometry"
                    )
                self._pointer_click(
                    candidate.client, candidate.monitor, *candidate.rect.center
                )
                succeeded = True
            elif candidate.action_index >= 0:
                succeeded = bool(candidate.node.get_action_iface().do_action(candidate.action_index))
            else:
                succeeded = bool(candidate.node.get_component_iface().grab_focus())
        except BackendError:
            raise
        except Exception as error:
            raise BackendError("activation-failed", f"The application rejected the action: {error}") from error
        if not succeeded:
            raise BackendError("activation-failed", "The application rejected the action")
        return {"type": "activated", "id": target_id, "ok": True}


def _emit(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, separators=(",", ":")), flush=True)


def _error_payload(error: Exception) -> dict[str, Any]:
    if isinstance(error, BackendError):
        return {"type": "error", "code": error.code, "message": str(error)}
    return {"type": "error", "code": "internal-error", "message": str(error)}


def run(probe: bool = False, mode: str = "hints") -> int:
    session = BackendSession()
    try:
        _emit(session.scan(context_callback=None if probe else _emit, mode=mode))
    except Exception as error:
        _emit(_error_payload(error))
        return 1

    if probe:
        return 0

    for line in sys.stdin:
        try:
            command = json.loads(line)
            command_type = command.get("type")
            if command_type == "activate":
                _emit(session.activate(int(command.get("id", -1))))
            elif command_type == "click":
                _emit(
                    session.click(
                        int(command.get("fallbackId", -1)),
                        float(command.get("x", float("nan"))),
                        float(command.get("y", float("nan"))),
                    )
                )
            elif command_type == "search-click":
                _emit(session.search_click(int(command.get("id", -1))))
            elif command_type == "copy":
                _emit(session.copy_paragraph(int(command.get("id", -1))))
            elif command_type == "scroll":
                _emit(
                    session.scroll(
                        int(command.get("id", -1)),
                        int(command.get("direction", 0)),
                    )
                )
            elif command_type == "quit":
                return 0
            else:
                raise BackendError("invalid-command", "Unknown backend command")
        except Exception as error:
            _emit(_error_payload(error))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="OmaJump accessibility backend")
    parser.add_argument("--probe", action="store_true", help="scan once without accepting actions")
    parser.add_argument(
        "--mode", choices=("hints", "search", "scroll", "paragraph"), default="hints",
        help="choose controls, text search, scroll regions, or paragraph copying",
    )
    args = parser.parse_args(argv)
    return run(probe=args.probe, mode=args.mode)


if __name__ == "__main__":
    raise SystemExit(main())

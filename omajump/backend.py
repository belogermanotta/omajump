"""Live AT-SPI session used by the OmaJump Quickshell overlay."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from dataclasses import dataclass
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


class BackendError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def choose_action(role: str, actions: Iterable[str], states: set[str]) -> tuple[int, str] | None:
    """Select a direct primary action, or focus for an editable control."""

    if role in STRUCTURAL_ROLES:
        return None

    normalized = [str(action or "").replace("-", "").replace("_", "").lower() for action in actions]
    for wanted in PRIMARY_ACTIONS:
        if wanted not in normalized:
            continue
        if wanted in EXPLICIT_ACTIONS or role in DEFAULT_ACTION_ROLES:
            return normalized.index(wanted), wanted

    if role in EDITABLE_ROLES and "focusable" in states:
        return -1, "focus"
    return None


def _json_command(*args: str) -> Any:
    try:
        completed = subprocess.run(args, check=True, text=True, capture_output=True, timeout=1.5)
        return json.loads(completed.stdout)
    except (FileNotFoundError, subprocess.SubprocessError, json.JSONDecodeError) as error:
        raise BackendError("hyprland-unavailable", f"Could not query Hyprland: {error}") from error


def _active_geometry() -> tuple[Client, Monitor]:
    active = _json_command("hyprctl", "-j", "activewindow")
    monitors_json = _json_command("hyprctl", "-j", "monitors")
    if not active or int(active.get("pid", 0)) <= 0:
        raise BackendError("no-active-window", "No application window is focused")

    client = Client(
        pid=int(active["pid"]),
        rect=Rect(
            float(active.get("at", [0, 0])[0]),
            float(active.get("at", [0, 0])[1]),
            float(active.get("size", [0, 0])[0]),
            float(active.get("size", [0, 0])[1]),
        ),
        monitor_id=int(active.get("monitor", -1)),
    )
    if client.rect.width <= 1 or client.rect.height <= 1:
        raise BackendError("no-active-window", "The focused window has no visible area")

    monitor_data = next(
        (item for item in monitors_json if int(item.get("id", -2)) == client.monitor_id),
        None,
    )
    if monitor_data is None:
        monitor_data = next((item for item in monitors_json if item.get("focused")), None)
    if monitor_data is None:
        raise BackendError("no-monitor", "Could not identify the focused monitor")

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
    return client, monitor


def _parent_pid(pid: int) -> int:
    try:
        # Field two may contain spaces and parentheses; split after its final ')'.
        with open(f"/proc/{pid}/stat", encoding="utf-8") as stat_file:
            tail = stat_file.read().rsplit(")", 1)[1].split()
        return int(tail[1])
    except (OSError, ValueError, IndexError):
        return 0


def _ancestry(pid: int, limit: int = 24) -> list[int]:
    result: list[int] = []
    current = pid
    while current > 1 and current not in result and len(result) < limit:
        result.append(current)
        current = _parent_pid(current)
    return result


def process_distance(left: int, right: int) -> int | None:
    if left <= 0 or right <= 0:
        return None
    left_chain = _ancestry(left)
    right_chain = _ancestry(right)
    right_positions = {pid: index for index, pid in enumerate(right_chain)}
    distances = [index + right_positions[pid] for index, pid in enumerate(left_chain) if pid in right_positions]
    return min(distances) if distances else None


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


def _children(node: Any, limit: int = 2000) -> list[Any]:
    children: list[Any] = []
    try:
        count = min(int(node.get_child_count()), limit)
        for index in range(count):
            try:
                child = node.get_child_at_index(index)
                if child is not None:
                    children.append(child)
            except Exception:
                continue
    except Exception:
        pass
    return children


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


def _find_window(desktop: Any, client: Client, monitor: Monitor, Atspi: Any) -> tuple[Any, Rect]:
    choices: list[tuple[float, Any, Rect]] = []
    focused_choices: list[tuple[float, Any, Rect]] = []
    for application in _children(desktop, 200):
        try:
            app_pid = int(application.get_process_id())
        except Exception:
            app_pid = 0
        frames = _top_level_frames(application, Atspi)
        if not frames:
            continue
        frame, rect = min(frames, key=lambda pair: frame_match_score(pair[1], client, monitor))
        geometry_score = frame_match_score(rect, client, monitor)
        distance = process_distance(client.pid, app_pid)
        if distance is not None:
            choices.append((distance * 5 + geometry_score, frame, rect))
        elif geometry_score < 0.25 and _has_focused_descendant(application, Atspi):
            focused_choices.append((geometry_score, frame, rect))

    available = choices or focused_choices
    if not available:
        raise BackendError(
            "accessibility-window-not-found",
            "The focused application did not expose a matching accessibility window",
        )
    _, frame, rect = min(available, key=lambda item: item[0])
    return frame, rect


def _action_names(node: Any) -> list[str]:
    try:
        interface = node.get_action_iface()
        return [str(interface.get_action_name(index) or "") for index in range(interface.get_n_actions())]
    except Exception:
        return []


@dataclass(slots=True)
class ScanResult:
    monitor: Monitor
    candidates: list[Candidate]
    nodes_scanned: int
    truncated: bool


class BackendSession:
    def __init__(self, max_nodes: int = 6000, max_targets: int = 250) -> None:
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
    ) -> dict[str, Any]:
        Atspi = self._load_atspi()
        client, monitor = _active_geometry()
        if context_callback is not None:
            context_callback({"type": "context", "monitor": monitor.name})
        desktop = Atspi.get_desktop(0)
        if desktop is None:
            raise BackendError("atspi-unavailable", "The AT-SPI desktop could not be opened")

        frame, frame_rect = _find_window(desktop, client, monitor, Atspi)
        mapper = CoordinateMapper.infer(client, monitor, frame_rect)
        candidates: list[Candidate] = []
        stack: list[tuple[Any, int, float]] = [(frame, 0, mapper.base_scale)]
        nodes_scanned = 0

        while stack and nodes_scanned < self.max_nodes:
            node, depth, inherited_scale = stack.pop()
            nodes_scanned += 1
            role = _role(node)
            raw_rect = _rect_for(node, Atspi)
            node_scale = (
                mapper.derive_scale(raw_rect, inherited_scale, role)
                if raw_rect is not None
                else inherited_scale
            )
            states = _node_states(node, Atspi)
            selected = choose_action(role, _action_names(node), states)

            if (
                raw_rect is not None
                and selected is not None
                and {"enabled", "showing", "visible"}.issubset(states)
            ):
                visible = mapper.visible_rect(raw_rect, node_scale)
                covers_window = (
                    visible is not None
                    and visible.area / max(1.0, client.rect.area) >= 0.65
                    and role not in DEFAULT_ACTION_ROLES
                    and role not in EDITABLE_ROLES
                )
                if (
                    visible is not None
                    and not covers_window
                    and visible.width >= 2
                    and visible.height >= 2
                ):
                    action_index, action_name = selected
                    candidates.append(
                        Candidate(
                            node=node,
                            rect=mapper.overlay_rect(visible),
                            role=role,
                            action_index=action_index,
                            action_name=action_name,
                        )
                    )

            if depth < 48:
                stack.extend(
                    (child, depth + 1, node_scale) for child in reversed(_children(node))
                )

        truncated = bool(stack)
        candidates = deduplicate_candidates(candidates)[: self.max_targets]
        truncated = truncated or len(candidates) >= self.max_targets
        self.result = ScanResult(monitor, candidates, nodes_scanned, truncated)
        labels = generate_labels(len(candidates))

        return {
            "type": "targets",
            "monitor": monitor.name,
            "targets": [
                {
                    "id": index,
                    "label": labels[index],
                    "x": round(candidate.rect.x, 2),
                    "y": round(candidate.rect.y, 2),
                    "width": round(candidate.rect.width, 2),
                    "height": round(candidate.rect.height, 2),
                }
                for index, candidate in enumerate(candidates)
            ],
            "meta": {
                "nodesScanned": nodes_scanned,
                "truncated": truncated,
                "scale": monitor.scale,
            },
        }

    def activate(self, target_id: int) -> dict[str, Any]:
        if self.result is None:
            raise BackendError("not-scanned", "No accessibility scan is active")
        if target_id < 0 or target_id >= len(self.result.candidates):
            raise BackendError("invalid-target", "The selected target no longer exists")

        candidate = self.result.candidates[target_id]
        try:
            if candidate.action_index >= 0:
                succeeded = bool(candidate.node.get_action_iface().do_action(candidate.action_index))
            else:
                succeeded = bool(candidate.node.get_component_iface().grab_focus())
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


def run(probe: bool = False) -> int:
    session = BackendSession()
    try:
        _emit(session.scan(context_callback=None if probe else _emit))
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
    args = parser.parse_args(argv)
    return run(probe=args.probe)


if __name__ == "__main__":
    raise SystemExit(main())

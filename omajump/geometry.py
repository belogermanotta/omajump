"""Coordinate mapping between AT-SPI, Hyprland, and a monitor-local overlay."""

from __future__ import annotations

from dataclasses import dataclass

from .model import Candidate, Client, Monitor, Rect, almost_equal


@dataclass(frozen=True, slots=True)
class CoordinateMapper:
    """Map AT-SPI extents into Hyprland's global logical coordinate space."""

    client: Client
    monitor: Monitor
    frame: Rect
    mode: str
    base_scale: float

    @classmethod
    def infer(cls, client: Client, monitor: Monitor, frame: Rect) -> "CoordinateMapper":
        scale = max(0.1, monitor.scale)
        size_is_logical = almost_equal(frame.width, client.rect.width) and almost_equal(
            frame.height, client.rect.height
        )
        size_is_scaled = almost_equal(frame.width / scale, client.rect.width) and almost_equal(
            frame.height / scale, client.rect.height
        )
        origin_is_global = almost_equal(frame.x, client.rect.x, 0.03) and almost_equal(
            frame.y, client.rect.y, 0.03
        )

        if origin_is_global and size_is_logical:
            return cls(client, monitor, frame, "global", 1.0)
        if size_is_scaled:
            return cls(client, monitor, frame, "local", scale)
        return cls(client, monitor, frame, "local", 1.0)

    def derive_scale(self, raw: Rect, inherited: float, role: str = "") -> float:
        """Detect device-pixel subtrees embedded in an otherwise logical tree.

        Chromium/Electron commonly exposes a logical top-level frame followed
        by a web document whose width and height are multiplied by the output
        scale. Once detected, descendants inherit that scale.
        """

        output_scale = max(1.0, self.monitor.scale)
        if output_scale <= 1.01:
            return inherited

        scaled_width = almost_equal(raw.width / output_scale, self.client.rect.width, 0.12)
        scaled_height = almost_equal(raw.height / output_scale, self.client.rect.height, 0.12)
        container_role = role in {
            "document web",
            "document frame",
            "panel",
            "section",
            "application",
            "frame",
        }
        substantially_larger = (
            raw.width > self.client.rect.width * 1.35
            or raw.height > self.client.rect.height * 1.35
        )
        if container_role and scaled_width and scaled_height and substantially_larger:
            return output_scale
        return inherited

    def to_global(self, raw: Rect, node_scale: float | None = None) -> Rect:
        scale = max(0.1, node_scale or self.base_scale)
        if self.mode == "global" and almost_equal(scale, 1.0, 0.01):
            return raw

        # Wayland accessibility coordinates are commonly window-local. Use the
        # active Hyprland client as the trusted global origin.
        return Rect(
            self.client.rect.x + raw.x / scale,
            self.client.rect.y + raw.y / scale,
            raw.width / scale,
            raw.height / scale,
        )

    def visible_rect(self, raw: Rect, node_scale: float | None = None) -> Rect | None:
        mapped = self.to_global(raw, node_scale)
        inside_window = mapped.intersection(self.client.rect)
        if inside_window is None:
            return None
        return inside_window.intersection(self.monitor.rect)

    def overlay_rect(self, global_rect: Rect) -> Rect:
        return Rect(
            global_rect.x - self.monitor.rect.x,
            global_rect.y - self.monitor.rect.y,
            global_rect.width,
            global_rect.height,
        )


def frame_match_score(frame: Rect, client: Client, monitor: Monitor) -> float:
    """Lower is better; accept logical or output-scaled top-level frames."""

    scale = max(0.1, monitor.scale)

    def size_error(rect: Rect, factor: float) -> float:
        width = rect.width / factor
        height = rect.height / factor
        return abs(width - client.rect.width) / max(1.0, client.rect.width) + abs(
            height - client.rect.height
        ) / max(1.0, client.rect.height)

    result = min(size_error(frame, 1.0), size_error(frame, scale))
    if frame.width <= 1 or frame.height <= 1:
        result += 10
    return result


def deduplicate_candidates(candidates: list[Candidate]) -> list[Candidate]:
    """Remove nested duplicates while preferring semantic and smaller targets."""

    semantic_roles = {
        "button",
        "check box",
        "combo box",
        "entry",
        "link",
        "menu item",
        "page tab",
        "password text",
        "radio button",
        "slider",
        "spin button",
        "text",
        "toggle button",
    }

    ordered = sorted(
        candidates,
        key=lambda item: (
            item.role not in semantic_roles,
            item.rect.area,
            item.rect.y,
            item.rect.x,
        ),
    )
    kept: list[Candidate] = []
    for candidate in ordered:
        duplicate = False
        for existing in kept:
            same_action = candidate.action_name == existing.action_name
            same_center = (
                abs(candidate.rect.center[0] - existing.rect.center[0]) <= 2
                and abs(candidate.rect.center[1] - existing.rect.center[1]) <= 2
            )
            if candidate.rect.iou(existing.rect) >= 0.9 or (same_action and same_center):
                duplicate = True
                break
        if not duplicate:
            kept.append(candidate)

    return sorted(kept, key=lambda item: (round(item.rect.y / 8), item.rect.x, item.rect.y))

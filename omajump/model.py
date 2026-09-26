"""Small dependency-free geometry models used by the live backend and tests."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Rect:
    x: float
    y: float
    width: float
    height: float

    @property
    def right(self) -> float:
        return self.x + self.width

    @property
    def bottom(self) -> float:
        return self.y + self.height

    @property
    def area(self) -> float:
        return max(0.0, self.width) * max(0.0, self.height)

    @property
    def center(self) -> tuple[float, float]:
        return (self.x + self.width / 2, self.y + self.height / 2)

    def intersection(self, other: "Rect") -> "Rect | None":
        left = max(self.x, other.x)
        top = max(self.y, other.y)
        right = min(self.right, other.right)
        bottom = min(self.bottom, other.bottom)
        if right <= left or bottom <= top:
            return None
        return Rect(left, top, right - left, bottom - top)

    def iou(self, other: "Rect") -> float:
        overlap = self.intersection(other)
        if overlap is None:
            return 0.0
        union = self.area + other.area - overlap.area
        return overlap.area / union if union > 0 else 0.0


@dataclass(frozen=True, slots=True)
class Monitor:
    name: str
    rect: Rect
    scale: float = 1.0
    monitor_id: int = -1


@dataclass(frozen=True, slots=True)
class Client:
    pid: int
    rect: Rect
    monitor_id: int


@dataclass(slots=True)
class Candidate:
    node: object
    rect: Rect
    role: str
    action_index: int
    action_name: str


def almost_equal(left: float, right: float, tolerance: float = 0.08) -> bool:
    """Compare dimensions with a relative tolerance and a one-pixel floor."""

    return abs(left - right) <= max(1.0, abs(right) * tolerance)

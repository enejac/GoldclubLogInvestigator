"""Live Bug Detector session event model (roulette-first)."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class EventCategory(str, Enum):
    """UI / markdown color buckets."""

    FREE_FLOW = "free_flow"  # idle heartbeats, age GET, poll 80/81
    HUMAN = "human"  # touch, layout remount, Collect, chip/menu puts that follow touch
    HARDWARE = "hardware"  # ticket / bill / Dallas key
    SAS = "sas"  # AFT/WAT/cashless / non-poll SAS
    MIDDLEWARE = "middleware"  # :8090 HTTP sniff or Sending put/GET with action detail
    CRITICAL = "critical"  # NRE, process exit, unhandled exception
    OTHER = "other"


# Dark-theme friendly HTML colors for live stream + reports
CATEGORY_COLORS: dict[EventCategory, str] = {
    EventCategory.FREE_FLOW: "#858585",
    EventCategory.HUMAN: "#4daaf9",
    EventCategory.HARDWARE: "#e6a817",
    EventCategory.SAS: "#c586c0",
    EventCategory.MIDDLEWARE: "#4ec9b0",
    EventCategory.CRITICAL: "#f44747",
    EventCategory.OTHER: "#d4d4d4",
}

CATEGORY_LABELS: dict[EventCategory, str] = {
    EventCategory.FREE_FLOW: "machine",
    EventCategory.HUMAN: "user",
    EventCategory.HARDWARE: "hardware",
    EventCategory.SAS: "SAS/cashless",
    EventCategory.MIDDLEWARE: "middleware",
    EventCategory.CRITICAL: "critical",
    EventCategory.OTHER: "other",
}


@dataclass
class SessionEvent:
    ts: str
    category: EventCategory
    source: str  # godot1 | ruleta | aurum | sasmsgr | sniff8090 | sniff30300 | …
    summary: str
    raw: str = ""
    commands: list[str] = field(default_factory=list)
    critical: bool = False

    def color(self) -> str:
        return CATEGORY_COLORS.get(self.category, CATEGORY_COLORS[EventCategory.OTHER])

    def label(self) -> str:
        return CATEGORY_LABELS.get(self.category, self.category.value)

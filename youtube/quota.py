"""Tracks estimated YouTube Data API quota usage for the current day so large batches can
warn/pause before hitting the account's daily cap instead of failing mid-batch."""
from __future__ import annotations

import threading
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from data import safe_io

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
QUOTA_PATH = DATA_DIR / "quota_usage.json"

COST_VIDEOS_LIST = 1
COST_PLAYLIST_ITEMS_LIST = 1
COST_SEARCH_LIST = 100
COST_VIDEOS_UPDATE = 50
COST_VIDEOS_INSERT = 1600
COST_PLAYLISTS_LIST = 1
COST_PLAYLIST_ITEMS_INSERT = 50


_record_lock = threading.Lock()


@dataclass
class QuotaTracker:
    day: str
    units_used: int

    @classmethod
    def load(cls) -> "QuotaTracker":
        today = date.today().isoformat()
        raw = safe_io.read_json_object(QUOTA_PATH, "quota_usage.json")
        if raw is not None and raw.get("day") == today:
            try:
                return cls(day=today, units_used=max(0, int(raw.get("units_used", 0))))
            except (TypeError, ValueError):
                pass  # an unusable count just starts today's tally from zero
        return cls(day=today, units_used=0)

    def save(self) -> None:
        try:
            safe_io.atomic_write_json(QUOTA_PATH, {"day": self.day, "units_used": self.units_used})
        except OSError:
            pass  # a usage counter is never worth failing an API call over

    def record(self, units: int) -> None:
        with _record_lock:  # called from every API thread
            today = date.today().isoformat()
            if today != self.day:
                self.day = today
                self.units_used = 0
            self.units_used += units
            self.save()

    def remaining(self, daily_budget: int) -> int:
        today = date.today().isoformat()
        if today != self.day:
            return daily_budget
        return max(0, daily_budget - self.units_used)

    def would_exceed(self, additional_units: int, daily_budget: int) -> bool:
        return additional_units > self.remaining(daily_budget)

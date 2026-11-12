"""The set of knobs that drive scheduling/engine.py's schedule computation."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, time


@dataclass
class ScheduleRule:
    start_date: date
    videos_per_day: int = 4
    window_start: time = time(6, 0)
    window_end: time = time(15, 0)
    # When True, a day that can't fit its target count (because of collisions with
    # already-scheduled videos) silently rolls the unfit videos to the next day.
    # When False, that situation is surfaced as a DayConflict for the user to resolve.
    auto_optimise: bool = True
    # Set by the conflict dialog ("Use earlier available times" / "Continue past 3:00 PM")
    # in response to a DayConflict; widens the same-day search range used to resolve
    # collisions with already-scheduled videos, down to 00:00 / up to 23:59.
    allow_before_window: bool = False
    allow_after_window: bool = False

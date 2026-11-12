"""The one daily-slot-count lookup shared by every scheduling engine in the app
(Increase Daily Slots, Auto Schedule's insertion, and One Upload Page's placement): how
many videos land on a given day decides which literal times they get, not the other way
around. Adding a video to an already-full-ish day changes everyone else's time on that
day too -- e.g. a 2-video day defaults to 08:00/10:00 (evenly spread across the
publishing window), but adding a 3rd video re-anchors the whole day to a 3-way spread
instead.

SET-0012.1 (Upload Time Presets): the actual times now come from
data/auto_schedule_config.py's AutoScheduleConfig.slot_presets -- a user-editable,
auto-saved time list per possible daily count (1-6), independent of each other, falling
back to times evenly distributed across the configured publishing window when no preset
has been saved yet for that count. This module is now just a thin, backward-compatible
delegate to that (get_daily_slots/get_daily_slot_times/MAX_DAILY_SLOTS all still work
exactly as before for next_slot.py and calendar_aggregator.py's sake) -- it replaces the
old hardcoded 06:00/08:00/10:00/12:00 table, which topped out at 4 and wasn't
configurable at all.
"""
from __future__ import annotations

import datetime as dt

from data.auto_schedule_config import MAX_SLOT_COUNT, AutoScheduleConfig

MAX_DAILY_SLOTS = MAX_SLOT_COUNT


def get_daily_slot_times(count: int) -> list[dt.time]:
    """Returns the list of times for a given daily count (max MAX_DAILY_SLOTS), read
    fresh from the currently-saved presets every call -- see
    AutoScheduleConfig.get_slot_times()."""
    return AutoScheduleConfig.load().get_slot_times(count)


def get_daily_slots(count: int) -> list[str]:
    """Same as get_daily_slot_times(), as HH:MM strings."""
    return [t.strftime("%H:%M") for t in get_daily_slot_times(count)]

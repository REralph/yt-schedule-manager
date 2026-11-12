"""Where newly-uploaded videos land in One Upload Page, at Auto Schedule's daily rate (1-6).

There is only ever automatic scheduling here -- no manual date override. The critical rule:
this is an append operation, never a gap search. It starts at the FURTHEST date that
already has a video scheduled today or later (not "tomorrow", not the first day with spare
capacity encountered while scanning forward) -- tops that date up to the daily rate if it
still has room, and only advances to the next calendar day once it's full. A gap earlier in
the schedule (e.g. left behind by days that have already published and dropped out of the
future list) is never backfilled; it only ever continues from the tail. Only when nothing
at all is scheduled yet does it fall back to starting at tomorrow.

Uses the shared slot lookup (scheduling/slot_rules.py), which reads whichever per-count time
preset is currently saved (SET-0012.1 -- Upload Time Presets). A day's times are the preset for
the configured videos-per-day RATE, filled in order -- at 2 per day the first upload of a day is
06:00 and the second 10:00 (or whatever the 2-slot preset says) -- and a new upload always
lands AFTER every video already on its day, so videos publish in the order they were uploaded.
Never assigns a video to an already-occupied exact minute, so this can't collide with what's
already there.
"""
from __future__ import annotations

import datetime as dt

from scheduling.slot_rules import MAX_DAILY_SLOTS, get_daily_slot_times
from youtube.models import Video


def _minute_key(t: dt.datetime) -> tuple:
    return (t.year, t.month, t.day, t.hour, t.minute)


def _occupied_from_today(existing: list[Video], today: dt.date) -> dict[dt.date, set]:
    occupied_by_day: dict[dt.date, set] = {}
    for v in existing:
        if not v.publish_at:
            continue
        local = v.publish_at.astimezone()
        if local.date() < today:
            continue
        occupied_by_day.setdefault(local.date(), set()).add(_minute_key(local))
    return occupied_by_day


def find_upload_slots_at_rate(existing: list[Video], count: int, videos_per_day: int) -> list[dt.datetime]:
    """Returns `count` upload datetimes, continuing the schedule forward from wherever it
    actually currently ends (the furthest today-or-later occupied date), topping that date
    up to videos_per_day before ever moving to the next day. Falls back to starting
    tomorrow only when there's nothing scheduled today or later at all."""
    if count <= 0:
        return []
    videos_per_day = max(1, min(MAX_DAILY_SLOTS, videos_per_day))

    today = dt.date.today()
    occupied_by_day = _occupied_from_today(existing, today)
    day = max(occupied_by_day.keys()) if occupied_by_day else today + dt.timedelta(days=1)

    placements: list[dt.datetime] = []
    while len(placements) < count:
        taken = occupied_by_day.get(day, set())
        capacity = videos_per_day - len(taken)
        if capacity <= 0:
            day += dt.timedelta(days=1)
            continue

        take = min(capacity, count - len(placements))
        # The day's times come from the table for the configured daily rate, filled in
        # order: with 2 per day the first upload gets the 1st time (06:00), the second the
        # 2nd (10:00). (This used to look up the table for "how many videos the day will
        # have after this upload" instead -- so the first upload on an empty day used the
        # 1-slot table, whose single time is the middle of the window (10:00), and the
        # second upload then took the only time left, 06:00: every day published in the
        # opposite order to the order the videos were uploaded in.)
        day_slots = [dt.datetime.combine(day, t).astimezone() for t in get_daily_slot_times(videos_per_day)]
        # Only times AFTER everything already on this day: a new upload must never publish
        # before a video that was uploaded earlier. If nothing later is left (e.g. a day
        # whose only video already sits at the last time), the rest rolls to the next day.
        latest_taken = max(taken) if taken else None
        free_slots = [
            s for s in day_slots
            if _minute_key(s) not in taken and (latest_taken is None or _minute_key(s) > latest_taken)
        ]
        placements.extend(free_slots[:take])
        # `day` always advances once processed -- it is never revisited, so partial
        # occupancy from this pass doesn't need to be written back anywhere.
        day += dt.timedelta(days=1)

    return placements[:count]

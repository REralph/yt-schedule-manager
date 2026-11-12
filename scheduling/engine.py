"""Computes new publish times for a selected batch of videos: evenly spaced across a
daily publishing window, rolling extra videos to subsequent days, and never silently
colliding with videos that are already scheduled (and not part of the selection).

See ScheduleRule (scheduling/rules.py) for the knobs. Calling build_schedule() is always
safe/side-effect-free -- it never touches YouTube, it just computes a plan.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from typing import Optional

from scheduling.rules import ScheduleRule
from youtube.models import Video


@dataclass
class Assignment:
    video_id: str
    title: str
    old_publish_at: Optional[dt.datetime]
    new_publish_at: dt.datetime
    out_of_window: bool = False


@dataclass
class DayConflict:
    day: dt.date
    target_count: int
    reason: str


@dataclass
class ScheduleResult:
    assignments: list[Assignment] = field(default_factory=list)
    conflicts: list[DayConflict] = field(default_factory=list)

    def merge(self, other: "ScheduleResult") -> "ScheduleResult":
        """Combines two results, letting `other`'s assignment win for any video present
        in both (used when a later pass, e.g. gap-clearing, refines an earlier one)."""
        by_id = {a.video_id: a for a in self.assignments}
        for a in other.assignments:
            by_id[a.video_id] = a
        return ScheduleResult(assignments=list(by_id.values()), conflicts=self.conflicts + other.conflicts)

    @property
    def has_conflicts(self) -> bool:
        return len(self.conflicts) > 0


def _minute_key(t: dt.datetime) -> tuple:
    return (t.year, t.month, t.day, t.hour, t.minute)


def _fmt_time(t: dt.time) -> str:
    return t.strftime("%I:%M %p").lstrip("0")


def ideal_times(day: dt.date, count: int, window_start: dt.time, window_end: dt.time) -> list[dt.datetime]:
    """`count` evenly-spaced datetimes across [window_start, window_end] on `day` -- a
    single video lands at window_start, two land at the two ends, three+ fill in between.
    Shared with Auto Schedule's insertion cascade (scheduling/auto_schedule.py), which
    needs the exact same even-spacing formula for its own publishing window."""
    if count <= 0:
        return []
    start_dt = dt.datetime.combine(day, window_start).astimezone()
    if count == 1:
        return [start_dt]
    end_dt = dt.datetime.combine(day, window_end).astimezone()
    span = (end_dt - start_dt).total_seconds()
    step = span / (count - 1)
    return [start_dt + dt.timedelta(seconds=step * i) for i in range(count)]


def _find_free_slot(
    ideal: dt.datetime, taken: set, lower_bound: dt.datetime, upper_bound: dt.datetime
) -> Optional[dt.datetime]:
    """Nearest free minute to `ideal`, searching outward, clipped to [lower_bound, upper_bound]."""
    if _minute_key(ideal) not in taken:
        return ideal
    max_offset = int((upper_bound - lower_bound).total_seconds() // 60) + 1
    for offset in range(1, max_offset + 1):
        for direction in (1, -1):
            candidate = ideal + dt.timedelta(minutes=offset * direction)
            if candidate < lower_bound or candidate > upper_bound:
                continue
            if _minute_key(candidate) not in taken:
                return candidate
    return None


def _try_fit_day(
    day: dt.date,
    target_count: int,
    occupied: list[dt.datetime],
    window_start: dt.time,
    window_end: dt.time,
    search_lower: dt.datetime,
    search_upper: dt.datetime,
) -> Optional[list[dt.datetime]]:
    """Attempts to place target_count videos on `day`, evenly spaced across
    window_start..window_end, nudging any collision with `occupied` to the nearest free
    minute within [search_lower, search_upper]. Returns None if it can't fully fit."""
    if target_count <= 0:
        return []
    ideal = ideal_times(day, target_count, window_start, window_end)
    taken = {_minute_key(t) for t in occupied}
    assigned: list[dt.datetime] = []
    for slot in ideal:
        found = _find_free_slot(slot, taken, search_lower, search_upper)
        if found is None:
            return None
        taken.add(_minute_key(found))
        assigned.append(found)
    return assigned


def _occupied_times_by_day(
    all_scheduled: list[Video], excluded_ids: set[str]
) -> dict[dt.date, list[dt.datetime]]:
    occupied: dict[dt.date, list[dt.datetime]] = {}
    for v in all_scheduled:
        if v.video_id in excluded_ids or not v.publish_at:
            continue
        local = v.publish_at.astimezone()
        occupied.setdefault(local.date(), []).append(local)
    return occupied


def build_schedule(selected: list[Video], all_scheduled: list[Video], rule: ScheduleRule) -> ScheduleResult:
    result = ScheduleResult()
    if not selected:
        return result

    selected_ids = {v.video_id for v in selected}
    ordered = sorted(selected, key=lambda v: v.publish_at or dt.datetime.max.replace(tzinfo=dt.timezone.utc))
    occupied_by_day = _occupied_times_by_day(all_scheduled, selected_ids)

    day = rule.start_date
    index = 0
    total = len(ordered)

    while index < total:
        remaining = total - index
        target_count = min(remaining, rule.videos_per_day)
        existing = occupied_by_day.get(day, [])

        lower = dt.datetime.combine(
            day, dt.time(0, 0) if rule.allow_before_window else rule.window_start
        ).astimezone()
        upper = dt.datetime.combine(
            day, dt.time(23, 59) if rule.allow_after_window else rule.window_end
        ).astimezone()

        assigned = _try_fit_day(day, target_count, existing, rule.window_start, rule.window_end, lower, upper)

        if assigned is None:
            if not rule.auto_optimise:
                result.conflicts.append(
                    DayConflict(
                        day=day,
                        target_count=target_count,
                        reason=(
                            f"There are not enough available publishing slots between "
                            f"{_fmt_time(rule.window_start)} and {_fmt_time(rule.window_end)} "
                            f"to fit all selected videos on {day.strftime('%d %b %Y')}."
                        ),
                    )
                )
            # Shrink the target for this day until it fits; whatever's left rolls to
            # the next day (the default/silent outcome either way -- auto_optimise only
            # controls whether we warned about it first).
            fit_count = target_count
            assigned = None
            while fit_count > 0:
                fit_count -= 1
                if fit_count == 0:
                    assigned = []
                    break
                assigned = _try_fit_day(day, fit_count, existing, rule.window_start, rule.window_end, lower, upper)
                if assigned is not None:
                    break
            if assigned is None:
                assigned = []

        for video, new_time in zip(ordered[index : index + len(assigned)], assigned):
            in_window = rule.window_start <= new_time.time() <= rule.window_end
            result.assignments.append(
                Assignment(
                    video_id=video.video_id,
                    title=video.title,
                    old_publish_at=video.publish_at,
                    new_publish_at=new_time,
                    out_of_window=not in_window,
                )
            )
            occupied_by_day.setdefault(day, []).append(new_time)

        index += len(assigned)
        day = day + dt.timedelta(days=1)

    return result

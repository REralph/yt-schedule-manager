"""INCREASE DAILY SLOTS (replaces the old "Push" concept -- see the "PUSH PAGE -- Change
to INCREASE DAILY SLOTS" spec): temporarily raises how many Shorts publish per day within
a specific future date range, pulling the existing queue forward to fill the extra
capacity, then resuming the app's normal (already-configured) daily rate immediately
after the range ends. Unlike the old Push feature, this never touches a Short scheduled
before the start date, and never permanently changes the app's normal daily rate --
config.normal_videos_per_day itself is never written here.

Order is structural, never just checked after the fact: the queue (every Short currently
scheduled on/after start_date, sorted by its current publish_at) is consumed strictly in
that order, one day's worth at a time -- the operation only ever changes WHEN a Short
publishes, never their relative sequence. scheduling/ordering.py's
validate_order_preserved() still runs as the final safety net right before anything
reaches YouTube (see ui/schedule_apply.py), exactly like every other scheduling feature
in this app.

Pure and side-effect-free -- no YouTube calls happen in this module.
"""
from __future__ import annotations

import dataclasses
import datetime as dt
from typing import Optional

from data.auto_schedule_config import MAX_SLOT_COUNT, MIN_SLOT_COUNT, AutoScheduleConfig
from scheduling.engine import Assignment, ScheduleResult
from youtube.models import Video

# Matches this app's existing "how soon is too soon" precedent (the old Push feature's
# own SAFETY_BUFFER, in what is now this module) -- if start_date is today, any slot
# time within this buffer of "now" is treated as already gone rather than risking a
# publish time that's effectively in the past by the time it reaches YouTube.
SAFETY_BUFFER = dt.timedelta(minutes=15)


@dataclasses.dataclass
class PreviewRow:
    video_id: str
    title: str
    old_publish_at: Optional[dt.datetime]
    new_publish_at: dt.datetime
    changed: bool


@dataclasses.dataclass
class IncreaseSlotsResult:
    result: ScheduleResult  # only the Short(s) whose time actually changes -- what gets sent to YouTube
    preview_rows: list[PreviewRow]  # every affected Short, changed or not -- what the page previews
    queue_size: int
    changed_count: int
    normal_rate: int  # the rate that resumes for every day after end_date
    queue_end_before: Optional[dt.date] = None  # the day the LAST Short in the queue publishes now...
    queue_end_after: Optional[dt.date] = None  # ...and the day it would publish after this operation


def _minute_key(t: dt.datetime) -> tuple:
    return (t.year, t.month, t.day, t.hour, t.minute)


def validate_request(start_date: dt.date, end_date: dt.date, slots_per_day: int, today: dt.date) -> Optional[str]:
    """Pre-flight checks before even computing a preview. Returns an error message, or
    None if the request is sound enough to compute."""
    if start_date < today:
        return "Start date can't be in the past."
    if end_date < start_date:
        return "End date must be on or after the start date."
    if not (MIN_SLOT_COUNT <= slots_per_day <= MAX_SLOT_COUNT):
        return f"Slots per day must be between {MIN_SLOT_COUNT} and {MAX_SLOT_COUNT}."
    return None


def compute_increase_slots_schedule(
    videos: list[Video],
    start_date: dt.date,
    end_date: dt.date,
    slots_per_day: int,
    config: AutoScheduleConfig,
    now: dt.datetime,
) -> IncreaseSlotsResult:
    """Rebuilds every Short currently scheduled on/after start_date, day by day starting
    at start_date: `slots_per_day`'s saved preset (SET-0012.1) while day <= end_date,
    then config.normal_videos_per_day's own preset for every day after -- filling each
    day to capacity before advancing (the same top-up-then-advance principle as
    scheduling/next_slot.py), and never touching a Short scheduled before start_date.
    Every video in the queue keeps its original relative position; only its date/time
    changes. A Short whose computed time doesn't actually differ from its current one is
    still included in preview_rows (so the preview can show it as unchanged) but never in
    result.assignments (so nothing pointless is sent to YouTube)."""
    # Only Shorts move. A longer video (a montage...) keeps the exact slot it was scheduled in, and
    # that minute is simply skipped when the Shorts around it are laid out.
    queue = sorted(
        (v for v in videos if v.publish_at and v.is_short and v.publish_at.astimezone().date() >= start_date),
        key=lambda v: v.publish_at,
    )
    fixed_minutes = {
        _minute_key(v.publish_at.astimezone())
        for v in videos if v.publish_at and not v.is_short and v.publish_at.astimezone().date() >= start_date
    }

    today = now.astimezone().date()
    preview_rows: list[PreviewRow] = []
    assignments: list[Assignment] = []

    day = start_date
    index = 0
    total = len(queue)
    while index < total:
        rate = slots_per_day if day <= end_date else config.normal_videos_per_day
        day_datetimes = [dt.datetime.combine(day, t).astimezone() for t in config.get_slot_times(rate)]
        if day == today:
            day_datetimes = [d for d in day_datetimes if d > now + SAFETY_BUFFER]
        if fixed_minutes:
            day_datetimes = [d for d in day_datetimes if _minute_key(d) not in fixed_minutes]

        day_batch = queue[index : index + len(day_datetimes)]
        for video, new_dt in zip(day_batch, day_datetimes):
            changed = video.publish_at.astimezone() != new_dt
            preview_rows.append(PreviewRow(
                video_id=video.video_id, title=video.title,
                old_publish_at=video.publish_at, new_publish_at=new_dt, changed=changed,
            ))
            if changed:
                assignments.append(Assignment(
                    video_id=video.video_id, title=video.title,
                    old_publish_at=video.publish_at, new_publish_at=new_dt,
                ))
        index += len(day_batch)
        day += dt.timedelta(days=1)  # always advances, even on a zero-capacity day (e.g. today with every slot already past) -- guarantees this loop terminates

    new_last = max((r.new_publish_at for r in preview_rows), default=None)
    return IncreaseSlotsResult(
        result=ScheduleResult(assignments=assignments),
        preview_rows=preview_rows,
        queue_size=total,
        changed_count=len(assignments),
        normal_rate=config.normal_videos_per_day,
        queue_end_before=queue[-1].publish_at.astimezone().date() if queue else None,
        queue_end_after=new_last.astimezone().date() if new_last else None,
    )

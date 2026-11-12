"""DAILY UPLOAD FREQUENCY: change how many Shorts publish per day. Only DATES AND TIMES change -- never the order.

THE RULE THIS MODULE EXISTS TO KEEP
The queue is the list of eligible Shorts in the order of their permanent sequence numbers (data/queue_order.py). New
dates and times are produced by WALKING THAT LIST: the first video gets the first valid slot, the second the next, and
so on, N slots per day. The queue is never sorted by its current scheduled time and that time is never treated as
authoritative -- so a schedule that is already out of order cannot pass its disorder on, and changing the frequency
cannot move a video relative to any other.

ELIGIBLE: a Short (<= 3 minutes) that is private with a publish time in the future, further away than SAFETY_BUFFER.
  * Already-published videos are never looked at.
  * A scheduled video that is not a Short (a long montage...) keeps its slot exactly, and that minute is skipped.
  * A Short about to publish (within SAFETY_BUFFER) is left alone for the same reason, and its minute is skipped.
Slot times come from the existing presets (AutoScheduleConfig.get_slot_times, 06:00-14:00 window, 15-minute spacing);
a preset that breaks those rules is reported and blocks applying.

Pure and side-effect-free: nothing here talks to YouTube. validate_plan() is run again right before anything is sent,
and verify_remote() compares what YouTube holds afterwards with the validated expectation.
"""
from __future__ import annotations

import collections
import dataclasses
import datetime as dt
from typing import Iterable, Optional

from data.auto_schedule_config import MIN_SLOT_SPACING_MINUTES, AutoScheduleConfig, validate_slot_times
from scheduling.engine import Assignment, ScheduleResult
from youtube.models import Video

# how close to its publish time a scheduled video is treated as untouchable (the same 15 minutes the app has always used)
SAFETY_BUFFER = dt.timedelta(minutes=15)

SEV_ERROR = "error"  # blocks applying
SEV_WARNING = "warning"  # shown, does not block

CODE_UNREGISTERED = "missing_order_record"
CODE_REGISTRY = "registry_integrity"
CODE_SLOT_PRESET = "invalid_slot_preset"
CODE_COVERAGE = "queue_coverage"
CODE_ORDER = "order_not_preserved"
CODE_SLOT = "invalid_slot"
CODE_CONFLICT = "slot_conflict"
CODE_INELIGIBLE = "ineligible_video"
CODE_NOTE = "note"


@dataclasses.dataclass(frozen=True)
class PlanIssue:
    severity: str
    code: str
    message: str

    @property
    def is_error(self) -> bool:
        return self.severity == SEV_ERROR


@dataclasses.dataclass
class QueueSnapshot:
    queue: list[Video]  # eligible Shorts, in sequence-number order
    unregistered: list[Video]  # eligible Shorts with NO sequence number (these block applying)
    locked: list[Video]  # Shorts scheduled within SAFETY_BUFFER: left alone, minute reserved
    long_videos: list[Video]  # scheduled non-Shorts: left alone, minute reserved
    registry_problems: list[str]
    seq: dict[str, int]

    @property
    def blocked_minutes(self) -> set:
        return {_minute_key(v.publish_at.astimezone()) for v in (*self.locked, *self.long_videos) if v.publish_at}


@dataclasses.dataclass
class PlanRow:
    position: int  # 1-based place in the queue (= sequence order)
    seq: int
    video_id: str
    title: str
    old_publish_at: Optional[dt.datetime]
    new_publish_at: dt.datetime
    position_by_date: int  # where this video stands now when the queue is ordered by its CURRENT date (differs from `position` when the schedule is out of order)

    @property
    def changed(self) -> bool:
        return self.old_publish_at is None or self.old_publish_at.astimezone() != self.new_publish_at.astimezone()


@dataclasses.dataclass
class FrequencyPlan:
    frequency: int
    rows: list[PlanRow]
    current_frequency: Optional[int]
    current_distribution: dict[int, int]  # videos-per-day -> number of days
    timeline_end_before: Optional[dt.date]
    timeline_end_after: Optional[dt.date]
    issues: list[PlanIssue]
    snapshot: QueueSnapshot

    @property
    def changed_rows(self) -> list[PlanRow]:
        return [r for r in self.rows if r.changed]

    @property
    def errors(self) -> list[PlanIssue]:
        return [i for i in self.issues if i.is_error]

    @property
    def ok(self) -> bool:
        return not self.errors

    @property
    def order_before(self) -> list[str]:
        """The queue as the current schedule has it (ordered by current date) -- shown next to the sequence order."""
        return [r.video_id for r in sorted(self.rows, key=lambda r: r.position_by_date)]

    @property
    def order_after(self) -> list[str]:
        return [r.video_id for r in self.rows]

    @property
    def out_of_order_now(self) -> int:
        return sum(1 for r in self.rows if r.position_by_date != r.position)

    def to_result(self) -> ScheduleResult:
        """Only the videos whose date/time actually changes, ordered by how soon each one MATTERS: the earlier of the time it
        has now and the time it is getting.

        Why: updating a large schedule can stop part-way (YouTube's daily quota is 10,000 units and each update costs 50). A
        video that has not been reached still publishes at its OLD time, and published videos cannot be taken back. In this
        order, whenever the run stops at some moment T (the soonest relevant time of the first video not yet updated), every video
        that is due before T is already at its new time and no un-updated video is due before T -- so everything that publishes
        up to T is exactly as planned, in queue order. Updating in queue order instead would leave old-time videos publishing
        ahead of correctly placed ones."""
        def matters_at(r: PlanRow):
            times = [r.new_publish_at.astimezone()]
            if r.old_publish_at is not None:
                times.append(r.old_publish_at.astimezone())
            return (min(times), r.seq)

        return ScheduleResult(assignments=[
            Assignment(video_id=r.video_id, title=r.title, old_publish_at=r.old_publish_at, new_publish_at=r.new_publish_at)
            for r in sorted((r for r in self.rows if r.changed), key=matters_at)
        ])


def _minute_key(t: dt.datetime) -> tuple:
    return (t.year, t.month, t.day, t.hour, t.minute)


def is_eligible(v: Video, now: dt.datetime) -> bool:
    return v.is_scheduled and v.is_short and v.publish_at is not None and v.publish_at > now + SAFETY_BUFFER


# ---------------------------------------------------------------------------------------------------------------

def build_queue(videos: Iterable[Video], registry, now: dt.datetime) -> QueueSnapshot:
    """Splits the schedule into the queue (in sequence order) and the videos that are left alone. `registry` is
    data/queue_order.QueueOrderStore (anything with seq_of() and integrity_problems())."""
    queue: list[Video] = []
    unregistered: list[Video] = []
    locked: list[Video] = []
    longs: list[Video] = []
    seq: dict[str, int] = {}
    seen: set[str] = set()
    for v in videos:
        if v.video_id in seen or not v.is_scheduled or v.publish_at is None or v.publish_at <= now:
            continue  # published/past/unscheduled videos are not part of any of this
        seen.add(v.video_id)
        if not v.is_short:
            longs.append(v)
        elif not is_eligible(v, now):
            locked.append(v)
        else:
            n = registry.seq_of(v.video_id)
            if n is None:
                unregistered.append(v)
            else:
                seq[v.video_id] = n
                queue.append(v)
    queue.sort(key=lambda v: seq[v.video_id])  # the SEQUENCE NUMBER decides the order -- never the date
    return QueueSnapshot(queue, unregistered, locked, longs, list(registry.integrity_problems()), seq)


def current_frequency(queue: list[Video]) -> tuple[Optional[int], dict[int, int]]:
    """The videos-per-day the schedule has now: the most common daily count among the queue's days (the last day is
    left out of that count when there are several, because the queue ends part-way through a day)."""
    per_day = collections.Counter(v.publish_at.astimezone().date() for v in queue)
    if not per_day:
        return None, {}
    days = sorted(per_day)
    counted = days[:-1] if len(days) > 2 else days
    dist = collections.Counter(per_day[d] for d in counted)
    mode = max(dist, key=lambda k: (dist[k], k))
    return mode, dict(sorted(collections.Counter(per_day.values()).items()))


def compute_frequency_plan(snapshot: QueueSnapshot, frequency: int, config: AutoScheduleConfig, now: dt.datetime) -> FrequencyPlan:
    """Dates and times for the whole queue at `frequency` Shorts per day, produced by walking the queue in sequence
    order from the next valid slot. Does not talk to YouTube."""
    queue = snapshot.queue
    slots = config.get_slot_times(frequency)
    blocked = snapshot.blocked_minutes
    by_date = {v.video_id: i for i, v in enumerate(sorted(queue, key=lambda v: (v.publish_at, snapshot.seq[v.video_id])), start=1)}

    rows: list[PlanRow] = []
    day = now.astimezone().date()
    index = 0
    guard = len(queue) * 2 + 800  # a day always has a free slot unless every slot of it is reserved, and reservations are finite
    while index < len(queue) and guard > 0:
        guard -= 1
        usable = [dt.datetime.combine(day, t).astimezone() for t in slots]
        usable = [d for d in usable if d > now + SAFETY_BUFFER and _minute_key(d) not in blocked]
        for new_time in usable:
            if index >= len(queue):
                break
            v = queue[index]
            index += 1
            rows.append(PlanRow(
                position=index, seq=snapshot.seq[v.video_id], video_id=v.video_id, title=v.title,
                old_publish_at=v.publish_at, new_publish_at=new_time, position_by_date=by_date[v.video_id],
            ))
        day += dt.timedelta(days=1)

    cur, dist = current_frequency(queue)
    plan = FrequencyPlan(
        frequency=frequency, rows=rows, current_frequency=cur, current_distribution=dist,
        timeline_end_before=max((v.publish_at.astimezone().date() for v in queue), default=None),
        timeline_end_after=max((r.new_publish_at.astimezone().date() for r in rows), default=None),
        issues=[], snapshot=snapshot,
    )
    plan.issues = validate_plan(plan, config, now)
    return plan


def validate_plan(plan: FrequencyPlan, config: AutoScheduleConfig, now: dt.datetime) -> list[PlanIssue]:
    """Every check that must pass before anything is sent to YouTube. Any SEV_ERROR aborts the whole operation."""
    snap = plan.snapshot
    out: list[PlanIssue] = []

    # ---- the order records
    if snap.unregistered:
        names = ", ".join(f'"{v.title[:30]}"' for v in snap.unregistered[:3])
        out.append(PlanIssue(SEV_ERROR, CODE_UNREGISTERED,
                             f"{len(snap.unregistered)} scheduled Short(s) have no sequence number, so their place in the order is unknown ({names}"
                             f"{', ...' if len(snap.unregistered) > 3 else ''}). Nothing will be changed until they are placed."))
    for problem in snap.registry_problems:
        out.append(PlanIssue(SEV_ERROR, CODE_REGISTRY, problem))

    # ---- the slot times
    problem = validate_slot_times(config.get_slot_times(plan.frequency), config.window_start, config.window_end)
    if problem:
        out.append(PlanIssue(SEV_ERROR, CODE_SLOT_PRESET, f"The {plan.frequency}-per-day slot times are not valid: {problem}"))

    # ---- every eligible Short exactly once, nothing else, sequence preserved
    queue_ids = [v.video_id for v in snap.queue]
    plan_ids = [r.video_id for r in plan.rows]
    if len(set(plan_ids)) != len(plan_ids):
        dupes = [k for k, n in collections.Counter(plan_ids).items() if n > 1]
        out.append(PlanIssue(SEV_ERROR, CODE_COVERAGE, f"{len(dupes)} video(s) appear more than once in the new schedule ({', '.join(dupes[:3])})."))
    lost = set(queue_ids) - set(plan_ids)
    extra = set(plan_ids) - set(queue_ids)
    if lost:
        out.append(PlanIssue(SEV_ERROR, CODE_COVERAGE, f"{len(lost)} eligible Short(s) are missing from the new schedule ({', '.join(sorted(lost)[:3])})."))
    if extra:
        out.append(PlanIssue(SEV_ERROR, CODE_INELIGIBLE, f"{len(extra)} video(s) in the new schedule are not eligible Shorts ({', '.join(sorted(extra)[:3])})."))
    if plan_ids != queue_ids:
        out.append(PlanIssue(SEV_ERROR, CODE_ORDER, "The new schedule lists the videos in a different order than the queue's sequence numbers."))
    for a, b in zip(plan.rows, plan.rows[1:]):
        if not a.seq < b.seq:
            out.append(PlanIssue(SEV_ERROR, CODE_ORDER, f'Sequence numbers are not strictly increasing at "{a.title[:30]}" -> "{b.title[:30]}".'))
            break
        if not a.new_publish_at.astimezone() < b.new_publish_at.astimezone():
            out.append(PlanIssue(SEV_ERROR, CODE_ORDER, f'"{b.title[:30]}" would publish at or before "{a.title[:30]}", which comes before it in the queue.'))
            break

    # ---- every new time is a valid, free slot
    blocked = snap.blocked_minutes
    start_t, end_t = config.window_start, config.window_end
    per_day: dict[dt.date, list[PlanRow]] = collections.defaultdict(list)
    taken: dict[tuple, PlanRow] = {}
    bad_window = bad_future = clashes = reserved = 0
    for r in plan.rows:
        local = r.new_publish_at.astimezone()
        per_day[local.date()].append(r)
        if not (start_t <= local.time().replace(second=0, microsecond=0) <= end_t):
            bad_window += 1
        if local <= now + SAFETY_BUFFER:
            bad_future += 1
        key = _minute_key(local)
        if key in taken:
            clashes += 1
        taken[key] = r
        if key in blocked:
            reserved += 1
    if bad_window:
        out.append(PlanIssue(SEV_ERROR, CODE_SLOT, f"{bad_window} new time(s) fall outside the {start_t:%H:%M}-{end_t:%H:%M} publishing window."))
    if bad_future:
        out.append(PlanIssue(SEV_ERROR, CODE_SLOT, f"{bad_future} new time(s) are not far enough in the future."))
    if clashes:
        out.append(PlanIssue(SEV_ERROR, CODE_CONFLICT, f"{clashes} Short(s) would publish at the very same minute as another."))
    if reserved:
        out.append(PlanIssue(SEV_ERROR, CODE_CONFLICT, f"{reserved} new time(s) land on a slot already used by a video that is not being moved."))
    too_many = [d for d, rs in per_day.items() if len(rs) > plan.frequency]
    if too_many:
        out.append(PlanIssue(SEV_ERROR, CODE_SLOT, f"{len(too_many)} day(s) would hold more than {plan.frequency} Short(s)."))
    tight = 0
    for rs in per_day.values():
        times = sorted(r.new_publish_at.astimezone() for r in rs)
        tight += sum(1 for a, b in zip(times, times[1:]) if (b - a) < dt.timedelta(minutes=MIN_SLOT_SPACING_MINUTES))
    if tight:
        out.append(PlanIssue(SEV_ERROR, CODE_SLOT, f"{tight} pair(s) of Shorts on the same day would be less than {MIN_SLOT_SPACING_MINUTES} minutes apart."))

    # ---- things worth knowing (never block)
    if snap.locked:
        out.append(PlanIssue(SEV_WARNING, CODE_NOTE, f"{len(snap.locked)} Short(s) publish within {int(SAFETY_BUFFER.total_seconds() // 60)} minutes and are left alone."))
    if snap.long_videos:
        out.append(PlanIssue(SEV_WARNING, CODE_NOTE, f"{len(snap.long_videos)} scheduled video(s) longer than a Short keep their slot; that slot is skipped."))
    return out


# ---------------------------------------------------------------------------------------------------------------

def verify_remote(plan: FrequencyPlan, remote_videos: Iterable[Video]) -> list[str]:
    """After applying: compare what YouTube actually holds with the validated expectation. Returns a list of
    mismatches (empty = YouTube's schedule is exactly the plan, in the plan's order)."""
    remote = {v.video_id: v for v in remote_videos}
    problems: list[str] = []
    for r in plan.rows:
        v = remote.get(r.video_id)
        if v is None:
            problems.append(f'"{r.title[:40]}": not found on YouTube.')
        elif not v.is_scheduled or v.publish_at is None:
            problems.append(f'"{r.title[:40]}": no longer scheduled on YouTube (it is {v.privacy_status}).')
        elif v.publish_at.astimezone() != r.new_publish_at.astimezone():
            problems.append(f'"{r.title[:40]}": YouTube has {v.publish_at.astimezone():%d %b %Y %H:%M}, expected {r.new_publish_at.astimezone():%d %b %Y %H:%M}.')
    if not problems:
        by_time = [vid for vid, _ in sorted(((r.video_id, remote[r.video_id].publish_at) for r in plan.rows), key=lambda p: p[1])]
        if by_time != [r.video_id for r in plan.rows]:
            problems.append("The order the videos will publish in on YouTube differs from the queue order.")
    return problems

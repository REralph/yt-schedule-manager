"""Core AUTO BOOST (formerly Auto Schedule) decision logic: given how recently-published Shorts actually
performed, work out how many Shorts the channel needs tomorrow to stay on pace for its
daily or weekly view target, then pull that many Shorts forward out of the EXISTING
future schedule (never inserts a separate "ready/unscheduled" pool -- see
compute_auto_schedule_pull_forward()).

Pure, side-effect-free functions -- no YouTube calls happen here. The headless runner
(auto_schedule_runner.py) and the GUI settings page's live preview both call into this the
same way, so there is exactly one place this logic lives.

The core idea: don't just "upload more whenever one video does badly" -- figure out
whether the CURRENT number of Shorts is producing enough combined views to stay on pace,
and only grow past the normal count by however many extra Shorts are actually needed to
close the gap. The moment performance recovers, this naturally recommends the normal
count again -- there's no persistent "increase counter" to separately step back down,
because every decision is computed fresh from real recent performance.
"""
from __future__ import annotations

import dataclasses
import datetime as dt
import math
from typing import Optional

from data.auto_schedule_config import TARGET_MODE_WEEKLY, AutoScheduleConfig
from scheduling.increase_slots import IncreaseSlotsResult, compute_increase_slots_schedule
from youtube.models import Video

COMPLETION_HOURS = 24  # a published day only counts once every Short on it has had this long to accumulate views


@dataclasses.dataclass
class DayPerformance:
    day: dt.date
    video_count: int
    combined_views: int


def group_by_published_day(videos: list[Video], now: dt.datetime) -> dict[dt.date, DayPerformance]:
    """Groups already-published (public) Shorts by the local calendar day they actually
    went live, combining every Short's views into one total per day (spec section 9) --
    only including a day once ALL its Shorts have had COMPLETION_HOURS to accumulate
    views, so a still-accumulating day never understates its own performance."""
    by_day: dict[dt.date, list[Video]] = {}
    for v in videos:
        if v.published_at is None:
            continue
        by_day.setdefault(v.published_at.astimezone().date(), []).append(v)

    required = dt.timedelta(hours=COMPLETION_HOURS)
    result: dict[dt.date, DayPerformance] = {}
    for day, vids in by_day.items():
        if any((now - v.published_at) < required for v in vids):
            continue
        result[day] = DayPerformance(day=day, video_count=len(vids), combined_views=sum(v.view_count or 0 for v in vids))
    return result


@dataclasses.dataclass
class PerformanceResult:
    ready: bool
    reason: str = ""
    recent_days: list[DayPerformance] = dataclasses.field(default_factory=list)  # up to 2, chronological, most recent last
    actual_views: int = 0        # the most recently completed day's combined views -- what shortfall is sized against
    trajectory_met: bool = False  # whether the 1-2 day combined trend is keeping pace with target


def evaluate_daily_performance(by_day: dict[dt.date, DayPerformance], today: dt.date, daily_target_views: int) -> PerformanceResult:
    """Spec section 7: don't rely exclusively on the single most recent day -- also look
    at the day before it, so one unusually good day right after a bad one doesn't trigger
    an unnecessary increase, and one unusually bad day right after a good one doesn't get
    over-corrected for either. The actual size of any shortfall is still sized off the
    single most recent completed day, since that's the day tomorrow's decision follows on
    from."""
    candidates = sorted((d for d in by_day if d < today), reverse=True)[:2]
    if not candidates:
        return PerformanceResult(ready=False, reason="No completed publishing day yet to evaluate.")

    recent = [by_day[d] for d in sorted(candidates)]
    last = recent[-1]
    combined_views = sum(d.combined_views for d in recent)
    combined_target = daily_target_views * len(recent)
    return PerformanceResult(ready=True, recent_days=recent, actual_views=last.combined_views, trajectory_met=combined_views >= combined_target)


@dataclasses.dataclass
class WeekProgress:
    week_start: dt.date
    week_end: dt.date
    target: int
    collected: int
    remaining: int
    days_remaining: int
    required_average: float
    day_performance: dict[dt.date, DayPerformance] = dataclasses.field(default_factory=dict)


def compute_week_progress(by_day: dict[dt.date, DayPerformance], today: dt.date, weekly_target_views: int) -> WeekProgress:
    """Spec sections 20-21: a Monday-Sunday week. "Days remaining" counts full calendar
    days still ahead of today within the week (today's own Shorts are still accumulating
    views and aren't "remaining" capacity to plan for -- tomorrow is the next day that can
    actually be adjusted)."""
    week_start = today - dt.timedelta(days=today.weekday())
    week_end = week_start + dt.timedelta(days=6)
    collected = sum(dp.combined_views for d, dp in by_day.items() if week_start <= d <= today)
    remaining = max(0, weekly_target_views - collected)
    days_remaining = max(0, (week_end - today).days)
    required_average = remaining / max(days_remaining, 1)
    return WeekProgress(
        week_start=week_start, week_end=week_end, target=weekly_target_views, collected=collected,
        remaining=remaining, days_remaining=days_remaining, required_average=required_average,
        day_performance={d: dp for d, dp in by_day.items() if week_start <= d <= week_end},
    )


@dataclasses.dataclass
class DecisionResult:
    video_count: int
    target_reached: Optional[bool]  # None if there wasn't enough data yet to judge
    expected_views_per_short: int
    explanation: str
    shortfall: int = 0
    additional_shorts: int = 0


def _clamp_count(config: AutoScheduleConfig, count: int) -> int:
    return max(1, min(count, config.max_videos_per_day))


def _extra_videos(config: AutoScheduleConfig, calculated: int, shortfall: float) -> int:
    """How many videos to add to tomorrow when behind target. If the user picked a fixed boost
    (+1 / +2 / +3 -> config.boost_extra_videos) that is exactly what is added; with "Calculated"
    (0) it is the number worked out from how far short the views were. Either way nothing is
    added unless there really IS a shortfall -- a day that reached its target is never boosted
    (a low day BEFORE it is not a reason to boost after a good one)."""
    if shortfall <= 0:
        return 0
    return config.boost_extra_videos if config.boost_extra_videos > 0 else calculated


def _boost_explanation(config: AutoScheduleConfig, additional: int, expected_views_per_short: int, count: int) -> str:
    how = (
        f"boost of +{additional} video(s)" if config.boost_extra_videos > 0
        else f"+{additional} short(s) at ~{expected_views_per_short:,} views each"
    )
    capped = " (capped at Maximum Shorts / Day)" if count < config.normal_videos_per_day + additional else ""
    return f"{how} -> {count}/day{capped}"


def decide_daily(performance: PerformanceResult, config: AutoScheduleConfig, expected_views_per_short: int) -> DecisionResult:
    if not performance.ready:
        return DecisionResult(
            video_count=config.normal_videos_per_day, target_reached=None, expected_views_per_short=expected_views_per_short,
            explanation=f"{performance.reason} Using the normal daily amount ({config.normal_videos_per_day}).",
        )
    if performance.trajectory_met:
        return DecisionResult(
            video_count=config.normal_videos_per_day, target_reached=True, expected_views_per_short=expected_views_per_short,
            explanation=f"{performance.actual_views:,} views met the {config.daily_target_views:,} daily target -> normal amount ({config.normal_videos_per_day}).",
        )
    shortfall = max(0, config.daily_target_views - performance.actual_views)
    calculated = math.ceil(shortfall / expected_views_per_short) if expected_views_per_short > 0 else 0
    additional = _extra_videos(config, calculated, shortfall)
    count = _clamp_count(config, config.normal_videos_per_day + additional)
    if shortfall <= 0:
        return DecisionResult(
            video_count=count, target_reached=True, expected_views_per_short=expected_views_per_short,
            explanation=f"{performance.actual_views:,} views met the {config.daily_target_views:,} target on the latest day -> normal amount ({count}).",
        )
    return DecisionResult(
        video_count=count, target_reached=False, expected_views_per_short=expected_views_per_short,
        shortfall=shortfall, additional_shorts=additional,
        explanation=(
            f"{performance.actual_views:,} views was {shortfall:,} short of the {config.daily_target_views:,} target -> "
            f"{_boost_explanation(config, additional, expected_views_per_short, count)}."
        ),
    )


def decide_weekly(week_progress: WeekProgress, config: AutoScheduleConfig, expected_views_per_short: int) -> DecisionResult:
    expected_from_normal = config.normal_videos_per_day * expected_views_per_short
    if week_progress.remaining <= 0 or expected_from_normal >= week_progress.required_average:
        return DecisionResult(
            video_count=config.normal_videos_per_day, target_reached=True, expected_views_per_short=expected_views_per_short,
            explanation=(
                f"On pace: ~{expected_from_normal:,} expected views/day from {config.normal_videos_per_day}/day already "
                f"covers the {week_progress.required_average:,.0f}/day still required -> normal amount ({config.normal_videos_per_day})."
            ),
        )
    shortfall = week_progress.required_average - expected_from_normal
    calculated = math.ceil(shortfall / expected_views_per_short) if expected_views_per_short > 0 else 0
    additional = _extra_videos(config, calculated, shortfall)
    count = _clamp_count(config, config.normal_videos_per_day + additional)
    return DecisionResult(
        video_count=count, target_reached=False, expected_views_per_short=expected_views_per_short,
        shortfall=round(shortfall), additional_shorts=additional,
        explanation=(
            f"Only ~{expected_from_normal:,} expected views/day from {config.normal_videos_per_day}/day but "
            f"{week_progress.required_average:,.0f}/day is required -> "
            f"{_boost_explanation(config, additional, expected_views_per_short, count)}."
        ),
    )


def decide_video_count(
    config: AutoScheduleConfig,
    daily_performance: Optional[PerformanceResult],
    week_progress: Optional[WeekProgress],
    expected_views_per_short: int,
) -> DecisionResult:
    """Spec section 8: which evaluation applies is decided automatically from
    config.target_mode -- there's nothing else for the user to configure."""
    if config.target_mode == TARGET_MODE_WEEKLY and week_progress is not None:
        return decide_weekly(week_progress, config, expected_views_per_short)
    return decide_daily(
        daily_performance or PerformanceResult(ready=False, reason="No performance data available."),
        config, expected_views_per_short,
    )


def compute_auto_schedule_pull_forward(
    all_scheduled: list[Video],
    target_date: dt.date,
    desired_total_for_target: int,
    config: AutoScheduleConfig,
    now: dt.datetime,
) -> IncreaseSlotsResult:
    """AS-001 points 11-13: brings target_date (normally "tomorrow") up to
    `desired_total_for_target` Shorts (capped at max_videos_per_day) by pulling the next
    Short(s) forward out of the EXISTING future schedule -- never a separate
    "ready/unscheduled" pool, and never a new upload (point 15). Every subsequent day then
    tops back up to config.normal_videos_per_day as the queue cascades forward (point 13's
    "Day 3 ends with Q105, Q106" example), using SET-0012.1's saved slot-time presets
    throughout (point 1: one source of truth for upload times) -- exactly what a single-day
    increase_slots.py range already does, so this is a thin, semantically-named wrapper
    around that shared, already-tested mechanism rather than a second implementation of
    the same pull-forward-and-cascade logic. Order is preserved structurally (see
    scheduling/increase_slots.py's own docstring) and only genuinely-changed Shorts appear
    in the result (point 21.8)."""
    cap = min(desired_total_for_target, config.max_videos_per_day)
    return compute_increase_slots_schedule(all_scheduled, target_date, target_date, cap, config, now)

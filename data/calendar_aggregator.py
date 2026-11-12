"""Pure date-grouping helpers for the Calendar tab: turn a flat video list into
per-date buckets, and classify a day's count into the calendar grid's colour tiers.
No YouTube calls, no widgets -- just grouping state.repo.videos by publish_at.date().
"""
from __future__ import annotations

import datetime as dt

from scheduling.slot_rules import MAX_DAILY_SLOTS
from youtube.models import Video

TIER_EMPTY = "empty"
TIER_PARTIAL = "partial"
TIER_FULL = "full"


def group_by_date(videos: list[Video]) -> dict[dt.date, list[Video]]:
    """Every scheduled video (publish_at is not None), grouped by its local publish
    date, each day's list sorted by time. Unscheduled videos are excluded."""
    by_date: dict[dt.date, list[Video]] = {}
    for v in videos:
        if not v.publish_at:
            continue
        by_date.setdefault(v.publish_at.astimezone().date(), []).append(v)
    for day_videos in by_date.values():
        day_videos.sort(key=lambda v: v.publish_at)
    return by_date


def count_for_month(by_date: dict[dt.date, list[Video]], year: int, month: int) -> int:
    return sum(len(v) for d, v in by_date.items() if d.year == year and d.month == month)


def tier_for_count(count: int) -> str:
    """empty (0) / partial (1..half the daily cap) / full (more than half) -- mirrors
    the "e.g. 1-2 amber, 3-4 green" example against the shared 4/day slot table."""
    if count <= 0:
        return TIER_EMPTY
    half = MAX_DAILY_SLOTS // 2
    return TIER_PARTIAL if count <= half else TIER_FULL

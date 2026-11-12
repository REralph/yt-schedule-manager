"""Giving a sequence number to scheduled Shorts that have none, AFTER an order has been recorded.

  * A Short uploaded after the newest video already in the queue order simply goes to the END (in upload order). That is
    the app's own rule for every upload, so it is done automatically and its evidence is stored.
  * A Short that is OLDER than some video that already has a number is a missing record: where it belongs is a
    judgement, so nothing is done with it until you choose -- "by upload time" (after the latest numbered video that
    was uploaded before it) or "at the end".
Videos with no usable upload time are reported and never placed.
"""
from __future__ import annotations

import dataclasses
import datetime as dt
from typing import Iterable, Optional

from data.queue_order import QueueOrderStore, SOURCE_INSERTED, SOURCE_UPLOAD
from youtube.models import Video

MODE_BY_TIME = "time"
MODE_AT_END = "end"


@dataclasses.dataclass
class Placement:
    video: Video
    created: dt.datetime
    after_id: Optional[str]  # the numbered video it follows when placed by upload time (None = before everything)
    after_title: str = ""


@dataclasses.dataclass
class RegistrationPlan:
    append_now: list[Video]  # newer than everything numbered: go to the end, in upload order
    older: list[Placement]  # need a decision
    no_time: list[Video]  # cannot be placed

    @property
    def is_empty(self) -> bool:
        return not (self.append_now or self.older or self.no_time)


def plan_registration(registry: QueueOrderStore, videos: Iterable[Video]) -> RegistrationPlan:
    shorts = [v for v in videos if v.is_scheduled and v.is_short]
    numbered = [v for v in shorts if registry.has(v.video_id) and v.published_at is not None]
    newest_numbered = max((v.published_at for v in numbered), default=None)
    if newest_numbered is None and registry.adopted_at:
        try:
            newest_numbered = dt.datetime.fromisoformat(registry.adopted_at)
        except ValueError:
            newest_numbered = None

    append_now: list[Video] = []
    older: list[Placement] = []
    no_time: list[Video] = []
    by_created = sorted(numbered, key=lambda v: v.published_at)
    for v in shorts:
        if registry.has(v.video_id):
            continue
        if v.published_at is None:
            no_time.append(v)
        elif newest_numbered is None or v.published_at > newest_numbered:
            append_now.append(v)
        else:
            prior = [n for n in by_created if n.published_at <= v.published_at]
            anchor = prior[-1] if prior else None
            older.append(Placement(v, v.published_at, anchor.video_id if anchor else None, anchor.title if anchor else ""))
    append_now.sort(key=lambda v: v.published_at)
    older.sort(key=lambda p: p.created)
    return RegistrationPlan(append_now, older, no_time)


def append_new_uploads(registry: QueueOrderStore, plan: RegistrationPlan) -> int:
    """Records the clear-cut ones (newer than everything numbered) at the end. Returns how many."""
    n = 0
    for v in plan.append_now:
        rec = registry.append(v.video_id, v.title, f"uploaded {v.published_at.astimezone():%d %b %Y %H:%M}, after the newest video already in the queue order; added to the end", SOURCE_UPLOAD)
        n += 1 if rec is not None else 0
    return n


def place_older(registry: QueueOrderStore, plan: RegistrationPlan, mode: str) -> int:
    """Applies the user's decision for the videos that are older than numbered ones. Returns how many were placed."""
    n = 0
    if mode == MODE_AT_END:
        for p in plan.older:
            registry.append(p.video.video_id, p.video.title,
                            f"had no sequence number; placed at the end by your choice (uploaded {p.created.astimezone():%d %b %Y %H:%M})", SOURCE_INSERTED)
            n += 1
        return n
    last_inserted: dict[Optional[str], str] = {}
    for p in plan.older:  # oldest first, so several videos with the same anchor keep their upload order
        after = last_inserted.get(p.after_id, p.after_id)
        registry.insert_between(p.video.video_id, after, p.video.title,
                                f"had no sequence number; placed by upload time ({p.created.astimezone():%d %b %Y %H:%M}) after "
                                f"\"{p.after_title[:40] or 'the start of the queue'}\" by your choice", SOURCE_INSERTED)
        last_inserted[p.after_id] = p.video.video_id
        n += 1
    return n

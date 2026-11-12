"""Enforces the app's highest-priority rule: the chronological order of videos must
never change, no matter what scheduling feature is in play.

validate_order_preserved() is a defensive check run right before every Apply (see
auto_schedule_runner.py), refusing to proceed if a computed result would reorder anything.
Every scheduling function in this app is constructed so this should never actually trigger
-- it's the safety net, not the mechanism."""
from __future__ import annotations

from scheduling.engine import ScheduleResult
from youtube.models import Video


def validate_order_preserved(full_order_before: list[Video], result: ScheduleResult) -> list[str]:
    """Confirms the computed result never reorders anything relative to the schedule as
    it stood before this operation. Returns human-readable violation descriptions
    (empty list = safe to apply)."""
    new_time_by_id = {a.video_id: a.new_publish_at for a in result.assignments}
    # Only Shorts are ever rescheduled; a long video (a montage...) stays exactly where it was
    # scheduled, so its position relative to the moving Shorts is not part of the rule.
    ordered = sorted((v for v in full_order_before if v.publish_at and v.is_short), key=lambda v: v.publish_at)

    effective = [(v, new_time_by_id.get(v.video_id, v.publish_at)) for v in ordered]

    violations: list[str] = []
    for i in range(len(effective) - 1):
        cur_v, cur_t = effective[i]
        nxt_v, nxt_t = effective[i + 1]
        if cur_t > nxt_t:
            violations.append(
                f"'{cur_v.title}' would be scheduled after '{nxt_v.title}', but it must stay before it."
            )
    return violations

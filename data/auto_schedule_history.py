"""Records every AUTO SCHEDULE decision -- what was evaluated, what was decided, and what
was actually done -- so the automation's behaviour can be audited after the fact. It runs
unattended with no confirmation dialog (spec: apply automatically, no confirmation), so
this is the only record of what it did and why. Persisted to
data/auto_schedule_history.json, separate from the app's regular reschedule History
(data/history.py), which still separately records the actual publish-time changes for
Undo -- this file is the *decision* trail, that one is the *change* trail.
"""
from __future__ import annotations

import uuid
from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Optional

from data import safe_io

DATA_DIR = Path(__file__).resolve().parent
AUTO_SCHEDULE_HISTORY_PATH = DATA_DIR / "auto_schedule_history.json"


@dataclass
class AutoScheduleDecision:
    decision_id: str
    timestamp: datetime
    target_date: date  # the day being scheduled (e.g. "tomorrow" at the time this ran)
    target_mode: str = "daily"  # "daily" or "weekly", whichever was active when this ran
    target_views_used: int = 0  # the daily target, or the weekly-derived required daily average
    actual_views: Optional[int] = None  # None if there wasn't enough data yet to judge (bootstrap run)
    target_reached: Optional[bool] = None
    expected_views_per_short: int = 0
    previous_daily_count: int = 0
    new_daily_count: int = 0
    explanation: str = ""
    scheduled_video_ids: list[str] = field(default_factory=list)
    applied: bool = False
    error: str = ""

    @staticmethod
    def new_id() -> str:
        return uuid.uuid4().hex


def _decision_to_dict(d: AutoScheduleDecision) -> dict:
    payload = asdict(d)
    payload["timestamp"] = d.timestamp.isoformat()
    payload["target_date"] = d.target_date.isoformat()
    return payload


def _decision_from_dict(raw: dict) -> AutoScheduleDecision:
    payload = dict(raw)
    payload["timestamp"] = datetime.fromisoformat(payload["timestamp"])
    payload["target_date"] = date.fromisoformat(payload["target_date"])
    known = {f: payload[f] for f in AutoScheduleDecision.__dataclass_fields__ if f in payload}
    return AutoScheduleDecision(**known)


class AutoScheduleHistoryStore:
    def __init__(self):
        self._decisions: list[AutoScheduleDecision] = []
        self._load()

    @property
    def decisions(self) -> list[AutoScheduleDecision]:
        """Most recent first."""
        return sorted(self._decisions, key=lambda d: d.timestamp, reverse=True)

    def record(self, decision: AutoScheduleDecision) -> None:
        self._decisions.append(decision)
        self._save()

    def latest(self) -> Optional[AutoScheduleDecision]:
        decisions = self.decisions
        return decisions[0] if decisions else None

    def latest_applied_for_date(self, target_date: date) -> Optional[AutoScheduleDecision]:
        for d in self.decisions:
            if d.target_date == target_date and d.applied:
                return d
        return None

    def _load(self) -> None:
        raw = safe_io.read_json_object(AUTO_SCHEDULE_HISTORY_PATH, "auto_schedule_history.json")
        if raw is None:
            return
        self._decisions = safe_io.parse_entries(raw.get("decisions", []), _decision_from_dict, "auto_schedule_history.json")

    def _save(self) -> None:
        safe_io.atomic_write_json(AUTO_SCHEDULE_HISTORY_PATH, {"decisions": [_decision_to_dict(d) for d in self._decisions]})

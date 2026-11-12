"""Records every Apply operation (old + new publish times per video) so it can be
undone, persisted to data/history.json."""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from data import safe_io

DATA_DIR = Path(__file__).resolve().parent
HISTORY_PATH = DATA_DIR / "history.json"


@dataclass
class HistoryChange:
    video_id: str
    title: str
    old_publish_at: Optional[datetime]
    new_publish_at: datetime


@dataclass
class HistoryEntry:
    entry_id: str
    timestamp: datetime
    description: str
    changes: list[HistoryChange] = field(default_factory=list)
    undone: bool = False

    @property
    def video_count(self) -> int:
        return len(self.changes)


def _change_to_dict(c: HistoryChange) -> dict:
    return {
        "video_id": c.video_id,
        "title": c.title,
        "old_publish_at": c.old_publish_at.isoformat() if c.old_publish_at else None,
        "new_publish_at": c.new_publish_at.isoformat(),
    }


def _change_from_dict(d: dict) -> HistoryChange:
    return HistoryChange(
        video_id=d["video_id"],
        title=d.get("title", ""),
        old_publish_at=datetime.fromisoformat(d["old_publish_at"]) if d.get("old_publish_at") else None,
        new_publish_at=datetime.fromisoformat(d["new_publish_at"]),
    )


def _entry_to_dict(e: HistoryEntry) -> dict:
    return {
        "entry_id": e.entry_id,
        "timestamp": e.timestamp.isoformat(),
        "description": e.description,
        "changes": [_change_to_dict(c) for c in e.changes],
        "undone": e.undone,
    }


def _entry_from_dict(d: dict) -> HistoryEntry:
    return HistoryEntry(
        entry_id=d["entry_id"],
        timestamp=datetime.fromisoformat(d["timestamp"]),
        description=d.get("description", ""),
        changes=[_change_from_dict(c) for c in d.get("changes", [])],
        undone=d.get("undone", False),
    )


class HistoryStore:
    def __init__(self):
        self._entries: list[HistoryEntry] = []
        self._load()

    @property
    def entries(self) -> list[HistoryEntry]:
        """Most recent first."""
        return sorted(self._entries, key=lambda e: e.timestamp, reverse=True)

    def record(self, description: str, changes: list[HistoryChange]) -> HistoryEntry:
        entry = HistoryEntry(
            entry_id=uuid.uuid4().hex,
            timestamp=datetime.now(timezone.utc),
            description=description,
            changes=changes,
        )
        self._entries.append(entry)
        self._save()
        return entry

    def remove_changes(self, entry_id: str, video_ids) -> None:
        """Takes changes out of an entry -- used when an update that was recorded in advance then did not happen.
        An entry left with no changes is removed altogether."""
        ids = set(video_ids)
        for e in list(self._entries):
            if e.entry_id == entry_id:
                e.changes = [c for c in e.changes if c.video_id not in ids]
                if not e.changes:
                    self._entries.remove(e)
        self._save()

    def mark_undone(self, entry_id: str) -> None:
        for e in self._entries:
            if e.entry_id == entry_id:
                e.undone = True
        self._save()

    def get(self, entry_id: str) -> Optional[HistoryEntry]:
        for e in self._entries:
            if e.entry_id == entry_id:
                return e
        return None

    def _load(self) -> None:
        raw = safe_io.read_json_object(HISTORY_PATH, "history.json")
        if raw is None:
            return
        self._entries = safe_io.parse_entries(raw.get("entries", []), _entry_from_dict, "history.json")

    def _save(self) -> None:
        safe_io.atomic_write_json(HISTORY_PATH, {"entries": [_entry_to_dict(e) for e in self._entries]})

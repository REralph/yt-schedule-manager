"""Persisted log of every error reported through the Developer Tools error system (see
ui/dev_reporting.py) -- purely diagnostic, never read by any scheduling or upload logic."""
from __future__ import annotations

import threading
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from data import safe_io
from data.diagnostics import redact_secrets

DATA_DIR = Path(__file__).resolve().parent
ERROR_LOG_PATH = DATA_DIR / "error_log.json"
APP_VERSION = "1.0.0"
MAX_ENTRIES = 200


@dataclass
class ErrorReport:
    report_id: str
    timestamp: datetime
    item_id: str
    page: str
    function: str
    error_message: str
    error_code: str = ""
    details: str = ""
    operation: str = ""

    def format(self) -> str:
        lines = [
            "ERROR REPORT", "",
            f"Item ID: {self.item_id}",
            f"Page: {self.page}",
            f"Function: {self.function}", "",
            "Error:", self.error_message, "",
        ]
        if self.error_code:
            lines += ["Error Code:", self.error_code, ""]
        if self.details:
            lines += ["Details:", self.details, ""]
        lines += [
            "Timestamp:", self.timestamp.astimezone().strftime("%Y-%m-%d %H:%M:%S"), "",
            "Application Version:", APP_VERSION, "",
        ]
        if self.operation:
            lines += ["Operation:", self.operation]
        return redact_secrets("\n".join(lines).rstrip())  # never let a token/key leak into a copied report

    @staticmethod
    def new_id() -> str:
        return uuid.uuid4().hex


def _to_dict(r: ErrorReport) -> dict:
    d = asdict(r)
    d["timestamp"] = r.timestamp.isoformat()
    return d


def _from_dict(d: dict) -> ErrorReport:
    d = dict(d)
    d["timestamp"] = datetime.fromisoformat(d["timestamp"])
    if d["timestamp"].tzinfo is None:
        d["timestamp"] = d["timestamp"].replace(tzinfo=timezone.utc)
    known = {f: d[f] for f in ErrorReport.__dataclass_fields__ if f in d}
    known.setdefault("report_id", ErrorReport.new_id())
    known.setdefault("item_id", "")
    known.setdefault("page", "")
    known.setdefault("function", "")
    known.setdefault("error_message", "")
    return ErrorReport(**known)


class ErrorLogStore:
    def __init__(self):
        self._lock = threading.RLock()
        self._entries: list[ErrorReport] = []
        self._load()

    @property
    def entries(self) -> list[ErrorReport]:
        """Most recent first."""
        return sorted(self._entries, key=lambda r: r.timestamp, reverse=True)

    def record(
        self, item_id: str, page: str, function: str, error_message: str,
        error_code: str = "", details: str = "", operation: str = "",
    ) -> ErrorReport:
        report = ErrorReport(
            report_id=ErrorReport.new_id(), timestamp=datetime.now(timezone.utc),
            item_id=item_id, page=page, function=function, error_message=error_message,
            error_code=error_code, details=details, operation=operation,
        )
        with self._lock:
            self._entries.append(report)
            self._entries = self._entries[-MAX_ENTRIES:]
            self._save()
        return report

    def clear(self) -> None:
        with self._lock:
            self._entries = []
            self._save()

    def remove_older_than(self, cutoff: datetime) -> int:
        with self._lock:
            before = len(self._entries)
            self._entries = [e for e in self._entries if e.timestamp >= cutoff]
            removed = before - len(self._entries)
            if removed:
                self._save()
            return removed

    def latest(self) -> Optional[ErrorReport]:
        entries = self.entries
        return entries[0] if entries else None

    def _load(self) -> None:
        raw = safe_io.read_json_object(ERROR_LOG_PATH, "error_log.json")
        if raw is None:
            return
        self._entries = safe_io.parse_entries(raw.get("entries", []), _from_dict, "error_log.json")

    def _save(self) -> None:
        try:
            safe_io.atomic_write_json(ERROR_LOG_PATH, {"entries": [_to_dict(r) for r in self._entries]})
        except OSError:
            pass  # recording an error must never itself raise

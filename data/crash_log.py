"""Persistent, cross-restart crash/diagnostic log -- separate from data/error_log.py's
ErrorReport (errors the app already handles gracefully and shows the user, e.g. an upload
failure they can just retry). This one holds everything that could otherwise take the app
down or vanish silently under pythonw.exe (no console to print a traceback to):

  * crash      -- anything caught by main.py's App.report_callback_exception override,
                  its threading/sys excepthooks, or the top-level try/except
  * recovered  -- a background operation that failed and was recovered from automatically
  * data       -- a damaged/invalid settings or data file replaced by safe defaults
  * shutdown   -- the previous session ended without a normal close (killed / native crash)

Recorded here so it survives restarts -- Settings -> Crash History and Developer Tools'
"LAST APPLICATION ERROR" both read it, even after the app that crashed has been reopened.

There is ONE store per process (CrashLogStore.shared()): main.py's handlers, AppState and
the UI all use the same instance. (They used to each construct their own, so a crash
recorded by main.py never appeared in Developer Tools until a restart, and clearing the log
in one place let the other resurrect the entries on its next save.)

Never stores credentials, tokens, or other sensitive data: build_crash_report() only ever
reads a short, hand-picked list of plain fields off AppState -- never the AppState/
AppConfig/YouTubeClient object itself -- and every report is passed through
data/diagnostics.redact_secrets() when it is formatted for copying.
"""
from __future__ import annotations

import platform
import sys
import threading
import time
import traceback
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from data import diagnostics, safe_io
from data.diagnostics import KIND_CRASH, KIND_LABELS, redact_secrets

DATA_DIR = Path(__file__).resolve().parent
CRASH_LOG_PATH = DATA_DIR / "crash_log.json"
APP_VERSION = "1.0.0"
MAX_ENTRIES = 200
DEDUPE_WINDOW = timedelta(minutes=10)  # an identical recovered/data problem inside this window bumps a counter instead of adding a row

_PROCESS_START = time.time()


@dataclass
class CrashReport:
    crash_id: str
    timestamp: datetime
    error_type: str
    error_message: str
    traceback_text: str
    current_page: str = ""
    current_item_id: str = ""
    current_operation: str = ""  # what the user was doing, e.g. "Uploading Q7.mov (attempt 2)"
    upload_filename: str = ""
    upload_attempt: int = 0
    app_version: str = APP_VERSION
    python_version: str = ""
    os_info: str = ""
    kind: str = KIND_CRASH
    count: int = 1  # how many times an identical recovered/data problem happened inside the dedupe window
    app_state: str = ""  # short plain-text summary of relevant application state (never secrets)

    def short_summary(self) -> str:
        """LAST APPLICATION ERROR's one-line form, e.g. "2026-09-05 00:16 -- Upload
        operation -- SSL: WRONG_VERSION_NUMBER"."""
        when = self.timestamp.astimezone().strftime("%Y-%m-%d %H:%M")
        context = self.current_operation or self.current_page or "Unknown operation"
        return redact_secrets(f"{when} — {context} — {self.error_message or self.error_type}")

    def error_text(self) -> str:
        """COPY ERROR: just the error for this entry -- no traceback, no context."""
        text = f"{self.error_type}: {self.error_message}" if self.error_message else self.error_type
        return redact_secrets(text)

    def format(self) -> str:
        """COPY CRASH REPORT: the full technical report for THIS entry only -- traceback
        included, nothing smoothed over (see the module docstring's "never hide the real
        cause" rule) -- with credentials masked."""
        lines = [
            "CRASH / ERROR REPORT", "",
            f"Type: {KIND_LABELS.get(self.kind, self.kind)}",
            f"Date/Time: {self.timestamp.astimezone().strftime('%Y-%m-%d %H:%M:%S')}",
            f"Exception Type: {self.error_type}",
            f"Error Message: {self.error_message}", "",
        ]
        if self.count > 1:
            lines.append(f"Occurrences: {self.count} (identical, within a short period)")
        if self.current_page:
            lines.append(f"Current Page: {self.current_page}")
        if self.current_item_id:
            lines.append(f"Current Item ID: {self.current_item_id}")
        if self.current_operation:
            lines.append(f"Current Operation: {self.current_operation}")
        if self.upload_filename:
            lines.append(f"Upload Filename: {self.upload_filename}")
        if self.upload_attempt:
            lines.append(f"Upload Attempt: {self.upload_attempt}")
        if self.app_state:
            lines.append(f"Application State: {self.app_state}")
        lines += ["", f"Application Version: {self.app_version}"]
        if self.python_version:
            lines.append(f"Python Version: {self.python_version}")
        if self.os_info:
            lines.append(f"OS: {self.os_info}")
        lines += ["", "Traceback:", (self.traceback_text or "(none)").rstrip()]
        return redact_secrets("\n".join(lines).rstrip())

    @staticmethod
    def new_id() -> str:
        return uuid.uuid4().hex


def _crash_signature(r: "CrashReport") -> tuple:
    """What makes two crashes 'the same one': the exception, its message, and the last frames of
    the traceback (where it happened)."""
    lines = [l.strip() for l in (r.traceback_text or "").splitlines() if l.strip()]
    return (r.error_type, r.error_message, tuple(lines[-6:]))


def _state_summary(state) -> str:
    parts: list[str] = []
    try:
        parts.append(f"developer_mode={bool(state.config.developer_mode)}")
    except Exception:
        pass
    try:
        parts.append(f"connected={state.client is not None}")
    except Exception:
        pass
    try:
        parts.append(f"cached_videos={len(state.repo.videos) if state.repo else 0}")
    except Exception:
        pass
    parts.append(f"uptime={int(time.time() - _PROCESS_START)}s")
    parts.append(f"threads={threading.active_count()}")
    return "; ".join(parts)


def build_crash_report(exc_type, exc_value, exc_tb, state=None, kind: str = KIND_CRASH) -> CrashReport:
    """state is an ui.app_state.AppState or None (e.g. a crash before login finished) --
    only the specific plain fields below are ever read off it, never the object itself."""
    try:
        tb_text = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
    except Exception:
        tb_text = f"{getattr(exc_type, '__name__', exc_type)}: {exc_value}"
    current_page = ""
    current_item_id = ""
    current_operation = ""
    upload_filename = ""
    upload_attempt = 0
    if state is not None:
        try:
            current_page = getattr(state, "current_tab", "") or ""
            current_item_id = getattr(state, "dev_selected_component", "") or ""
            current_operation = getattr(state, "last_action", "") or ""
            upload_filename = getattr(state, "current_upload_filename", "") or ""
            upload_attempt = getattr(state, "current_upload_attempt", 0) or 0
        except Exception:
            pass
    return CrashReport(
        crash_id=CrashReport.new_id(),
        timestamp=datetime.now(timezone.utc),
        error_type=exc_type.__name__ if exc_type else "UnknownError",
        error_message=str(exc_value) if exc_value else "",
        traceback_text=tb_text,
        current_page=current_page,
        current_item_id=current_item_id,
        current_operation=current_operation,
        upload_filename=upload_filename,
        upload_attempt=upload_attempt,
        python_version=sys.version.split()[0],
        os_info=f"{platform.system()} {platform.release()}",
        kind=kind,
        app_state=_state_summary(state) if state is not None else f"uptime={int(time.time() - _PROCESS_START)}s; threads={threading.active_count()}",
    )


def _to_dict(r: CrashReport) -> dict:
    d = asdict(r)
    d["timestamp"] = r.timestamp.isoformat()
    return d


def _from_dict(d: dict) -> CrashReport:
    d = dict(d)
    d["timestamp"] = datetime.fromisoformat(d["timestamp"])
    if d["timestamp"].tzinfo is None:
        d["timestamp"] = d["timestamp"].replace(tzinfo=timezone.utc)
    known = {f: d[f] for f in CrashReport.__dataclass_fields__ if f in d}
    known.setdefault("crash_id", CrashReport.new_id())
    known.setdefault("error_type", "UnknownError")
    known.setdefault("error_message", "")
    known.setdefault("traceback_text", "")
    return CrashReport(**known)


_shared: Optional["CrashLogStore"] = None
_shared_path: Optional[Path] = None
_shared_guard = threading.Lock()


class CrashLogStore:
    def __init__(self):
        self._lock = threading.RLock()
        self._entries: list[CrashReport] = []
        self._load()

    @classmethod
    def shared(cls) -> "CrashLogStore":
        """The process-wide store (see the module docstring). Re-created if the storage
        path changed, which only ever happens in tests."""
        global _shared, _shared_path
        with _shared_guard:
            if _shared is None or _shared_path != CRASH_LOG_PATH:
                _shared = cls()
                _shared_path = CRASH_LOG_PATH
            return _shared

    @property
    def entries(self) -> list[CrashReport]:
        """Most recent first."""
        with self._lock:
            return sorted(self._entries, key=lambda r: r.timestamp, reverse=True)

    def record(self, report: CrashReport) -> CrashReport:
        """Records a crash. The SAME crash repeating (same exception, same place) within
        DEDUPE_WINDOW bumps the existing entry's counter instead of adding a row -- one fault
        that fires on every window event once produced 200 identical entries in two hours and
        buried everything else."""
        with self._lock:
            signature = _crash_signature(report)
            for existing in self._entries:
                if (
                    existing.kind == report.kind and _crash_signature(existing) == signature
                    and report.timestamp - existing.timestamp <= DEDUPE_WINDOW
                ):
                    existing.count += max(1, report.count)
                    existing.timestamp = report.timestamp
                    self._save()
                    return existing
            self._entries.append(report)
            self._trim()
            self._save()
        return report

    def record_event(
        self, kind: str, error_type: str, message: str, traceback_text: str = "", page: str = "",
        item_id: str = "", operation: str = "",
    ) -> CrashReport:
        """The diagnostics sink (data/diagnostics.py): records a non-crash event. An
        identical recovered/data problem repeated within DEDUPE_WINDOW bumps the existing
        entry's counter instead of adding another row -- a flaky connection retrying a
        background refresh shouldn't bury everything else in the history."""
        now = datetime.now(timezone.utc)
        with self._lock:
            for existing in self._entries:
                if (
                    existing.kind == kind and existing.error_type == error_type
                    and existing.error_message == message and existing.current_operation == operation
                    and now - existing.timestamp <= DEDUPE_WINDOW
                ):
                    existing.count += 1
                    existing.timestamp = now
                    self._save()
                    return existing
            report = CrashReport(
                crash_id=CrashReport.new_id(), timestamp=now, error_type=error_type, error_message=message,
                traceback_text=traceback_text, current_page=page, current_item_id=item_id, current_operation=operation,
                python_version=sys.version.split()[0], os_info=f"{platform.system()} {platform.release()}",
                kind=kind, app_state=f"uptime={int(time.time() - _PROCESS_START)}s; threads={threading.active_count()}",
            )
            self._entries.append(report)
            self._trim()
            self._save()
            return report

    def latest(self) -> Optional[CrashReport]:
        entries = self.entries
        return entries[0] if entries else None

    def latest_of_kinds(self, *kinds: str) -> Optional[CrashReport]:
        return next((e for e in self.entries if e.kind in kinds), None)

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

    def _trim(self) -> None:
        """Keeps the newest MAX_ENTRIES, dropping the low-severity (recovered/data) ones
        first so a burst of background noise can never push a real crash out of the log."""
        overflow = len(self._entries) - MAX_ENTRIES
        if overflow <= 0:
            return
        noise = sorted((e for e in self._entries if e.kind != KIND_CRASH), key=lambda r: r.timestamp)
        drop = {e.crash_id for e in noise[:overflow]}
        if len(drop) < overflow:
            oldest = sorted(self._entries, key=lambda r: r.timestamp)
            extra = [e.crash_id for e in oldest if e.crash_id not in drop][: overflow - len(drop)]
            drop |= set(extra)
        self._entries = [e for e in self._entries if e.crash_id not in drop]

    def _load(self) -> None:
        raw = safe_io.read_json_object(CRASH_LOG_PATH, "crash_log.json")
        if raw is None:
            return
        skipped = 0
        entries: list[CrashReport] = []
        items = raw.get("entries", [])
        for d in items if isinstance(items, list) else []:
            try:
                entries.append(_from_dict(d))
            except Exception:
                skipped += 1
        self._entries = entries
        if skipped:
            diagnostics.report_data_problem("crash_log.json", f"{skipped} unreadable entr{'y' if skipped == 1 else 'ies'} skipped")

    def _save(self) -> None:
        try:
            safe_io.atomic_write_json(CRASH_LOG_PATH, {"entries": [_to_dict(r) for r in self._entries]})
        except OSError:
            pass  # a disk problem must not turn recording a crash into a second crash; the entry stays in memory

"""The unified, read-only view behind Settings -> Crash History (APP-OPT-001).

Two stores feed it: data/crash_log.py (crashes, background failures the app recovered from,
repaired data files, unexpected exits) and data/error_log.py (errors that were shown to the
user, e.g. a failed upload). This module merges them into one newest-first list of rows,
each carrying the ready-to-copy text for THAT entry alone -- so "Copy Crash Report" can never
grab anything else -- and provides the clearing operations.

Every text produced here has already been passed through diagnostics.redact_secrets() (by
CrashReport.format() / ErrorReport.format()), so no credentials can reach the clipboard.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from data.crash_log import CrashLogStore
from data.diagnostics import KIND_ERROR, KIND_LABELS, redact_secrets
from data.error_log import ErrorLogStore

SUMMARY_MAX_CHARS = 90
OPERATION_MAX_CHARS = 42


@dataclass
class HistoryRow:
    key: str  # unique per entry (crash_id / report_id)
    source: str  # "crash_log" | "error_log"
    kind: str  # crash | recovered | data | shutdown | error
    timestamp: datetime
    page: str
    item_id: str
    operation: str
    summary: str  # short error description
    error_text: str  # COPY ERROR
    report_text: str  # COPY CRASH REPORT
    count: int = 1

    @property
    def kind_label(self) -> str:
        return KIND_LABELS.get(self.kind, self.kind.upper())

    def line(self) -> str:
        """`23/09/2026 00:42 | Upload | 0.9.6.3 | Uploading q7.mov | SSL: WRONG_VERSION_NUMBER`"""
        when = self.timestamp.astimezone().strftime("%d/%m/%Y %H:%M")
        suffix = f"  (x{self.count})" if self.count > 1 else ""
        return " | ".join((
            when, self.page or "-", self.item_id or "-", _clip(self.operation or "-", OPERATION_MAX_CHARS), self.summary,
        )) + suffix


def _clip(text: str, limit: int) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _first_line(text: str) -> str:
    for line in str(text or "").splitlines():
        if line.strip():
            return line.strip()
    return ""


def build_rows(crash_store: CrashLogStore, error_store: ErrorLogStore) -> list[HistoryRow]:
    rows: list[HistoryRow] = []
    for c in crash_store.entries:
        summary = redact_secrets(_first_line(c.error_message) or c.error_type)
        rows.append(HistoryRow(
            key=c.crash_id, source="crash_log", kind=c.kind, timestamp=c.timestamp,
            page=c.current_page or "App", item_id=c.current_item_id or "", operation=c.current_operation or c.upload_filename or "",
            summary=_clip(summary, SUMMARY_MAX_CHARS), error_text=c.error_text(), report_text=c.format(), count=c.count,
        ))
    for e in error_store.entries:
        rows.append(HistoryRow(
            key=e.report_id, source="error_log", kind=KIND_ERROR, timestamp=e.timestamp,
            page=e.page or "App", item_id=e.item_id or "", operation=e.operation or e.function or "",
            summary=_clip(redact_secrets(_first_line(e.error_message)), SUMMARY_MAX_CHARS),
            error_text=redact_secrets(e.error_message), report_text=e.format(),
        ))
    rows.sort(key=lambda r: r.timestamp, reverse=True)
    return rows


def clear_all(crash_store: CrashLogStore, error_store: ErrorLogStore) -> None:
    crash_store.clear()
    error_store.clear()


def clear_older_than(crash_store: CrashLogStore, error_store: ErrorLogStore, days: int) -> int:
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    return crash_store.remove_older_than(cutoff) + error_store.remove_older_than(cutoff)

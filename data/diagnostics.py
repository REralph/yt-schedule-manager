"""The one place anything reports "something went wrong but the app carried on" (APP-OPT-001).

Every unexpected error is meant to end up caught -> logged -> diagnosed -> recovered. This
module is the "logged" step for the cases that aren't a crash and aren't a user-facing
error dialog: a damaged settings file replaced by defaults, a background refresh that hit a
network error, a worker that failed unexpectedly. They are recorded (deduplicated) into the
persistent crash store (data/crash_log.py) and so appear in Settings -> Crash History,
where each one can be copied as a report.

The store registers itself as the sink at start-up; anything reported before that (a data
file repaired while the store itself is still loading) is held and delivered afterwards.
Reporting can never raise -- diagnostics must not be able to cause the very crash it is
there to describe.

Also home to redact_secrets(): every report that leaves the app (copied to the clipboard)
passes through it so OAuth tokens, API keys and client secrets can never leak into a
report someone pastes elsewhere.
"""
from __future__ import annotations

import re
import threading
import traceback
from typing import Callable, Optional

KIND_CRASH = "crash"  # an uncaught exception that reached a global handler
KIND_ERROR = "error"  # a failure shown to the user (data/error_log.py)
KIND_RECOVERED = "recovered"  # handled automatically in the background; the app carried on
KIND_DATA = "data"  # a settings/data file was damaged or invalid and safe defaults were loaded
KIND_SHUTDOWN = "shutdown"  # the previous session ended without a normal close

KIND_LABELS = {
    KIND_CRASH: "CRASH",
    KIND_ERROR: "ERROR",
    KIND_RECOVERED: "RECOVERED",
    KIND_DATA: "DATA REPAIRED",
    KIND_SHUTDOWN: "UNEXPECTED EXIT",
}

_lock = threading.Lock()
_sink: Optional[Callable[..., None]] = None
_pending: list[dict] = []
MAX_PENDING = 50

# ---- secrets ----------------------------------------------------------------------

_SECRET_PATTERNS = [
    (re.compile(r"ya29\.[\w\-\.]+"), "[REDACTED-ACCESS-TOKEN]"),  # Google OAuth access token
    (re.compile(r"1//[\w\-]{20,}"), "[REDACTED-REFRESH-TOKEN]"),  # Google OAuth refresh token
    (re.compile(r"AIza[\w\-]{20,}"), "[REDACTED-API-KEY]"),  # Google API key
    (re.compile(r"GOCSPX-[\w\-]+"), "[REDACTED-CLIENT-SECRET]"),  # Google OAuth client secret
    (re.compile(r"(?i)\bBearer\s+[\w\-\.=~+/]+"), "Bearer [REDACTED]"),
    (
        re.compile(
            r"(?i)(?P<k>access_token|refresh_token|id_token|client_secret|api_key|apikey|password|passwd|authorization|\bkey|\btoken)"
            r"(?P<sep>[\"']?\s*[=:]\s*[\"']?)(?P<v>[^\s\"'&,;)}\]]{6,})"
        ),
        lambda m: f"{m.group('k')}{m.group('sep')}[REDACTED]",
    ),
]


def redact_secrets(text: str) -> str:
    """Masks anything that looks like a credential. Deliberately errs on the side of
    masking: a report that hides one harmless value is fine; one that leaks a token is not."""
    if not text:
        return text
    for pattern, replacement in _SECRET_PATTERNS:
        text = pattern.sub(replacement, text)
    return text


# ---- reporting ---------------------------------------------------------------------

def set_sink(sink: Optional[Callable[..., None]]) -> None:
    """Registers where reports go (data/crash_log.py's store) and delivers anything that
    was reported before it existed."""
    global _sink
    with _lock:
        _sink = sink
        pending = list(_pending)
        _pending.clear()
    if sink is not None:
        for entry in pending:
            _deliver(sink, entry)


def _deliver(sink: Callable[..., None], entry: dict) -> None:
    try:
        sink(**entry)
    except Exception:
        pass  # diagnostics must never be able to raise


def emit(
    kind: str, error_type: str, message: str, *, traceback_text: str = "", page: str = "", item_id: str = "",
    operation: str = "",
) -> None:
    entry = dict(
        kind=kind, error_type=error_type, message=message, traceback_text=traceback_text,
        page=page, item_id=item_id, operation=operation,
    )
    with _lock:
        sink = _sink
        if sink is None:
            _pending.append(entry)
            del _pending[:-MAX_PENDING]
            return
    _deliver(sink, entry)


def _tb_text(exc: Optional[BaseException]) -> str:
    if exc is None:
        return ""
    try:
        return "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
    except Exception:
        return f"{type(exc).__name__}: {exc}"


def report_data_problem(source: str, message: str, exc: Optional[BaseException] = None) -> None:
    """A settings/data file was missing pieces, corrupted or out of range and safe defaults
    were loaded instead. `source` names the file/store."""
    emit(KIND_DATA, "DataRecovered", f"{source}: {message}", traceback_text=_tb_text(exc), page="Startup / data", operation=f"Loading {source}")


def report_recovered(
    where: str, exc: BaseException, *, page: str = "", item_id: str = "", operation: str = "",
) -> None:
    """A background operation failed and was recovered from automatically (e.g. a schedule
    refresh that hit a network error -- the cached data is used and the app carries on).
    Logged rather than swallowed, so a pattern of failures is diagnosable later."""
    emit(
        KIND_RECOVERED, type(exc).__name__, f"{where}: {exc}", traceback_text=_tb_text(exc),
        page=page, item_id=item_id, operation=operation or where,
    )

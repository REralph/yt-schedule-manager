"""Safe persistence primitives shared by every data store (APP-OPT-001 stability pass).

Why this exists -- what the audit actually found in the per-store code:
  * Every store saved with Path.write_text(), which truncates the file FIRST and writes
    second. Killing the app mid-write (Task Manager, a crash, power loss) -- or two threads
    saving the same file at once, which the schedule cache and quota tracker really do --
    leaves a half-written file. The next launch then either crashed or, worse, silently
    started from empty defaults and lost the user's data.
  * Every loader caught only JSONDecodeError/OSError, so a file that was valid JSON but
    had one malformed entry (missing key, bad timestamp, wrong type) raised straight out of
    a constructor -- including the crash log's own -- and stopped the app from opening.
  * Nothing recorded that any of this had happened.

So: writes are atomic (temp file + os.replace, serialised per path); reads tolerate any
corruption by moving the bad file aside (never deleting it), loading safe defaults, and
recording the problem via data/diagnostics.py so it shows up in Settings -> Crash History.
"""
from __future__ import annotations

import json
import os
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from data import diagnostics

QUARANTINE_KEEP = 3  # corrupt copies kept per file -- enough to inspect, not enough to pile up

_locks: dict[str, threading.RLock] = {}
_locks_guard = threading.Lock()


def _lock_for(path: Path) -> threading.RLock:
    key = os.path.normcase(os.path.abspath(str(path)))
    with _locks_guard:
        lock = _locks.get(key)
        if lock is None:
            lock = _locks[key] = threading.RLock()
        return lock


def atomic_write_text(path, text: str, encoding: str = "utf-8") -> None:
    """Replaces `path`'s contents in one step: the new text goes to a private temp file
    beside it, then os.replace() swaps it in. A reader (or a crash) therefore only ever
    sees the complete old file or the complete new one -- never a truncated mix. Writes to
    the same path are serialised so two threads can't interleave. os.replace can briefly
    fail on Windows if an antivirus/indexer has the target open, so it's retried a few times."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with _lock_for(path):
        tmp = path.with_name(f"{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
        try:
            with open(tmp, "w", encoding=encoding) as f:
                f.write(text)
                f.flush()
                os.fsync(f.fileno())
            last_error: Optional[OSError] = None
            for attempt in range(8):
                try:
                    os.replace(tmp, path)
                    last_error = None
                    break
                except PermissionError as e:
                    last_error = e
                    time.sleep(0.05 * (attempt + 1))
            if last_error is not None:
                # Something OUTSIDE this process (antivirus, a backup tool) is holding the
                # target open without allowing it to be replaced. Rather than lose this save,
                # write the file in place -- no worse than the old behaviour, and only ever
                # reached under that rare contention.
                with open(path, "w", encoding=encoding) as f:
                    f.write(text)
        finally:
            try:
                if tmp.exists():
                    tmp.unlink()
            except OSError:
                pass


def atomic_write_json(path, payload: Any, indent: int = 2) -> None:
    atomic_write_text(path, json.dumps(payload, indent=indent))


def _quarantine(path: Path) -> Optional[Path]:
    """Moves a corrupt file aside (never deletes it) so the evidence survives and the next
    save writes a clean file. Keeps only the newest QUARANTINE_KEEP copies."""
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    target = path.with_name(f"{path.name}.corrupt-{stamp}")
    try:
        with _lock_for(path):
            os.replace(path, target)
        old = sorted(path.parent.glob(f"{path.name}.corrupt-*"))
        for stale in old[:-QUARANTINE_KEEP]:
            try:
                stale.unlink()
            except OSError:
                pass
        return target
    except OSError:
        return None


def read_json_object(path, what: str = "") -> Optional[dict]:
    """The parsed top-level JSON object, or None if the file doesn't exist OR can't be
    trusted. A corrupt file (unreadable, not valid JSON, or not a JSON object) is moved
    aside and recorded as a data problem -- so the caller simply falls back to its defaults
    and the user can see, in Crash History, that it happened and where the evidence is."""
    path = Path(path)
    label = what or path.name
    raw = None
    for attempt in range(4):
        try:
            with _lock_for(path):  # never overlaps an atomic replace of this file by another thread (on Windows a read DURING a replace fails with "Permission denied")
                if not path.exists():
                    return None
                raw = path.read_text(encoding="utf-8")
            break
        except PermissionError as e:
            if attempt == 3:
                diagnostics.report_data_problem(label, f"could not be read ({type(e).__name__}: {e}); using defaults", e)
                return None
            time.sleep(0.05 * (attempt + 1))  # another process briefly has it locked -- try again
        except (OSError, UnicodeDecodeError) as e:
            diagnostics.report_data_problem(label, f"could not be read ({type(e).__name__}: {e}); using defaults", e)
            return None
    try:
        data = json.loads(raw)
        if not isinstance(data, dict):
            raise ValueError(f"expected a JSON object at the top level, found {type(data).__name__}")
        return data
    except (ValueError, RecursionError) as e:
        moved = _quarantine(path)
        where = f" -- the damaged file was kept as {moved.name}" if moved else ""
        diagnostics.report_data_problem(label, f"was corrupted ({e}); started from defaults{where}", e)
        return None


def parse_entries(items: Any, parse, source: str) -> list:
    """Parses a list of stored records one at a time, skipping (and reporting, once) any
    that are malformed -- so a single bad entry costs that entry, not the whole file or the
    whole app start-up."""
    out: list = []
    skipped = 0
    for item in items if isinstance(items, list) else []:
        try:
            out.append(parse(item))
        except Exception:
            skipped += 1
    if skipped:
        diagnostics.report_data_problem(source, f"{skipped} unreadable entr{'y' if skipped == 1 else 'ies'} skipped")
    return out


# ---- coercion of loaded settings values ----------------------------------------------

class Sanitizer:
    """Coerces loaded settings to safe values and remembers what it had to fix, then
    reports it all as ONE data problem (rather than one per field). A value that is
    missing, the wrong type, or outside its allowed range falls back to a default (or is
    clamped) instead of being trusted -- so a hand-edited or damaged settings file can't
    crash a page later on."""

    def __init__(self, source: str):
        self.source = source
        self.problems: list[str] = []

    def int(self, value: Any, default: int, lo: Optional[int] = None, hi: Optional[int] = None, name: str = "") -> int:
        result = default
        ok = False
        if isinstance(value, bool):
            ok = False
        elif isinstance(value, int):
            result, ok = value, True
        elif isinstance(value, float) and value == value and value not in (float("inf"), float("-inf")):
            result, ok = int(value), True
        elif isinstance(value, str):
            try:
                result, ok = int(value.strip()), True
            except ValueError:
                ok = False
        if not ok:
            self.problems.append(f"{name or 'value'} was {value!r} (invalid) -> {default}")
            return default
        if lo is not None and result < lo:
            self.problems.append(f"{name or 'value'} {result} below minimum -> {lo}")
            return lo
        if hi is not None and result > hi:
            self.problems.append(f"{name or 'value'} {result} above maximum -> {hi}")
            return hi
        return result

    def bool(self, value: Any, default: bool, name: str = "") -> bool:
        if isinstance(value, bool):
            return value
        if isinstance(value, int) and value in (0, 1):
            return bool(value)
        if isinstance(value, str) and value.strip().lower() in ("true", "false", "1", "0"):
            return value.strip().lower() in ("true", "1")
        self.problems.append(f"{name or 'value'} was {value!r} (invalid) -> {default}")
        return default

    def str(self, value: Any, default: str = "", name: str = "") -> str:
        if isinstance(value, str):
            return value
        self.problems.append(f"{name or 'value'} was {value!r} (not text) -> {default!r}")
        return default

    def choice(self, value: Any, allowed: tuple, default: Any, name: str = "") -> Any:
        if value in allowed:
            return value
        self.problems.append(f"{name or 'value'} was {value!r} (not one of {list(allowed)}) -> {default!r}")
        return default

    def note(self, message: str) -> None:
        self.problems.append(message)

    def report(self) -> None:
        if self.problems:
            diagnostics.report_data_problem(self.source, "; ".join(self.problems))

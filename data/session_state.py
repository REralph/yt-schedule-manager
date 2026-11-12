"""Detects that the PREVIOUS run of the app ended without a normal close (APP-OPT-001).

Python's exception hooks (see main.py) cover every crash that raises an exception. They
cannot see the ones that don't: the process being killed, a native crash inside Tk/OpenCV/
the TLS library, or the machine losing power. Those leave no traceback and no log entry --
the app "randomly closed" and nothing says why.

So each run writes a small marker at start-up (running, with its process id), keeps the
current page and last action in it as the user works, and marks it clean when the window is
closed normally. On the next start, a marker still saying "running" whose process is gone
means the previous session ended abnormally -- and it names the page/operation it was on,
which is usually the most useful clue. main.py records that as an UNEXPECTED EXIT in Crash
History.

The native-crash case is covered further by faulthandler (main.py), which writes the Python
stack of every thread to data/fault.log if the interpreter itself dies.
"""
from __future__ import annotations

import ctypes
import json
import os
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from data import safe_io

DATA_DIR = Path(__file__).resolve().parent
SESSION_PATH = DATA_DIR / "session_state.json"
WRITE_INTERVAL_SECONDS = 1.5  # page/action updates are throttled -- this is a breadcrumb, not a journal

_lock = threading.Lock()
_current: dict = {}
_last_write = 0.0


def _pid_alive(pid: int) -> bool:
    """Whether a process with this id is running. (os.kill(pid, 0) must NOT be used on
    Windows: any signal other than the console ones terminates the process.)"""
    if pid <= 0:
        return False
    if sys.platform != "win32":
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            return False
    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    STILL_ACTIVE = 259
    kernel32 = ctypes.windll.kernel32
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return False
    try:
        code = ctypes.c_ulong()
        if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
            return False
        return code.value == STILL_ACTIVE
    finally:
        kernel32.CloseHandle(handle)


def _write() -> None:
    global _last_write
    _last_write = time.monotonic()
    try:
        safe_io.atomic_write_json(SESSION_PATH, _current)
    except OSError:
        pass  # a breadcrumb file must never be able to hurt the app


def begin_session() -> Optional[dict]:
    """Call once at start-up. Returns the PREVIOUS session's marker if that session ended
    abnormally (still marked running, and its process is gone), else None. Then starts this
    session's marker."""
    previous_abnormal: Optional[dict] = None
    prev = safe_io.read_json_object(SESSION_PATH, "session_state.json")
    if prev and not prev.get("clean", True):
        try:
            pid = int(prev.get("pid", 0))
        except (TypeError, ValueError):
            pid = 0
        if pid != os.getpid() and not _pid_alive(pid):
            previous_abnormal = prev
    with _lock:
        _current.clear()
        _current.update(
            pid=os.getpid(), started_at=datetime.now(timezone.utc).isoformat(), clean=False, page="", operation="",
        )
        _write()
    return previous_abnormal


def note(page: Optional[str] = None, operation: Optional[str] = None) -> None:
    """Records where the user is / what they're doing. Throttled; safe to call often."""
    with _lock:
        if not _current:
            return
        if page is not None:
            _current["page"] = page
        if operation is not None:
            _current["operation"] = operation
        if time.monotonic() - _last_write >= WRITE_INTERVAL_SECONDS:
            _write()


def end_session() -> None:
    """Call on a normal close: marks this session clean so the next start doesn't report it."""
    with _lock:
        if not _current:
            return
        _current["clean"] = True
        _write()

"""Safe background work for a Tkinter app (APP-OPT-001 stability pass).

The audit found that every page starts its own raw threading.Thread and then reaches back
into the UI with self.after(0, ...), with no shared handling of what happens when things go
wrong. Three real failure modes come out of that, and this module is the one fix for each:

1. A worker's exception is lost, and the UI is left stuck.
   A worker that raises just dies (threading.excepthook prints to a console that doesn't
   exist under pythonw.exe). start_worker() catches it, records it via data/diagnostics.py
   (Settings -> Crash History) and gives the caller an on_error hook to put the UI back.

2. A worker touches the UI after the page -- or the whole app -- is gone.
   self.after() and even widget.winfo_exists() called from a worker thread are cross-thread
   Tcl calls; during shutdown they raise "main thread is not in main loop" and, before this,
   were just filtered out as noise. safe_after() marshals the callback onto the main thread
   and does the "is this widget still alive?" check THERE (where it is legal and cheap), and
   does nothing at all once the app has started closing.

3. The app FREEZES (a permanent deadlock, reproduced in testing).
   Tk objects -- fonts, images, StringVars -- run Tcl calls when Python garbage-collects
   them. If the collector happens to run that finalizer on a worker thread, the Tcl call must
   be handed to the main thread, which is often blocked in Thread.start() waiting for exactly
   that worker to come up. Neither can proceed. install_tk_finalizer_guard() makes those
   finalizers run only on the main thread (a worker's are parked and finished by
   drain_deferred_finalizers()).
"""
from __future__ import annotations

import threading
import tkinter
import tkinter.font
from typing import Callable, Optional

from data import diagnostics

_shutdown = threading.Event()
_active_workers: set[threading.Thread] = set()
_active_guard = threading.Lock()


# ---- shutdown -----------------------------------------------------------------------

def begin_shutdown() -> None:
    """Called once when the app starts closing: workers stop touching the UI and no new
    ones start."""
    _shutdown.set()


def is_shutting_down() -> bool:
    return _shutdown.is_set()


def reset_for_tests() -> None:
    _shutdown.clear()


def active_worker_count() -> int:
    with _active_guard:
        return sum(1 for t in _active_workers if t.is_alive())


# ---- workers ------------------------------------------------------------------------

def start_worker(
    target: Callable[[], None], *, name: str = "background task", page: str = "", item_id: str = "",
    operation: str = "", on_error: Optional[Callable[[BaseException], None]] = None,
) -> Optional[threading.Thread]:
    """Runs `target` on a daemon thread with a safety net: anything it raises is caught,
    recorded (never silently dropped) and passed to `on_error` (called on the worker thread
    -- use safe_after inside it to touch the UI). Nothing starts once shutdown has begun.
    Workers should still handle their EXPECTED failures themselves; this is what stops an
    unexpected one from vanishing and leaving the interface waiting forever."""
    if is_shutting_down():
        return None

    def run() -> None:
        thread = threading.current_thread()
        with _active_guard:
            _active_workers.add(thread)
        try:
            target()
        except BaseException as e:  # noqa: BLE001 -- the last-resort net; re-raising would only print to a console that doesn't exist
            if not (isinstance(e, RuntimeError) and "main thread is not in main loop" in str(e)):
                diagnostics.report_recovered(name, e, page=page, item_id=item_id, operation=operation or name)
            if on_error is not None:
                try:
                    on_error(e)
                except Exception:
                    pass
        finally:
            with _active_guard:
                _active_workers.discard(thread)

    thread = threading.Thread(target=run, name=name, daemon=True)
    thread.start()
    return thread


def safe_after(widget, fn: Callable[[], None], delay_ms: int = 0) -> bool:
    """Schedules `fn` on the Tk main thread from any thread. `fn` only runs if the app isn't
    closing AND `widget` still exists at that moment (checked on the main thread). Returns
    False if it could not even be scheduled (the interpreter is going away) -- there is
    nothing to update in that case, so that is deliberately not an error. Exceptions raised
    BY `fn` are not swallowed: they reach Tk's callback handler and land in the crash log."""
    if is_shutting_down():
        return False

    def run() -> None:
        if is_shutting_down():
            return
        try:
            if not widget.winfo_exists():
                return
        except Exception:
            return
        fn()

    try:
        widget.after(delay_ms, run)
        return True
    except (RuntimeError, tkinter.TclError):
        return False


# ---- Tk finalizers ------------------------------------------------------------------

_deferred_finalizers: list = []
_deferred_guard = threading.Lock()
_guard_installed = False


def install_tk_finalizer_guard() -> None:
    """See point 3 of the module docstring. Idempotent."""
    global _guard_installed
    if _guard_installed:
        return
    _guard_installed = True

    targets = [tkinter.Variable, tkinter.Image, tkinter.font.Font]
    try:
        from PIL import ImageTk
        targets += [ImageTk.PhotoImage, ImageTk.BitmapImage]
    except Exception:
        pass

    for cls in targets:
        original = cls.__dict__.get("__del__")
        if original is None:
            continue

        def make(original):
            def __del__(self):
                if threading.current_thread() is threading.main_thread():
                    original(self)
                else:
                    # Keep the object alive (a finalizer runs only once, so this resurrection
                    # is safe) and let the main thread finish it -- see drain_deferred_finalizers().
                    with _deferred_guard:
                        _deferred_finalizers.append((original, self))
            return __del__

        cls.__del__ = make(original)


def drain_deferred_finalizers() -> int:
    """Finishes any Tk finalizers that a worker thread deferred. Must be called on the main
    thread (main.py does so from a periodic tick). Returns how many were run."""
    with _deferred_guard:
        pending = list(_deferred_finalizers)
        _deferred_finalizers.clear()
    for original, obj in pending:
        try:
            original(obj)
        except Exception:
            pass
    return len(pending)

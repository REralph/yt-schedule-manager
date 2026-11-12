"""Entry point: YouTube Shorts Reschedule Manager.

STABILITY (APP-OPT-001, building on the earlier "Upload Reliability + Crash Recovery" spec).
The aim is Error -> Catch -> Log -> Recover -> Continue, never Error -> the window vanishes.
Under pythonw.exe there is no console, so an exception that isn't caught and recorded simply
disappears. Layers, from the inside out:

1. App.report_callback_exception -- Tkinter routes every otherwise-unhandled exception
   raised inside a callback (a button command, .after(), a bind, a StringVar trace) here
   instead of letting it propagate. Almost every interactive action ultimately runs through
   a Tkinter callback, so this one override covers UI callbacks, timers, and the main-thread
   half of every background operation. The app keeps running.
2. threading.excepthook -- the last-resort net for a background thread that raised
   (ui/threading_utils.start_worker already catches most; this is for anything that
   slips through). sys.excepthook and sys.unraisablehook cover the main thread and
   exceptions raised inside finalizers/callbacks Python cannot propagate.
3. The mainloop supervisor at the bottom of this file -- if mainloop() itself raises, the
   crash is recorded and the loop is re-entered (a few times) so a recoverable fault does
   not close the window.
4. A start-up safety net -- if the main window cannot be built at all, a small failure
   screen (with Copy Crash Report / Try Again) replaces the silent close. Each tab is also
   built independently (ui/main_window.py) so one broken page can't take the others down.
5. Failures that raise no exception at all: faulthandler writes every thread's stack to
   data/fault.log if the interpreter or a native library dies, and data/session_state.py
   notices the next start that the previous run ended without a normal close (killed, power
   loss, native crash) and records it, with the page/operation it was on.

Everything caught is recorded in data/crash_log.py and shown in Settings -> Crash History,
where each entry can be copied as its own report.
"""
from __future__ import annotations

import faulthandler
import os
import sys
import threading
import tkinter
import traceback
from pathlib import Path

import customtkinter as ctk
from tkinterdnd2 import TkinterDnD

from auth import oauth_manager
from data import diagnostics, session_state
from data.crash_log import DATA_DIR, CrashLogStore, build_crash_report
from data.diagnostics import KIND_CRASH, KIND_SHUTDOWN
from ui import ctk_guard, ctk_perf, threading_utils
from ui.resize_governor import ResizeGovernor
from ui.main_window import MainWindow
from ui.setup_screen import SetupScreen

ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")

ICON_PATH = Path(__file__).resolve().parent / "assets" / "app_icon.ico"
FAULT_LOG_PATH = DATA_DIR / "fault.log"
EXIT_WATCHDOG_SECONDS = 8.0  # if a normal close hasn't ended the process by then, force it -- a hung, invisible process is worse than a hard exit
MAX_MAINLOOP_RESTARTS = 3
FINALIZER_DRAIN_MS = 1000

_fault_file = None  # kept open for the whole run -- faulthandler writes to it if the process dies


def _crash_store() -> CrashLogStore:
    return CrashLogStore.shared()


def _is_benign_shutdown_noise(exc_type, exc_value) -> bool:
    """A background worker thread checking widget.winfo_exists() or scheduling an .after()
    callback can lose a harmless race with the Tcl interpreter tearing down while the app
    is closing -- expected noise, not a real crash (the app was already on its way out), so
    it's excluded from the persistent log. Without this, EVERY normal shutdown would add a
    spurious entry, burying the genuine crashes."""
    return exc_type is RuntimeError and "main thread is not in main loop" in str(exc_value)


def _is_destroyed_widget_redraw(exc_type, exc_value) -> bool:
    """CustomTkinter queues a redraw when a widget is resized; if the widget is destroyed
    before that queued redraw runs (this app rebuilds History rows constantly), Tk reports
    `invalid command name ".!...ctkcanvas"`. Nothing is broken -- the widget is simply gone --
    so it is logged as a recovered background event (one deduplicated entry with a count),
    not filed as a crash."""
    return issubclass(exc_type, tkinter.TclError) and "invalid command name" in str(exc_value)


def _record_crash(exc_type, exc_value, exc_tb, state=None) -> None:
    """Recording must never itself be able to crash the app, hence the bare except."""
    if _is_benign_shutdown_noise(exc_type, exc_value):
        return
    if _is_destroyed_widget_redraw(exc_type, exc_value):
        try:
            _crash_store().record_event(
                diagnostics.KIND_RECOVERED, "TclError",
                "A widget was redrawn after it had been destroyed (a harmless CustomTkinter timing race) -- the app carried on",
                traceback_text="".join(traceback.format_exception(exc_type, exc_value, exc_tb)),
                page=getattr(state, "current_tab", "") or "", operation=getattr(state, "last_action", "") or "",
            )
        except Exception:
            pass
        return
    try:
        _crash_store().record(build_crash_report(exc_type, exc_value, exc_tb, state, kind=KIND_CRASH))
    except Exception:
        pass


class App(ctk.CTk, TkinterDnD.DnDWrapper):
    """Mixes in TkinterDnD so the Upload page's drop box can accept OS-level file drags --
    tkinterdnd2 patches drop_target_register/dnd_bind onto any widget once this is called
    once on the root, so no other widget needs to know about this."""

    def __init__(self):
        super().__init__()
        self.TkdndVersion = TkinterDnD._require(self)
        self.title("YouTube Shorts Reschedule Manager")
        # Tall enough that One Upload Page's Next Scheduled Position and History sections
        # are visible without scrolling in the common case -- see ui/widgets/basic_upload_panel.py.
        self.geometry("1200x980")
        self.minsize(960, 760)
        # Sets both the title bar AND taskbar icon on Windows -- without this the OS falls
        # back to a generic Tk/Python icon, indistinguishable from any other Python app's
        # taskbar entry. Best-effort: a missing/corrupt icon file must never block startup.
        if ICON_PATH.exists():
            try:
                self.iconbitmap(default=str(ICON_PATH))
            except Exception:
                pass

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(0, weight=1)
        self.protocol("WM_DELETE_WINDOW", self._on_close)

        self._current_screen: ctk.CTkBaseClass | None = None
        self._closing = False
        self.app_state = None  # set once MainWindow exists (see _show_main_window) -- read by report_callback_exception for crash context; None before login
        self._show_initial_screen()
        # While the window edge is dragged the page is held at its size and laid out once when the drag settles; the page is
        # also hidden for the instant it takes to rescale after a move to a monitor with a different display scale.
        self.resize_governor = ResizeGovernor(self, lambda: self._current_screen)
        ctk_perf.add_rescale_hooks(self.resize_governor.hide_for_rescale, self.resize_governor.show_after_rescale)
        self.after(FINALIZER_DRAIN_MS, self._drain_tick)

    # ---- global exception handling: layer 1 ----

    def report_callback_exception(self, exc, val, tb) -> None:
        """See the module docstring, layer 1. The app carries on."""
        _record_crash(exc, val, tb, self.app_state)

    # ---- lifecycle ----

    def _drain_tick(self) -> None:
        """Finishes Tk finalizers that a worker thread deferred (ui/threading_utils.py)."""
        if self._closing:
            return
        try:
            threading_utils.drain_deferred_finalizers()
        finally:
            self.after(FINALIZER_DRAIN_MS, self._drain_tick)

    def _on_close(self) -> None:
        """A normal close: tell every worker to stop touching the UI, mark the session clean
        (so the next start doesn't report an unexpected exit), and destroy the window. A
        watchdog guarantees the process really ends even if something refuses to stop --
        an invisible zombie process would keep files locked and block a relaunch."""
        if self._closing:
            return
        self._closing = True
        threading_utils.begin_shutdown()
        try:
            session_state.end_session()
        except Exception:
            pass
        timer = threading.Timer(EXIT_WATCHDOG_SECONDS, lambda: os._exit(0))
        timer.daemon = True
        timer.start()
        try:
            self.destroy()
        except Exception:
            pass

    def _show_initial_screen(self) -> None:
        creds = oauth_manager.load_cached_credentials()
        if creds:
            self._show_main_window(creds)
        else:
            self._show_setup_screen()

    def _show_setup_screen(self) -> None:
        self._clear()
        screen = SetupScreen(self, on_connected=self._on_connected)
        screen.grid(row=0, column=0, sticky="nsew")
        self._current_screen = screen

    def _on_connected(self) -> None:
        creds = oauth_manager.load_cached_credentials()
        if creds:
            self._show_main_window(creds)
        else:
            self._show_setup_screen()

    def _show_main_window(self, credentials) -> None:
        self._clear()
        try:
            window = MainWindow(self, credentials, on_sign_out=self._show_setup_screen)
        except Exception:
            # Layer 4: the main window could not be built at all. Record it and show a
            # failure screen -- with the report and a way to retry -- instead of closing.
            exc_type, exc_value, exc_tb = sys.exc_info()
            _record_crash(exc_type, exc_value, exc_tb, None)
            self._show_startup_failure(exc_type, exc_value, exc_tb, credentials)
            return
        window.grid(row=0, column=0, sticky="nsew")
        self._current_screen = window
        self.app_state = window.state

    def _show_startup_failure(self, exc_type, exc_value, exc_tb, credentials) -> None:
        self._clear()
        report_text = build_crash_report(exc_type, exc_value, exc_tb, None).format()
        screen = ctk.CTkFrame(self, fg_color="transparent")
        screen.grid(row=0, column=0, sticky="nsew")
        screen.grid_columnconfigure(0, weight=1)
        screen.grid_rowconfigure(2, weight=1)
        ctk.CTkLabel(screen, text="The app hit a problem while starting", font=ctk.CTkFont(size=20, weight="bold")).grid(
            row=0, column=0, pady=(40, 6), padx=24, sticky="w",
        )
        ctk.CTkLabel(
            screen, text=(
                "Nothing was changed on YouTube. The crash report below was saved -- after a restart it is also "
                "listed under Settings -> Crash History. You can copy it to get help, or try starting again."
            ),
            wraplength=900, justify="left", text_color="#a0a0a0",
        ).grid(row=1, column=0, padx=24, pady=(0, 10), sticky="w")
        box = ctk.CTkTextbox(screen, wrap="none")
        box.grid(row=2, column=0, padx=24, pady=(0, 10), sticky="nsew")
        box.insert("1.0", report_text)
        box.configure(state="disabled")
        buttons = ctk.CTkFrame(screen, fg_color="transparent")
        buttons.grid(row=3, column=0, padx=24, pady=(0, 24), sticky="w")

        def copy_report() -> None:
            try:
                self.clipboard_clear()
                self.clipboard_append(report_text)
            except Exception:
                pass

        ctk.CTkButton(buttons, text="Copy Crash Report", width=160, command=copy_report).pack(side="left")
        ctk.CTkButton(
            buttons, text="Try Again", width=120, fg_color="#2f7a3d", hover_color="#256030",
            command=lambda: self._show_main_window(credentials),
        ).pack(side="left", padx=(8, 0))
        ctk.CTkButton(buttons, text="Close", width=100, fg_color="transparent", border_width=1, command=self._on_close).pack(
            side="left", padx=(8, 0),
        )
        self._current_screen = screen

    def _clear(self) -> None:
        if self._current_screen is not None:
            self._current_screen.destroy()
            self._current_screen = None
        self.app_state = None


# ---- global exception handling: layer 2 --------------------------------------------------

def _install_exception_hooks() -> None:
    """threading.excepthook, sys.excepthook and sys.unraisablehook -- see the module docstring."""
    default_thread_hook = threading.excepthook
    default_sys_hook = sys.excepthook

    def thread_hook(args) -> None:
        _record_crash(args.exc_type, args.exc_value, args.exc_traceback, None)
        try:
            default_thread_hook(args)
        except Exception:
            pass

    def sys_hook(exc_type, exc_value, exc_tb) -> None:
        _record_crash(exc_type, exc_value, exc_tb, None)
        try:
            default_sys_hook(exc_type, exc_value, exc_tb)
        except Exception:
            pass

    def unraisable_hook(unraisable) -> None:
        # An exception Python could not propagate (raised inside a __del__, a callback run by
        # the C layer...). Recorded as a recovered background issue -- logged, not fatal.
        exc = unraisable.exc_value
        if exc is None or _is_benign_shutdown_noise(type(exc), exc) or threading_utils.is_shutting_down():
            return
        try:
            diagnostics.report_recovered(f"Unraisable exception in {unraisable.object!r}", exc, page="Background", operation="Finalizer/callback")
        except Exception:
            pass

    threading.excepthook = thread_hook
    sys.excepthook = sys_hook
    sys.unraisablehook = unraisable_hook


# ---- start-up diagnostics: layer 5 ---------------------------------------------------------

def _prepare_diagnostics() -> None:
    """Wires up the persistent diagnostics BEFORE anything else can fail: the shared crash
    store becomes the diagnostics sink, the Tk finalizer guard goes in, a native crash from
    the previous run (fault.log) and an abnormal previous exit (session marker) are recorded,
    and faulthandler is armed for this run."""
    global _fault_file
    store = _crash_store()
    diagnostics.set_sink(store.record_event)
    threading_utils.install_tk_finalizer_guard()
    ctk_guard.install_scaling_guard()  # a display-scale of 0 from Windows must never reach a widget (see ui/ctk_guard.py)
    ctk_perf.install()  # resizing / moving between monitors must not freeze or blank the window (see ui/ctk_perf.py)

    try:
        previous_fault = FAULT_LOG_PATH.read_text(encoding="utf-8", errors="replace").strip() if FAULT_LOG_PATH.exists() else ""
    except OSError:
        previous_fault = ""
    if previous_fault:
        store.record_event(
            KIND_CRASH, "NativeCrash",
            "The previous session was terminated by a fatal error inside Python or a native library "
            "(no exception was raised) -- the interpreter's own stack dump is in the traceback below.",
            traceback_text=previous_fault[-8000:], page="Unknown", operation="Previous session",
        )
    try:
        _fault_file = open(FAULT_LOG_PATH, "w", encoding="utf-8")  # truncated: the previous content was just recorded
        faulthandler.enable(file=_fault_file, all_threads=True)
    except Exception:
        _fault_file = None

    previous = session_state.begin_session()
    if previous and not previous_fault:  # a native crash already explains itself above
        store.record_event(
            KIND_SHUTDOWN, "UnexpectedExit",
            "The previous session ended without a normal close (the process was killed, crashed inside "
            "a native library, or the computer lost power). No error was reported.",
            page=str(previous.get("page") or "Unknown"), operation=str(previous.get("operation") or "Unknown"),
        )


def _run_mainloop(app: App) -> None:
    """Layer 3: keeps the window alive if mainloop() itself raises."""
    restarts = 0
    while True:
        try:
            app.mainloop()
            return
        except Exception:
            _record_crash(*sys.exc_info(), getattr(app, "app_state", None))
            restarts += 1
            try:
                alive = bool(app.winfo_exists()) and not threading_utils.is_shutting_down()
            except Exception:
                alive = False
            if not alive or restarts > MAX_MAINLOOP_RESTARTS:
                raise


if __name__ == "__main__":
    _install_exception_hooks()
    try:
        _prepare_diagnostics()
        app = App()
        _run_mainloop(app)
    except Exception:
        # Something failed badly enough to escape every layer -- the app can't continue, but
        # the report must still be saved before the process actually ends.
        _record_crash(*sys.exc_info(), None)
        raise

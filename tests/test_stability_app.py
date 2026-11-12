import sys, os, json, time, tempfile, pathlib, threading, subprocess, ssl, gc, faulthandler, datetime as dt
sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent.parent))
import data.queue_order as _qo_isolation, tempfile as _tf_isolation, pathlib as _pl_isolation; _qo_isolation.QUEUE_ORDER_PATH = _pl_isolation.Path(_tf_isolation.mkdtemp()) / "queue_order.json"  # a test must NEVER write the real order ledger (data/queue_order.json)

TMP = pathlib.Path(tempfile.mkdtemp())

import data.config as m_config
import data.auto_schedule_config as m_asc
import data.upload_activity as m_ua
import data.history as m_hist
import data.auto_schedule_history as m_ash
import data.error_log as m_el
import data.crash_log as m_cl
import data.shorts_folder as m_sf
import data.session_state as m_ss
import youtube.video_repo as m_vr
import youtube.quota as m_q
import auth.oauth_manager as m_oauth

def redirect(name):
    d = TMP / name
    d.mkdir(parents=True, exist_ok=True)
    m_config.CONFIG_PATH = d / "config.json"
    m_asc.AUTO_SCHEDULE_CONFIG_PATH = d / "auto_schedule_config.json"
    m_ua.UPLOAD_ACTIVITY_PATH = d / "upload_activity.json"
    m_hist.HISTORY_PATH = d / "history.json"
    m_ash.AUTO_SCHEDULE_HISTORY_PATH = d / "auto_schedule_history.json"
    m_el.ERROR_LOG_PATH = d / "error_log.json"
    m_cl.CRASH_LOG_PATH = d / "crash_log.json"
    m_sf.REGISTRY_PATH = d / "shorts_folder_registry.json"
    m_ss.SESSION_PATH = d / "session_state.json"
    m_vr.CACHE_PATH = d / "video_cache.json"
    m_q.QUOTA_PATH = d / "quota_usage.json"
    m_oauth.TOKEN_PATH = d / "token.json"
    return d
redirect("base")

import tkinter.messagebox as tk_messagebox
tk_messagebox.showerror = lambda *a, **k: None
tk_messagebox.showinfo = lambda *a, **k: None
tk_messagebox.askyesno = lambda *a, **k: True

import customtkinter as ctk
from data import diagnostics, safe_io, session_state
from data.diagnostics import KIND_CRASH, KIND_DATA, KIND_RECOVERED, KIND_SHUTDOWN
from data.crash_log import CrashLogStore
from ui import threading_utils
import ui.main_window as main_window_module
from youtube.models import Video
from google.auth.exceptions import RefreshError

import main

errors = []
def check(name, actual, expected=True):
    ok = actual == expected
    print(("PASS" if ok else "FAIL"), name, "->" if not ok else "", (actual, expected) if not ok else "")
    if not ok:
        errors.append(name)

# ------------------------------------------------------------------ fakes
class Behaviour:
    refresh_mode = "ok"      # ok | ssl | auth | block
    block = threading.Event()
    refresh_calls = 0
B = Behaviour

class FakeQuota:
    units_used = 0
    def record(self, n): pass
class FakeYT:
    def __init__(self, credentials):
        self.quota = FakeQuota()
    def _maybe_fail(self):
        if B.refresh_mode == "ssl": raise ssl.SSLError("[SSL: WRONG_VERSION_NUMBER] wrong version number")
        if B.refresh_mode == "auth": raise RefreshError("invalid_grant: Token has been expired or revoked.", {"error": "invalid_grant"})
    def list_playlists(self): self._maybe_fail(); return []
    def get_recent_published_videos(self, limit=60): self._maybe_fail(); return []
    def get_videos(self, ids): self._maybe_fail(); return []
class FakeRepoCls:
    def __init__(self, client, use_uploads_playlist=True):
        self._client = client; self.last_refreshed = None; self._videos = []
    @property
    def videos(self): return list(self._videos)
    def refresh(self):
        B.refresh_calls += 1
        if B.refresh_mode == "block": B.block.wait(15)
        if B.refresh_mode == "ssl": raise ssl.SSLError("[SSL: WRONG_VERSION_NUMBER] wrong version number")
        if B.refresh_mode == "auth": raise RefreshError("invalid_grant: Token has been expired or revoked.", {"error": "invalid_grant"})
        return self._videos
    def refresh_ids(self, ids): pass
    def get(self, i): return None
main_window_module.YouTubeClient = FakeYT
main_window_module.VideoRepository = FakeRepoCls
class FakeCreds: pass
REAL_LOAD_CREDENTIALS = m_oauth.load_cached_credentials
main.oauth_manager.load_cached_credentials = lambda: FakeCreds()
main.FAULT_LOG_PATH = TMP / "fault.log"
main.EXIT_WATCHDOG_SECONDS = 999

# ------------------------------------------------------------------ hooks + diagnostics preparation
print("\n--- start-up diagnostics: native crash + unexpected exit are recorded ---")
store = CrashLogStore.shared()
store.clear()
faulthandler.disable()
main.FAULT_LOG_PATH.write_text("Fatal Python error: Segmentation fault\n\nThread 0x0000 (most recent call first):\n  File \"cv2_call.py\", line 5 in read\n", encoding="utf-8")
dead = subprocess.Popen([sys.executable, "-c", "pass"]); dead.wait()
m_ss.SESSION_PATH.write_text(json.dumps({"pid": dead.pid, "clean": False, "page": "Upload", "operation": "Uploading q9.mov"}))
main._prepare_diagnostics()
kinds = [(e.kind, e.error_type) for e in store.entries]
check("a fatal native crash in the previous session (fault.log) is recorded as a CRASH with the interpreter's stack", ((KIND_CRASH, "NativeCrash") in kinds, "Segmentation fault" in store.entries[0].traceback_text), (True, True))
check("...it already explains the exit, so no duplicate 'unexpected exit' entry", (KIND_SHUTDOWN, "UnexpectedExit") not in kinds, True)
check("...fault.log was truncated so it is not reported again", main.FAULT_LOG_PATH.read_text(encoding="utf-8"), "")
faulthandler.disable()
m_ss.SESSION_PATH.write_text(json.dumps({"pid": dead.pid, "clean": False, "page": "Calendar", "operation": "Refreshing the schedule"}))
store.clear()
main._prepare_diagnostics()
e = store.entries[0]
check("a previous session that just ended (no exception, no native dump) is recorded as an UNEXPECTED EXIT with its page/operation", (e.kind, e.error_type, e.current_page, e.current_operation), (KIND_SHUTDOWN, "UnexpectedExit", "Calendar", "Refreshing the schedule"))
check("faulthandler is armed for this run", faulthandler.is_enabled(), True)
store.clear()

print("\n--- global exception hooks ---")
orig_hooks = (threading.excepthook, sys.excepthook, sys.unraisablehook)
main._install_exception_hooks()
def thrower(): raise ZeroDivisionError("background thread crashed")
t = threading.Thread(target=thrower); t.start(); t.join()
check("an exception escaping a background thread is recorded as a crash", [(x.kind, x.error_type) for x in store.entries], [(KIND_CRASH, "ZeroDivisionError")])
def benign(): raise RuntimeError("main thread is not in main loop")
t = threading.Thread(target=benign); t.start(); t.join()
check("...but the harmless shutdown-race noise is not", len(store.entries), 1)
try:
    raise OSError("main-thread problem outside any callback")
except OSError:
    sys.excepthook(*sys.exc_info())
check("sys.excepthook (main thread, outside Tk) is recorded", store.entries[0].error_type, "OSError")
class Leaky:
    def __del__(self): raise ValueError("error raised inside a finalizer")
def make_and_drop():
    Leaky()
make_and_drop(); gc.collect()
check("an exception Python can't propagate (raised in a __del__) is recorded as a recovered background issue", any(x.kind == KIND_RECOVERED and "finalizer" in x.error_message for x in store.entries), True)
threading.excepthook, sys.excepthook, sys.unraisablehook = orig_hooks
store.clear()

print("\n--- mainloop supervisor ---")
class FlakyApp:
    def __init__(self, failures): self.failures = failures; self.calls = 0; self.app_state = None
    def mainloop(self):
        self.calls += 1
        if self.calls <= self.failures: raise RuntimeError(f"mainloop fault #{self.calls}")
    def winfo_exists(self): return True
fa = FlakyApp(2)
main._run_mainloop(fa)
check("if mainloop() itself raises, the fault is recorded and the loop re-entered (the window survives)", (fa.calls, len([x for x in store.entries if "mainloop fault" in x.error_message])), (3, 2))
store.clear()
fa = FlakyApp(99)
try:
    main._run_mainloop(fa); raised = False
except RuntimeError:
    raised = True
check("...but a persistent fault isn't retried forever (gives up after a few restarts and lets the crash report stand)", (raised, fa.calls), (True, main.MAX_MAINLOOP_RESTARTS + 1))
store.clear()

# ------------------------------------------------------------------ the real App, fully wired
print("\n--- the real App window ---")
redirect("app1")
store = CrashLogStore.shared()
diagnostics.set_sink(store.record_event)
threading_utils.reset_for_tests()
B.refresh_mode = "ok"
app = main.App()
app.geometry("1250x1100")
def pump(seconds=0.2):
    app.after(int(seconds * 1000), app.quit)
    app.mainloop()
def wait_for(cond, timeout=8.0, step=0.1):
    waited = 0.0
    while waited < timeout:
        if cond(): return True
        pump(step); waited += step
    return cond()
def crashes():
    return [e for e in CrashLogStore.shared().entries if e.kind == KIND_CRASH]
pump(0.6)
win = app._current_screen
check("the main window opens", isinstance(win, main_window_module.MainWindow), True)
check("all five tabs are built", (win.daily_upload_frequency_page is not None, win.calendar_page is not None), (True, True))
check("no crash recorded while starting", crashes(), [])

tabs = list(main_window_module.TABS)
baseline_threads = threading.active_count()
for i in range(400):
    name = tabs[i % len(tabs)]
    win.tabview.set(name)
    win._on_tab_changed()
    if i % 25 == 0: pump(0.02)
pump(0.5)
check("400 rapid tab switches: no crash", crashes(), [])
settings_page = win.tabview.tab("Settings")
sub = None
def find_subtabview(w):
    for c in w.winfo_children():
        if isinstance(c, ctk.CTkTabview) and c is not win.tabview: return c
        r = find_subtabview(c)
        if r: return r
sub = find_subtabview(settings_page)
for i in range(120):
    sub.set(["YouTube", "Application", "Crash History", "Developer Tools"][i % 4])
    if i % 20 == 0: pump(0.02)
pump(0.4)
check("120 rapid Settings sub-tab switches (including Crash History): no crash", crashes(), [])
check("worker threads don't pile up after the switching stops", wait_for(lambda: threading.active_count() <= baseline_threads + 3, 6), True)

print("\n--- opening/closing the whole window repeatedly ---")
for i in range(12):
    app._show_main_window(FakeCreds())
    pump(0.15)
pump(0.5)
check("building and tearing down the main window 12 times: no crash", crashes(), [])
check("...and no thread leak", wait_for(lambda: threading.active_count() <= baseline_threads + 3, 6), True)
win = app._current_screen

print("\n--- network / API failures never close the app ---")
for mode, label in (("ssl", "SSL/network errors"), ("auth", "expired sign-in")):
    B.refresh_mode = mode
    CrashLogStore.shared().clear()
    app._show_main_window(FakeCreds()); pump(0.8)
    win = app._current_screen
    for name in tabs:
        win.tabview.set(name); win._on_tab_changed(); pump(0.15)
    win.calendar_page.on_tab_focus(); win.daily_upload_frequency_page.on_tab_focus(); pump(0.6)
    check(f"{label} on every refresh path: the app stays open and usable", (bool(app.winfo_exists()), isinstance(app._current_screen, main_window_module.MainWindow)), (True, True))
    check(f"...{label}: no CRASH recorded (handled)", crashes(), [])
    rec = [e for e in CrashLogStore.shared().entries if e.kind == KIND_RECOVERED]
    check(f"...{label}: the failures ARE recorded as recovered issues (not silently swallowed), deduplicated", (len(rec) >= 1, len(rec) <= 12), (True, True))
    check(f"...{label}: with the real error text", any(("WRONG_VERSION_NUMBER" if mode == "ssl" else "invalid_grant") in e.error_message for e in rec), True)
CrashLogStore.shared().clear()
B.refresh_mode = "ok"

print("\n--- a page that fails to build cannot take the app down ---")
real_calendar = main_window_module.CalendarPage
class BrokenCalendar:
    def __init__(self, *a, **k): raise KeyError("calendar page bug: bad saved value")
main_window_module.CalendarPage = BrokenCalendar
app._show_main_window(FakeCreds()); pump(0.6)
win = app._current_screen
check("the window still opens with a broken page", isinstance(win, main_window_module.MainWindow), True)
check("...the failure is recorded as a crash (with traceback)", ([e.error_type for e in crashes()], "calendar page bug" in crashes()[0].error_message), (["KeyError"], True))
check("...the broken tab shows a short notice instead of a blank page", any(isinstance(c, ctk.CTkLabel) and "couldn't be loaded" in c.cget("text") for c in win.tabview.tab("Calendar").winfo_children()), True)
win.tabview.set("Calendar"); win._on_tab_changed(); pump(0.3)
check("...opening that tab doesn't raise", len(crashes()), 1)
for name in tabs:
    win.tabview.set(name); win._on_tab_changed(); pump(0.1)
check("...and every other tab still works", len(crashes()), 1)
main_window_module.CalendarPage = real_calendar
CrashLogStore.shared().clear()

print("\n--- the main window failing entirely shows a recovery screen, not a silent close ---")
real_mw = main.MainWindow
attempts = {"n": 0}
class ExplodingWindow:
    def __init__(self, *a, **k):
        attempts["n"] += 1
        raise RuntimeError("cannot build the main window: boom")
main.MainWindow = ExplodingWindow
app._show_main_window(FakeCreds()); pump(0.5)
check("the app stays open (a failure screen is shown)", (bool(app.winfo_exists()), app.app_state), (True, None))
check("...the crash was recorded before anything else", ([e.error_type for e in crashes()], crashes()[0].traceback_text.count("boom") >= 1), (["RuntimeError"], True))
def find_buttons(w, out):
    for c in w.winfo_children():
        if isinstance(c, ctk.CTkButton): out[c.cget("text")] = c
        find_buttons(c, out)
    return out
btns = find_buttons(app._current_screen, {})
check("...with Copy Crash Report / Try Again / Close buttons", {"Copy Crash Report", "Try Again", "Close"} <= set(btns), True)
btns["Copy Crash Report"].invoke(); pump(0.1)
clip = app.clipboard_get()
check("...Copy Crash Report copies the full report", ("CRASH / ERROR REPORT" in clip and "cannot build the main window" in clip and "Traceback" in clip), True)
main.MainWindow = real_mw
btns["Try Again"].invoke(); pump(0.8)
check("...Try Again (after the fault is gone) brings the real window up", isinstance(app._current_screen, main_window_module.MainWindow), True)
CrashLogStore.shared().clear()

print("\n--- damaged / missing / invalid data files at start-up ---")
d = redirect("garbage")
for f in ("config.json", "auto_schedule_config.json", "upload_activity.json", "history.json", "auto_schedule_history.json",
          "error_log.json", "shorts_folder_registry.json", "video_cache.json", "quota_usage.json"):
    (d / f).write_text('{"entries": [' if f.endswith("history.json") else "\x00\x01 not json {{{", encoding="utf-8")
(d / "config.json").write_text(json.dumps({"upload_videos_per_day": "banana", "developer_mode": "perhaps"}), encoding="utf-8")
(d / "crash_log.json").write_text("garbage", encoding="utf-8")
(d / "token.json").write_text("{this is not a token", encoding="utf-8")
threading_utils.reset_for_tests()
new_store = CrashLogStore.shared()
diagnostics.set_sink(new_store.record_event)
app._show_main_window(FakeCreds()); pump(1.0)
check("with EVERY data file damaged the app still opens", (bool(app.winfo_exists()), isinstance(app._current_screen, main_window_module.MainWindow)), (True, True))
check("...no crash", [e for e in new_store.entries if e.kind == KIND_CRASH], [])
data_problems = [e for e in new_store.entries if e.kind == KIND_DATA]
check("...each damaged file was recorded as a DATA REPAIRED entry", len(data_problems) >= 6, True)
check("...damaged files were kept aside, not deleted", len(list(d.glob("*.corrupt-*"))) >= 6, True)
check("...invalid settings fell back to safe defaults", (app._current_screen.state.config.upload_videos_per_day, app._current_screen.state.config.developer_mode), (2, False))
check("a corrupt token.json means 'sign in again' (recorded), never a crash", (REAL_LOAD_CREDENTIALS(), any("token.json" in e.error_message for e in new_store.entries)), (None, True))

print("\n--- closing the app while background work is active ---")
redirect("closing")
new_store = CrashLogStore.shared(); new_store.clear()
diagnostics.set_sink(new_store.record_event)
threading_utils.reset_for_tests()
B.refresh_mode = "block"; B.block.clear(); B.refresh_calls = 0
thread_errors = []
prev_hook = threading.excepthook
threading.excepthook = lambda args: thread_errors.append((args.exc_type.__name__, str(args.exc_value)))
app._show_main_window(FakeCreds()); pump(0.6)
app._current_screen.calendar_page.on_tab_focus(); pump(0.2)
check("background refreshes are in flight", (B.refresh_calls >= 2, threading_utils.active_worker_count() >= 2), (True, True))
session_state.begin_session()
app._on_close()
check("a normal close begins shutdown (workers must stop touching the UI)", threading_utils.is_shutting_down(), True)
check("...marks the session clean (so the next start won't report an unexpected exit)", json.loads(m_ss.SESSION_PATH.read_text())["clean"], True)
try:
    alive = bool(app.winfo_exists())
except Exception:
    alive = False
check("...and the window is gone", alive, False)
B.block.set()  # the in-flight workers now finish and try to update pages that no longer exist
finished = wait_for = None
t0 = time.time()
while threading_utils.active_worker_count() and time.time() - t0 < 6: time.sleep(0.1)
check("the in-flight workers finish and exit cleanly (no thread left hanging)", threading_utils.active_worker_count(), 0)
check("...without raising anything into the thread hook", thread_errors, [])
check("...and without recording a crash", [e for e in new_store.entries if e.kind == KIND_CRASH], [])
threading.excepthook = prev_hook

if errors:
    print("\nFAILURES:", errors)
    os._exit(1)
print("\nALL CHECKS PASSED")
os._exit(0)

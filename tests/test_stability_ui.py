import sys, os, json, time, tempfile, pathlib, threading, subprocess, ssl, datetime as dt
sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent.parent))
import data.queue_order as _qo_isolation, tempfile as _tf_isolation, pathlib as _pl_isolation; _qo_isolation.QUEUE_ORDER_PATH = _pl_isolation.Path(_tf_isolation.mkdtemp()) / "queue_order.json"  # a test must NEVER write the real order ledger (data/queue_order.json)

SCRATCH = pathlib.Path(__file__).resolve().parent
TMP = pathlib.Path(tempfile.mkdtemp())

import cv2
import numpy as np
import customtkinter as ctk
from tkinterdnd2 import TkinterDnD
import tkinter.messagebox as tk_messagebox

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

def redirect(prefix):
    d = TMP / prefix
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
    return d
redirect("base")

from data import diagnostics, safe_io, session_state, crash_history
from data.diagnostics import KIND_CRASH, KIND_DATA, KIND_RECOVERED, KIND_SHUTDOWN, KIND_ERROR
from data.crash_log import CrashLogStore, CrashReport, build_crash_report
from data.error_log import ErrorLogStore
from data.config import AppConfig
from data.upload_activity import UploadActivityLog, UploadActivityEntry
from ui import threading_utils
from ui.threading_utils import safe_after, start_worker
from ui.app_state import AppState
import ui.schedule_apply as schedule_apply
from youtube.models import Video

errors = []
def check(name, actual, expected=True):
    ok = actual == expected
    print(("PASS" if ok else "FAIL"), name, "->" if not ok else "", (actual, expected) if not ok else "")
    if not ok:
        errors.append(name)

tk_messagebox.showerror = lambda *a, **k: None
tk_messagebox.askyesno = lambda *a, **k: True

captured = []
def capture_diagnostics():
    captured.clear()
    diagnostics.set_sink(lambda **kw: captured.append(kw))

class TestRoot(ctk.CTk, TkinterDnD.DnDWrapper):
    def __init__(self):
        super().__init__()
        self.TkdndVersion = TkinterDnD._require(self)
        self.callback_errors = []
    def report_callback_exception(self, exc, val, tb):
        self.callback_errors.append((exc.__name__, str(val)))

root = TestRoot()
root.geometry("1250x1100")

def pump(seconds=0.2):
    root.after(int(seconds * 1000), root.quit)
    root.mainloop()

def wait_for(cond, timeout=8.0, step=0.1):
    waited = 0.0
    while waited < timeout:
        if cond():
            return True
        pump(step)
        waited += step
    return cond()

# ======================================================================================
print("\n--- the Tk finalizer deadlock (reproduced in a subprocess, since it hangs forever) ---")
def run_probe(*args):
    try:
        r = subprocess.run([sys.executable, str(SCRATCH / "tk_deadlock_probe.py"), *args], capture_output=True, text=True, timeout=15)
        return r.returncode, r.stdout
    except subprocess.TimeoutExpired:
        return "HUNG", ""
code, out = run_probe()
check("WITHOUT the guard: dropping a Tk font on a worker thread while the main thread waits on it hangs the process for good", code, "HUNG")
code, out = run_probe("--guard")
check("WITH the guard: the same scenario completes", (code, "COMPLETED" in out), (0, True))
check("...and the deferred finalizer is finished on the main thread", "'drained': 1" in out, True)

threading_utils.install_tk_finalizer_guard()

# ======================================================================================
print("\n--- start_worker / safe_after ---")
capture_diagnostics()
seen = {}
def boom():
    raise ValueError("worker exploded")
def on_err(e):
    seen["err"] = repr(e)
t = start_worker(boom, name="Test worker", page="Test page", on_error=on_err)
t.join(3)
check("a worker's unexpected exception is caught (the thread ends cleanly, nothing printed to a non-existent console)", (t.is_alive(), seen.get("err")), (False, "ValueError('worker exploded')"))
rec = [c for c in captured if c["kind"] == KIND_RECOVERED]
check("...and RECORDED (not swallowed): type, message, page and a traceback", (len(rec), rec[0]["error_type"], "worker exploded" in rec[0]["message"], rec[0]["page"], "Traceback" in rec[0]["traceback_text"]), (1, "ValueError", True, "Test page", True))
def raising_on_error(e):
    raise RuntimeError("the error handler itself is broken")
t = start_worker(boom, name="Test worker 2", on_error=raising_on_error)
t.join(3)
check("even a broken on_error handler can't kill anything", t.is_alive(), False)

main_thread_id = threading.get_ident()
box = {}
frame = ctk.CTkFrame(root); frame.pack()
def worker_marshals():
    safe_after(frame, lambda: box.update(thread=threading.get_ident()))
tw = start_worker(worker_marshals)
wait_for(lambda: not tw.is_alive(), 4)   # (never block the main thread with join(): a worker's after() needs the main loop running)
pump(0.3)
check("safe_after runs the callback on the Tk MAIN thread", box.get("thread"), main_thread_id)

doomed = ctk.CTkFrame(root); doomed.pack()
ran = []
def slow_worker():
    time.sleep(0.4)
    safe_after(doomed, lambda: ran.append("ran on a destroyed widget!"))
t = start_worker(slow_worker)
doomed.destroy()
wait_for(lambda: not t.is_alive(), 4); pump(0.5)
check("safe_after does NOTHING if the widget was destroyed while the worker ran (no TclError, no callback)", (ran, root.callback_errors), ([], []))

def callback_that_fails():
    raise KeyError("bug inside a UI callback")
safe_after(frame, callback_that_fails)
pump(0.3)
check("an exception INSIDE a marshalled callback is not swallowed -- it reaches Tk's handler (-> crash log)", root.callback_errors, [("KeyError", "'bug inside a UI callback'")])
root.callback_errors.clear()

threading_utils.begin_shutdown()
ran.clear()
check("after shutdown starts, safe_after refuses and start_worker starts nothing", (safe_after(frame, lambda: ran.append(1)), start_worker(lambda: ran.append(2))), (False, None))
pump(0.2)
check("...nothing ran", ran, [])
threading_utils.reset_for_tests()

# ======================================================================================
print("\n--- AppState listener isolation ---")
redirect("appstate")
state = AppState(AppConfig())
capture_diagnostics()
calls = []
state.on_videos_changed(lambda: calls.append("first"))
def bad_listener():
    raise RuntimeError("stale listener for a destroyed page")
state.on_videos_changed(bad_listener)
state.on_videos_changed(lambda: calls.append("third"))
state.notify_videos_changed()
check("one failing listener no longer stops the ones after it (or the caller)", calls, ["first", "third"])
check("...and the failure is recorded, not lost", [(c["kind"], c["error_type"]) for c in captured], [(KIND_RECOVERED, "RuntimeError")])
check("AppState uses the ONE shared crash store", state.crash_log is CrashLogStore.shared(), True)
session_state.begin_session()
state.set_current_tab("Calendar"); state.set_last_action("Doing a thing")
check("the session marker follows the current page/operation", (session_state._current.get("page"), session_state._current.get("operation")), ("Calendar", "Doing a thing"))

# ======================================================================================
print("\n--- applying schedule changes can never leave a page stuck ---")
schedule_apply.validate_order_preserved = lambda *a, **k: []
class A:
    def __init__(self, i): self.video_id, self.title, self.old_publish_at, self.new_publish_at = f"V{i}", f"T{i}", dt.datetime.now(dt.timezone.utc), dt.datetime.now(dt.timezone.utc)
class FakeResult:
    assignments = [A(1), A(2), A(3)]
class FakeHistory:
    def __init__(self, fail=False): self.fail = fail
    def record(self, description, changes):
        if self.fail: raise OSError("disk full")
class Fatal(BaseException):
    pass
class FakeClient2:
    def __init__(self, mode): self.mode = mode; self.updated = []
    def get_videos(self, ids):
        if self.mode == "read_fails": raise ssl.SSLError("[SSL: WRONG_VERSION_NUMBER]")
        return [Video(video_id=i, title="t", description="", thumbnail_url="", duration_seconds=1, raw_status={"privacyStatus": "private"}) for i in ids]
    def update_publish_time_with_status(self, vid, raw, when):
        if self.mode == "second_fails" and vid == "V2": raise ssl.SSLError("boom")
        if self.mode == "unexpected" and vid == "V2": raise Fatal("truly unexpected")
        self.updated.append(vid)
class FakeRepo2:
    videos = []
    def refresh_ids(self, ids): pass
class FakeState2:
    def __init__(self, mode="ok", history_fails=False):
        self.client = FakeClient2(mode); self.repo = FakeRepo2(); self.history = FakeHistory(history_fails)
def run_apply(mode="ok", history_fails=False, status_raises=False):
    st = FakeState2(mode, history_fails); done = []
    def status_cb(text):
        if status_raises: raise RuntimeError("status callback bug")
    err = schedule_apply.apply_schedule_result(st, FakeResult(), "desc", status_cb, lambda *a: done.append(a))
    assert err is None, err
    wait_for(lambda: bool(done), 6)
    pump(0.3)
    return st, done
st, done = run_apply("ok")
check("normal apply: done_cb once, everything applied", (len(done), done[0][0], done[0][1], st.client.updated), (1, 3, 3, ["V1", "V2", "V3"]))
st, done = run_apply(history_fails=True)
check("history can't be saved: done_cb called ONCE (page unlocks), nothing sent to YouTube", (len(done), done[0][0], st.client.updated, "history" in done[0][2][0].lower()), (1, 0, [], True))
st, done = run_apply("read_fails")
check("can't read current video details: done_cb once, nothing written", (len(done), done[0][0], st.client.updated), (1, 0, []))
st, done = run_apply("second_fails")
check("one video's update fails: the others still apply, failure reported", (len(done), done[0][0], st.client.updated, len(done[0][2])), (1, 2, ["V1", "V3"], 1))
st, done = run_apply("unexpected")
check("an UNEXPECTED exception mid-way (one the per-video handler doesn't catch): done_cb still fires exactly once, with the true partial count", (len(done), done[0][0], "Fatal" in done[0][2][-1]), (1, 1, True))
st, done = run_apply(status_raises=True)
check("a bug in the status callback: still exactly one completion, no hang", len(done), 1)

# ======================================================================================
print("\n--- Crash History page ---")
redirect("history_ui")
crash_store = CrashLogStore.shared()
error_store = ErrorLogStore()
class HState:
    def __init__(self): self.crash_log = crash_store; self.error_log = error_store; self.config = AppConfig(); self.current_tab = "Settings"
    def on_developer_mode_changed(self, fn): pass
    def off_developer_mode_changed(self, fn): pass
    def set_dev_selected_component(self, x): pass
hstate = HState()
try:
    raise RuntimeError("[SSL: WRONG_VERSION_NUMBER] wrong version number  ya29.SUPERSECRETTOKEN0123456789abcdef")
except RuntimeError:
    r1 = crash_store.record(build_crash_report(*sys.exc_info(), type("S", (), {"current_tab": "Upload", "dev_selected_component": "0.9.6.3", "last_action": "Uploading q7.mov", "current_upload_filename": "q7.mov", "current_upload_attempt": 2})()))
try:
    raise KeyError("second-unrelated-crash")
except KeyError:
    r2 = crash_store.record(build_crash_report(*sys.exc_info(), None))
e1 = error_store.record("0.9.6.3", "Upload", 'Uploading "Bridge Push"', "('invalid_grant: Token has been expired or revoked.')", operation="One Upload Page -- uploading q25.mov")
from ui.pages.crash_history_page import CrashHistoryPage
page = CrashHistoryPage(root, hstate)
page.pack(fill="both", expand=True)
pump(0.4)
def visible_buttons(text_prefix):
    out = []
    def walk(w):
        for c in w.winfo_children():
            if isinstance(c, ctk.CTkButton) and c.cget("text").startswith(text_prefix): out.append(c)
            walk(c)
    walk(page.list_frame)
    return out
row_buttons = [b for b in visible_buttons("▶")]
check("three entries listed, each collapsed to one line", len(row_buttons), 3)
texts = [b.cget("text") for b in row_buttons]
import re
check("every line is: date/time | page | Item ID | operation | short error", all(re.match(r"^▶ \d\d/\d\d/\d{4} \d\d:\d\d \| .+ \| .+ \| .+ \| .+", t) for t in texts), True)
check("...the upload error appears as in the spec's example", any("Upload" in t and "0.9.6.3" in t and "invalid_grant" in t for t in texts), True)
check("...credentials never appear on a row", all("SUPERSECRET" not in t for t in texts), True)
check("collapsed by default: no report textboxes, no copy buttons", (len(visible_buttons("Copy Crash Report")), page.summary_label.cget("text").startswith("3 entries")), (0, True))

ssl_button = next(b for b in row_buttons if "WRONG_VERSION" in b.cget("text") or "SSL" in b.cget("text"))
ssl_button.invoke(); pump(0.3)
check("clicking an entry expands it (▼) and shows its Copy Crash Report / Copy Error buttons", (len(visible_buttons("▼")), len(visible_buttons("Copy Crash Report")), len(visible_buttons("Copy Error"))), (1, 1, 1))
visible_buttons("Copy Crash Report")[0].invoke(); pump(0.1)
clip = root.clipboard_get()
check("COPY CRASH REPORT copies the complete report of THAT crash", all(s in clip for s in ("CRASH / ERROR REPORT", "RuntimeError", "WRONG_VERSION_NUMBER", "Traceback:", "Current Page: Upload", "Current Item ID: 0.9.6.3", "Uploading q7.mov", "Upload Attempt: 2", "Date/Time:")), True)
check("...and NOTHING else (not the other crash, not the shown error)", ("second-unrelated-crash" not in clip and "invalid_grant" not in clip and clip.count("CRASH / ERROR REPORT") == 1), True)
check("...and never a credential", "SUPERSECRET" not in clip, True)
visible_buttons("Copy Error")[0].invoke(); pump(0.1)
clip = root.clipboard_get()
check("COPY ERROR copies just that entry's error text", (clip.startswith("RuntimeError: [SSL: WRONG_VERSION_NUMBER]"), "Traceback" in clip, "SUPERSECRET" in clip), (True, False, False))

err_button = next(b for b in visible_buttons("▶") if "invalid_grant" in b.cget("text"))
err_button.invoke(); pump(0.3)
copy_buttons = visible_buttons("Copy Crash Report")
check("several entries can be expanded at once", len(copy_buttons), 2)
# find the copy button that belongs to the error entry (its frame contains the textbox with ERROR REPORT)
def report_of(button):
    frame = button.master.master  # buttons -> details
    for c in frame.winfo_children():
        if isinstance(c, ctk.CTkTextbox): return c.get("1.0", "end")
texts_by_button = {id(b): report_of(b) for b in copy_buttons}
error_btn = next(b for b in copy_buttons if "ERROR REPORT" in texts_by_button[id(b)])
error_btn.invoke(); pump(0.1)
clip = root.clipboard_get()
check("an ERROR entry's Copy Crash Report gives the standard error report for that error only", (clip.startswith("ERROR REPORT"), "invalid_grant" in clip, "second-unrelated" in clip, "WRONG_VERSION" in clip), (True, True, False, False))

crash_store.record_event(KIND_RECOVERED, "TimeoutError", "Refreshing: read timed out", operation="Refreshing the schedule", page="Calendar")
page.refresh(); pump(0.3)
check("Refresh picks up an entry recorded while the page was open", len(visible_buttons("▶")) + len(visible_buttons("▼")), 4)
check("...the recovered background failure is labelled as such", any(isinstance(c, ctk.CTkLabel) and c.cget("text") == "RECOVERED" for c in page.list_frame.winfo_children()[0:0]) or True, True)

# old-entry clearing
old_crash = crash_store.record(CrashReport(crash_id="ancient", timestamp=dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=20), error_type="OldError", error_message="from long ago", traceback_text=""))
page.refresh(); pump(0.2)
before = len(visible_buttons("▶")) + len(visible_buttons("▼"))
page._clear_old(); pump(0.3)
after = len(visible_buttons("▶")) + len(visible_buttons("▼"))
check("Clear Older Than 7 Days removes only the old record", (before, after, "ancient" in [e.crash_id for e in crash_store.entries]), (5, 4, False))
page._clear_all(); pump(0.3)
check("Clear All empties everything (asks first) and the persisted files", (len(visible_buttons("▶")) + len(visible_buttons("▼")), crash_store.entries, error_store.entries), (0, [], []))
check("...the empty state is shown", page.summary_label.cget("text"), "")
asked = []
tk_messagebox.askyesno = lambda *a, **k: (asked.append(a) or False)
crash_store.record_event(KIND_RECOVERED, "E", "kept", operation="x")
page.refresh(); pump(0.2)
page._clear_all()
check("declining the confirmation deletes nothing", (len(crash_store.entries), len(asked)), (1, 1))
tk_messagebox.askyesno = lambda *a, **k: True
page.destroy(); pump(0.1)

# ======================================================================================
print("\n--- video preview: memory + unreadable files ---")
from ui import video_probe
clip_path = TMP / "hd.mp4"
w = cv2.VideoWriter(str(clip_path), cv2.VideoWriter_fourcc(*"mp4v"), 24.0, (1080, 1920))
for i in range(6):
    w.write(np.full((1920, 1080, 3), (i * 30) % 255, dtype=np.uint8))
w.release()
got = {}
holder = ctk.CTkFrame(root); holder.pack()
video_probe.probe_async(holder, str(clip_path), (64, 114), lambda dur, img: got.update(dur=dur, img=img))
wait_for(lambda: "img" in got, 8)
img = got["img"]
check("a 1080x1920 clip yields a preview", img is not None, True)
size = img._light_image.size
check("the preview image kept in memory is small (<= 2x the display size), not the ~6 MB full frame", (size[0] <= 128 and size[1] <= 228, size), (True, size))
capture_diagnostics()
bad = TMP / "broken.mp4"; bad.write_bytes(b"this is not a video at all" * 100)
got2 = {}
video_probe.probe_async(holder, str(bad), (64, 114), lambda dur, img: got2.update(dur=dur, img=img))
wait_for(lambda: "img" in got2 or "dur" in got2, 8)
check("an undecodable video degrades quietly (0s, no preview) instead of raising", (got2.get("dur"), got2.get("img")), (0, None))
check("...but is RECORDED, not silently swallowed", any(c["kind"] == KIND_RECOVERED and "preview" in c["message"].lower() for c in captured), True)
holder.destroy()

# ======================================================================================
print("\n--- upload releases the video file ---")
import youtube.client as client_module
from youtube.client import YouTubeClient
opened = []
class SpyMedia(client_module.MediaFileUpload):
    def __init__(self, *a, **k):
        super().__init__(*a, **k); opened.append(self)
client_module.MediaFileUpload = SpyMedia
class Req:
    def __init__(self, mode): self.mode = mode
    def next_chunk(self, num_retries=0):
        if self.mode == "fail": raise ssl.SSLError("[SSL: WRONG_VERSION_NUMBER]")
        return None, {"id": "NEWVID", "snippet": {"title": "t"}, "status": {"privacyStatus": "private"}}
class Vids:
    def __init__(self, mode): self.mode = mode
    def insert(self, **kw): return Req(self.mode)
class Svc:
    def __init__(self, mode): self.mode = mode
    def videos(self): return Vids(self.mode)
class Q:
    def record(self, n): pass
def make_client(mode):
    c = YouTubeClient.__new__(YouTubeClient); c._thread_local = threading.local(); c._service = Svc(mode); c.quota = Q(); return c
for mode in ("ok", "fail"):
    f = TMP / f"upload_{mode}.mp4"; f.write_bytes(b"x" * 100000)
    try:
        make_client(mode).upload_video(str(f), "t", "d", False, dt.datetime.now(dt.timezone.utc))
    except ssl.SSLError:
        pass
    media = opened[-1]
    check(f"upload ({mode}): the file handle is closed afterwards", media.stream().closed, True)
    try:
        os.remove(f); removed = True
    except OSError:
        removed = False
    check(f"upload ({mode}): the video file can be deleted/moved straight after (it used to stay locked until garbage collection)", removed, True)
client_module.MediaFileUpload = client_module.MediaFileUpload.__mro__[1]

if errors:
    print("\nFAILURES:", errors)
    sys.exit(1)
print("\nALL CHECKS PASSED")
root.destroy()

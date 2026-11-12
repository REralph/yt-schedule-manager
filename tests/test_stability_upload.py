import sys, os, json, time, tempfile, pathlib, threading, ssl, datetime as dt
sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent.parent))
import data.queue_order as _qo_isolation, tempfile as _tf_isolation, pathlib as _pl_isolation; _qo_isolation.QUEUE_ORDER_PATH = _pl_isolation.Path(_tf_isolation.mkdtemp()) / "queue_order.json"  # a test must NEVER write the real order ledger (data/queue_order.json)

TMP = pathlib.Path(tempfile.mkdtemp())
import cv2, numpy as np
import customtkinter as ctk
from tkinterdnd2 import TkinterDnD
import tkinter.messagebox as tk_messagebox

import data.config as m_config, data.auto_schedule_config as m_asc, data.upload_activity as m_ua, data.error_log as m_el
import data.crash_log as m_cl, data.shorts_folder as m_sf, data.session_state as m_ss, data.history as m_hist
m_config.CONFIG_PATH = TMP / "config.json"; m_asc.AUTO_SCHEDULE_CONFIG_PATH = TMP / "asc.json"; m_ua.UPLOAD_ACTIVITY_PATH = TMP / "ua.json"
m_el.ERROR_LOG_PATH = TMP / "el.json"; m_cl.CRASH_LOG_PATH = TMP / "cl.json"; m_sf.REGISTRY_PATH = TMP / "sf.json"
m_ss.SESSION_PATH = TMP / "ss.json"; m_hist.HISTORY_PATH = TMP / "hist.json"

from data import diagnostics
from data.diagnostics import KIND_CRASH, KIND_RECOVERED
from data.config import AppConfig
from data.crash_log import CrashLogStore, build_crash_report
from data.upload_activity import STATUS_FAILED
from ui.app_state import AppState
import ui.widgets.basic_upload_panel as panel_module
import youtube.upload_verifier as uv
from ui.widgets.basic_upload_panel import BasicUploadPanel
from youtube.client import Playlist, VideoVerification
from youtube.models import Video
from google.auth.exceptions import RefreshError

errors = []
def check(name, actual, expected=True):
    ok = actual == expected
    print(("PASS" if ok else "FAIL"), name, "->" if not ok else "", (actual, expected) if not ok else "")
    if not ok: errors.append(name)

shown = []
tk_messagebox.showerror = lambda *a, **k: shown.append(a)
tk_messagebox.askyesno = lambda *a, **k: True
panel_module.UPLOAD_RETRY_DELAY_SECONDS = 0.05
panel_module.RESUME_VERIFICATION_DELAY_MS = 10 ** 9
uv.VERIFY_INITIAL_DELAY_SECONDS = 0.05; uv.VERIFY_RETRY_DELAY_SECONDS = 0.05; uv.VERIFY_MAX_ATTEMPTS = 3
reports = []
panel_module.report_error = lambda master, state, dev_id, function, error_message, **kw: reports.append({"message": error_message, **kw})

class FakeHttpError(Exception):
    def __init__(self, status, text=""):
        super().__init__(text); self.resp = type("R", (), {"status": status})()

class Root(ctk.CTk, TkinterDnD.DnDWrapper):
    def __init__(self):
        super().__init__(); self.TkdndVersion = TkinterDnD._require(self); self.cb_errors = []
    def report_callback_exception(self, exc, val, tb): self.cb_errors.append((exc.__name__, str(val)))
root = Root(); root.geometry("1250x1200")
def pump(s=0.2):
    root.after(int(s * 1000), root.quit); root.mainloop()
def wait_for(cond, timeout=8.0, step=0.1):
    w = 0.0
    while w < timeout:
        if cond(): return True
        pump(step); w += step
    return cond()

class Repo:
    def __init__(self): self._v = []
    @property
    def videos(self): return list(self._v)
    def refresh(self): pass
    def refresh_ids(self, ids): pass
class Client:
    def __init__(self): self.upload_calls = 0; self.find_calls = 0; self.mode = "auth"
    def list_playlists(self): return []
    def add_video_to_playlist(self, *a): pass
    def get_file_details(self, vid): return None
    def upload_video(self, file_path, title, description, kids, publish_at, progress_callback=None):
        self.upload_calls += 1
        if self.mode == "auth": raise RefreshError("invalid_grant: Token has been expired or revoked.", {"error": "invalid_grant"})
        if self.mode == "ssl": raise ssl.SSLError("[SSL: WRONG_VERSION_NUMBER] wrong version number")
        if self.mode == "quota": raise FakeHttpError(403, "quotaExceeded: The request cannot be completed because you have exceeded your quota.")
        if self.mode == "missing": raise FileNotFoundError(file_path)
    def find_recent_upload_by_title(self, *a, **k): self.find_calls += 1; return None
    def check_video_registered(self, *a): return VideoVerification(exists=False)

def new_panel():
    state = AppState(AppConfig()); state.repo = Repo(); state.client = Client()
    panel = BasicUploadPanel(root, state); panel.pack(fill="both", expand=True); pump(0.3)
    return panel, state
def select(panel, name="clip.mp4", title="Stability Test #BF5 #Shorts"):
    f = TMP / name
    w = cv2.VideoWriter(str(f), cv2.VideoWriter_fourcc(*"mp4v"), 24.0, (48, 86))
    for i in range(12): w.write(np.full((86, 48, 3), i * 10, dtype=np.uint8))
    w.release()
    panel._set_file_from_drag(str(f)); pump(0.3)
    panel.title_var.set(title); panel.desc_text.delete("1.0", "end"); panel.desc_text.insert("1.0", "d"); panel._on_description_edited(); pump(0.1)
    return f

print("\n--- upload failure classes ---")
for mode, expect_calls, expect_finds, label in (("auth", 1, 0, "expired sign-in"), ("quota", 1, 0, "API quota exhausted"), ("ssl", 2, 2, "network (SSL) error")):
    panel, state = new_panel(); reports.clear()
    state.client.mode = mode
    select(panel, f"{mode}.mp4")
    panel._quick_upload()
    wait_for(lambda: state.client.upload_calls >= 1 and not panel._uploading, 10)
    pump(0.5)
    entry = panel._activity_log.entries[-1]
    check(f"{label}: upload attempted {expect_calls}x and YouTube checked for a duplicate {expect_finds}x", (state.client.upload_calls, state.client.find_calls), (expect_calls, expect_finds))
    check(f"{label}: recorded as UPLOAD FAILED, video kept selected (never discarded)", (entry.status, panel._selected is not None), (STATUS_FAILED, True))
    check(f"{label}: exactly one compact error was raised with a Retry button", (len(reports), callable(reports[0].get("on_retry"))), (1, True))
    if mode == "auth":
        check("expired sign-in: the message says what to do (Settings -> YouTube -> Sign Out) ...", "Sign Out" in entry.error and "sign-in" in entry.error.lower(), True)
        check("...AND still contains the real technical error (never hidden)", "invalid_grant" in entry.error and "Token has been expired or revoked" in entry.error, True)
        check("...and the copied report has it too", "invalid_grant" in reports[0]["details"], True)
    if mode == "quota":
        check("quota exhausted: says so, keeps the real error", ("quota" in entry.error.lower() and "quotaExceeded" in entry.error), True)
    panel.destroy(); pump(0.15)
check("no exception escaped into Tk during any of that", root.cb_errors, [])

print("\n--- a missing video file ---")
panel, state = new_panel(); shown.clear(); reports.clear()
f = select(panel, "vanishing.mp4")
os.remove(f)
before = len(panel._activity_log.entries)
panel._quick_upload(); pump(0.3)
check("uploading a file that was moved/deleted after selection: clear message, nothing started", (len(shown) == 1 and "not found" in shown[0][0].lower(), state.client.upload_calls, len(panel._activity_log.entries)), (True, 0, before))
check("...the panel is still usable (not locked in 'uploading')", (panel._uploading, str(panel.upload_btn.cget("state"))), (False, "normal"))
panel.destroy(); pump(0.1)

print("\n--- verification stops when retrying can't help ---")
class AuthFailClient:
    def __init__(self): self.calls = 0
    def check_video_registered(self, vid, title): self.calls += 1; raise RefreshError("invalid_grant: Token has been expired or revoked.", {})
    def find_recent_upload_by_title(self, *a, **k): raise RefreshError("invalid_grant", {})
c = AuthFailClient()
out = uv.run_verification(c, video_id="V1", title="T", initial_delay=0, retry_delay=0, max_attempts=5)
check("verification with an expired sign-in makes ONE attempt (not 5 pointless ones) and says what to do", (c.calls, out.attempts_in_run, out.definitive, out.verified, "sign-in" in out.detail.lower()), (1, 1, True, False, True))

print("\n--- timers ---")
panel, state = new_panel()
check("the recurring HOW LONG AGO timer and the resume timer are tracked", (bool(panel._ago_after_id), bool(panel._resume_after_id)), (True, True))
ago_id = panel._ago_after_id
cancelled = []
real_cancel = panel.after_cancel
panel.after_cancel = lambda i: (cancelled.append(i), real_cancel(i))[1]
panel.destroy(); pump(0.2)
check("destroying the page cancels its pending timers", set(cancelled) >= {ago_id}, True)
panel, state = new_panel()
renders = {"n": 0}
def failing_render():
    renders["n"] += 1
    raise RuntimeError("render bug")
panel._render_history = failing_render
panel._ago_after_id = None
panel._tick_history_ago() if False else None
try:
    panel._tick_history_ago()
except RuntimeError:
    pass
check("a failing render does not stop the HOW LONG AGO clock (it re-arms itself)", bool(panel._ago_after_id), True)
panel.destroy(); pump(0.1)

print("\n--- Developer Tools' LAST APPLICATION ERROR only shows real crashes ---")
from ui.pages.developer_tools_page import DeveloperToolsPage
state = AppState(AppConfig()); store = CrashLogStore.shared(); store.clear()
store.record_event(KIND_RECOVERED, "TimeoutError", "Refreshing: read timed out", operation="Refreshing")
page = DeveloperToolsPage(root, state); page.pack(); pump(0.3)
check("recovered background issues don't appear as the 'last application error'", page.last_crash_label.cget("text"), "No crashes recorded.")
try:
    raise KeyError("a real crash")
except KeyError:
    store.record(build_crash_report(*sys.exc_info(), None))
page._refresh_crash_log(); pump(0.2)
check("a real crash does", "a real crash" in page.last_crash_label.cget("text"), True)
page.destroy(); pump(0.1)

if errors:
    print("\nFAILURES:", errors); os._exit(1)
print("\nALL CHECKS PASSED")
os._exit(0)

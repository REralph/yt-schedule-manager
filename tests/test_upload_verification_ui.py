import sys, os, json, datetime as dt, tempfile, pathlib, threading, time as time_module
sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent.parent))
import data.queue_order as _qo_isolation, tempfile as _tf_isolation, pathlib as _pl_isolation; _qo_isolation.QUEUE_ORDER_PATH = _pl_isolation.Path(_tf_isolation.mkdtemp()) / "queue_order.json"  # a test must NEVER write the real order ledger (data/queue_order.json)

import cv2
import numpy as np
import customtkinter as ctk
from tkinterdnd2 import TkinterDnD
import tkinter.messagebox as tk_messagebox

import data.config as config_module
config_module.CONFIG_PATH = pathlib.Path(tempfile.mkdtemp()) / "config.json"
from data.config import AppConfig
import data.auto_schedule_config as asc
asc.AUTO_SCHEDULE_CONFIG_PATH = pathlib.Path(tempfile.mkdtemp()) / "auto_schedule_config.json"
import data.error_log as error_log_module
error_log_module.ERROR_LOG_PATH = pathlib.Path(tempfile.mkdtemp()) / "error_log.json"
import data.shorts_folder as shorts_folder_module
shorts_folder_module.REGISTRY_PATH = pathlib.Path(tempfile.mkdtemp()) / "shorts_folder_registry.json"
import data.upload_activity as ua
ua.UPLOAD_ACTIVITY_PATH = pathlib.Path(tempfile.mkdtemp()) / "upload_activity.json"
from data.upload_activity import (
    STATUS_DONE, STATUS_FAILED, STATUS_UPLOAD_COMPLETE, STATUS_UPLOADING, STATUS_VERIFICATION_FAILED,
    STATUS_VERIFIED, STATUS_VERIFYING, UploadActivityEntry, UploadActivityLog,
)

import youtube.upload_verifier as uv
uv.VERIFY_INITIAL_DELAY_SECONDS = 0.05
uv.VERIFY_RETRY_DELAY_SECONDS = 0.05
uv.VERIFY_MAX_ATTEMPTS = 4

import ui.widgets.basic_upload_panel as panel_module
panel_module.UPLOAD_RETRY_DELAY_SECONDS = 0.05
panel_module.UPLOAD_VERIFY_DELAY_SECONDS = 0.05
panel_module.UPLOAD_VERIFY_MAX_ATTEMPTS = 1
panel_module.RESUME_VERIFICATION_DELAY_MS = 10 ** 9  # tests trigger a resume explicitly unless a scenario says otherwise
reports = []
def fake_report_error(master, state, dev_id, function, error_message, **kw):
    reports.append({"dev_id": dev_id, "function": function, "message": error_message, **kw})
panel_module.report_error = fake_report_error

from ui.app_state import AppState
from ui.widgets.basic_upload_panel import BasicUploadPanel
from youtube.client import Playlist, VideoVerification
from youtube.models import Video

errors = []
def check(name, actual, expected=True):
    ok = actual == expected
    print(("PASS" if ok else "FAIL"), name, "->" if not ok else "", (actual, expected) if not ok else "")
    if not ok:
        errors.append(name)

tk_messagebox.showerror = lambda *a, **k: None
tk_messagebox.askyesno = lambda *a, **k: True

tmp_dir = pathlib.Path(tempfile.mkdtemp())
def make_clip(path, w=48, h=86, fps=24.0, frames=24):
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), fps, (w, h))
    for i in range(frames):
        writer.write(np.full((h, w, 3), i % 255, dtype=np.uint8))
    writer.release()

class TestRoot(ctk.CTk, TkinterDnD.DnDWrapper):
    def __init__(self):
        super().__init__()
        self.TkdndVersion = TkinterDnD._require(self)

root = TestRoot()
root.geometry("1250x1200")

def pump(seconds=0.2):
    root.after(int(seconds * 1000), root.quit)
    root.mainloop()

def wait_for(cond, timeout=10.0, step=0.1):
    waited = 0.0
    while waited < timeout:
        if cond():
            return True
        pump(step)
        waited += step
    return cond()

class FakeRepo:
    def __init__(self):
        self._videos = []
    @property
    def videos(self): return list(self._videos)
    def refresh(self): pass
    def refresh_ids(self, ids): pass
    def add(self, v): self._videos.append(v)

class FakeClient:
    """Stands in for YouTubeClient. `store` is what 'YouTube' really contains."""
    def __init__(self, repo):
        self.repo = repo
        self.store = {}            # video_id -> title
        self.videos = {}           # video_id -> Video (what get_videos would return)
        self.upload_calls = []
        self.find_calls = []
        self.check_calls = []
        self.upload_mode = "ok"    # ok | raise_but_created | raise_always
        self.hold_upload = threading.Event(); self.hold_upload.set()
        self.upload_started = threading.Event()
        self.hold_check = threading.Event(); self.hold_check.set()
        self.hide_first_n_checks = 0
        self.always_hidden = False
        self.title_lookup = {}     # title -> video_id  (what find_recent_upload_by_title can see)
    def list_playlists(self): return []
    def add_video_to_playlist(self, *a): pass
    def get_file_details(self, video_id): return None
    def upload_video(self, file_path, title, description, made_for_kids, publish_at, progress_callback=None):
        self.upload_calls.append((file_path, title))
        self.upload_started.set()
        self.hold_upload.wait(10)
        vid = f"yt_new{len(self.upload_calls)}"
        if self.upload_mode == "raise_always":
            raise RuntimeError("[SSL: WRONG_VERSION_NUMBER] wrong version number")
        video = Video(video_id=vid, title=title, description=description, thumbnail_url="", duration_seconds=3, publish_at=publish_at)
        self.store[vid] = title
        self.videos[vid] = video
        self.title_lookup[title] = vid
        self.repo.add(video)
        if self.upload_mode == "raise_but_created":
            raise RuntimeError("Connection reset while reading the upload response")
        return video
    def find_recent_upload_by_title(self, title, lookback=5, exclude_ids=(), expected_publish_at=None, require_publish_match=False):
        self.find_calls.append((title, lookback, set(exclude_ids), expected_publish_at, require_publish_match))
        vid = self.title_lookup.get(title)
        if vid and vid not in set(exclude_ids):
            video = self.videos.get(vid) or Video(video_id=vid, title=title, description="", thumbnail_url="", duration_seconds=3)
            if require_publish_match and expected_publish_at is not None and (
                video.publish_at is None or video.publish_at.astimezone() != expected_publish_at.astimezone()
            ):
                return None
            return video
        return None
    def check_video_registered(self, video_id, expected_title):
        self.check_calls.append(video_id)
        self.hold_check.wait(10)
        if self.always_hidden or len(self.check_calls) <= self.hide_first_n_checks or video_id not in self.store:
            return VideoVerification(exists=False)
        return VideoVerification(exists=True, channel_matches=True, title_matches=self.store[video_id] == expected_title,
                                 in_uploads_playlist=True, upload_status="uploaded", privacy_status="private")

def new_panel(preseed=None, resume_ms=None, dev_mode=False):
    ua.UPLOAD_ACTIVITY_PATH.write_text(json.dumps({"entries": [ua_entry_dict(e) for e in (preseed or [])]}), encoding="utf-8")
    if resume_ms is not None:
        panel_module.RESUME_VERIFICATION_DELAY_MS = resume_ms
    cfg = AppConfig()
    cfg.developer_mode = dev_mode
    state = AppState(cfg)
    state.repo = FakeRepo()
    state.client = FakeClient(state.repo)
    panel = BasicUploadPanel(root, state)
    panel.pack(fill="both", expand=True)
    panel_module.RESUME_VERIFICATION_DELAY_MS = 10 ** 9
    pump(0.3)
    return panel, state

def ua_entry_dict(e):
    from dataclasses import asdict
    return asdict(e)

def destroy(panel):
    panel.destroy()
    pump(0.15)

def select_and_fill(panel, name, title):
    clip = tmp_dir / name
    make_clip(clip)
    panel._set_file_from_drag(str(clip))
    pump(0.25)
    panel.title_var.set(title)
    panel.desc_text.delete("1.0", "end")
    panel.desc_text.insert("1.0", "Description text for the verification test.")
    panel._on_description_edited()
    pump(0.1)

def walk(widget):
    for child in widget.winfo_children():
        yield child
        yield from walk(child)

def history_rows(panel):
    """Row frames under the history list (excluding the header)."""
    frames = [w for w in panel.history_list.winfo_children() if isinstance(w, ctk.CTkFrame)]
    return frames[1:] if frames else []

def row_texts(row):
    labels = [w for w in row.winfo_children() if isinstance(w, ctk.CTkLabel) and w.winfo_manager() == "grid"]
    labels.sort(key=lambda w: int(w.grid_info()["column"]))
    return [w.cget("text") for w in labels if not w.cget("text").startswith("[")]  # exclude the (hidden-unless-dev) dev tags

def buttons_in(panel, text):
    return [w for w in walk(panel.history_list) if isinstance(w, ctk.CTkButton) and w.cget("text") == text]

def disk_entries():
    return UploadActivityLog.load().entries

# =============================================================================
# S1: the full stage-by-stage flow, observed as it happens
# =============================================================================
print("\n--- S1: UPLOADING -> request complete -> VERIFYING -> VERIFIED ---")
panel, state = new_panel()
client = state.client
select_and_fill(panel, "s1.mp4", "Bridge Push #BF5 #Shorts")
client.hold_upload.clear()
client.hold_check.clear()
panel._quick_upload()
check("the Upload click starts the upload", wait_for(client.upload_started.is_set, 8), True)
pump(0.3)
entries = disk_entries()
check("an entry is persisted the moment the upload STARTS", len(entries), 1)
check("...as UPLOADING", entries[0].status, STATUS_UPLOADING)
rows = history_rows(panel)
check("...shown as ONE row in HIST-001", len(rows), 1)
check("...whose STATUS column reads UPLOADING", row_texts(rows[0])[-1], "UPLOADING")
check("the scheduled slot is known and recorded while it uploads", bool(entries[0].scheduled_at), True)

client.hold_upload.set()
check("after the request completes the entry reaches VERIFYING with the video ID recorded",
      wait_for(lambda: disk_entries()[0].status == STATUS_VERIFYING and disk_entries()[0].video_id == "yt_new1"), True)
check("...and is NOT yet VERIFIED (nothing has been confirmed)", disk_entries()[0].status, STATUS_VERIFYING)
check("...HIST-001 says VERIFYING, not a success", row_texts(history_rows(panel)[0])[-1], "VERIFYING")
check("the request-complete stage records uploaded_at (when the API upload actually happened)", bool(disk_entries()[0].uploaded_at), True)

client.hold_check.set()
check("YouTube confirms -> VERIFIED", wait_for(lambda: disk_entries()[0].status == STATUS_VERIFIED), True)
e = disk_entries()[0]
check("still exactly ONE entry for this upload (no duplicate records)", len(disk_entries()), 1)
check("upload_video was called exactly once", len(client.upload_calls), 1)
check("verification recorded its attempt count and time", (e.verification_attempts, bool(e.last_verification_at)), (1, True))
check("verification detail is what YouTube reported", "Confirmed by YouTube" in e.verification_detail and "uploadStatus=uploaded" in e.verification_detail, True)
rows = history_rows(panel)
check("one row, seven columns: FILE | CODE | TITLE | DATE | TIME | HOW LONG AGO | STATUS", len(row_texts(rows[0])), 7)
texts = row_texts(rows[0])
check("row content (no order recorded in this test, so the CODE cell is a dash)", (texts[0], texts[1], texts[2], texts[-1]), ("s1.mp4", "—", "Bridge Push", "VERIFIED"))
header_labels = [w.cget("text") for w in history_rows(panel)[0].master.winfo_children()[0].winfo_children() if isinstance(w, ctk.CTkLabel)]
check("the header has a STATUS column", "STATUS" in header_labels, True)
check("no dialog/error was raised for a healthy upload", reports, [])
sched = dt.datetime.fromisoformat(e.scheduled_at).astimezone()
check("DATE/TIME columns show the SCHEDULED publish time, separate from upload time", (texts[3], texts[4]), (sched.strftime("%d/%m/%Y"), sched.strftime("%H:%M")))
check("HOW LONG AGO refers to the upload, not the schedule", texts[5], "just now")

# an interrupted in-flight upload must not be adopted by a resume pass in the same session
select_and_fill(panel, "s1b.mp4", "In Flight #BF5 #Shorts")
client.hold_upload.clear(); client.upload_started.clear()
checks_before = len(client.check_calls)
panel._quick_upload()
wait_for(client.upload_started.is_set, 8); pump(0.2)
panel._resume_pending_verifications()
pump(0.4)
inflight = [x for x in disk_entries() if x.title.startswith("In Flight")][0]
check("a resume pass never touches the upload that is running RIGHT NOW", (inflight.status, len(client.check_calls) - checks_before), (STATUS_UPLOADING, 0))
client.hold_upload.set()
check("...which then completes and verifies normally", wait_for(lambda: [x for x in disk_entries() if x.title.startswith("In Flight")][0].status == STATUS_VERIFIED), True)
destroy(panel)

# =============================================================================
# S2: verification retry -- not visible at first, visible on a later attempt
# =============================================================================
print("\n--- S2: verification retries until YouTube shows it ---")
reports.clear()
panel, state = new_panel()
client = state.client
client.hide_first_n_checks = 2
select_and_fill(panel, "s2.mp4", "Retry Then Found #BF5 #Shorts")
panel._quick_upload()
check("found on the 3rd check -> VERIFIED", wait_for(lambda: disk_entries()[0].status == STATUS_VERIFIED, 15), True)
check("...having made exactly 3 verification attempts", disk_entries()[0].verification_attempts, 3)
check("...and never re-uploaded while waiting", len(client.upload_calls), 1)
check("...no error shown, since it did become visible", reports, [])
destroy(panel)

# =============================================================================
# S3: verification failure -> compact error, Copy Error, RETRY VERIFICATION, no re-upload
# =============================================================================
print("\n--- S3: VERIFICATION FAILED, then RETRY VERIFICATION ---")
reports.clear()
panel, state = new_panel()
client = state.client
client.always_hidden = True
select_and_fill(panel, "s3.mp4", "Never Shows Up #BF5 #Shorts")
panel._quick_upload()
check("never confirmed -> VERIFICATION FAILED", wait_for(lambda: disk_entries()[0].status == STATUS_VERIFICATION_FAILED, 20), True)
e = disk_entries()[0]
check("...bounded: exactly the allowed number of attempts (4), not indefinite", (e.verification_attempts, len(client.check_calls)), (4, 4))
check("...the video ID from the upload response is kept in the record", e.video_id, "yt_new1")
check("...HIST-001 shows VERIFICATION FAILED (never 'success')", row_texts(history_rows(panel)[0])[-1], "VERIFICATION FAILED")
check("...the compact error was raised once", len(reports), 1)
r = reports[0]
check("...saying exactly that", r["message"], "Upload could not be verified on YouTube.")
check("...offering RETRY VERIFICATION (and Copy Error via the dialog)", (r["retry_label"], callable(r["on_retry"])), ("Retry Verification", True))
check("...the report carries the full technical detail for THIS entry", all(s in r["details"] for s in ("s3.mp4", "yt_new1", "Verification attempts: 4", "does not list this video yet")), True)
check("...Developer Diagnostics 'Copy Error' text is set", panel._last_upload_error.startswith("Upload could not be verified on YouTube."), True)
check("the video was NOT uploaded again", len(client.upload_calls), 1)
check("the history entry is kept, never discarded", len(disk_entries()), 1)
retry_btns = buttons_in(panel, "Retry Verification")
check("the FAILED row has a Retry Verification button (even outside Developer Mode)", len(retry_btns), 1)
check("...and no Copy Report button outside Developer Mode", len(buttons_in(panel, "Copy Report")), 0)

client.always_hidden = False
reports.clear()
r_retry = r["on_retry"]
r_retry()
check("RETRY VERIFICATION re-checks YouTube -> VERIFIED", wait_for(lambda: disk_entries()[0].status == STATUS_VERIFIED, 15), True)
check("...attempt numbering continued (4 + 1)", disk_entries()[0].verification_attempts, 5)
check("...STILL only one upload ever made -- retry verification cannot duplicate a Short", len(client.upload_calls), 1)
check("...and no new error", reports, [])
check("...and the retry button is gone once verified", len(buttons_in(panel, "Retry Verification")), 0)
destroy(panel)

# the same retry through the row button itself
reports.clear()
panel, state = new_panel()
client = state.client
client.always_hidden = True
select_and_fill(panel, "s3b.mp4", "Row Button Retry #BF5 #Shorts")
panel._quick_upload()
wait_for(lambda: disk_entries()[0].status == STATUS_VERIFICATION_FAILED, 20)
pump(0.3)
client.always_hidden = False
buttons_in(panel, "Retry Verification")[0].invoke()
check("the row's Retry Verification button works too", wait_for(lambda: disk_entries()[0].status == STATUS_VERIFIED, 15), True)
check("...still one upload", len(client.upload_calls), 1)
destroy(panel)

# =============================================================================
# S4: duplicate protection -- the upload 'failed' but YouTube actually has it
# =============================================================================
print("\n--- S4: duplicate protection ---")
reports.clear()
preexisting = UploadActivityEntry(filename="other.mp4", uploaded_at=dt.datetime.now(dt.timezone.utc).isoformat(),
                                  status=STATUS_VERIFIED, title="Lost Response #BF5 #Shorts", video_id="OTHER_VIDEO")
panel, state = new_panel(preseed=[preexisting])
client = state.client
client.upload_mode = "raise_but_created"
select_and_fill(panel, "s4.mp4", "Lost Response #BF5 #Shorts")
panel._quick_upload()
mine = lambda: [x for x in disk_entries() if x.filename == "s4.mp4"][0]
check("upload raised but YouTube has the video -> adopted and VERIFIED", wait_for(lambda: mine().status == STATUS_VERIFIED, 15), True)
check("...upload_video was called ONCE -- never uploaded again", len(client.upload_calls), 1)
check("...the existing video's ID is attached to the history record", mine().video_id, "yt_new1")
check("...not marked UPLOAD FAILED, and no error dialog", (mine().status != STATUS_FAILED, reports), (True, []))
check("...the existing-video lookup excluded ANOTHER entry's video", "OTHER_VIDEO" in client.find_calls[0][2], True)
check("...and insisted on the slot that was requested (a same-titled video at another time is a different upload)", (client.find_calls[0][3] is not None, client.find_calls[0][4]), (True, True))
check("...the other (same-titled) entry was left completely alone", [x for x in disk_entries() if x.filename == "other.mp4"][0].video_id, "OTHER_VIDEO")
destroy(panel)

reports.clear()
panel, state = new_panel()
client = state.client
client.upload_mode = "raise_always"
select_and_fill(panel, "s4b.mp4", "Truly Failed #BF5 #Shorts")
panel._quick_upload()
check("upload genuinely fails and YouTube doesn't have it -> UPLOAD FAILED", wait_for(lambda: disk_entries()[0].status == STATUS_FAILED, 15), True)
check("...both automatic attempts were made", len(client.upload_calls), 2)
check("...YouTube was checked for the video after each failed attempt (before any repeat)", len(client.find_calls), 2)
check("...the error text is kept", "WRONG_VERSION_NUMBER" in disk_entries()[0].error, True)
check("...STATUS shows UPLOAD FAILED", row_texts(history_rows(panel)[0])[-1], "UPLOAD FAILED")
check("...no verification was attempted for something that never uploaded", len(client.check_calls), 0)
check("...the error dialog offers Retry (re-upload) as before", (len(reports), callable(reports[0].get("on_retry")), reports[0].get("retry_label", "Retry")), (1, True, "Retry"))
check("...and the video is still selected, never discarded", panel._selected is not None, True)
destroy(panel)

# =============================================================================
# S5: verification survives an app restart
# =============================================================================
print("\n--- S5: restart resume ---")
reports.clear()
now = dt.datetime.now(dt.timezone.utc).isoformat()
def mk(**kw):
    return UploadActivityEntry(uploaded_at=now, **kw)
seed = [
    mk(filename="A.mp4", status=STATUS_VERIFYING, title="Title A", video_id="vidA", verification_attempts=2),
    mk(filename="B.mp4", status=STATUS_UPLOAD_COMPLETE, title="Title B", video_id="vidB"),
    mk(filename="C.mp4", status=STATUS_UPLOADING, title="Orphan Found"),
    mk(filename="D.mp4", status=STATUS_UPLOADING, title="Orphan Lost"),
    mk(filename="E.mp4", status=STATUS_VERIFIED, title="Title E", video_id="vidE"),
    mk(filename="F.mp4", status=STATUS_FAILED, title="Title F", error="boom"),
    mk(filename="G.mp4", status=STATUS_DONE, title="Legacy G"),
    mk(filename="H.mp4", status=STATUS_DONE, title="Legacy H On YouTube"),
    UploadActivityEntry(filename="I.mp4", status=STATUS_DONE, title="Legacy I Old",
                        uploaded_at=(dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=3)).isoformat()),
    mk(filename="J.mp4", status=STATUS_DONE, title="Twin Title"),
    mk(filename="K.mp4", status=STATUS_DONE, title="Twin Title"),
]
ua.UPLOAD_ACTIVITY_PATH.write_text(json.dumps({"entries": [ua_entry_dict(x) for x in seed]}), encoding="utf-8")
cfg = AppConfig(); cfg.developer_mode = False
state = AppState(cfg)
state.repo = FakeRepo()
client = FakeClient(state.repo)
client.store.update({"vidA": "Title A", "vidB": "Title B", "vidOrphan": "Orphan Found"})
client.title_lookup["Orphan Found"] = "vidOrphan"
client.store["vidH"] = "Legacy H On YouTube"; client.title_lookup["Legacy H On YouTube"] = "vidH"
client.store["vidTwin"] = "Twin Title"; client.title_lookup["Twin Title"] = "vidTwin"   # ONE video on YouTube, but TWO legacy entries with that title
state.client = client
panel_module.RESUME_VERIFICATION_DELAY_MS = 50   # exercise the real automatic start-up path
panel = BasicUploadPanel(root, state)
panel.pack(fill="both", expand=True)
panel_module.RESUME_VERIFICATION_DELAY_MS = 10 ** 9
by_file = lambda f: [x for x in disk_entries() if x.filename == f][0]
check("on start-up an interrupted VERIFYING entry is resumed and confirmed", wait_for(lambda: by_file("A.mp4").status == STATUS_VERIFIED, 15), True)
check("...its attempt numbering continued from where it left off (2 -> 3)", by_file("A.mp4").verification_attempts, 3)
check("an entry stuck at 'request complete' is resumed too", wait_for(lambda: by_file("B.mp4").status == STATUS_VERIFIED, 15), True)
check("an upload cut off mid-way is looked up by title -> found -> VERIFIED with its video ID attached", wait_for(lambda: by_file("C.mp4").status == STATUS_VERIFIED, 15), True)
check("...(the existing video's id)", by_file("C.mp4").video_id, "vidOrphan")
check("an upload cut off mid-way that YouTube never received -> VERIFICATION FAILED, honestly", wait_for(lambda: by_file("D.mp4").status == STATUS_VERIFICATION_FAILED, 20), True)
check("...with the interruption recorded in its report", "Interrupted" in by_file("D.mp4").format_report(), True)
check("NOTHING was uploaded during any of this (verification never uploads)", client.upload_calls, [])
check("finished entries were left alone", (by_file("E.mp4").status, by_file("F.mp4").status, by_file("F.mp4").error), (STATUS_VERIFIED, STATUS_FAILED, "boom"))
check("a legacy entry (today) that YouTube DOES have is looked up by title and becomes VERIFIED, id attached", wait_for(lambda: by_file("H.mp4").status == STATUS_VERIFIED, 20), True)
check("...its video id", by_file("H.mp4").video_id, "vidH")
check("a legacy entry (today) YouTube does NOT have is never promoted to VERIFIED -- it's flagged", wait_for(lambda: by_file("G.mp4").status == STATUS_VERIFICATION_FAILED, 20), True)
check("an OLD legacy entry (not today) is left exactly as it was", by_file("I.mp4").status, STATUS_DONE)
wait_for(lambda: by_file("J.mp4").status != STATUS_DONE and by_file("K.mp4").status != STATUS_DONE, 20)
wait_for(lambda: not [x for x in disk_entries() if x.is_pending], 20)
twins = sorted([by_file("J.mp4").status, by_file("K.mp4").status])
check("two same-titled legacy entries, ONE video on YouTube: exactly one is confirmed, the other flagged (never both claiming it)", twins, sorted([STATUS_VERIFIED, STATUS_VERIFICATION_FAILED]))
check("...and the single video id is attached to only one entry", sorted([by_file("J.mp4").video_id, by_file("K.mp4").video_id]), ["", "vidTwin"])
check("resume-time failures show in the row but do NOT pop dialogs (no stacked modals at start-up)", [r for r in reports if r.get("show_dialog", True)], [])
check("...though the failure is still recorded via report_error", any(r.get("show_dialog") is False for r in reports), True)
check("no entry is left stuck in an in-flight state", [x.filename for x in disk_entries() if x.is_pending], [])
check("a second resume pass finds nothing left to do (no repeated lookups)", (panel._resume_pending_verifications(), panel._resume_queue), (None, []))
statuses = {row_texts(r)[0]: row_texts(r)[-1] for r in history_rows(panel)}
check("HIST-001 lists today's ten entries, one row each (the 3-day-old one isn't 'today')", len(history_rows(panel)), 10)
check("...with the right status on each row", {k: v for k, v in statuses.items() if k not in ("J.mp4", "K.mp4")}, {
    "A.mp4": "VERIFIED", "B.mp4": "VERIFIED", "C.mp4": "VERIFIED", "D.mp4": "VERIFICATION FAILED",
    "E.mp4": "VERIFIED", "F.mp4": "UPLOAD FAILED", "G.mp4": "VERIFICATION FAILED", "H.mp4": "VERIFIED",
})
destroy(panel)

# =============================================================================
# S6: per-entry report + Developer Mode
# =============================================================================
print("\n--- S6: Developer Mode reports ---")
reports.clear()
seed = [
    mk(filename="Q7.mp4", status=STATUS_VERIFIED, title="Bridge Push", video_id="VID7", verification_attempts=1, last_verification_at=now,
       verification_detail="Confirmed by YouTube -- exists=yes"),
    mk(filename="Q6.mp4", status=STATUS_VERIFICATION_FAILED, title="Bren Gun Ambush", video_id="VID6", verification_attempts=4,
       last_verification_at=now, verification_detail="YouTube does not list this video yet."),
]
panel, state = new_panel(preseed=seed, dev_mode=True)
check("Developer Mode: every history row gets its own Copy Report button", len(buttons_in(panel, "Copy Report")), 2)
rows = {row_texts(r)[0]: r for r in history_rows(panel)}
q6_btn = [w for w in walk(rows["Q6.mp4"]) if isinstance(w, ctk.CTkButton) and w.cget("text") == "Copy Report"][0]
q6_btn.invoke()
clip = panel.clipboard_get()
check("Copy Report copies THAT entry's full report", all(s in clip for s in ("Q6.mp4", "VID6", "VERIFICATION FAILED", "Verification attempts: 4", "does not list this video yet.")), True)
check("...and NOTHING from any other history entry", "Q7.mp4" not in clip and "VID7" not in clip, True)
q7_btn = [w for w in walk(rows["Q7.mp4"]) if isinstance(w, ctk.CTkButton) and w.cget("text") == "Copy Report"][0]
q7_btn.invoke()
clip = panel.clipboard_get()
check("the other row copies only its own", "Q7.mp4" in clip and "Q6.mp4" not in clip, True)
panel._update_diagnostics()
diag = panel.diagnostics_label.cget("text")
for needle in ("Latest upload (HIST-001)", "YouTube video ID", "Upload timestamp", "Verification attempt", "Last verification attempt", "Verification result / API response", "current state"):
    check(f"Developer Diagnostics shows {needle!r}", needle in diag, True)
check("...Copy Upload Report is enabled and copies the latest entry only", str(panel.diag_copy_report_btn.cget("state")), "normal")
panel._copy_latest_upload_report()
latest_clip = panel.clipboard_get()
check("...(latest by upload time)", latest_clip.count("UPLOAD REPORT"), 1)
rows = {row_texts(r)[0]: r for r in history_rows(panel)}
title_lbl = [w for w in rows["Q6.mp4"].winfo_children() if isinstance(w, ctk.CTkLabel) and w.cget("text") == "Bren Gun Ambush"][0]
panel.clipboard_clear(); panel.clipboard_append("SENTINEL")
title_lbl._label.event_generate("<Button-1>", x=2, y=2)
pump(0.2)
c2 = panel.clipboard_get()
check("...(row click)", "Q6.mp4" in c2 and "Q7.mp4" not in c2, True)
destroy(panel)

panel, state = new_panel(preseed=seed, dev_mode=False)
check("outside Developer Mode there are no Copy Report buttons (page stays clean)", len(buttons_in(panel, "Copy Report")), 0)
check("...but the failed row still has Retry Verification", len(buttons_in(panel, "Retry Verification")), 1)
destroy(panel)

panel, state = new_panel(preseed=[seed[0]], dev_mode=False)
check("with nothing failed and no Developer Mode, no action column/buttons appear at all", (len(buttons_in(panel, "Retry Verification")), len(buttons_in(panel, "Copy Report"))), (0, 0))
destroy(panel)

# =============================================================================
# S7: one-row layout regardless of state
# =============================================================================
print("\n--- S7: layout ---")
seed = [
    mk(filename="Q1.mp4", status=STATUS_UPLOADING, title="One"),
    mk(filename="Q2.mp4", status=STATUS_VERIFYING, title="Two", video_id="v2"),
    mk(filename="Q3.mp4", status=STATUS_VERIFIED, title="Three " + "very long " * 30, video_id="v3"),
    mk(filename="Q4.mp4", status=STATUS_VERIFICATION_FAILED, title="Four", video_id="v4"),
    mk(filename="Q5.mp4", status=STATUS_FAILED, title="Five", error="Some long error " * 20),
]
panel, state = new_panel(preseed=seed, dev_mode=True, resume_ms=10 ** 9)
rows = history_rows(panel)
check("five entries -> five rows, never extra lines for the same upload", len(rows), 5)
check("Developer Mode: no row is squeezed until its TITLE vanishes (every label stays on screen)", all(w.winfo_ismapped() for r in rows for w in r.winfo_children() if w.winfo_manager() == "grid" and isinstance(w, ctk.CTkLabel) and not w.cget("text").startswith("[")), True)
check("every row keeps every label on grid row 0 (single line)", all(int(w.grid_info()["row"]) == 0 for r in rows for w in r.winfo_children() if w.winfo_manager() == "grid"), True)
check("a very long title is truncated, not wrapped", all("..." in t for r in rows for t in row_texts(r) if t.startswith("Three")), True)
destroy(panel)

if errors:
    print("\nFAILURES:", errors)
    sys.exit(1)
print("\nALL CHECKS PASSED")
root.destroy()

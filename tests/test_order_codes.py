"""ORDER CODES: every upload gets a permanent code (0001, 0002 ... 0232) that is kept in the app with its title and description and in the
upload History, and is never sent to YouTube."""
import sys, os, json, datetime as dt, tempfile, pathlib, threading, time as time_module, types
sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent.parent))
TMP = pathlib.Path(tempfile.mkdtemp())

import data.queue_order as qo
qo.QUEUE_ORDER_PATH = TMP / "queue_order.json"          # a test must NEVER write the real order ledger
import data.config as config_module
config_module.CONFIG_PATH = TMP / "config.json"
import data.auto_schedule_config as asc
asc.AUTO_SCHEDULE_CONFIG_PATH = TMP / "asc.json"
import data.error_log as error_log_module
error_log_module.ERROR_LOG_PATH = TMP / "el.json"
import data.crash_log as cl_module
cl_module.CRASH_LOG_PATH = TMP / "cl.json"
import data.shorts_folder as shorts_folder_module
shorts_folder_module.REGISTRY_PATH = TMP / "sf.json"
import data.upload_activity as ua
ua.UPLOAD_ACTIVITY_PATH = TMP / "upload_activity.json"
import data.history as hist_module
hist_module.HISTORY_PATH = TMP / "hist.json"

import cv2
import numpy as np
import customtkinter as ctk
from tkinterdnd2 import TkinterDnD
import tkinter.messagebox as tk_messagebox
from data.config import AppConfig
from data.queue_order import QueueOrderStore, QueueRecord, format_code, SEQ_STEP
from data.upload_activity import STATUS_FAILED, STATUS_VERIFIED, UploadActivityEntry, UploadActivityLog
from data.order_codes import backfill_order_codes
from youtube.models import Video

import youtube.upload_verifier as uv
uv.VERIFY_INITIAL_DELAY_SECONDS = 0.05; uv.VERIFY_RETRY_DELAY_SECONDS = 0.05; uv.VERIFY_MAX_ATTEMPTS = 4
import ui.widgets.basic_upload_panel as panel_module
panel_module.UPLOAD_RETRY_DELAY_SECONDS = 0.05; panel_module.UPLOAD_VERIFY_DELAY_SECONDS = 0.05; panel_module.UPLOAD_VERIFY_MAX_ATTEMPTS = 1
panel_module.RESUME_VERIFICATION_DELAY_MS = 10 ** 9
reports = []
panel_module.report_error = lambda master, state, dev_id, function, error_message, **kw: reports.append((function, error_message))
from ui.app_state import AppState
from ui.widgets.basic_upload_panel import BasicUploadPanel
from youtube.client import VideoVerification

errors = []
def check(name, actual, expected=True):
    ok = actual == expected
    print(("PASS" if ok else "FAIL"), name, "->" if not ok else "", (actual, expected) if not ok else "")
    if not ok: errors.append(name)
tk_messagebox.showerror = lambda *a, **k: None; tk_messagebox.askyesno = lambda *a, **k: True

def fresh_ledger(name="q.json"):
    qo.QUEUE_ORDER_PATH = TMP / f"{name}_{time_module.time_ns()}.json"; QueueOrderStore.reset_shared()
    return QueueOrderStore.shared()
def video(i, desc="", title=None):
    return Video(video_id=f"V{i}", title=title or f"Title {i}", description=desc, thumbnail_url="", duration_seconds=20, publish_at=dt.datetime(2030, 1, 1, tzinfo=dt.timezone.utc))

# =================================================================== the code itself
print("\n--- what a code looks like ---")
check("1000 -> 0001, 232000 -> 0232, 9999000 -> 9999", [format_code(1000), format_code(232000), format_code(9999000)], ["0001", "0232", "9999"])
check("a video placed between two others gets a fractional code that still sorts", [format_code(45500), format_code(500)], ["0045.500", "0000.500"])
check("codes sort in the same order as the numbers behind them", sorted(format_code(x) for x in (3000, 45500, 45000, 46000)) == [format_code(x) for x in (3000, 45000, 45500, 46000)], True)

print("\n--- the ledger: codes, descriptions, permanence ---")
reg = fresh_ledger()
check("before an order is recorded there is no next code", (reg.next_code(), reg.code_of("A")), ("", ""))
reg.adopt([("A", "Title A", "e", "recovered"), ("B", "Title B", "e", "recovered"), ("C", "Title C", "e", "recovered")])
check("the recorded order is numbered 0001, 0002, 0003", [reg.code_of(v) for v in "ABC"], ["0001", "0002", "0003"])
check("the next upload will get 0004", reg.next_code(), "0004")
rec = reg.append("D", "Title D", "uploaded", description="The description of D")
check("an upload gets the next code and its title + description are kept with it", (rec.code, rec.title, rec.description), ("0004", "Title D", "The description of D"))
check("...and the code after that is 0005", reg.next_code(), "0005")
check("a code can be looked up: '4', '0004' and '0004.0' all find D", [reg.find_by_code(c).video_id for c in ("4", "0004", "4.0")], ["D"] * 3)
check("an unknown code finds nothing", (reg.find_by_code("0999"), reg.find_by_code("nonsense")), (None, None))
reg.insert_between("E", "A", "Title E", "placed", "inserted")
check("a video placed after A gets a fractional code between 0001 and 0002", (reg.code_of("E"), reg.find_by_code("0001.500").video_id), ("0001.500", "E"))
check("...which does not disturb the next whole code", reg.next_code(), "0005")
before = {v: reg.code_of(v) for v in "ABCDE"}
reg.append("F", "Title F"); reg.append("G", "Title G")
check("no existing code ever changes as more videos are added", {v: reg.code_of(v) for v in "ABCDE"}, before)
check("setting details changes title/description only -- never the number or code", (reg.set_details("A", description="new text"), reg.get("A").description, reg.code_of("A"), reg.seq_of("A")), (True, "new text", "0001", 1000))
check("...and does nothing for an unknown video", reg.set_details("NOPE", description="x"), False)
raw = json.loads(qo.QUEUE_ORDER_PATH.read_text())
check("the file reads on its own: each record carries its code, title and description", (raw["records"][0]["code"], raw["records"][0]["title"], raw["records"][0]["description"]), ("0001", "Title A", "new text"))
QueueOrderStore.reset_shared(); reg2 = QueueOrderStore.shared()
check("everything survives a restart (codes, descriptions)", ([reg2.code_of(v) for v in "ABCD"], reg2.get("D").description), (["0001", "0002", "0003", "0004"], "The description of D"))
legacy = json.loads(qo.QUEUE_ORDER_PATH.read_text()); [r.pop("description") for r in legacy["records"]]; [r.pop("code") for r in legacy["records"]]
qo.QUEUE_ORDER_PATH.write_text(json.dumps(legacy)); QueueOrderStore.reset_shared(); reg3 = QueueOrderStore.shared()
check("a ledger written before descriptions/codes existed still loads (codes are derived, descriptions start empty)", (reg3.code_of("C"), reg3.get("C").description, len(reg3)), ("0003", "", 7))

print("\n--- filling in descriptions for videos that were recorded without one ---")
reg = fresh_ledger()
reg.adopt([(f"V{i}", f"Title {i}", "e", "recovered") for i in (1, 2, 3)])
n = reg.backfill_details([video(1, "Description one"), video(2, ""), video(3, "Description three"), video(99, "not in the ledger")])
check("only recorded videos that have a description on YouTube are filled in", (n, reg.get("V1").description, reg.get("V2").description, reg.get("V3").description), (2, "Description one", "", "Description three"))
n = reg.backfill_details([video(1, "A DIFFERENT description")])
check("an existing description is never overwritten", (n, reg.get("V1").description), (0, "Description one"))

# =================================================================== the upload history record
print("\n--- the code is kept in the upload History ---")
e = UploadActivityEntry(filename="q1.mov", uploaded_at=dt.datetime.now(dt.timezone.utc).isoformat(), status=STATUS_VERIFIED, title="T", video_id="V1", order_code="0001")
d = json.loads(json.dumps(__import__("dataclasses").asdict(e)))
check("an entry stores its code and reads it back", ua._entry_from_dict(d).order_code, "0001")
del d["order_code"]
check("an entry saved before codes existed loads with an empty code", ua._entry_from_dict(d).order_code, "")
log = UploadActivityLog(); log.entries = [
    UploadActivityEntry(filename="a.mov", uploaded_at="2026-10-01T10:00:00+00:00", status=STATUS_VERIFIED, title="One", video_id="V1"),
    UploadActivityEntry(filename="b.mov", uploaded_at="2026-10-01T11:00:00+00:00", status=STATUS_VERIFIED, title="Two", video_id="V2", order_code="0077"),
    UploadActivityEntry(filename="c.mov", uploaded_at="2026-10-01T12:00:00+00:00", status=STATUS_VERIFIED, title="Three", video_id="NOT_IN_LEDGER"),
    UploadActivityEntry(filename="d.mov", uploaded_at="2026-10-01T13:00:00+00:00", status=STATUS_FAILED, title="Four", video_id=""),
]
check("backfill gives the history its codes: only entries whose video is in the ledger and that have none yet",
      (log.backfill_order_codes(reg), [x.order_code for x in log.entries]), (1, ["0001", "0077", "", ""]))
check("...and a second run changes nothing", log.backfill_order_codes(reg), 0)
ua.UPLOAD_ACTIVITY_PATH.write_text("{}") if False else None
log.save(); check("the codes are on disk", [x.order_code for x in UploadActivityLog.load().entries], ["0001", "0077", "", ""])
fresh_unadopted = fresh_ledger("unadopted")
check("before an order is recorded the helper does nothing at all", backfill_order_codes([video(1, "x")], fresh_unadopted, log), (0, 0))
reg = fresh_ledger(); reg.adopt([("V1", "Title 1", "e", "recovered")])
UploadActivityLog._shared = None
sh = UploadActivityLog.shared(); sh.entries[:] = [UploadActivityEntry(filename="a.mov", uploaded_at="2026-10-01T10:00:00+00:00", status=STATUS_VERIFIED, title="One", video_id="V1")]
described, coded = backfill_order_codes([video(1, "Description one")])
check("the shared helper fills both the ledger descriptions and the history codes", (described, coded, sh.entries[0].order_code, reg.get("V1").description), (1, 1, "0001", "Description one"))
report = sh.entries[0].format_report()
check("a copied upload report shows the code, the title and the description from the ledger", ("Order code: 0001" in report, "Title: One" in report, "Description: Description one" in report), (True, True, True))
sh.entries[0].order_code = ""; sh.entries[0].video_id = ""
check("a report for an upload with no code says so", "Order code: (none)" in sh.entries[0].format_report(), True)

# =================================================================== start-up backfill
print("\n--- the app fills in codes at start-up ---")
import ui.main_window as mw
reg = fresh_ledger(); reg.adopt([("V1", "Title 1", "e", "recovered"), ("V2", "Title 2", "e", "recovered")])
UploadActivityLog._shared = None
sh = UploadActivityLog.shared(); sh.entries[:] = [UploadActivityEntry(filename="a.mov", uploaded_at="2026-10-01T10:00:00+00:00", status=STATUS_VERIFIED, title="One", video_id="V2")]
notified = []
stub = types.SimpleNamespace(state=types.SimpleNamespace(repo=types.SimpleNamespace(videos=[video(2, "Two's description")]), notify_history_changed=lambda: notified.append(1)))
mw.MainWindow._backfill_order_codes(stub)
check("start-up gives the History its codes, keeps descriptions beside them, and tells the History page to redraw", (sh.entries[0].order_code, reg.get("V2").description, notified), ("0002", "Two's description", [1]))
mw.MainWindow._backfill_order_codes(stub)
check("...and does not redraw again when there was nothing to do", notified, [1])

# =================================================================== end to end: uploads through the real panel
print("\n--- a real upload through the panel ---")
tmp_dir = pathlib.Path(tempfile.mkdtemp())
def make_clip(path, w=48, h=86, fps=24.0, frames=24):
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), fps, (w, h))
    for i in range(frames): writer.write(np.full((h, w, 3), i % 255, dtype=np.uint8))
    writer.release()
class TestRoot(ctk.CTk, TkinterDnD.DnDWrapper):
    def __init__(self): super().__init__(); self.TkdndVersion = TkinterDnD._require(self)
root = TestRoot(); root.geometry("1250x1200")
def pump(s=0.2): root.after(int(s * 1000), root.quit); root.mainloop()
def wait_for(cond, timeout=12.0, step=0.1):
    waited = 0.0
    while waited < timeout:
        if cond(): return True
        pump(step); waited += step
    return cond()
def walk(w):
    for c in w.winfo_children():
        yield c; yield from walk(c)
class FakeRepo:
    def __init__(self): self._videos = []
    @property
    def videos(self): return list(self._videos)
    def refresh(self): pass
    def refresh_ids(self, ids): pass
    def add(self, v): self._videos.append(v)
class FakeClient:
    def __init__(self, repo):
        self.repo = repo; self.store = {}; self.videos = {}; self.upload_calls = []; self.mode = "ok"; self.sent = []
    def list_playlists(self): return []
    def add_video_to_playlist(self, *a): pass
    def get_file_details(self, video_id): return None
    def upload_video(self, file_path, title, description, made_for_kids, publish_at, progress_callback=None):
        self.upload_calls.append((file_path, title)); self.sent.append((title, description))
        if self.mode == "raise_always": raise RuntimeError("[SSL: WRONG_VERSION_NUMBER] wrong version number")
        vid = f"yt_code{len(self.upload_calls)}"
        v = Video(video_id=vid, title=title, description=description, thumbnail_url="", duration_seconds=3, publish_at=publish_at)
        self.store[vid] = title; self.videos[vid] = v; self.repo.add(v)
        if self.mode == "raise_but_created": raise RuntimeError("Connection reset while reading the upload response")
        return v
    def find_recent_upload_by_title(self, title, lookback=5, exclude_ids=(), expected_publish_at=None, require_publish_match=False):
        return next((v for vid, v in self.videos.items() if v.title == title and vid not in set(exclude_ids)), None)
    def check_video_registered(self, video_id, expected_title):
        if video_id not in self.store: return VideoVerification(exists=False)
        return VideoVerification(exists=True, channel_matches=True, title_matches=self.store[video_id] == expected_title, in_uploads_playlist=True, upload_status="uploaded", privacy_status="private")
def new_panel(register_first=True):
    ua.UPLOAD_ACTIVITY_PATH.write_text(json.dumps({"entries": []}), encoding="utf-8")
    UploadActivityLog._shared = None
    reg = fresh_ledger("panel")
    if register_first: reg.adopt([("OLD1", "Old one", "e", "recovered"), ("OLD2", "Old two", "e", "recovered"), ("OLD3", "Old three", "e", "recovered")])
    state = AppState(AppConfig()); state.repo = FakeRepo(); state.client = FakeClient(state.repo)
    panel = BasicUploadPanel(root, state); panel.pack(fill="both", expand=True); pump(0.3)
    return panel, state, reg
def select_and_fill(panel, name, title, description):
    clip = tmp_dir / name; make_clip(clip)
    panel._set_file_from_drag(str(clip)); pump(0.25)
    panel.title_var.set(title); panel.desc_text.delete("1.0", "end"); panel.desc_text.insert("1.0", description)
    panel._on_description_edited(); pump(0.1)
def row_cells(panel):
    frames = [w for w in panel.history_list.winfo_children() if isinstance(w, ctk.CTkFrame)][1:]
    out = []
    for r in frames:
        labels = sorted((w for w in r.winfo_children() if isinstance(w, ctk.CTkLabel) and w.winfo_manager() == "grid"), key=lambda w: int(w.grid_info()["column"]))
        out.append([w.cget("text") for w in labels if not w.cget("text").startswith("[")])
    return out
def entries(): return UploadActivityLog.load().entries

panel, state, reg = new_panel()
check("nothing selected: no code is shown", panel.order_code_label.cget("text"), "")
select_and_fill(panel, "c1.mp4", "First New Video #BF5 #Shorts", "Description of the first new video.")
check("selecting a video shows the code it WILL get", panel.order_code_label.cget("text"), "CODE 0004")
panel._quick_upload()
check("the upload finishes and verifies", wait_for(lambda: entries() and entries()[0].status == STATUS_VERIFIED), True)
e1 = entries()[0]
check("the history entry carries the code and it is on disk", (e1.order_code, e1.video_id), ("0004", "yt_code1"))
rec = reg.get("yt_code1")
check("the ledger holds that code with the title and the description you typed", (rec.code, rec.title, rec.description), ("0004", "First New Video #BF5 #Shorts", "Description of the first new video."))
check("NOTHING about the code was sent to YouTube: the title and description published are exactly what was typed", state.client.sent[0], ("First New Video #BF5 #Shorts", "Description of the first new video."))
check("the History row shows the code between the file and the title", (row_cells(panel)[0][0], row_cells(panel)[0][1], row_cells(panel)[0][2]), ("c1.mp4", "0004", "First New Video"))
check("the label moved on: the next video will be 0005", (panel.order_code_label.cget("text"), reg.next_code()), ("", "0005"))

select_and_fill(panel, "c2.mp4", "Second New Video", "Second description.")
check("the next selection shows 0005", panel.order_code_label.cget("text"), "CODE 0005")
panel._quick_upload()
check("the second upload verifies", wait_for(lambda: len(entries()) == 2 and all(x.status == STATUS_VERIFIED for x in entries())), True)
second = [x for x in entries() if x.title.startswith("Second")][0]
check("it is numbered 0005 -- the order of upload is the order of the codes", (second.order_code, [x.order_code for x in sorted(entries(), key=lambda x: x.uploaded_at)]), ("0005", ["0004", "0005"]))

state.client.mode = "raise_always"
select_and_fill(panel, "c3.mp4", "Third Video That Fails", "Third description.")
panel._quick_upload()
check("a failed upload", wait_for(lambda: any(x.status == STATUS_FAILED for x in entries()), 15), True)
failed = [x for x in entries() if x.status == STATUS_FAILED][0]
check("...gets no code and does not use one up", (failed.order_code, reg.next_code(), len(reg)), ("", "0006", 5))
check("...its History row shows a dash in the CODE column", [c[1] for c in row_cells(panel) if "Failed" in " ".join(c)] == ["—"], True)

state.client.mode = "raise_but_created"
panel._clear_selection(); pump(0.2)
select_and_fill(panel, "c4.mp4", "Fourth Video Interrupted", "Fourth description.")
panel._quick_upload()
check("an upload that was interrupted but really reached YouTube still ends up verified", wait_for(lambda: any(x.title.startswith("Fourth") and x.status == STATUS_VERIFIED for x in entries()), 20), True)
fourth = [x for x in entries() if x.title.startswith("Fourth")][0]
check("...with exactly one code (0006), once", (fourth.order_code, sum(1 for v in reg.records if v.title.startswith("Fourth"))), ("0006", 1))
check("the codes in the ledger run 0001..0006 with no gaps and no repeats", [r.code for r in reg.records], [f"{i:04d}" for i in range(1, 7)])

panel.destroy(); pump(0.2)
state2 = AppState(AppConfig()); state2.repo = FakeRepo(); state2.client = FakeClient(state2.repo)
UploadActivityLog._shared = None
panel = BasicUploadPanel(root, state2); panel.pack(fill="both", expand=True); pump(0.4)
panel._history_view = "All"; panel._render_history(); pump(0.2)
cells = {c[2][:6]: c[1] for c in row_cells(panel) if len(c) > 2}
check("after the app is restarted the History still shows every code", (cells.get("First "), cells.get("Second"), cells.get("Fourth")), ("0004", "0005", "0006"))
panel.destroy(); pump(0.2)

print("\n--- before an order is recorded: no codes, and nothing breaks ---")
reports.clear()   # (the failed upload above reported its failure on purpose)
panel, state, reg = new_panel(register_first=False)
select_and_fill(panel, "d1.mp4", "Unrecorded Order Video", "Description.")
check("no code is promised", panel.order_code_label.cget("text"), "")
panel._quick_upload()
check("the upload still works", wait_for(lambda: entries() and entries()[0].status == STATUS_VERIFIED), True)
check("...without a code (and the ledger stays empty: recovery will find it by its upload time)", (entries()[0].order_code, len(reg)), ("", 0))
check("no problem was reported", reports, [])
panel.destroy(); pump(0.2)

if errors:
    print("\nFAILURES:", errors); os._exit(1)
print("\nALL CHECKS PASSED"); os._exit(0)

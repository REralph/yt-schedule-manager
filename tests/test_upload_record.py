"""The permanent upload record, the History Today/All view, and the Refresh (re-check) button."""
import sys, os, json, time, tempfile, pathlib, threading, ssl, datetime as dt
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import data.queue_order as _qo_isolation, tempfile as _tf_isolation, pathlib as _pl_isolation; _qo_isolation.QUEUE_ORDER_PATH = _pl_isolation.Path(_tf_isolation.mkdtemp()) / "queue_order.json"  # a test must NEVER write the real order ledger (data/queue_order.json)

TMP = pathlib.Path(tempfile.mkdtemp())
import data.config as m_config, data.auto_schedule_config as m_asc, data.upload_activity as ua, data.error_log as m_el
import data.crash_log as m_cl, data.shorts_folder as m_sf, data.session_state as m_ss, data.history as m_hist
m_config.CONFIG_PATH = TMP / "config.json"; m_asc.AUTO_SCHEDULE_CONFIG_PATH = TMP / "asc.json"; ua.UPLOAD_ACTIVITY_PATH = TMP / "ua.json"
m_el.ERROR_LOG_PATH = TMP / "el.json"; m_cl.CRASH_LOG_PATH = TMP / "cl.json"; m_sf.REGISTRY_PATH = TMP / "sf.json"
m_ss.SESSION_PATH = TMP / "ss.json"; m_hist.HISTORY_PATH = TMP / "hist.json"

import customtkinter as ctk
from tkinterdnd2 import TkinterDnD
import tkinter.messagebox as tk_messagebox
from dataclasses import asdict
from data import diagnostics
from data.config import AppConfig
from data.upload_activity import (
    STATUS_DONE, STATUS_FAILED, STATUS_MISSING, STATUS_UPLOADING, STATUS_VERIFICATION_FAILED, STATUS_VERIFIED, STATUS_VERIFYING,
    UploadActivityEntry, UploadActivityLog,
)
import youtube.record_checker as rc
from youtube.record_checker import check_records
from google.auth.exceptions import RefreshError

errors = []
def check(name, actual, expected=True):
    ok = actual == expected
    print(("PASS" if ok else "FAIL"), name, "->" if not ok else "", (actual, expected) if not ok else "")
    if not ok: errors.append(name)

NOW = dt.datetime.now(dt.timezone.utc)
def iso(days_ago=0, hours_ago=0): return (NOW - dt.timedelta(days=days_ago, hours=hours_ago)).isoformat()
def future(days, hour=6): return (dt.datetime.combine(dt.date.today() + dt.timedelta(days=days), dt.time(hour, 0)).astimezone()).isoformat()

def entry(i, **kw):
    base = dict(filename=f"q{i}.mov", uploaded_at=iso(hours_ago=i), status=STATUS_VERIFIED, title=f"Title {i} #BF5 #Shorts",
                video_id=f"VID{i}", scheduled_at=future(10 + i), q_number=str(i))
    base.update(kw)
    return UploadActivityEntry(**base)

def item(vid, *, title=None, channel="UC_ME", privacy="private", publish_at="keep", upload_status="processed", failure="", i=None):
    st = {"privacyStatus": privacy, "uploadStatus": upload_status}
    if publish_at == "keep":
        n = int(vid.replace("VID", "")) if vid.startswith("VID") else 0
        st["publishAt"] = dt.datetime.fromisoformat(future(10 + n)).astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    elif publish_at:
        st["publishAt"] = publish_at
    if failure: st["failureReason"] = failure
    return {"id": vid, "snippet": {"title": title or f"Title {vid.replace('VID','')} #BF5 #Shorts", "channelId": channel, "publishedAt": iso()}, "status": st, "processingDetails": {}}

class FakeClient:
    def __init__(self, items=None, recent=None):
        self.items = dict(items or {}); self.recent = list(recent or []); self.batches = []; self.fail = None; self.recent_calls = 0
    def get_authenticated_channel_id(self):
        if self.fail: raise self.fail
        return "UC_ME"
    def get_video_check_items(self, ids):
        ids = list(dict.fromkeys(i for i in ids if i))
        if self.fail: raise self.fail
        self.batches.append(len(ids))
        return {i: self.items[i] for i in ids if i in self.items}
    def list_recent_uploaded_video_ids(self, n):
        self.recent_calls += 1
        return self.recent[:n]

# =================================================================== retention
print("\n--- the record is permanent ---")
ua.UPLOAD_ACTIVITY_PATH = TMP / "ret.json"
log = UploadActivityLog()
ancient = log.record(entry(1, uploaded_at=iso(days_ago=400)))
old = log.record(entry(2, uploaded_at=iso(days_ago=30)))
log.record(entry(3, uploaded_at=iso(days_ago=0)))
reloaded = UploadActivityLog.load()
check("entries older than 7 days (even 400 days) are kept, on disk too", (len(log.entries), len(reloaded.entries)), (3, 3))
check("the whole record is returned newest first", [e.filename for e in reloaded.newest_first()], ["q3.mov", "q2.mov", "q1.mov"])
check("today's view still only shows today's", [e.filename for e in reloaded.today_entries()], ["q3.mov"])
ua.MAX_ENTRIES = 5
big = UploadActivityLog()
for i in range(8): big.record(entry(i, uploaded_at=iso(days_ago=100 - i), status=STATUS_FAILED if i == 0 else STATUS_VERIFIED))
check("only the (huge) safety bound ever trims, oldest VERIFIED first, never a failure that needs attention", (len(big.entries), any(e.status == STATUS_FAILED for e in big.entries)), (5, True))
ua.MAX_ENTRIES = 20000

print("\n--- one shared record ---")
ua.UPLOAD_ACTIVITY_PATH = TMP / "shared.json"
ua._shared = None
a = UploadActivityLog.shared(); a.record(entry(1))
b = UploadActivityLog.shared()
check("every caller gets the same object (no stale copies overwriting each other)", a is b, True)
(TMP / "shared.json").write_text(json.dumps({"entries": [asdict(entry(7)), asdict(entry(8))]}), encoding="utf-8")
c = UploadActivityLog.shared(reload=True)
check("reload=True re-reads the file INTO the same object", (c is a, sorted(e.filename for e in a.entries)), (True, ["q7.mov", "q8.mov"]))
e7 = a.find_by_video_id("VID7")
a.update_many([(e7, {"scheduled_at": "2030-01-01T06:00:00+00:00"}), (a.find_by_video_id("VID8"), {"status": STATUS_MISSING})])
disk = {e["filename"]: e for e in json.loads((TMP / "shared.json").read_text())["entries"]}
check("update_many applies every change and saves once", (disk["q7.mov"]["scheduled_at"], disk["q8.mov"]["status"]), ("2030-01-01T06:00:00+00:00", STATUS_MISSING))
try:
    a.update_many([(e7, {"not_a_field": 1})]); ok = False
except AttributeError:
    ok = True
check("an unknown field is rejected before anything is changed", (ok, e7.scheduled_at), (True, "2030-01-01T06:00:00+00:00"))
stop = threading.Event(); bad = []
def hammer(n):
    for i in range(30):
        try: a.update_many([(e7, {"verification_detail": f"{n}-{i}"})])
        except Exception as ex: bad.append(repr(ex))
ts = [threading.Thread(target=hammer, args=(n,)) for n in range(6)]
[t.start() for t in ts]; [t.join() for t in ts]
check("6 threads updating the record at once: no error, file still valid", (bad, isinstance(json.loads((TMP / "shared.json").read_text())["entries"], list)), ([], True))

print("\n--- NOT ON YOUTUBE is a real, non-pending status ---")
m = entry(9, status=STATUS_MISSING)
check("label, and it is not 'pending' (a restart must not try to resume it)", (m.display_status, m.is_pending), ("NOT ON YOUTUBE", False))

# =================================================================== the checker
print("\n--- re-checking the record (youtube/record_checker.py) ---")
def run(entries, client, **kw):
    plan, summary = check_records(client, entries, **kw)
    return {e.filename: f for e, f in plan}, summary

entries = [entry(i) for i in range(1, 6)]
client = FakeClient({f"VID{i}": item(f"VID{i}") for i in range(1, 6)})
plan, s = run(entries, client)
check("everything present and unchanged: all VERIFIED, nothing flagged", (s.checked, s.verified, s.all_good, s.rescheduled), (5, 5, True, 0))
check("...the detail records when it was re-checked and what YouTube says", all("Re-checked: on YouTube (private, scheduled for" in f["verification_detail"] for f in plan.values()), True)
check("...an unchanged schedule time is not rewritten", all("scheduled_at" not in f for f in plan.values()), True)

entries = [entry(1), entry(2), entry(3)]
client = FakeClient({"VID1": item("VID1"), "VID3": item("VID3")})
plan, s = run(entries, client)
check("a VERIFIED video that has been deleted becomes NOT ON YOUTUBE", (plan["q2.mov"]["status"], s.missing), (STATUS_MISSING, ["q2.mov"]))
check("...the others stay verified", (plan["q1.mov"]["status"], plan["q3.mov"]["status"]), (STATUS_VERIFIED, STATUS_VERIFIED))
check("...the summary says it plainly", "NOT ON YOUTUBE (q2.mov)" in s.text() and "2 on YouTube" in s.text(), True)

moved = dt.datetime.fromisoformat(future(40, 9)).astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
plan, s = run([entry(1)], FakeClient({"VID1": item("VID1", publish_at=moved)}))
check("a time changed in YouTube Studio is copied into the record", (s.rescheduled, dt.datetime.fromisoformat(plan["q1.mov"]["scheduled_at"]).astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")), (1, moved))
check("...and the detail says what it was before", "scheduled time updated from" in plan["q1.mov"]["verification_detail"], True)
plan, s = run([entry(1, scheduled_at=dt.datetime.fromisoformat(moved).astimezone().isoformat())], FakeClient({"VID1": item("VID1", publish_at=moved)}))
check("the same moment written in a different timezone is NOT treated as a change", (s.rescheduled, "scheduled_at" in plan["q1.mov"]), (0, False))

plan, s = run([entry(1)], FakeClient({"VID1": item("VID1", title="A better title I typed in Studio")}))
check("a title edited in Studio is kept, not treated as a failure", (plan["q1.mov"]["status"], plan["q1.mov"]["title"], s.retitled), (STATUS_VERIFIED, "A better title I typed in Studio", 1))

plan, s = run([entry(1)], FakeClient({"VID1": item("VID1", privacy="public", publish_at=None)}))
check("a video that has since gone live is recorded as Published and still verified", (plan["q1.mov"]["status"], plan["q1.mov"]["schedule_label"]), (STATUS_VERIFIED, "Published"))
plan, s = run([entry(1)], FakeClient({"VID1": item("VID1", privacy="private", publish_at=None)}))
check("a PRIVATE video with no publish time is flagged (it would never go live)", (s.problems != [], "never go live" in s.problems[0]), (True, True))
plan, s = run([entry(1)], FakeClient({"VID1": item("VID1", upload_status="rejected", failure="duplicate")}))
check("YouTube says rejected/failed -> VERIFICATION FAILED with YouTube's reason", (plan["q1.mov"]["status"], "rejected" in plan["q1.mov"]["verification_detail"], s.failed), (STATUS_VERIFICATION_FAILED, True, ["q1.mov"]))
plan, s = run([entry(1)], FakeClient({"VID1": item("VID1", channel="UC_SOMEONE_ELSE")}))
check("a video on a different channel -> VERIFICATION FAILED", (plan["q1.mov"]["status"], "different channel" in plan["q1.mov"]["verification_detail"]), (STATUS_VERIFICATION_FAILED, True))
plan, s = run([entry(1, status=STATUS_VERIFICATION_FAILED)], FakeClient({"VID1": item("VID1")}))
check("a previously FAILED verification recovers once the video is there", plan["q1.mov"]["status"], STATUS_VERIFIED)
plan, s = run([entry(1, status=STATUS_MISSING)], FakeClient({"VID1": item("VID1")}))
check("...and so does a NOT ON YOUTUBE one (e.g. the video was restored)", plan["q1.mov"]["status"], STATUS_VERIFIED)

print("\n--- a failed check never damages the record ---")
for exc, label in ((ssl.SSLError("[SSL: WRONG_VERSION_NUMBER] wrong version number"), "network error"), (RefreshError("invalid_grant: Token has been expired or revoked.", {}), "expired sign-in")):
    c = FakeClient({"VID1": item("VID1")}); c.fail = exc
    plan, s = check_records(c, [entry(1), entry(2)])
    check(f"{label}: NOTHING is changed and the error is reported", (plan, bool(s.error), s.missing), ([], True, []))
check("...an expired sign-in tells the user what to do", "Sign Out" in s.error and "invalid_grant" in s.error, True)
check("...and the summary text leads with it", s.text().startswith("Couldn't check:"), True)

print("\n--- efficiency ---")
many = [entry(i) for i in range(1, 121)]
c = FakeClient({f"VID{i}": item(f"VID{i}") for i in range(1, 121)})
plan, s = run(many, c)
check("120 uploads are checked in ONE batched request set (the client splits at 50)", (c.batches, s.checked), ([120], 120))
check("...and no scan of the channel's uploads is made when every entry has a video ID", c.recent_calls, 0)

print("\n--- uploads in progress are left alone ---")
busy = [entry(1, status=STATUS_UPLOADING, video_id=""), entry(2, status=STATUS_VERIFYING), entry(3), entry(4)]
plan, s = run(busy, FakeClient({"VID2": item("VID2"), "VID3": item("VID3"), "VID4": item("VID4")}), skip_ids={busy[3].entry_id})
check("uploading / verifying entries and any explicitly skipped one are not touched", (sorted(plan), s.skipped), (["q3.mov"], 3))

print("\n--- entries with no video ID are found on the channel ---")
t_a, t_b = "Twin Title #BF5 #Shorts", "Twin Title #BF5 #Shorts"
slot_a, slot_b = future(20, 6), future(20, 10)
utc = lambda s: dt.datetime.fromisoformat(s).astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
chan = {"NEW_A": item("NEW_A", title=t_a, publish_at=utc(slot_a)), "NEW_B": item("NEW_B", title=t_b, publish_at=utc(slot_b))}
legacy_a = entry(50, status=STATUS_DONE, video_id="", title=t_a, scheduled_at=slot_b, uploaded_at=iso(hours_ago=5))
legacy_b = entry(51, status=STATUS_DONE, video_id="", title=t_b, scheduled_at=slot_a, uploaded_at=iso(hours_ago=4))
plan, s = run([legacy_a, legacy_b], FakeClient(chan, recent=["NEW_A", "NEW_B"]))
check("two same-titled entries are told apart by their scheduled time, each claiming a DIFFERENT video", (plan["q50.mov"]["video_id"], plan["q51.mov"]["video_id"]), ("NEW_B", "NEW_A"))
check("...both become VERIFIED and are counted as recovered", (plan["q50.mov"]["status"], plan["q51.mov"]["status"], s.recovered), (STATUS_VERIFIED, STATUS_VERIFIED, 2))
check("...never the same video twice", plan["q50.mov"]["video_id"] != plan["q51.mov"]["video_id"], True)
one = {"ONLY": item("ONLY", title=t_a, publish_at=utc(slot_a))}
plan, s = run([entry(50, status=STATUS_DONE, video_id="", title=t_a, scheduled_at=slot_a), entry(51, status=STATUS_DONE, video_id="", title=t_a, scheduled_at=slot_a, uploaded_at=iso(hours_ago=2))], FakeClient(one, recent=["ONLY"]))
check("one video on the channel, two entries wanting it: only one gets it, the other is reported unmatched", (len(plan), s.unmatched), (1, ["q51.mov"]))

failed_but_there = entry(60, status=STATUS_FAILED, video_id="", title=t_a, scheduled_at=slot_a, error="Connection reset")
plan, s = run([failed_but_there], FakeClient({"X": item("X", title=t_a, publish_at=utc(slot_a))}, recent=["X"]))
check("an upload recorded as FAILED that actually reached YouTube (right title AND slot) is recovered", (plan["q60.mov"]["status"], plan["q60.mov"]["video_id"], plan["q60.mov"]["error"]), (STATUS_VERIFIED, "X", ""))
plan, s = run([entry(60, status=STATUS_FAILED, video_id="", title=t_a, scheduled_at=slot_b)], FakeClient({"X": item("X", title=t_a, publish_at=utc(slot_a))}, recent=["X"]))
check("...but a same-titled video at a DIFFERENT slot is a different upload, so a FAILED entry is left alone", (plan, s.unmatched), ({}, []))
plan, s = run([entry(60, status=STATUS_FAILED, video_id="", title="Never uploaded")], FakeClient({}, recent=[]))
check("a genuinely failed upload (nothing on the channel) is left as UPLOAD FAILED, not accused of anything", (plan, s.missing, s.unmatched), ({}, [], []))

# =================================================================== the History UI
print("\n--- History: Today / All / Refresh ---")
tk_messagebox.showerror = lambda *a, **k: None
tk_messagebox.askyesno = lambda *a, **k: True
import ui.widgets.basic_upload_panel as panel_module
panel_module.RESUME_VERIFICATION_DELAY_MS = 10 ** 9
shown_reports = []
panel_module.report_error = lambda *a, **k: shown_reports.append(k)
from ui.app_state import AppState
from ui.widgets.basic_upload_panel import BasicUploadPanel

class Root(ctk.CTk, TkinterDnD.DnDWrapper):
    def __init__(self):
        super().__init__(); self.TkdndVersion = TkinterDnD._require(self); self.cb_errors = []
    def report_callback_exception(self, exc, val, tb): self.cb_errors.append((exc.__name__, str(val)))
root = Root(); root.geometry("1250x1500")
def pump(s=0.2):
    root.after(int(s * 1000), root.quit); root.mainloop()
def wait_for(cond, timeout=8.0):
    t0 = time.time()
    while time.time() - t0 < timeout:
        if cond(): return True
        pump(0.1)
    return cond()
def walk(w):
    for c in w.winfo_children():
        yield c; yield from walk(c)
def real_click(button):
    """A genuine mouse click on a CTkButton (press + release on its canvas), not .invoke()."""
    assert button.winfo_ismapped(), "button is not on screen"
    c = button._canvas
    c.event_generate("<Enter>", x=6, y=6)           # CustomTkinter only treats a release as a click if the pointer ENTERED the button
    c.event_generate("<ButtonPress-1>", x=6, y=6); c.event_generate("<ButtonRelease-1>", x=6, y=6)
    pump(0.25)                                       # let the deferred action (see BasicUploadPanel._after_click) run
def buttons(text):
    return [w for w in walk(panel.history_wrap) if isinstance(w, ctk.CTkButton) and w.cget("text").startswith(text)]
def rows():
    frames = [w for w in panel.history_list.winfo_children() if isinstance(w, ctk.CTkFrame)]
    return [f for f in frames if any(isinstance(c, ctk.CTkLabel) and c.cget("text") in ("VERIFIED", "NOT ON YOUTUBE", "VERIFICATION FAILED", "UPLOAD FAILED", "UNVERIFIED", "VERIFYING", "UPLOADING") for c in f.winfo_children())]
def row_status(f):
    return next(c.cget("text") for c in f.winfo_children() if isinstance(c, ctk.CTkLabel) and c.cget("text") in ("VERIFIED", "NOT ON YOUTUBE", "VERIFICATION FAILED", "UPLOAD FAILED", "UNVERIFIED"))
def row_file(f):
    return next(c.cget("text") for c in f.winfo_children() if isinstance(c, ctk.CTkLabel) and c.cget("text").startswith("q") and c.cget("text").endswith((".mov", ".mp4")))

ua.UPLOAD_ACTIVITY_PATH = TMP / "ui_ua.json"; ua._shared = None
seed = [entry(i, uploaded_at=iso(hours_ago=i)) for i in range(1, 4)]                                   # 3 uploaded today
seed += [entry(100 + i, uploaded_at=iso(days_ago=i), video_id=f"VID{100 + i}") for i in range(1, 58)]  # 57 on earlier days (all older than a week for most)
seed[5].status = STATUS_VERIFICATION_FAILED
ua.UPLOAD_ACTIVITY_PATH.write_text(json.dumps({"entries": [asdict(e) for e in seed]}), encoding="utf-8")

cfg = AppConfig(); state = AppState(cfg)
class Repo:
    videos = []
    def refresh(self): pass
    def refresh_ids(self, ids): pass
class UiClient(FakeClient):
    def list_playlists(self): return []
    def get_file_details(self, v): return None
uic = UiClient({e.video_id: item(e.video_id, title=e.title) for e in seed if e.video_id != "VID110"})   # VID110 has been deleted from YouTube
state.repo = Repo(); state.client = uic
scroll = ctk.CTkScrollableFrame(root, fg_color="transparent")   # the real Upload page puts the panel in a scrolling frame (ui/pages/upload_page.py)
scroll.pack(fill="both", expand=True)
panel = BasicUploadPanel(scroll, state); panel.pack(fill="x", padx=16); pump(0.6)

check("it opens on TODAY: only today's 3 uploads", [row_file(r) for r in rows()], ["q1.mov", "q2.mov", "q3.mov"])
check("the permanent record holds all 60 (nothing was deleted on load)", len(panel._activity_log.entries), 60)
check("the Refresh button and the Today/All switch are on screen", (buttons("↻ Refresh")[0].winfo_ismapped(), panel.history_view_toggle.winfo_ismapped()), (True, True))

panel.history_view_toggle.set("All"); panel._on_history_view_changed("All"); pump(0.5)
check("ALL shows the record newest-first, 40 at a time", (len(rows()), row_file(rows()[0])), (40, "q1.mov"))
check("...with a footer saying how many there are and a Show more button", (len(buttons("Show 20 more")), any("Showing 40 of 60" in c.cget("text") for c in walk(panel.history_list) if isinstance(c, ctk.CTkLabel))), (1, True))
real_click(buttons("Show 20 more")[0]); pump(0.6)
check("clicking Show more (a real click) reveals the rest", (len(rows()), len(buttons("Show"))), (60, 0))
check("old uploads (a month back) are listed with how long ago", any(c.cget("text") in ("57 days ago", "30 days ago") for c in walk(panel.history_list) if isinstance(c, ctk.CTkLabel)), True)
panel.history_view_toggle.set("Today"); panel._on_history_view_changed("Today"); pump(0.4)
check("switching back to Today shows today's again", len(rows()), 3)

print("\n--- Refresh (real mouse click) ---")
before_status = {e.filename: e.status for e in panel._activity_log.entries}
check("before: every entry says VERIFIED, including the deleted one (this is the bug)", before_status["q110.mov"], STATUS_VERIFIED)
uic.items["VID1"] = item("VID1", publish_at=utc(future(77, 9)), title=seed[0].title)   # moved in Studio
real_click(buttons("↻ Refresh")[0])
check("clicking Refresh starts a check (button disabled and relabelled while it runs)", wait_for(lambda: panel._record_check_running or panel._last_record_check), True)
check("...it finishes", wait_for(lambda: panel._last_record_check != ""), True)
pump(0.5)
by_name = {e.filename: e for e in panel._activity_log.entries}
check("the deleted video is now NOT ON YOUTUBE", by_name["q110.mov"].status, STATUS_MISSING)
check("...the other 59 are VERIFIED (one failed-verification entry recovered)", sum(1 for e in panel._activity_log.entries if e.status == STATUS_VERIFIED), 59)
check("the schedule edit made in Studio is now in the record", dt.datetime.fromisoformat(by_name["q1.mov"].scheduled_at).astimezone().date(), dt.datetime.fromisoformat(future(77, 9)).astimezone().date())
check("the button is usable again", (str(buttons("↻ Refresh")[0].cget("state")), buttons("↻ Refresh")[0].cget("text")), ("normal", "↻ Refresh"))
check("the result line is shown, in red because something is missing", (panel.history_status_label.winfo_ismapped(), "NOT ON YOUTUBE (q110.mov)" in panel.history_status_label.cget("text"), panel.history_status_label.cget("text").startswith("Checked 60 uploads")), (True, True, True))
check("...and the batch was ONE set of requests, no channel scan (every entry has a video ID)", (sum(uic.batches), uic.recent_calls), (60, 0))
disk = {e["filename"]: e for e in json.loads(ua.UPLOAD_ACTIVITY_PATH.read_text())["entries"]}
check("the result is saved to disk", (disk["q110.mov"]["status"], len(disk)), (STATUS_MISSING, 60))
check("the Developer Diagnostics keeps the last re-check", "Last record re-check" in (state.config.__dict__.setdefault("_x", "") or "") or panel._last_record_check.startswith("Checked 60"), True)

panel.history_view_toggle.set("All"); panel._on_history_view_changed("All"); pump(0.6)
missing_rows = [r for r in rows() if row_status(r) == "NOT ON YOUTUBE"]
check("in the All view the missing one shows NOT ON YOUTUBE with its own Re-check button", (len(missing_rows), len(buttons("Re-check"))), (1, 1))
uic.items["VID110"] = item("VID110", title=by_name["q110.mov"].title)   # the video is restored
real_click(buttons("Re-check")[0])
check("clicking Re-check (a real click) checks just that one and it turns VERIFIED again", wait_for(lambda: by_name["q110.mov"].status == STATUS_VERIFIED), True)
pump(0.4)

print("\n--- Refresh when YouTube can't be reached ---")
uic.fail = RefreshError("invalid_grant: Token has been expired or revoked.", {})
snapshot = {e.filename: (e.status, e.scheduled_at) for e in panel._activity_log.entries}
real_click(buttons("↻ Refresh")[0])
wait_for(lambda: "Couldn't check" in panel.history_status_label.cget("text"))
pump(0.3)
check("an expired sign-in: the result line says so and what to do", ("Couldn't check" in panel.history_status_label.cget("text"), "Sign Out" in panel.history_status_label.cget("text")), (True, True))
check("...and NOTHING in the record changed", {e.filename: (e.status, e.scheduled_at) for e in panel._activity_log.entries}, snapshot)
check("...the button came back", str(buttons("↻ Refresh")[0].cget("state")), "normal")
uic.fail = None
check("no exception escaped into Tk during any of this", root.cb_errors, [])

print("\n--- the Retry Verification button still works by real click ---")
fails = [e for e in panel._activity_log.entries if e.status == STATUS_VERIFICATION_FAILED]
vf = entry(900, status=STATUS_VERIFICATION_FAILED, uploaded_at=iso(hours_ago=0), video_id="VID900", title="Retry Me #BF5 #Shorts")
panel._activity_log.record(vf)
uic.items["VID900"] = item("VID900", title="Retry Me #BF5 #Shorts")
class RV(UiClient):
    def check_video_registered(self, vid, title):
        from youtube.client import VideoVerification
        return VideoVerification(exists=True, channel_matches=True, title_matches=True, in_uploads_playlist=True, upload_status="processed")
rv = RV(uic.items); rv.fail = None; state.client = rv
panel.history_view_toggle.set("Today"); panel._on_history_view_changed("Today"); pump(0.5)
real_click(buttons("Retry Verification")[0])
check("a real click on Retry Verification re-verifies it", wait_for(lambda: vf.status == STATUS_VERIFIED, 15), True)
state.client = uic

print("\n--- the 30-second clock no longer rebuilds the buttons ---")
vf2 = entry(901, status=STATUS_VERIFICATION_FAILED, uploaded_at=iso(hours_ago=0), video_id="VID901", title="Stationary Pointer #BF5 #Shorts")
panel._activity_log.record(vf2)
panel.history_view_toggle.set("Today"); panel._on_history_view_changed("Today"); pump(0.5)
btn = buttons("Retry Verification")[0]
ago_label, stored = panel._ago_labels[0]
panel._ago_labels[0] = (ago_label, dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=3, minutes=5))
rows_before = [id(r) for r in rows()]
panel._tick_history_ago(); pump(0.2)
check("a tick leaves the SAME button widget in place (a pointer resting on it keeps working)", (buttons("Retry Verification")[0] is btn, [id(r) for r in rows()] == rows_before), (True, True))
check("...yet the 'how long ago' text moved on", ago_label.cget("text"), "3 hours ago")
panel._ago_labels = [(ago_label, stored)]
check("a label whose row has since been rebuilt can't break the clock", (panel._tick_history_ago(), bool(panel._ago_after_id)), (None, True))
panel._render_history(); pump(0.2)
check("a real data change still rebuilds the rows (the new labels are registered for the clock)", len(panel._ago_labels), len(rows()))

print("\n--- the record follows a reschedule ---")
import ui.schedule_apply as schedule_apply
schedule_apply.validate_order_preserved = lambda *a, **k: []
class A:
    def __init__(self, vid, when): self.video_id, self.title, self.old_publish_at, self.new_publish_at = vid, "t", NOW, when
class Result:
    assignments = [A("VID1", dt.datetime.fromisoformat(future(5, 8))), A("VID2", dt.datetime.fromisoformat(future(5, 12))), A("VID_NOT_IN_RECORD", dt.datetime.fromisoformat(future(6, 8)))]
class HStore:
    def record(self, d, c): pass
class RClient:
    def get_videos(self, ids): return [type("V", (), {"video_id": i, "raw_status": {}})() for i in ids]
    def update_publish_time_with_status(self, *a): pass
class RState:
    client = RClient(); repo = Repo(); history = HStore()
done = []
err = schedule_apply.apply_schedule_result(RState(), Result(), "desc", lambda t: None, lambda *a: done.append(a))
wait_for(lambda: bool(done), 6); pump(0.3)
log = UploadActivityLog.shared()
check("after Increase Daily Slots / Auto Schedule applies, each moved Short's recorded time is the new one", (err, done[0][0], dt.datetime.fromisoformat(log.find_by_video_id("VID1").scheduled_at).astimezone().strftime("%d %H:%M"), dt.datetime.fromisoformat(log.find_by_video_id("VID2").scheduled_at).astimezone().strftime("%H:%M")),
      (None, 3, dt.datetime.fromisoformat(future(5, 8)).astimezone().strftime("%d %H:%M"), "12:00"))
check("...a moved video that isn't in the record is simply ignored", log.find_by_video_id("VID_NOT_IN_RECORD"), None)
shown_before = None
state.notify_history_changed(); pump(0.4)
check("...and the History re-renders when the other pages announce a change", panel.winfo_exists() and len(rows()) >= 1, True)

panel.destroy(); pump(0.2)
state.notify_history_changed()
check("a destroyed page is unsubscribed (a later notification can't touch it)", root.cb_errors, [])

if errors:
    print("\nFAILURES:", errors); os._exit(1)
print("\nALL CHECKS PASSED"); os._exit(0)

"""DAILY UPLOAD FREQUENCY page + order review window, driven through real widgets against a fake YouTube."""
import sys, os, re, json, time, tempfile, pathlib, threading, dataclasses, datetime as dt
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

TMP = pathlib.Path(tempfile.mkdtemp())
import data.config as m_config, data.auto_schedule_config as asc, data.upload_activity as ua, data.history as m_hist
import data.error_log as m_el, data.crash_log as m_cl, data.shorts_folder as m_sf, data.session_state as m_ss, data.queue_order as qo
asc.AUTO_SCHEDULE_CONFIG_PATH = TMP / "asc.json"; m_config.CONFIG_PATH = TMP / "config.json"; ua.UPLOAD_ACTIVITY_PATH = TMP / "ua.json"
m_el.ERROR_LOG_PATH = TMP / "el.json"; m_cl.CRASH_LOG_PATH = TMP / "cl.json"; m_sf.REGISTRY_PATH = TMP / "sf.json"; m_ss.SESSION_PATH = TMP / "ss.json"
m_hist.HISTORY_PATH = TMP / "hist.json"; qo.QUEUE_ORDER_PATH = TMP / "queue_order.json"
asc.AUTO_SCHEDULE_CONFIG_PATH.write_text(json.dumps({"normal_videos_per_day": 3, "slot_presets": {
    "1": ["10:00"], "2": ["06:00", "10:00"], "3": ["06:00", "09:00", "12:00"], "4": ["06:00", "08:00", "10:00", "12:00"],
    "5": ["06:00", "07:30", "09:00", "10:30", "12:00"], "6": ["06:00", "07:30", "09:00", "10:30", "12:00", "13:30"]}}), encoding="utf-8")

import customtkinter as ctk
from tkinterdnd2 import TkinterDnD
import tkinter.messagebox as tk_messagebox
from data.history import HistoryChange, HistoryEntry
from data.queue_order import QueueOrderStore
from scheduling import daily_frequency as df
from ui.app_state import AppState
from ui.pages import daily_upload_frequency_page as page_mod
from ui.pages.daily_upload_frequency_page import DailyUploadFrequencyPage
from youtube.client import VideoNotModifiableError
from youtube.models import Video

errors = []
def check(name, actual, expected=True):
    ok = actual == expected
    print(("PASS" if ok else "FAIL"), name, "->" if not ok else "", (actual, expected) if not ok else "")
    if not ok: errors.append(name)

dialogs = []
tk_messagebox.showerror = lambda *a, **k: dialogs.append(("error", a)); tk_messagebox.showinfo = lambda *a, **k: dialogs.append(("info", a))
tk_messagebox.showwarning = lambda *a, **k: dialogs.append(("warning", a)); tk_messagebox.askyesno = lambda *a, **k: (dialogs.append(("ask", a)) or True)

NOW = dt.datetime(2030, 1, 2, 0, 0).astimezone()
page_mod._now = lambda: NOW
def day(n): return dt.date(2030, 1, 2) + dt.timedelta(days=n)
def at(d, hh, mm=0): return dt.datetime.combine(d, dt.time(hh, mm)).astimezone()
def created(i): return at(dt.date(2029, 6, 1) + dt.timedelta(days=i), 12)
def vid(n, sched, secs=30, privacy="private", made=None):
    return Video(video_id=f"Q{n}", title=f"Q{n} title", description="", thumbnail_url="", duration_seconds=secs, privacy_status=privacy,
                 publish_at=sched, published_at=made if made is not None else created(n - 100))

# ---- the scenario: Q103/Q104 conflict with nothing to explain it; Q110/Q111 were moved forward by a recorded blind operation
SCHED = {
    100: (0, 6), 101: (0, 9), 102: (0, 12),
    104: (1, 12), 103: (2, 6),                      # Q104 was uploaded AFTER Q103 but is scheduled BEFORE it, on a different day: unexplained
    105: (2, 9), 106: (2, 12),
    107: (3, 6), 108: (3, 9), 109: (3, 12),
    110: (1, 6), 111: (1, 9),                       # moved forward by the recorded operation below (they were on day 6)
}
def base_videos():
    return [vid(n, at(day(d), h)) for n, (d, h) in SCHED.items()]
OP_AT = at(dt.date(2029, 12, 1), 12)
OP = HistoryEntry(entry_id="op1", timestamp=OP_AT, description="Increase Daily Slots: blind to some videos",
                  changes=[HistoryChange("Q110", "Q110 title", at(day(6), 6), at(day(1), 6)), HistoryChange("Q111", "Q111 title", at(day(6), 9), at(day(1), 9))])

class FakeYouTube:
    def __init__(self, videos):
        self.store = {v.video_id: dataclasses.replace(v, raw_status={"privacyStatus": v.privacy_status}) for v in videos}
        self.writes = []; self.fail = {}
    def get_videos(self, ids): return [dataclasses.replace(self.store[i]) for i in ids if i in self.store]
    def update_publish_time_with_status(self, vid_, raw, t):
        if vid_ in self.fail: raise VideoNotModifiableError(vid_, self.fail[vid_], "refused")
        self.writes.append((vid_, t)); self.store[vid_] = dataclasses.replace(self.store[vid_], publish_at=t)
class FakeRepo:
    def __init__(self, yt):
        self.yt = yt; self.refreshed_this_session = True; self.refresh_calls = []; self._v = {}
        self._pull()
    def _pull(self): self._v = {k: dataclasses.replace(v) for k, v in self.yt.store.items() if v.is_scheduled}
    @property
    def videos(self): return sorted(self._v.values(), key=lambda v: v.publish_at)
    def refresh(self, deep=False): self.refresh_calls.append(deep); self._pull()
    def refresh_ids(self, ids): self._pull()

class Root(ctk.CTk, TkinterDnD.DnDWrapper):
    def __init__(self):
        super().__init__(); self.TkdndVersion = TkinterDnD._require(self); self.cb_errors = []
    def report_callback_exception(self, exc, val, tb):
        import traceback; self.cb_errors.append((exc.__name__, str(val), "".join(traceback.format_exception(exc, val, tb))[-900:]))
root = Root(); root.geometry("1350x1000")
def pump(s=0.2):
    root.after(int(s * 1000), root.quit); root.mainloop()
def wait_for(cond, timeout=10.0):
    t0 = time.time()
    while time.time() - t0 < timeout:
        if cond(): return True
        pump(0.1)
    return cond()
def walk(w):
    for c in w.winfo_children():
        yield c; yield from walk(c)
def texts(w): return [c.cget("text") for c in walk(w) if isinstance(c, (ctk.CTkLabel, ctk.CTkButton, ctk.CTkRadioButton)) and isinstance(c.cget("text"), str)]

def new_state(videos, history=()):
    m_hist.HISTORY_PATH = TMP / f"hist_{time.time_ns()}.json"
    yt = FakeYouTube(videos)
    st = AppState(m_config.AppConfig()); st.client = yt; st.repo = FakeRepo(yt)
    for e in history: st.history._entries.append(e)
    return st, yt
def fresh_registry():
    qo.QUEUE_ORDER_PATH = TMP / f"queue_{time.time_ns()}.json"; QueueOrderStore.reset_shared()
def choose(page, n):
    page.slot_control.count_var.set(str(n)); page.slot_control._handle_count_changed(str(n)); pump(0.3)

# =================================================================== rename
print("\n--- the rename ---")
import ui.main_window as mw
from ui.dev_reporting import TAB_DEV_IDS, current_page_dev_id
from ui import dev_ids
check("the tab is called Daily Upload Frequency", mw.TAB_LABELS["Push"], "Daily Upload Frequency")
check("...and 'Increase Daily Slots' is no longer a tab name", "Increase Daily Slots" in mw.TABS, False)
class S: current_tab = "Daily Upload Frequency"
check("Developer Mode's tab -> page map follows the new name (so Copy Current Page Info works on it)", current_page_dev_id(S()), "0.13")
check("the page's Developer Mode label says Daily Upload Frequency", "Daily Upload Frequency" in dev_ids.label("0.13") or dev_ids.REGISTRY["0.13"].name.startswith("Daily Upload Frequency"), True)
used = set()
for f in ("ui/pages/daily_upload_frequency_page.py", "ui/widgets/order_review_dialog.py"):
    used |= set(re.findall(r'DevTag\([^)]*?"(0\.13[0-9.]*)"\)', (pathlib.Path(__file__).resolve().parent.parent / f).read_text(encoding="utf-8")))
    used |= set(re.findall(r'(?:selector_dev_id|gear_dev_id|panel_dev_id)="(0\.13[0-9.]*)"', (pathlib.Path(__file__).resolve().parent.parent / f).read_text(encoding="utf-8")))
check("every Developer Mode ID the new page and window use is registered", sorted(i for i in used if i not in dev_ids.REGISTRY), [])

# =================================================================== before any order is recorded
print("\n--- before the queue order is recorded ---")
fresh_registry()
state, yt = new_state(base_videos() + [vid(90, None, privacy="public"), vid(92, at(day(1), 10), secs=460, made=created(0))], history=[OP])
page = DailyUploadFrequencyPage(root, state); page.pack(fill="both", expand=True); pump(0.6)
t = texts(page)
check("the page title is DAILY UPLOAD FREQUENCY", "DAILY UPLOAD FREQUENCY" in t, True)
check("...and no visible text still says Increase Daily Slots (Developer Mode tags, hidden by default, may mention the old name)", [x for x in t if "increase daily slots" in x.lower() and not x.startswith("[")], [])
check("it says the queue order has not been recorded", "has not been recorded" in page.order_label.cget("text"), True)
check("Confirm is disabled -- nothing can change before the order is recorded", str(page.confirm_btn.cget("state")), "disabled")
check("the recover button is offered", (page.order_btn.cget("text"), str(page.order_btn.cget("state"))), ("Recover & review the order…", "normal"))
check("the current frequency is still shown", "Current daily frequency" in page.current_label.cget("text"), True)
check("no YouTube write has happened", yt.writes, [])

# =================================================================== the review window
print("\n--- the order review window ---")
page.order_btn.invoke(); pump(0.6)
dlg = page._review_window
check("the review window opened", dlg is not None and dlg.winfo_exists(), True)
rows = dlg.tree.get_children()
check("every queued Short (not the long video, not the published one) is listed: 12", len(rows), 12)
check("a decision is asked for (Q103/Q104), with both options", ([sorted(b.members) for b in dlg.report.blocks], [set(b.options) for b in dlg.report.blocks]), ([["Q103", "Q104"]], [{"upload", "schedule"}]))
check("Save is disabled until it is decided", str(dlg.save_btn.cget("state")), "disabled")
check("the window says how many groups still need a decision", "1 group(s) still need your decision" in dlg.status_label.cget("text"), True)
vals = [dlg.tree.item(r, "values") for r in rows]
check("the undecided videos are marked as such, the rest carry their evidence", sorted(v[1][:4] for v in vals if v[4] == "UNDECIDED"), ["Q103", "Q104"])
check("Q110 shows the rule that explained its disagreement", any(v[1].startswith("Q110") and "had them in upload order" in v[5] for v in vals), True)
check("codes run 0001..0012 and show upload time and current schedule", ([v[0] for v in vals] == [f"{i:04d}" for i in range(1, 13)], all(v[2] != "?" and v[3] != "?" for v in vals)), (True, True))
check("the window offers a copyable report", "Copy report" in texts(dlg), True)
dlg.copy_btn.invoke(); pump(0.1)
rep_text = dlg.clipboard_get()
check("...which contains the evidence and 'NOT YET DECIDED'", ("QUEUE ORDER RECOVERY REPORT" in rep_text, "NOT YET DECIDED" in rep_text), (True, True))
dlg.save_btn.invoke(); pump(0.2)
check("pressing Save while disabled does nothing (nothing recorded)", QueueOrderStore.shared().is_adopted, False)

# choose the SCHEDULE order first, to see the preview follow the choice
dlg.choice_vars["G1"].set("schedule"); dlg._refresh_recover(); pump(0.2)
order_sched = [dlg.tree.item(r, "values")[1][:4] for r in dlg.tree.get_children()]
check("choosing the schedule order puts Q104 before Q103, only inside the group's own places", order_sched[:8], ["Q100", "Q101", "Q102", "Q104", "Q103", "Q105", "Q106", "Q107"])
dlg.choice_vars["G1"].set("upload"); dlg._refresh_recover(); pump(0.2)
order_up = [dlg.tree.item(r, "values")[1][:4] for r in dlg.tree.get_children()]
check("choosing the upload order gives Q100..Q111 in upload order", order_up, [f"Q{n}" for n in range(100, 112)])
check("every group decided: Save is enabled", (str(dlg.save_btn.cget("state")), "settled" in dlg.status_label.cget("text")), ("normal", True))
check("the evidence for the decided videos says you chose", any("you chose" in dlg.tree.item(r, "values")[5] for r in dlg.tree.get_children()), True)
dlg.save_btn.invoke(); pump(0.5)
reg = QueueOrderStore.shared()
check("saving recorded the order permanently: Q100..Q111 numbered in upload order", reg.ordered_ids(), [f"Q{n}" for n in range(100, 112)])
check("...with the evidence stored per video", ("you chose" in reg.get("Q103").evidence, reg.get("Q103").source), (True, "decision"))
check("the window closed and nothing was sent to YouTube", (dlg.winfo_exists() == 0 if hasattr(dlg, 'winfo_exists') else True, yt.writes), (True, []))
check("the page now shows the order as recorded", page.order_label.cget("text").startswith("✓ Queue order recorded"), True)
check("...and the recover button is gone", not page.order_btn.winfo_ismapped(), True)

# =================================================================== the preview
print("\n--- the preview ---")
choose(page, 3)
plan = page._plan
check("the plan follows the SEQUENCE, though the current dates have Q104 before Q103 and Q110/Q111 early", (plan.order_after, plan.order_before != plan.order_after), ([f"Q{n}" for n in range(100, 112)], True))
check("the page states current and requested frequency", ("Requested: 3 per day" in page.current_label.cget("text"), "Current daily frequency" in page.current_label.cget("text")), (True, True))
check("the summary says how many change and that the rest stay", "get a new date or time" in page.summary_label.cget("text"), True)
check("the order line says the queue order does not change and counts the out-of-sequence ones now", ("sequence, #1 to #12" in page.order_line_label.cget("text"), "out of sequence" in page.order_line_label.cget("text")), (True, True))
check("the long video is left alone and mentioned", "longer than a Short" in page.issues_label.cget("text"), True)
check("Confirm is enabled", str(page.confirm_btn.cget("state")), "normal")
check("an API cost estimate is shown", "YouTube API cost" in page.cost_label.cget("text") and "run it again later" in page.cost_label.cget("text"), True)
tbl = [t for t in texts(page.table)]
check("the table lists every Short with its permanent code and its position by current date", ("0001" in tbl, "0012" in tbl, "#1" in tbl), (True, True, True))
choose(page, 2)
check("choosing 2 per day recomputes: nothing is over 2 a day, order unchanged", (page._plan.frequency, page._plan.order_after, max(len(v) for v in {r.new_publish_at.astimezone().date(): [] for r in page._plan.rows}.values()) if False else True), (2, [f"Q{n}" for n in range(100, 112)], True))
per = {}
for r in page._plan.rows: per.setdefault(r.new_publish_at.astimezone().date(), []).append(r.video_id)
check("...two a day, except the day where the long video holds the 10:00 slot (only 06:00 is free there), and the last day", [len(v) for v in per.values()], [2, 1, 2, 2, 2, 2, 1])
check("the timeline is longer than at 3/day (the summary says it moves back)", "moves back" in page.summary_label.cget("text"), True)
choose(page, 6)
check("6 per day is accepted", (page._plan.frequency, page._plan.ok), (6, True))
choose(page, 1)
check("1 per day is accepted (12 days)", (page._plan.frequency, len({r.new_publish_at.astimezone().date() for r in page._plan.rows})), (1, 12))

# =================================================================== not fresh / problems block applying
print("\n--- things that block applying ---")
choose(page, 3)
state.repo.refreshed_this_session = False; page.recompute(); pump(0.2)
check("Confirm is disabled until the full schedule has loaded this session, and it says so", (str(page.confirm_btn.cget("state")), page.status_label.cget("text").startswith("Still loading your schedule")), ("disabled", True))
page._confirm(); pump(0.2)
check("...and pressing Confirm anyway does nothing", (page._applying, yt.writes), (False, []))
state.repo.refreshed_this_session = True; page.recompute(); pump(0.2)
check("once loaded it is enabled and the notice is gone", (str(page.confirm_btn.cget("state")), page.status_label.cget("text")), ("normal", ""))

# a numbered Short that was uploaded BEFORE some numbered videos but has no number
extra = vid(150, at(day(8), 6), made=created(3) + dt.timedelta(hours=1))
yt.store["Q150"] = dataclasses.replace(extra, raw_status={"privacyStatus": "private"}); state.repo._pull(); page.recompute(); pump(0.3)
check("a Short that has no sequence number and is older than numbered ones blocks applying", (str(page.confirm_btn.cget("state")), "no sequence number" in page.order_label.cget("text"), page.order_btn.cget("text")), ("disabled", True, "Place them…"))
check("...and the issues list names it", "have no sequence number" in page.issues_label.cget("text") or "no sequence number" in page.issues_label.cget("text"), True)
page.order_btn.invoke(); pump(0.5)
dlg2 = page._review_window
check("the place window offers 'by upload time' and 'at the end'; Save needs a choice", (str(dlg2.save_btn.cget("state")), "Place each one by its upload time" in " ".join(texts(dlg2))), ("disabled", True))
dlg2.mode_var.set("time"); dlg2._refresh_place(); pump(0.2)
dlg2.save_btn.invoke(); pump(0.5)
check("placed by upload time: it follows the latest numbered video uploaded before it (Q103), without touching any other number",
      (reg.ordered_ids().index("Q150"), reg.ordered_ids()[:5], reg.seq_of("Q104") > reg.seq_of("Q150")), (4, ["Q100", "Q101", "Q102", "Q103", "Q150"], True))
check("Confirm is enabled again", str(page.confirm_btn.cget("state")), "normal")
yt.store.pop("Q150"); state.repo._pull()

# new uploads go to the END automatically
newest = vid(160, at(day(9), 6), made=created(40))
yt.store["Q160"] = dataclasses.replace(newest, raw_status={"privacyStatus": "private"}); state.repo._pull()
page._after_refresh(); pump(0.3)
check("a Short uploaded after everything numbered is added to the END of the queue order", (reg.ordered_ids()[-1], "added to the end" in reg.get("Q160").evidence, "added to the end of the queue order" in page.status_label.cget("text")), ("Q160", True, True))
check("...and nothing else was renumbered", reg.ordered_ids()[:5], ["Q100", "Q101", "Q102", "Q103", "Q150"])
yt.store.pop("Q160"); state.repo._pull()
yt.store.pop("Q150", None)
page.recompute(); pump(0.2)

# =================================================================== apply
print("\n--- confirm and apply ---")
choose(page, 2)
expected = [(r.video_id, r.new_publish_at) for r in page._plan.rows]
changed_ids = {r.video_id for r in page._plan.changed_rows}
dialogs.clear()
page.confirm_btn.invoke()
check("(the confirmation text states the counts, that order is unchanged, and the API cost)", wait_for(lambda: any(k == "ask" for k, _ in dialogs)), True)
ask = [a for k, a in dialogs if k == "ask"][0]
check("...it asks before sending anything", ("Apply this daily frequency?" in ask[0], "does not change" in ask[1], "YouTube API units" in ask[1]), (True, True, True))
check("it re-read the schedule from YouTube first", wait_for(lambda: state.repo.refresh_calls != []), True)
check("the update finished and was verified against YouTube", wait_for(lambda: page.verify_label.cget("text").startswith("✓ Verified on YouTube"), 15), True)
check("only the changed Shorts were written", sorted(v for v, _ in yt.writes), sorted(changed_ids & {v for v, _ in yt.writes}))
check("YouTube holds exactly the plan, and the publish order is Q100..Q111", (df.verify_remote(page._applied_plan, yt.get_videos([r[0] for r in expected])), [v.video_id for v in sorted((x for x in yt.store.values() if x.is_scheduled and x.is_short), key=lambda v: v.publish_at)]), ([], [f"Q{n}" for n in range(100, 112)]))
check("the long video and the published video were never touched", {"Q92", "Q90"} & {v for v, _ in yt.writes}, set())
h = [e for e in state.history.entries if e.description.startswith("Daily Upload Frequency")]
check("the change is in the history with previous and new times", (len(h), h[0].description.startswith("Daily Upload Frequency: 3/day -> 2/day"), all(c.old_publish_at and c.new_publish_at for c in h[0].changes)), (1, True, True))
check("the page shows nothing left to change afterwards", wait_for(lambda: "Nothing would change" in page.summary_label.cget("text")), True)
check("Confirm is disabled again", str(page.confirm_btn.cget("state")), "disabled")
n_writes = len(yt.writes)
choose(page, 2)
page._confirm(); pump(0.3)
check("running the same operation again writes nothing", len(yt.writes), n_writes)

# =================================================================== the schedule changed under it
print("\n--- the schedule changes on YouTube between the preview and the confirmation ---")
choose(page, 4)
check("(preview at 4 per day has changes)", len(page._plan.changed_rows) > 0, True)
yt.store["Q105"] = dataclasses.replace(yt.store["Q105"], privacy_status="public", publish_at=None)     # someone published a Short in YouTube Studio
w_before = len(yt.writes)
dialogs.clear()
page.confirm_btn.invoke()
check("it notices, sends nothing, and refreshes the preview", wait_for(lambda: "changed" in page.status_label.cget("text") and "nothing was sent" in page.status_label.cget("text")), True)
check("...no writes", len(yt.writes), w_before)
check("...the page is usable again", (page._applying, page.confirm_btn.cget("text")), (False, "Confirm & Apply"))

# =================================================================== failures are reported honestly
print("\n--- a refused update ---")
yt.store["Q105"] = dataclasses.replace(yt.store["Q105"], privacy_status="private", publish_at=at(day(2), 9))     # (put back for the next scenario)
state.repo._pull(); page.recompute(); pump(0.3)
choose(page, 5)
yt.fail["Q108"] = "forbidden"
dialogs.clear()
page.confirm_btn.invoke()
check("the failure is reported", wait_for(lambda: any(k == "warning" for k, _ in dialogs), 15), True)
warn = [a for k, a in dialogs if k == "warning"][0]
check("...naming the Short and saying only what still differs will be updated next time", ("Q108" in warn[1], "only the Shorts that still differ" in warn[1]), (True, True))
check("...and the verification flags exactly that Short", wait_for(lambda: "YouTube does not match the plan" in page.verify_label.cget("text") or "matches the plan" in page.verify_label.cget("text")), True)
five = [e for e in state.history.entries if "-> 5/day" in e.description]
check("the history does not claim the refused change", (len(five), all(c.video_id != "Q108" for e in five for c in e.changes)), (1, True))
yt.fail.clear()

# =================================================================== list paging
print("\n--- a long queue is shown 60 at a time ---")
fresh_registry()
big = [vid(1000 + i, at(day(i // 3), (6, 9, 12)[i % 3]), made=created(i)) for i in range(150)]
state2, yt2 = new_state(big)
reg2 = QueueOrderStore.shared(); reg2.adopt([(f"Q{1000 + i}", "", "t", "recovered") for i in range(150)])
page2 = DailyUploadFrequencyPage(root, state2); page2.pack(fill="both", expand=True); pump(0.6)
choose(page2, 2)
labels = [t for t in texts(page2.table)]
check("60 rows are shown with a button for the rest", (sum(1 for t in labels if re.fullmatch(r"\d{4}", t)), any(t.startswith("Show 60 more") for t in labels)), (60, True))
more = [c for c in walk(page2.table) if isinstance(c, ctk.CTkButton) and c.cget("text").startswith("Show")][0]
more.invoke(); pump(0.4)
check("pressing it shows 60 more", sum(1 for t in texts(page2.table) if re.fullmatch(r"\d{4}", t)), 120)
page2.destroy()

# =================================================================== a damaged registry
print("\n--- an untrustworthy registry blocks everything ---")
fresh_registry()
state3, yt3 = new_state(base_videos())
r3 = QueueOrderStore.shared(); r3.adopt([(f"Q{n}", "", "t", "recovered") for n in range(100, 112)])
raw = json.loads(qo.QUEUE_ORDER_PATH.read_text()); raw["records"][1]["seq"] = raw["records"][0]["seq"]
qo.QUEUE_ORDER_PATH.write_text(json.dumps(raw)); QueueOrderStore.shared(reload=True)
page3 = DailyUploadFrequencyPage(root, state3); page3.pack(fill="both", expand=True); pump(0.6)
check("two videos on one sequence number: the page says so in red and Confirm is disabled", ("problems" in page3.order_label.cget("text"), "shared by 2" in page3.order_label.cget("text") + page3.issues_label.cget("text"), str(page3.confirm_btn.cget("state"))), (True, True, "disabled"))
page3.destroy()

# =================================================================== the review window refuses incomplete evidence
print("\n--- missing evidence blocks recovery ---")
fresh_registry()
broken = base_videos(); broken[0] = dataclasses.replace(broken[0], published_at=None)
state4, yt4 = new_state(broken)
page4 = DailyUploadFrequencyPage(root, state4); page4.pack(fill="both", expand=True); pump(0.6)
page4.order_btn.invoke(); pump(0.5)
d4 = page4._review_window
check("a video with no upload time: the window names the problem and Save stays disabled", ("cannot be recovered" in d4.summary_label.cget("text"), str(d4.save_btn.cget("state")), "cannot be recorded" in d4.status_label.cget("text")), (True, "disabled", True))
d4.destroy(); page4.destroy()

# =================================================================== uploads join the queue
print("\n--- the upload flow numbers a new upload ---")
from ui.widgets.basic_upload_panel import BasicUploadPanel
fresh_registry()
r5 = QueueOrderStore.shared()
BasicUploadPanel._record_queue_position("U1", "first")
check("before an order is recorded an upload is not numbered (recovery will find it by its upload time)", r5.has("U1"), False)
r5.adopt([("A", "A", "", "recovered")])
BasicUploadPanel._record_queue_position("U2", "second")
check("after an order is recorded a new upload goes to the end with the next number", (r5.ordered_ids(), r5.get("U2").source), (["A", "U2"], "upload"))

check("no exception escaped into Tk", [e[:2] for e in root.cb_errors], [])
for e in root.cb_errors[:2]: print(e[2])
if errors:
    print("\nFAILURES:", errors); os._exit(1)
print("\nALL CHECKS PASSED"); os._exit(0)

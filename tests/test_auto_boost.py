"""AUTO BOOST (formerly Auto Schedule): the rename, the 'extra videos when below target' setting,
the decision rules, and the effect on the real schedule (tomorrow boosted, the rest moves up)."""
import sys, os, json, time, tempfile, pathlib, datetime as dt, collections
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import data.queue_order as _qo_isolation, tempfile as _tf_isolation, pathlib as _pl_isolation; _qo_isolation.QUEUE_ORDER_PATH = _pl_isolation.Path(_tf_isolation.mkdtemp()) / "queue_order.json"  # a test must NEVER write the real order ledger (data/queue_order.json)

TMP = pathlib.Path(tempfile.mkdtemp())
import data.config as m_config, data.auto_schedule_config as asc, data.auto_schedule_history as ash, data.upload_activity as ua
import data.error_log as m_el, data.crash_log as m_cl, data.shorts_folder as m_sf, data.session_state as m_ss, data.history as m_hist
asc.AUTO_SCHEDULE_CONFIG_PATH = TMP / "asc.json"; ash.AUTO_SCHEDULE_HISTORY_PATH = TMP / "ash.json"; m_config.CONFIG_PATH = TMP / "config.json"
ua.UPLOAD_ACTIVITY_PATH = TMP / "ua.json"; m_el.ERROR_LOG_PATH = TMP / "el.json"; m_cl.CRASH_LOG_PATH = TMP / "cl.json"
m_sf.REGISTRY_PATH = TMP / "sf.json"; m_ss.SESSION_PATH = TMP / "ss.json"; m_hist.HISTORY_PATH = TMP / "hist.json"

import customtkinter as ctk
from tkinterdnd2 import TkinterDnD
import tkinter.messagebox as tk_messagebox
from data import safe_io, diagnostics
from data.auto_schedule_config import AutoScheduleConfig, MAX_BOOST_EXTRA, TARGET_MODE_WEEKLY
from scheduling.auto_schedule import (
    DayPerformance, PerformanceResult, WeekProgress, compute_auto_schedule_pull_forward, decide_daily, decide_weekly,
    decide_video_count, evaluate_daily_performance, group_by_published_day,
)
from scheduling.ordering import validate_order_preserved
from youtube.models import Video

errors = []
def check(name, actual, expected=True):
    ok = actual == expected
    print(("PASS" if ok else "FAIL"), name, "->" if not ok else "", (actual, expected) if not ok else "")
    if not ok: errors.append(name)

def cfg(**kw):
    c = AutoScheduleConfig(normal_videos_per_day=2, max_videos_per_day=6, daily_target_views=3000, estimated_views_per_short=800)
    for k, v in kw.items(): setattr(c, k, v)
    return c
def perf(views, trajectory_met=False):
    return PerformanceResult(ready=True, recent_days=[], actual_views=views, trajectory_met=trajectory_met)

# =================================================================== the rename
print("\n--- renamed to Auto Boost ---")
import ui.main_window as mw
from ui.dev_reporting import TAB_DEV_IDS, current_page_dev_id
from ui import dev_ids
check("the tab is called Auto Boost", mw.TAB_LABELS["AutoSchedule"], "Auto Boost")
check("...and 'Auto Schedule' is no longer a tab name", "Auto Schedule" in mw.TABS, False)
check("Developer Mode's tab->page map follows the new name", (TAB_DEV_IDS.get("Auto Boost"), "Auto Schedule" in TAB_DEV_IDS), ("0.11", False))
class S: current_tab = "Auto Boost"
check("...so 'Copy Current Page Info' still works on that tab", current_page_dev_id(S()), "0.11")
check("the page's Developer Mode label says Auto Boost (the AS-001 code you know is kept)", dev_ids.label("0.11"), "AS-001 — Auto Boost Settings")

# =================================================================== decision rules
print("\n--- the decision: below target -> extra videos tomorrow ---")
d = decide_daily(perf(3500, True), cfg(), 800)
check("target reached -> the normal amount, no boost", (d.video_count, d.target_reached), (2, True))
d = decide_daily(perf(1000), cfg(), 800)
check("CALCULATED (default): 2000 short / ~800 each = +3 -> 5/day", (d.video_count, d.additional_shorts, d.target_reached), (5, 3, False))
for extra in (1, 2, 3):
    d = decide_daily(perf(1000), cfg(boost_extra_videos=extra), 800)
    check(f"fixed +{extra}: below target -> exactly normal + {extra} = {2 + extra}/day (not the calculated 5)", (d.video_count, d.additional_shorts), (2 + extra, extra))
    check(f"...the explanation says it's a boost of +{extra}", f"boost of +{extra} video(s)" in d.explanation, True)
d = decide_daily(perf(2999), cfg(boost_extra_videos=1), 800)
check("even a tiny miss (2,999 of 3,000) gets the fixed boost", d.video_count, 3)
d = decide_daily(perf(0), cfg(boost_extra_videos=3, max_videos_per_day=4), 800)
check("never more than Maximum Shorts / Day (2 + 3 capped at 4) -- and it says so", (d.video_count, "capped" in d.explanation), (4, True))
d = decide_daily(perf(3600, False), cfg(boost_extra_videos=2), 800)
check("yesterday REACHED the target (even if the day before was low): no boost", (d.video_count, d.target_reached, d.additional_shorts), (2, True, 0))
d = decide_daily(PerformanceResult(ready=False, reason="No completed publishing day yet to evaluate."), cfg(boost_extra_videos=2), 800)
check("no data yet -> normal, no boost", (d.video_count, d.target_reached), (2, None))
d = decide_daily(perf(1000, trajectory_met=True), cfg(boost_extra_videos=2), 800)
check("the 2-day trend is on pace -> normal (one bad day after a good one isn't over-corrected)", d.video_count, 2)

wp_behind = WeekProgress(week_start=dt.date(2026, 10, 19), week_end=dt.date(2026, 10, 25), target=21000, collected=1000, remaining=20000, days_remaining=4, required_average=5000.0)
wp_ok = WeekProgress(week_start=dt.date(2026, 10, 19), week_end=dt.date(2026, 10, 25), target=21000, collected=20000, remaining=1000, days_remaining=4, required_average=250.0)
d = decide_weekly(wp_behind, cfg(boost_extra_videos=2, target_mode=TARGET_MODE_WEEKLY), 800)
check("weekly mode, behind pace: the fixed boost applies too", (d.video_count, d.additional_shorts), (4, 2))
d = decide_weekly(wp_ok, cfg(boost_extra_videos=2, target_mode=TARGET_MODE_WEEKLY), 800)
check("weekly mode, on pace: normal", d.video_count, 2)
d = decide_video_count(cfg(boost_extra_videos=1), perf(500), None, 800)
check("decide_video_count routes the daily mode through the same rule", d.video_count, 3)

# =================================================================== the setting itself
print("\n--- the setting is saved and sanitised ---")
check("defaults to Calculated (0) so nothing changes until you choose", AutoScheduleConfig().boost_extra_videos, 0)
c = AutoScheduleConfig(); c.boost_extra_videos = 2; c.save()
check("saved and reloaded", AutoScheduleConfig.load().boost_extra_videos, 2)
for bad, want in (("lots", 0), (99, MAX_BOOST_EXTRA), (-4, 0), (None, 0), (2.0, 2)):
    asc.AUTO_SCHEDULE_CONFIG_PATH.write_text(json.dumps({"boost_extra_videos": bad}), encoding="utf-8")
    check(f"a damaged value ({bad!r}) can't crash anything: becomes {want}", AutoScheduleConfig.load().boost_extra_videos, want)
asc.AUTO_SCHEDULE_CONFIG_PATH.write_text(json.dumps({"normal_videos_per_day": 3}), encoding="utf-8")
check("a config saved before this setting existed loads fine (Calculated)", (AutoScheduleConfig.load().boost_extra_videos, AutoScheduleConfig.load().normal_videos_per_day), (0, 3))

# =================================================================== the effect on the real schedule
print("\n--- the schedule: tomorrow boosted, everything after moves up, order kept ---")
today = dt.date.today(); tomorrow = today + dt.timedelta(days=1)
queue_cfg = cfg(); queue_cfg.save_slot_preset(2, [dt.time(6, 0), dt.time(10, 0)])
for n in (3, 4, 5): queue_cfg.save_slot_preset(n, [dt.time(6, 0), dt.time(8, 0), dt.time(10, 0), dt.time(12, 0), dt.time(13, 0)][:n])
def make_queue(days=10, per_day=2):
    out = []
    for d in range(days):
        for k, hh in enumerate((6, 10)[:per_day]):
            out.append(Video(video_id=f"V{d * per_day + k:03d}", title=f"t{d * per_day + k}", description="", thumbnail_url="", duration_seconds=1,
                             publish_at=dt.datetime.combine(tomorrow + dt.timedelta(days=d), dt.time(hh, 0)).astimezone()))
    return out
queue = make_queue()
now = dt.datetime.now(dt.timezone.utc)
for extra in (1, 2):
    c = cfg(boost_extra_videos=extra)
    decision = decide_daily(perf(500), c, 800)
    r = compute_auto_schedule_pull_forward(queue, tomorrow, decision.video_count, queue_cfg, now)
    new_time = {a.video_id: a.new_publish_at for a in r.result.assignments}
    after = {v.video_id: new_time.get(v.video_id, v.publish_at).astimezone() for v in queue}
    per_day = collections.Counter(t.date() for t in after.values())
    check(f"+{extra}: tomorrow has {2 + extra} videos", per_day[tomorrow], 2 + extra)
    later = [per_day[tomorrow + dt.timedelta(days=i)] for i in range(1, 4)]
    check(f"+{extra}: the days after go straight back to the normal 2/day", later, [2, 2, 2])
    check(f"+{extra}: the whole queue shifted up -- the LAST video now publishes earlier than before", max(after.values()) < max(v.publish_at.astimezone() for v in queue), True)
    ids = [v.video_id for v in sorted(queue, key=lambda v: v.publish_at)]
    check(f"+{extra}: publish ORDER of every video is unchanged", [vid for vid, _ in sorted(after.items(), key=lambda kv: kv[1])] == ids, True)
    check(f"+{extra}: the safety guard sees no order violation", validate_order_preserved(queue, r.result), [])
    check(f"+{extra}: no video moves LATER, nothing is uploaded (only existing videos are rescheduled)", all(after[v.video_id] <= v.publish_at.astimezone() for v in queue), True)
    check(f"+{extra}: a video is only touched if its time actually changed", all(a.new_publish_at != a.old_publish_at for a in r.result.assignments), True)

# =================================================================== the page
print("\n--- the page ---")
tk_messagebox.showerror = lambda *a, **k: None; tk_messagebox.showinfo = lambda *a, **k: None; tk_messagebox.askyesno = lambda *a, **k: True
from ui.app_state import AppState
from ui.pages.auto_schedule_settings_page import AutoScheduleSettingsPage, BOOST_CHOICES, boost_label, boost_value
check("the choices offered are Calculated, +1, +2, +3", BOOST_CHOICES, ["Calculated", "+1", "+2", "+3"])
check("labels <-> values round trip", [boost_value(boost_label(n)) for n in (0, 1, 2, 3)], [0, 1, 2, 3])

class Root(ctk.CTk, TkinterDnD.DnDWrapper):
    def __init__(self):
        super().__init__(); self.TkdndVersion = TkinterDnD._require(self); self.cb_errors = []
    def report_callback_exception(self, exc, val, tb): self.cb_errors.append((exc.__name__, str(val)))
root = Root(); root.geometry("1200x1600")
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

local_midnight = lambda d: dt.datetime.combine(d, dt.time(0, 0)).astimezone()
def published(i, hours, views):
    return Video(video_id=f"P{i}", title=f"p{i}", description="", thumbnail_url="", duration_seconds=1, privacy_status="public",
                 published_at=local_midnight(today - dt.timedelta(days=2)) + dt.timedelta(hours=hours), view_count=views)
class Repo:
    def __init__(self, videos): self._v = list(videos)
    @property
    def videos(self): return list(self._v)
    def refresh(self): pass
    def refresh_ids(self, ids): pass
class Client:
    def __init__(self, recent): self.recent = recent
    def get_recent_published_videos(self, limit=60): return list(self.recent)
    def get_videos(self, ids): return []

asc.AUTO_SCHEDULE_CONFIG_PATH.write_text(json.dumps({"normal_videos_per_day": 2, "max_videos_per_day": 6, "daily_target_views": 3000, "estimated_views_per_short": 800,
                                                      "slot_presets": {"2": ["06:00", "10:00"], "3": ["06:00", "08:00", "10:00"], "4": ["06:00", "08:00", "10:00", "12:00"]}}), encoding="utf-8")
state = AppState(m_config.AppConfig())
state.repo = Repo(make_queue()); state.client = Client([published(1, 6, 90), published(2, 10, 110)])   # 200 views against a 3,000 target
page = AutoScheduleSettingsPage(root, state); page.pack(fill="both", expand=True); pump(0.5)
texts = [w.cget("text") for w in walk(page) if isinstance(w, (ctk.CTkLabel, ctk.CTkButton)) and isinstance(w.cget("text"), str)]
check("the page title is AUTO BOOST", "AUTO BOOST" in texts, True)
check("...and no visible text on the page still says 'Auto Schedule'", [t for t in texts if "auto schedule" in t.lower()], [])
check("the new control is on the page, set to Calculated", (page.boost_var.get(), "Calculated" in page.boost_hint_label.cget("text")), ("Calculated", True))
check("...the helper line explains Calculated", "Estimated Views Per Short" in page.boost_hint_label.cget("text"), True)
check("the live preview runs (previous days' views read, shortfall found)", wait_for(lambda: page.estimate_summary_label.cget("text").startswith("Target:")), True)
t = page.estimate_summary_label.cget("text")
check("...Calculated mode: 2,800 short / ~800 = +4 -> 6 videos tomorrow", ("Recommended Next-Day Slots:  6" in t, "Estimated Views/Short" in t, "Current Performance:         200" in t), (True, True, True))

page.boost_var.set("+1"); page._update_boost_hint(); pump(0.2)
check("choosing +1 updates the helper line immediately", "exactly +1 extra video" in page.boost_hint_label.cget("text"), True)
page.save_btn.invoke(); pump(0.5)
check("Save Settings stores it", AutoScheduleConfig.load().boost_extra_videos, 1)
check("...and the preview recomputes: below target + a fixed +1 -> 3 videos tomorrow", wait_for(lambda: "Recommended Next-Day Slots:  3" in page.estimate_summary_label.cget("text")), True)
t = page.estimate_summary_label.cget("text")
check("...it shows the boost setting instead of the estimate", ("Boost Setting:               +1 per missed day" in t, "Estimated Views/Short" in t), (True, False))
check("...and the number of videos that would be pulled forward", "Videos To Pull Forward:" in t and not t.rstrip().endswith("0"), True)
check("...the explanation names the boost", "boost of +1 video(s)" in page.recommendation_label.cget("text"), True)
check("the Apply button is enabled (there is something to do)", str(page.apply_btn.cget("state")), "normal")

page.boost_var.set("+2"); page.save_btn.invoke(); pump(0.4)
check("+2: tomorrow gets 4", wait_for(lambda: "Recommended Next-Day Slots:  4" in page.estimate_summary_label.cget("text")), True)
page.destroy(); pump(0.2)

# restart: it remembers
page2 = AutoScheduleSettingsPage(root, state); page2.pack(fill="both", expand=True); pump(0.5)
check("after a restart the control still shows +2", page2.boost_var.get(), "+2")

# target met -> no boost
state.client = Client([published(1, 6, 1800), published(2, 10, 1700)])
page2._refresh_preview(); wait_for(lambda: "Current Performance:         3,500" in page2.estimate_summary_label.cget("text"))
check("when yesterday HITS the target nothing is boosted: tomorrow stays at 2", "Recommended Next-Day Slots:  2" in page2.estimate_summary_label.cget("text"), True)
check("...and there is nothing to apply", str(page2.apply_btn.cget("state")) in ("disabled", "normal") and "Videos To Pull Forward:      0" in page2.estimate_summary_label.cget("text"), True)
check("no exception escaped into Tk", root.cb_errors, [])
page2.destroy(); pump(0.2)

if errors:
    print("\nFAILURES:", errors); os._exit(1)
print("\nALL CHECKS PASSED"); os._exit(0)

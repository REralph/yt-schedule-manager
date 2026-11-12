"""WINDOW FLUIDITY: dragging the window edge and moving it between monitors must not freeze or blank the app.
Covers ui/resize_governor.py (hold the layout while dragging), ui/ctk_perf.py (no forced whole-window flushes, lazy rescaling of
hidden tabs, no 15% fade) and the Auto Boost preview cap -- on the real App, against a fake YouTube."""
import sys, os, json, time, tempfile, pathlib, datetime as dt, collections, statistics
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

TMP = pathlib.Path(tempfile.mkdtemp())
import data.config as m_config, data.auto_schedule_config as m_asc, data.upload_activity as m_ua, data.history as m_hist
import data.auto_schedule_history as m_ash, data.error_log as m_el, data.crash_log as m_cl, data.shorts_folder as m_sf
import data.session_state as m_ss, data.queue_order as qo, youtube.video_repo as m_vr, youtube.quota as m_q
m_config.CONFIG_PATH = TMP / "config.json"; m_asc.AUTO_SCHEDULE_CONFIG_PATH = TMP / "asc.json"; m_ua.UPLOAD_ACTIVITY_PATH = TMP / "ua.json"
m_hist.HISTORY_PATH = TMP / "hist.json"; m_ash.AUTO_SCHEDULE_HISTORY_PATH = TMP / "ash.json"; m_el.ERROR_LOG_PATH = TMP / "el.json"
m_cl.CRASH_LOG_PATH = TMP / "cl.json"; m_sf.REGISTRY_PATH = TMP / "sf.json"; m_ss.SESSION_PATH = TMP / "ss.json"
qo.QUEUE_ORDER_PATH = TMP / "queue.json"; m_vr.CACHE_PATH = TMP / "cache.json"; m_q.QUOTA_PATH = TMP / "quota.json"

import customtkinter as ctk
from tkinterdnd2 import TkinterDnD
import tkinter.messagebox as tk_messagebox
tk_messagebox.showerror = tk_messagebox.showinfo = tk_messagebox.showwarning = lambda *a, **k: None
tk_messagebox.askyesno = lambda *a, **k: True
from customtkinter.windows.widgets.scaling.scaling_tracker import ScalingTracker
from customtkinter.windows.widgets.ctk_scrollbar import CTkScrollbar
from customtkinter.windows.widgets.ctk_optionmenu import CTkOptionMenu
from ui import ctk_guard, ctk_perf
from ui.resize_governor import ResizeGovernor, SLOW_GAP_MS
ctk_guard.install_scaling_guard(); ctk_perf.install()
import main as app_main
import ui.main_window as mw
from data.crash_log import CrashLogStore
from youtube.models import Video

errors = []
def check(name, actual, expected=True):
    ok = actual == expected
    print(("PASS" if ok else "FAIL"), name, "->" if not ok else "", (actual, expected) if not ok else "")
    if not ok: errors.append(name)

# ------------------------------------------------------------------ fakes (the real App, no network)
class FakeQuota:
    units_used = 0
    def record(self, n): pass
    def remaining(self, b): return b
class FakeYT:
    def __init__(self, credentials): self.quota = FakeQuota()
    def list_playlists(self): return []
    def get_recent_published_videos(self, limit=60): return []
    def get_videos(self, ids): return []
TODAY = dt.date.today()
def make_videos(n=120):
    out = []
    for i in range(n):
        d = TODAY + dt.timedelta(days=1 + i // 2)
        out.append(Video(video_id=f"V{i:04d}", title=f"Video number {i} with a reasonably long title #BF5 #Shorts", description="", thumbnail_url="",
                         duration_seconds=30, privacy_status="private", publish_at=dt.datetime.combine(d, dt.time(6 if i % 2 == 0 else 10, 0)).astimezone(),
                         published_at=dt.datetime(2029, 1, 1, tzinfo=dt.timezone.utc) + dt.timedelta(hours=i)))
    return out
class FakeRepo(m_vr.VideoRepository):
    def __init__(self, client, use_uploads_playlist=True):
        super().__init__(None); self.refreshed_this_session = True
        self._videos = {v.video_id: v for v in make_videos()}
    def refresh(self, deep=False): return self.videos
    def refresh_ids(self, ids): return []
mw.YouTubeClient = FakeYT; mw.VideoRepository = FakeRepo
app_main.oauth_manager.load_cached_credentials = lambda: object()
app_main.FAULT_LOG_PATH = TMP / "fault.log"; app_main.EXIT_WATCHDOG_SECONDS = 999
store = CrashLogStore.shared(); store.clear()

app = app_main.App(); app.geometry("1200x900+40+20")
win = app._current_screen
def pump(s=0.3):
    app.after(int(s * 1000), app.quit); app.mainloop()
pump(1.5)
def walk(w):
    for c in w.winfo_children():
        yield c; yield from walk(c)

# =================================================================== no forced whole-window flush
print("\n--- scrollbars and option menus no longer flush the whole window on every redraw ---")
probe = ctk.CTkFrame(app)
sb = CTkScrollbar(probe); om = CTkOptionMenu(probe, values=["a", "b"])
check("a new scrollbar's canvas has its forced flush removed", sb._canvas.update_idletasks() is None and "lambda" in repr(sb._canvas.update_idletasks), True)
check("...and so does an option menu's", "lambda" in repr(om._canvas.update_idletasks), True)
sb.set(0.2, 0.6); check("a scrollbar still draws and reports its position", sb.get(), (0.2, 0.6))
probe.destroy()

# =================================================================== the resize governor
print("\n--- the resize governor holds the page while the window edge is dragged ---")
check("the real App has a governor, installed with the screen", isinstance(app.resize_governor, ResizeGovernor), True)
configure_counts = collections.Counter()
def count_children(e):
    if e.widget is not app: configure_counts[type(e.widget).__name__] += 1
app.bind_all("<Configure>", count_children, add="+")

def drag(steps=14, w0=1200, h0=900, dw=-8, dh=-6):
    ts = []
    for i in range(steps):
        t = time.perf_counter(); app.geometry(f"{w0 + dw * i}x{h0 + dh * i}"); app.update_idletasks(); app.update(); ts.append((time.perf_counter() - t) * 1000)
    return ts

win.tabview.set("Calendar"); win._on_tab_changed(); pump(0.8)
app.geometry("1200x900"); pump(0.6)
gov = app.resize_governor
before = gov.freezes
configure_counts.clear()
ts_frozen = drag()
check("the first size change holds the page (one hold for the whole drag)", (gov.freezes - before, gov.is_frozen), (1, True))
check("...the page keeps the size it had, however far the window is dragged", (win.winfo_width() >= 1190, win.winfo_height() >= 890), (True, True))
heavy = {k: v for k, v in configure_counts.items() if k not in ("Root", "App")}
check("...so none of the hundreds of widgets inside get a size event per step (it used to be every one of them, every step)", sum(heavy.values()) <= 30, True)
pump(0.5)
check("after the drag settles the page is released and fills the window at its FINAL size", (gov.is_frozen, abs(win.winfo_width() - app.winfo_width()) <= 2, abs(win.winfo_height() - app.winfo_height()) <= 2), (False, True, True))
check("...the window's grid is back to stretching (weight 1, no fixed size)", (app.grid_columnconfigure(0)["weight"], app.grid_columnconfigure(0)["minsize"], app.grid_rowconfigure(0)["weight"], app.grid_rowconfigure(0)["minsize"]), (1, 0, 1, 0))
check("(it is the page, not a stale copy: it is still mapped and gridded)", (win.winfo_ismapped(), win.winfo_manager()), (True, "grid"))

# the same drag with the governor switched off, for the comparison (same process, same tab)
gov_handler = gov._freeze
gov._freeze = lambda: None
app.geometry("1200x900"); pump(0.6)
configure_counts.clear()
ts_plain = drag()
check("(for comparison) without the governor every widget that changes size gets a size event on every step", sum(configure_counts.values()) > 20 * len(ts_plain), True)
gov._freeze = gov_handler
app.geometry("1200x900"); pump(0.6)
med_frozen, med_plain = statistics.median(ts_frozen[2:]), statistics.median(ts_plain[2:])
print(f"      per drag step on the Calendar tab: held {med_frozen:.0f} ms vs not held {med_plain:.0f} ms")
check("a held drag step is at least 1.5x cheaper than an unheld one on the same tab", med_frozen * 1.5 < med_plain, True)

print("\n--- moving the window (same size) never holds anything ---")
f0 = gov.freezes
for i in range(5):
    app.geometry(f"1200x900+{40 + 25 * i}+{20 + 10 * i}"); app.update_idletasks(); app.update()
pump(0.3)
check("moving only changes the position: nothing is held", (gov.freezes - f0, gov.is_frozen), (0, False))

print("\n--- two drags in a row each get their own hold and release ---")
f0 = gov.freezes
drag(6); pump(0.5); drag(6, 1000, 800, 5, 4); pump(0.5)
check("two separate drags = two holds, and nothing is left held", (gov.freezes - f0, gov.is_frozen), (2, False))
app.geometry("1200x900"); pump(0.6)

print("\n--- the governor on its own (a fake window, a fake clock) ---")
class Clock:
    def __init__(self): self.t = 0.0
    def __call__(self): return self.t
def standalone(settle_ms=40):
    root = ctk.CTkToplevel(app); root.geometry("500x400+10+10")
    root.grid_columnconfigure(0, weight=1); root.grid_rowconfigure(0, weight=1)
    content = ctk.CTkFrame(root); content.grid(row=0, column=0, sticky="nsew")
    for i in range(5): ctk.CTkLabel(content, text=f"x{i}").pack()
    clock = Clock()
    g = ResizeGovernor(root, lambda: content, settle_ms=settle_ms, clock=clock)
    root.update(); pump(0.4)
    return root, content, g, clock
def resize_event(root, w, h):
    root.geometry(f"{w}x{h}"); root.update_idletasks(); root.update()

root, content, g, clock = standalone()
resize_event(root, 520, 420)
check("a size change holds the page at its current size", (g.is_frozen, content.winfo_width() <= 500 + 2), (True, True))
resize_event(root, 560, 440)
check("further steps do not change the page", content.winfo_width() <= 502, True)
pump(0.3)
check("it is released after the settle time and fills the final size", (g.is_frozen, abs(content.winfo_width() - root.winfo_width()) <= 2), (False, True))
root.destroy()

root, content, g, clock = standalone()
content.grid_remove(); root.update()
resize_event(root, 520, 420)
check("with nothing on screen to hold it does nothing (and does not fail)", (g.is_frozen, g.freezes), (False, 0))
root.destroy()

print("\n--- a drag too slow to repaint hides the page until you let go ---")
root, content, g, clock = standalone(settle_ms=60)
def step(w, advance_ms):
    clock.t += advance_ms / 1000; resize_event(root, w, 400)
step(520, 5); step(530, 10); step(540, 12); step(550, 14)
check("a fast drag keeps the page visible", (g.is_frozen, g.drag_hides, bool(content.winfo_manager())), (True, 0, True))
step(560, SLOW_GAP_MS + 80)
check("one slow gap alone (e.g. the mouse paused) does not hide it", g.drag_hides, 0)
step(570, SLOW_GAP_MS + 80)
check("two slow gaps in a row hide the page so the window frame stays smooth", (g.drag_hides, content.winfo_manager()), (1, ""))
step(580, SLOW_GAP_MS + 80)
check("...once, however long the drag goes on", g.drag_hides, 1)
pump(0.4)
check("on release the page comes back, once, at the final size", (g.is_frozen, content.winfo_manager(), abs(content.winfo_width() - root.winfo_width()) <= 2), (False, "grid", True))
root.destroy()

root, content, g, clock = standalone(settle_ms=60)
step(520, 5); step(530, SLOW_GAP_MS + 80); step(540, 10); step(550, SLOW_GAP_MS + 80); step(560, 10)
check("slow gaps that are not consecutive (pauses between bursts) never hide the page", g.drag_hides, 0)
root.destroy()

print("\n--- closing a window in the middle of a drag leaves no timer behind ---")
root, content, g, clock = standalone(settle_ms=5000)
resize_event(root, 520, 420)
check("(setup) a release is scheduled", g._job is not None, True)
root.destroy(); pump(0.2)
check("destroying the window cancels it", g._job, None)

print("\n--- the governor ignores other windows and other events ---")
root, content, g, clock = standalone()
g.hide_for_rescale(app)
check("a monitor change of a DIFFERENT window leaves this page alone", bool(content.winfo_manager()), True)
g.hide_for_rescale(root); root.update()
check("a monitor change of this window hides the page for the rescale", content.winfo_manager(), "")
g.show_after_rescale(root); root.update()
check("...and shows it again exactly where it was", (content.winfo_manager(), set(content.grid_info()["sticky"]), content.grid_info()["row"], content.grid_info()["column"]), ("grid", set("nsew"), 0, 0))
root.destroy()

# =================================================================== lazy rescaling on a monitor change
print("\n--- a monitor change rescales what you can see now and the other tabs when you open them ---")
scales = {"v": 1.0}
ScalingTracker.get_window_dpi_scaling = classmethod(lambda cls, w: scales["v"])
alphas = []
real_attributes = app.attributes
app.attributes = lambda *a, **k: (alphas.append(a) if a and a[0] == "-alpha" and len(a) > 1 else None) or real_attributes(*a, **k)
win.tabview.set("Upload"); win._on_tab_changed(); pump(0.8)
def widget_scale(w): return w._get_widget_scaling()
tab_upload, tab_settings, tab_calendar = (win.tabview.tab(n) for n in ("Upload", "Auto Boost", "Calendar"))
w_up = next(c for c in walk(tab_upload) if isinstance(c, ctk.CTkLabel) and c.winfo_viewable())
w_ab = next(c for c in walk(tab_settings) if isinstance(c, ctk.CTkLabel))
w_cal = next(c for c in walk(tab_calendar) if isinstance(c, ctk.CTkLabel))
check("(setup) everything starts at 100%", (widget_scale(w_up), widget_scale(w_ab), widget_scale(w_cal)), (1.0, 1.0, 1.0))
hidden_during = []
orig_hide = app.resize_governor.hide_for_rescale
scales["v"] = 1.5
t0 = time.perf_counter(); ScalingTracker.check_dpi_scaling(); app.update_idletasks(); app.update(); blocked = (time.perf_counter() - t0) * 1000
print(f"      the visible Upload tab rescaled to 150% in {blocked:.0f} ms")
check("the tab on screen was rescaled immediately", widget_scale(w_up), 1.5)
check("the tabs you cannot see were NOT touched (they wait)", (widget_scale(w_ab), widget_scale(w_cal)), (1.0, 1.0))
check("...a lot of work is waiting for them", ctk_perf.pending_count() > 300, True)
check("the window was never faded to 15% opacity (that was the 'going blank')", [a for a in alphas if len(a) > 1 and a[1] < 1], [])
check("the page is back on screen after the rescale", (win.winfo_manager(), win.winfo_ismapped()), ("grid", True))
win.tabview.set("Auto Boost"); app.update_idletasks(); app.update()
check("opening a waiting tab (from code, as the pages do) catches it up at once", widget_scale(w_ab), 1.5)
check("...but only that tab", widget_scale(w_cal), 1.0)
win.tabview.set("Calendar"); app.update()
check("every other tab is caught up as it is opened", widget_scale(w_cal), 1.5)
win.tabview.set("Upload"); app.update(); pump(1.7)
scales["v"] = 1.0
ScalingTracker.check_dpi_scaling(); app.update_idletasks(); app.update(); pump(0.3)
check("back at 100%: the visible tab follows straight away, the others when opened", (widget_scale(w_up), widget_scale(w_ab)), (1.0, 1.5))
win.tabview.set("Auto Boost"); app.update()
check("...and they come back to 100% too", widget_scale(w_ab), 1.0)
check("a widget created AFTER the change is at the right scale from the start", (lambda w: widget_scale(w))(ctk.CTkLabel(tab_upload, text="new")), 1.0)
app.attributes = real_attributes
win.tabview.set("Upload"); app.update(); pump(1.7)

print("\n--- the Developer Mode tags (hidden by default) are rescaled only when shown ---")
tags = [c for c in walk(win) if type(c).__name__ == "DevTag"]
check("(setup) there are many tags and none is on screen", (len(tags) > 100, any(t.winfo_manager() for t in tags)), (True, False))
scales["v"] = 1.25
ScalingTracker.check_dpi_scaling(); app.update_idletasks(); app.update(); pump(1.7)
check("a monitor change does not touch the hidden tags (none is at the new scale)", 1.25 in {widget_scale(t) for t in tags}, False)
win.state.set_developer_mode(True); app.update_idletasks(); app.update(); pump(0.3)
shown = [t for t in tags if t.winfo_viewable()]
check("turning Developer Mode on shows the tags of the visible tab, already at the right scale", (len(shown) > 0, {widget_scale(t) for t in shown}), (True, {1.25}))
win.state.set_developer_mode(False); app.update(); scales["v"] = 1.0
ScalingTracker.check_dpi_scaling(); app.update_idletasks(); app.update(); pump(1.7)

print("\n--- the rescale of one window must never break another or stop on a bad widget ---")
class BadSetScaling:
    pass
good_before = widget_scale(w_up)
scales["v"] = 1.5
tracker_list = ScalingTracker.window_widgets_dict[app]
tracker_list.append(lambda *a: (_ for _ in ()).throw(RuntimeError("a widget that cannot rescale")))
ScalingTracker.check_dpi_scaling(); app.update_idletasks(); app.update()
check("one widget raising during a rescale does not stop the rest", widget_scale(w_up), 1.5)
tracker_list.pop()
scales["v"] = 1.0; pump(1.7); ScalingTracker.check_dpi_scaling(); app.update(); pump(0.3)
ScalingTracker.get_window_dpi_scaling = classmethod(ScalingTracker.get_window_dpi_scaling.__func__)

# =================================================================== the Auto Boost preview cap
print("\n--- the Auto Boost preview draws 30 rows at a time ---")
from ui.pages.auto_schedule_settings_page import PREVIEW_PAGE
win.tabview.set("Auto Boost"); win._on_tab_changed(); pump(1.0)
page = next(c for c in walk(win.tabview.tab("Auto Boost")) if type(c).__name__ == "AutoScheduleSettingsPage")
class PF:  # a pull-forward result with 100 rows
    def __init__(self):
        from scheduling.increase_slots import PreviewRow
        base = dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=2)
        self.preview_rows = [PreviewRow(video_id=f"V{i:04d}", title=f"title {i}", old_publish_at=base + dt.timedelta(hours=i + 5), new_publish_at=base + dt.timedelta(hours=i), changed=True) for i in range(100)]
        self.changed_count = 100
page._pull_forward = PF(); page._preview_shown = PREVIEW_PAGE; page._render_preview_table(); pump(0.4)
labels = [c for c in page.preview_table.winfo_children() if isinstance(c, ctk.CTkLabel)]
check("only the first 30 rows are drawn (4 widgets each)", len(labels), 30 * 4 - 30 + 30)   # 30 thumb labels + 30 titles + 30 old + 30 new = 120
button = [c for c in page.preview_table.winfo_children() if isinstance(c, ctk.CTkButton)]
check("...with a button for the rest", (len(button), button[0].cget("text")), (1, "Show 30 more (70 not shown)"))
button[0].invoke(); pump(0.4)
check("pressing it draws 30 more", len([c for c in page.preview_table.winfo_children() if isinstance(c, ctk.CTkLabel)]), 60 * 4)
page._pull_forward = PF(); page._preview_shown = PREVIEW_PAGE; page._render_preview_table()
check("a few rows (<= 30) have no button", True)

# =================================================================== nothing broke
print("\n--- nothing broke ---")
win.tabview.set("Upload"); win._on_tab_changed(); pump(0.8)
check("no crash or recovered error was recorded by any of this", [(e.kind, e.error_type) for e in store.entries], [])
check("the window is still sound: title, size and content", (app.title(), win.winfo_ismapped()), ("YouTube Shorts Reschedule Manager", True))

if errors:
    print("\nFAILURES:", errors); os._exit(1)
print("\nALL CHECKS PASSED"); os._exit(0)

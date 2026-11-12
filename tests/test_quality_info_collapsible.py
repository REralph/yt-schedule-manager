import sys, os, json, datetime as dt, tempfile, pathlib
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

import data.auto_schedule_config as auto_schedule_config_module
auto_schedule_config_module.AUTO_SCHEDULE_CONFIG_PATH = pathlib.Path(tempfile.mkdtemp()) / "auto_schedule_config.json"
import data.error_log as error_log_module
error_log_module.ERROR_LOG_PATH = pathlib.Path(tempfile.mkdtemp()) / "error_log.json"
import data.shorts_folder as shorts_folder_module
shorts_folder_module.REGISTRY_PATH = pathlib.Path(tempfile.mkdtemp()) / "shorts_folder_registry.json"
import data.upload_activity as upload_activity_module
upload_activity_module.UPLOAD_ACTIVITY_PATH = pathlib.Path(tempfile.mkdtemp()) / "upload_activity.json"

from ui.app_state import AppState
from ui.widgets.basic_upload_panel import BasicUploadPanel

errors = []
def check(name, actual, expected=True):
    ok = actual == expected
    print(("PASS" if ok else "FAIL"), name, "->" if not ok else "", (actual, expected) if not ok else "")
    if not ok:
        errors.append(name)

tk_messagebox.showerror = lambda *a, **k: None
tk_messagebox.askyesno = lambda *a, **k: True

tmp_dir = pathlib.Path(tempfile.mkdtemp())
def make_clip(path, w=64, h=114, fps=30.0, frames=30):
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), fps, (w, h))
    for i in range(frames):
        writer.write(np.full((h, w, 3), i % 255, dtype=np.uint8))
    writer.release()

class TestRoot(ctk.CTk, TkinterDnD.DnDWrapper):
    def __init__(self):
        super().__init__()
        self.TkdndVersion = TkinterDnD._require(self)

root = TestRoot()
root.geometry("1100x1300")

def pump(seconds=0.3):
    root.after(int(seconds * 1000), root.quit)
    root.mainloop()

class FakeRepo:
    videos = []
    def refresh(self): pass
    def refresh_ids(self, ids): pass

class FakeClient:
    def list_playlists(self): return []

# ===== config default + backward compat =====
check("default is COLLAPSED", AppConfig().quality_info_expanded, False)
config_module.CONFIG_PATH.write_text(json.dumps({"upload_videos_per_day": 3}), encoding="utf-8")  # an old config.json with no such field
check("an older config.json without the field loads as collapsed", AppConfig.load().quality_info_expanded, False)
check("...and its other saved values are untouched", AppConfig.load().upload_videos_per_day, 3)

state = AppState(AppConfig.load())
state.repo = FakeRepo()
state.client = FakeClient()

panel = BasicUploadPanel(root, state)
panel.pack(fill="both", expand=True)
pump(0.3)

clip = tmp_dir / "collapse_clip.mp4"
make_clip(clip)
panel._set_file_from_drag(str(clip))
pump(0.5)

# ===== default state on load: compact header only =====
check("the compact header/arrow is visible", panel.tech_toggle_btn.winfo_ismapped(), True)
check("arrow shows the collapsed indicator", panel.tech_toggle_btn.cget("text"), "▼")
check("the detail body is hidden by default", panel.tech_body.winfo_ismapped(), False)
check("...none of the field labels are on screen", any(l.winfo_ismapped() for l in panel.tech_info_labels.values()), False)
check("the information is NOT deleted -- all 8 fields still exist", len(panel.tech_info_labels), 8)
check("...and are still being populated in the background while collapsed", panel.tech_info_labels["Resolution"].cget("text"), "64x114")
check("the pre-upload snapshot (needed by post-upload verification) is still captured while collapsed", panel._tech_info is not None, True)

collapsed_detail_h = panel.detail_frame.winfo_height()

# ===== arrow button expands =====
panel.tech_toggle_btn.invoke()
pump(0.3)
check("clicking the arrow expands the body", panel.tech_body.winfo_ismapped(), True)
check("arrow now shows the expanded indicator", panel.tech_toggle_btn.cget("text"), "▲")
check("the field labels are on screen", all(l.winfo_ismapped() for l in panel.tech_info_labels.values()), True)
check("all existing information is present (filename)", panel.tech_info_labels["Filename"].cget("text"), "collapse_clip.mp4")
check("expanded state is persisted to config.json", json.loads(config_module.CONFIG_PATH.read_text(encoding="utf-8"))["quality_info_expanded"], True)
check("collapsed takes less vertical space than expanded", collapsed_detail_h < panel.detail_frame.winfo_height(), True)
check("the section still sits ABOVE the status line (layout order preserved)",
      panel.tech_body.winfo_rooty() < panel.status_label.winfo_rooty(), True)

# ===== clicking the header text / header bar collapses too =====
tech_title = next(w for w in panel.tech_toggle_btn.master.winfo_children() if isinstance(w, ctk.CTkLabel) and w.cget("text") == "QUALITY INFORMATION")
tech_title._label.event_generate("<Button-1>", x=2, y=2)
pump(0.3)
check("clicking the header title collapses it", panel.tech_body.winfo_ismapped(), False)
check("arrow back to collapsed indicator", panel.tech_toggle_btn.cget("text"), "▼")
check("collapsed choice persisted", json.loads(config_module.CONFIG_PATH.read_text(encoding="utf-8"))["quality_info_expanded"], False)

panel.tech_toggle_btn.master._canvas.event_generate("<Button-1>", x=2, y=2)  # blank area of the header bar
pump(0.3)
check("clicking the empty part of the header bar expands it", panel.tech_body.winfo_ismapped(), True)
panel.tech_toggle_btn.invoke()
pump(0.3)
check("collapsing again returns to the original compact height", panel.detail_frame.winfo_height(), collapsed_detail_h)

# ===== persistence across a rebuild / restart: expanded is remembered =====
panel.tech_toggle_btn.invoke()  # expand and leave it expanded
pump(0.2)
panel.destroy()
pump(0.1)
state2 = AppState(AppConfig.load())  # a fresh AppConfig read from disk, like a restart
state2.repo = FakeRepo()
state2.client = FakeClient()
check("a fresh load restores the last choice (expanded)", state2.config.quality_info_expanded, True)
panel2 = BasicUploadPanel(root, state2)
panel2.pack(fill="both", expand=True)
pump(0.3)
panel2._set_file_from_drag(str(clip))
pump(0.5)
check("restored expanded on the new page: body shown", panel2.tech_body.winfo_ismapped(), True)
check("restored expanded on the new page: arrow is up", panel2.tech_toggle_btn.cget("text"), "▲")
check("all fields still populated after restore", panel2.tech_info_labels["Resolution"].cget("text"), "64x114")

# and collapsed is remembered the same way
panel2.tech_toggle_btn.invoke()
pump(0.2)
panel2.destroy()
pump(0.1)
state3 = AppState(AppConfig.load())
state3.repo = FakeRepo()
state3.client = FakeClient()
panel3 = BasicUploadPanel(root, state3)
panel3.pack(fill="both", expand=True)
pump(0.3)
panel3._set_file_from_drag(str(clip))
pump(0.5)
check("restored collapsed on the new page: body hidden", panel3.tech_body.winfo_ismapped(), False)
check("restored collapsed on the new page: arrow is down", panel3.tech_toggle_btn.cget("text"), "▼")

# toggling doesn't disturb any other saved setting
check("other saved settings untouched by toggling", AppConfig.load().upload_videos_per_day, 3)

panel3.destroy()
pump(0.1)
root.destroy()

if errors:
    print("\nFAILURES:", errors)
    sys.exit(1)
print("\nALL CHECKS PASSED")

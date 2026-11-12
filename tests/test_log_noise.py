"""The crash log must stay useful: expected / harmless events must not flood it or masquerade as crashes."""
import sys, os, tempfile, pathlib, tkinter, time, threading
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

TMP = pathlib.Path(tempfile.mkdtemp())
import data.crash_log as m_cl
m_cl.CRASH_LOG_PATH = TMP / "crash_log.json"
import customtkinter as ctk
import requests
from data import diagnostics
from data.crash_log import CrashLogStore
from data.diagnostics import KIND_CRASH, KIND_RECOVERED
import ui.thumbnail_cache as thumbs
import main

errors = []
def check(name, actual, expected=True):
    ok = actual == expected
    print(("PASS" if ok else "FAIL"), name, "->" if not ok else "", (actual, expected) if not ok else "")
    if not ok: errors.append(name)

store = CrashLogStore.shared()
diagnostics.set_sink(store.record_event)

root = ctk.CTk(); root.geometry("200x100")
def pump(s=0.3):
    root.after(int(s * 1000), root.quit); root.mainloop()
def wait_for(cond, timeout=6.0):
    t0 = time.time()
    while time.time() - t0 < timeout:
        if cond(): return True
        pump(0.1)
    return cond()

class Resp:
    def __init__(self, status): self.status_code = status; self.content = b""
    def raise_for_status(self):
        if self.status_code >= 400:
            e = requests.HTTPError(f"{self.status_code} Client Error: for url: https://i9.ytimg.com/vi/XYZ/mqdefault.jpg?sqp=SECRETISH"); e.response = self; raise e

calls = []
def fake_get(url, timeout=10):
    calls.append(url)
    return Resp(404 if "missing" in url else 500)
requests.get = fake_get

print("\n--- thumbnails ---")
holder = ctk.CTkFrame(root); holder.pack()
for i in range(40):
    thumbs.get_async(holder, f"https://i9.ytimg.com/vi/missing{i}/mqdefault.jpg?sqp=x{i}", (80, 45), lambda img: None)
wait_for(lambda: len(calls) >= 40); pump(0.5)
check("40 videos with no thumbnail yet (HTTP 404) are NOT logged at all", store.entries, [])
before = len(calls)
for i in range(40):
    thumbs.get_async(holder, f"https://i9.ytimg.com/vi/missing{i}/mqdefault.jpg?sqp=x{i}", (80, 45), lambda img: None)
pump(0.6)
check("...and are not requested again on the next redraw", len(calls), before)

for i in range(25):
    thumbs.get_async(holder, f"https://i9.ytimg.com/vi/broken{i}/mqdefault.jpg?sqp=x{i}", (80, 45), lambda img: None)
wait_for(lambda: len(calls) >= before + 25); pump(0.6)
rec = [e for e in store.entries if e.kind == KIND_RECOVERED]
check("25 DIFFERENT thumbnails failing with a real server error (HTTP 500) fold into ONE entry with a count", (len(rec), rec[0].count if rec else None), (1, 25))
check("...whose text names the problem but not the individual URLs", (rec[0].error_message if rec else "").count("ytimg") == 0 and "HTTP 500" in rec[0].error_message, True)
holder.destroy()
store.clear()

print("\n--- destroyed-widget redraw race ---")
class St: current_tab = "Upload"; last_action = "Uploading q1.mov"
for i in range(13):
    try:
        raise tkinter.TclError(f'invalid command name ".!mainwindow.!ctktabview.!ctkframe.!uploadpage.!ctklabel{i}.!ctkcanvas"')
    except tkinter.TclError:
        main._record_crash(*sys.exc_info(), St())
check("13 'invalid command name' races (different widget paths) -> ONE recovered entry with a count, NOT crashes", ([e.kind for e in store.entries], store.entries[0].count), ([KIND_RECOVERED], 13))
check("...with the page/operation and the traceback kept", (store.entries[0].current_page, store.entries[0].current_operation, "TclError" in store.entries[0].traceback_text), ("Upload", "Uploading q1.mov", True))
try:
    raise tkinter.TclError("image \"pyimage5\" doesn't exist")
except tkinter.TclError:
    main._record_crash(*sys.exc_info(), St())
try:
    raise KeyError("a genuine bug")
except KeyError:
    main._record_crash(*sys.exc_info(), St())
check("other Tcl errors and real exceptions are STILL recorded as crashes", sorted(e.kind for e in store.entries), sorted([KIND_RECOVERED, KIND_CRASH, KIND_CRASH]))
check("...and Developer Tools' last-error query sees only those", store.latest_of_kinds(KIND_CRASH).error_type in ("KeyError", "TclError"), True)

if errors:
    print("\nFAILURES:", errors); os._exit(1)
print("\nALL CHECKS PASSED"); os._exit(0)

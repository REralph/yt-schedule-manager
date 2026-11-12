"""The 'display scale of 0' fault (hundreds of ZeroDivisionError crashes) and crash-storm de-duplication."""
import sys, os, json, subprocess, tempfile, pathlib, datetime as dt, textwrap
ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

errors = []
def check(name, actual, expected=True):
    ok = actual == expected
    print(("PASS" if ok else "FAIL"), name, "->" if not ok else "", (actual, expected) if not ok else "")
    if not ok: errors.append(name)

# ---------------------------------------------------------------- reproduce in a separate process
PROBE = textwrap.dedent('''
    import sys, os
    sys.path.insert(0, {root!r})
    guard = "--guard" in sys.argv
    if guard:
        from ui import ctk_guard
        ctk_guard.install_scaling_guard()
    import customtkinter as ctk
    from customtkinter.windows.widgets.scaling.scaling_tracker import ScalingTracker
    # Windows answering "scale 0" for the monitor (screen off / locked / display change) -- exactly
    # what GetDpiForMonitor does on failure
    ScalingTracker.get_window_dpi_scaling = classmethod(lambda cls, window: 0)
    class App(ctk.CTk):
        errors = []
        def report_callback_exception(self, exc, val, tb): App.errors.append(exc.__name__)
    app = App(); app.geometry("400x300")
    ScalingTracker.check_dpi_scaling()          # the tracker's periodic poll: this is what pushes the 0 into the window
    for w in (500, 420, 610, 480):               # the user resizing / moving the window
        app.geometry(f"{{w}}x300"); app.update(); app.update_idletasks()
    app.after(300, app.quit); app.mainloop()
    print("RESULT", sorted(set(App.errors)), len(App.errors), flush=True)
    os._exit(0)
''').format(root=str(ROOT))
probe_path = pathlib.Path(tempfile.mkdtemp()) / "probe.py"
probe_path.write_text(PROBE, encoding="utf-8")
def run_probe(*args):
    r = subprocess.run([sys.executable, str(probe_path), *args], capture_output=True, text=True, timeout=60)
    line = [l for l in r.stdout.splitlines() if l.startswith("RESULT")]
    return line[0] if line else f"NO RESULT: {r.stdout[-300:]} {r.stderr[-300:]}"
without = run_probe()
check("WITHOUT the guard a display scale of 0 makes window events raise ZeroDivisionError (the bug)", "ZeroDivisionError" in without, True)
with_guard = run_probe("--guard")
check("WITH the guard the same scenario raises nothing", with_guard, "RESULT [] 0")

# ---------------------------------------------------------------- the guard's own behaviour
from ui import ctk_guard
from customtkinter.windows.widgets.scaling.scaling_tracker import ScalingTracker
answers = iter([1.25, 0, None, float("nan"), 400, 1.5, "garbage"])
ScalingTracker.get_window_dpi_scaling = classmethod(lambda cls, window: next(answers))
ctk_guard._installed = False
ctk_guard.install_scaling_guard()
got = [ScalingTracker.get_window_dpi_scaling(None) for _ in range(7)]
check("a real scale is passed through; zero / None / NaN / absurd / garbage fall back to the last good one", got, [1.25, 1.25, 1.25, 1.25, 1.25, 1.5, 1.5])
ctk_guard.install_scaling_guard()   # idempotent
check("installing twice is harmless", ScalingTracker.get_window_dpi_scaling(None) in (1.5, 1.25), True)

# ---------------------------------------------------------------- crash storms are folded into one entry
import data.crash_log as cl
cl.CRASH_LOG_PATH = pathlib.Path(tempfile.mkdtemp()) / "crash_log.json"
from data.crash_log import CrashLogStore, CrashReport, build_crash_report
store = CrashLogStore()
def boom(n):
    try:
        raise ZeroDivisionError("float division by zero")
    except ZeroDivisionError:
        return build_crash_report(*sys.exc_info(), None)
for i in range(200): store.record(boom(i))
check("200 identical crashes in a row become ONE entry counting 200", (len(store.entries), store.entries[0].count), (1, 200))
check("...the report says how many times it happened", "Occurrences: 200" in store.entries[0].format(), True)
try:
    raise KeyError("a different bug")
except KeyError:
    store.record(build_crash_report(*sys.exc_info(), None))
check("a DIFFERENT crash still gets its own entry", len(store.entries), 2)
def same_message_elsewhere():
    raise ZeroDivisionError("float division by zero")
try:
    same_message_elsewhere()
except ZeroDivisionError:
    store.record(build_crash_report(*sys.exc_info(), None))
check("the same message from a DIFFERENT place in the code is a separate entry", len(store.entries), 3)
old = store.entries[-1]
old_report = boom(0); old_report.timestamp = dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=1)
store.record(old_report)
check("the same crash an hour LATER is a new entry again (so recurrence stays visible)", len(store.entries), 4)
reloaded = CrashLogStore()
check("counts survive a restart", sorted(e.count for e in reloaded.entries)[-1], 200)

if errors:
    print("\nFAILURES:", errors); sys.exit(1)
print("\nALL CHECKS PASSED")

import sys, os, json, time, tempfile, pathlib, threading, subprocess, socket, ssl, datetime as dt
sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent.parent))
import data.queue_order as _qo_isolation, tempfile as _tf_isolation, pathlib as _pl_isolation; _qo_isolation.QUEUE_ORDER_PATH = _pl_isolation.Path(_tf_isolation.mkdtemp()) / "queue_order.json"  # a test must NEVER write the real order ledger (data/queue_order.json)

TMP = pathlib.Path(tempfile.mkdtemp())

# ---- redirect EVERY store to a temp dir before anything loads
import data.config as m_config
import data.auto_schedule_config as m_asc
import data.upload_activity as m_ua
import data.history as m_hist
import data.auto_schedule_history as m_ash
import data.error_log as m_el
import data.crash_log as m_cl
import data.shorts_folder as m_sf
import data.session_state as m_ss
import youtube.video_repo as m_vr
import youtube.quota as m_q
import auth.oauth_manager as m_oauth

m_config.CONFIG_PATH = TMP / "config.json"
m_asc.AUTO_SCHEDULE_CONFIG_PATH = TMP / "auto_schedule_config.json"
m_ua.UPLOAD_ACTIVITY_PATH = TMP / "upload_activity.json"
m_hist.HISTORY_PATH = TMP / "history.json"
m_ash.AUTO_SCHEDULE_HISTORY_PATH = TMP / "auto_schedule_history.json"
m_el.ERROR_LOG_PATH = TMP / "error_log.json"
m_cl.CRASH_LOG_PATH = TMP / "crash_log.json"
m_sf.REGISTRY_PATH = TMP / "shorts_folder_registry.json"
m_ss.SESSION_PATH = TMP / "session_state.json"
m_vr.CACHE_PATH = TMP / "video_cache.json"
m_q.QUOTA_PATH = TMP / "quota_usage.json"
m_oauth.TOKEN_PATH = TMP / "token.json"

from data import diagnostics, safe_io, session_state
from data.diagnostics import redact_secrets, KIND_CRASH, KIND_DATA, KIND_RECOVERED, KIND_SHUTDOWN, KIND_ERROR
from data.config import AppConfig
from data.auto_schedule_config import AutoScheduleConfig
from data.upload_activity import UploadActivityLog, UploadActivityEntry
from data.history import HistoryStore
from data.auto_schedule_history import AutoScheduleHistoryStore
from data.error_log import ErrorLogStore
from data.crash_log import CrashLogStore, CrashReport, build_crash_report
from data.shorts_folder import ShortsFolderRegistry
from data import crash_history
from youtube.video_repo import VideoRepository
from youtube.quota import QuotaTracker
from youtube.errors import classify_exception, KIND_AUTH, KIND_NETWORK, KIND_QUOTA, KIND_FILE, KIND_OTHER

errors = []
def check(name, actual, expected=True):
    ok = actual == expected
    print(("PASS" if ok else "FAIL"), name, "->" if not ok else "", (actual, expected) if not ok else "")
    if not ok:
        errors.append(name)

captured = []
def install_capture():
    captured.clear()
    diagnostics.set_sink(lambda **kw: captured.append(kw))
def problems():
    return [c for c in captured if c["kind"] == KIND_DATA]

# =============================================================== safe_io: atomic writes
print("\n--- atomic writes ---")
p = TMP / "a.json"
safe_io.atomic_write_json(p, {"x": 1})
check("atomic write produces the file", json.loads(p.read_text()), {"x": 1})
check("no temp files left behind", [f.name for f in TMP.glob("a.json.*.tmp")], [])

# concurrent writers + a reader that must NEVER see a partial file
stop = threading.Event()
bad_reads = []
prod_read_problems = []
diagnostics.set_sink(lambda **kw: prod_read_problems.append(kw))
def reader():
    # the production read path (safe_io.read_json_object) -- what every store uses
    while not stop.is_set():
        data = safe_io.read_json_object(p, "a.json")
        if data is not None and set(data) - {"writer", "i", "pad", "x", "keep", "new", "after"}:
            bad_reads.append("unexpected content")
def outside_reader():
    # models ANOTHER process reading the file: it may be refused mid-replace (PermissionError),
    # but must never be handed a truncated file
    while not stop.is_set():
        try:
            json.loads(p.read_text())
        except (FileNotFoundError, PermissionError):
            pass
        except Exception as e:
            bad_reads.append("outside reader saw a partial file: " + repr(e))
        time.sleep(0.002)
def writer(n):
    for i in range(40):
        safe_io.atomic_write_json(p, {"writer": n, "i": i, "pad": "x" * 5000})
rt = threading.Thread(target=reader); rt.start()
rt2 = threading.Thread(target=outside_reader); rt2.start()
ws = [threading.Thread(target=writer, args=(n,)) for n in range(8)]
[w.start() for w in ws]; [w.join() for w in ws]
stop.set(); rt.join(); rt2.join()
check("8 writers x 40 saves of the same file: no reader EVER saw a truncated/partial file", bad_reads, [])
check("...and the app's own read path never misreported a save-in-progress as corruption/unreadable", [d["message"] for d in prod_read_problems if d["kind"] == "data"], [])
final = json.loads(p.read_text())
check("...and the final file is one complete payload", set(final) == {"writer", "i", "pad"}, True)
check("...with no temp files left", [f.name for f in TMP.glob("a.json.*.tmp")], [])

# failure between write and replace leaves the ORIGINAL intact
safe_io.atomic_write_json(p, {"keep": "me"})
real_replace = os.replace
def failing_replace(a, b):
    raise OSError("disk exploded")
os.replace = failing_replace
try:
    try:
        safe_io.atomic_write_json(p, {"new": "data"})
        check("a failing replace raises to the caller", False)
    except OSError:
        check("a failing replace raises to the caller", True)
finally:
    os.replace = real_replace
check("...and the previous file is fully intact (not truncated, not half-new)", json.loads(p.read_text()), {"keep": "me"})
check("...and the temp file was cleaned up", [f.name for f in TMP.glob("a.json.*.tmp")], [])

# transient PermissionError (antivirus holding the target) is retried
calls = {"n": 0}
def flaky_replace(a, b):
    calls["n"] += 1
    if calls["n"] <= 2:
        raise PermissionError("in use")
    return real_replace(a, b)
os.replace = flaky_replace
try:
    safe_io.atomic_write_json(p, {"after": "retry"})
finally:
    os.replace = real_replace
check("a transient PermissionError is retried and the save succeeds", (json.loads(p.read_text()), calls["n"]), ({"after": "retry"}, 3))

# =============================================================== safe_io: tolerant reads
print("\n--- tolerant reads ---")
install_capture()
check("missing file -> None, not a problem", (safe_io.read_json_object(TMP / "nope.json", "nope"), problems()), (None, []))

c = TMP / "trunc.json"; c.write_text('{"entries": [{"a": 1}, {"b"', encoding="utf-8")
install_capture()
check("truncated JSON -> None (no exception)", safe_io.read_json_object(c, "trunc.json"), None)
check("...the damaged file was moved aside, not deleted", (c.exists(), len(list(TMP.glob("trunc.json.corrupt-*")))), (False, 1))
check("...and ONE data problem was recorded, naming the file", (len(problems()), "trunc.json" in problems()[0]["message"]), (1, True))

l = TMP / "list.json"; l.write_text("[1, 2, 3]", encoding="utf-8")
install_capture()
check("valid JSON that isn't an object -> treated as corrupt", (safe_io.read_json_object(l, "list.json"), len(problems())), (None, 1))

b = TMP / "binary.json"; b.write_bytes(b"\xff\xfe\x00garbage\x80\x81")
install_capture()
check("undecodable bytes -> None, recorded, no exception", (safe_io.read_json_object(b, "binary.json"), len(problems())), (None, 1))

for i in range(6):
    q = TMP / "many.json"; q.write_text("{bad", encoding="utf-8")
    safe_io.read_json_object(q, "many.json")
    time.sleep(1.05)
check("only the newest 3 corrupt copies are kept", len(list(TMP.glob("many.json.corrupt-*"))), 3)

s = safe_io.Sanitizer("t")
check("int: '5' -> 5", s.int("5", 1), 5)
check("int: 7.0 -> 7", s.int(7.0, 1), 7)
check("int: True is NOT an int -> default", s.int(True, 3), 3)
check("int: None -> default", s.int(None, 4), 4)
check("int: below/above range clamped", (s.int(-5, 1, lo=1, hi=6), s.int(99, 1, lo=1, hi=6)), (1, 6))
check("int: nan/inf -> default", (s.int(float("nan"), 9), s.int(float("inf"), 9)), (9, 9))
check("bool: 'true'/'0'/2", (s.bool("true", False), s.bool("0", True), s.bool(2, True)), (True, False, True))
check("choice: bad value -> default", s.choice("x", ("a", "b"), "a"), "a")
check("str: non-string -> default", s.str(5, "d"), "d")
check("sanitizer remembered every problem", len(s.problems) >= 8, True)

# =============================================================== redaction
print("\n--- secrets are redacted ---")
samples = {
    "google access token": "Authorization failed for ya29.a0AfB_byCdEfGhIjKlMnOpQrStUvWxYz0123456789_-abc",
    "refresh token": "refresh_token: 1//0gAbCdEfGhIjKlMnOpQrStUvWxYz-0123456789ABCDEFG",
    "api key in a url": "GET https://www.googleapis.com/youtube/v3/videos?part=id&key=AIzaSyA1234567890abcdefghijklmnopqrstuv failed",
    "bearer header": "headers={'Authorization': 'Bearer abc.def-ghi_123456'}",
    "client secret": '{"client_secret": "GOCSPX-abcdefghijklmnop123456"}',
    "generic token=": "callback?access_token=abcd1234efgh5678&state=1",
    "password": "password=hunter2hunter2",
}
secrets = ["ya29.a0AfB", "1//0gAbCd", "AIzaSyA1234", "abc.def-ghi", "GOCSPX-abcdef", "abcd1234efgh5678", "hunter2hunter2"]
for (label, text), secret in zip(samples.items(), secrets):
    out = redact_secrets(text)
    check(f"redacted: {label}", (secret not in out, "REDACTED" in out), (True, True))
harmless = "('invalid_grant: Token has been expired or revoked.', {'error': 'invalid_grant'}) SSL: WRONG_VERSION_NUMBER q25.mov"
check("ordinary error text is left alone", redact_secrets(harmless), harmless)

# =============================================================== diagnostics plumbing
print("\n--- diagnostics ---")
diagnostics.set_sink(None); diagnostics._pending.clear()
diagnostics.report_data_problem("early.json", "reported before the store exists")
got = []
diagnostics.set_sink(lambda **kw: got.append(kw))
check("a problem reported before the sink existed is delivered once it does", [g["message"] for g in got], ["early.json: reported before the store exists"])
def boom(**kw): raise RuntimeError("sink is broken")
diagnostics.set_sink(boom)
try:
    diagnostics.report_recovered("x", ValueError("y")); ok = True
except Exception:
    ok = False
check("a broken sink can never make reporting raise", ok, True)

# =============================================================== crash log
print("\n--- crash log ---")
diagnostics.set_sink(None); diagnostics._pending.clear()
m_cl.CRASH_LOG_PATH = TMP / "crash1.json"
store = CrashLogStore.shared()
check("shared() returns ONE process-wide store", CrashLogStore.shared() is store, True)
try:
    raise ValueError("first crash Bearer abc123def456ghi")
except ValueError:
    store.record(build_crash_report(*sys.exc_info(), None))
check("a crash recorded through one handle is visible through every other (they were separate before)", len(CrashLogStore.shared().entries), 1)
store.clear()
check("clear() really clears -- another handle cannot resurrect entries", (len(CrashLogStore.shared().entries), json.loads((TMP / "crash1.json").read_text())["entries"]), (0, []))

class FakeState:
    current_tab = "Upload"; dev_selected_component = "0.9.6.3"; last_action = "Uploading q25.mov (attempt 2/2)"
    current_upload_filename = "q25.mov"; current_upload_attempt = 2
    class config: developer_mode = True
    class _Client: token = "ya29.SHOULD-NEVER-APPEAR-1234567890"; secret = "GOCSPX-never-appear"
    client = _Client()
    repo = None
try:
    raise RuntimeError("[SSL: WRONG_VERSION_NUMBER] wrong version number key=AIzaSyA1234567890abcdefghijklmnopqrstuv")
except RuntimeError:
    rep = build_crash_report(*sys.exc_info(), FakeState())
text = rep.format()
for needle in ("CRASH / ERROR REPORT", "RuntimeError", "WRONG_VERSION_NUMBER", "Traceback:", "test_stability_data.py", "Current Page: Upload",
               "Current Item ID: 0.9.6.3", "Uploading q25.mov (attempt 2/2)", "Upload Filename: q25.mov", "Upload Attempt: 2", "Application State:", "Date/Time:"):
    check(f"report contains {needle!r}", needle in text, True)
check("report never contains credentials (state's client object is never read; message keys are masked)", all(s not in text for s in ("SHOULD-NEVER-APPEAR", "never-appear", "AIzaSyA1234567890")), True)
check("error_text is just type: message (masked)", (rep.error_text().startswith("RuntimeError: [SSL:"), "AIzaSyA" not in rep.error_text()), (True, True))

# tolerant load
good = json.loads(json.dumps({"entries": [
    {"crash_id": "ok1", "timestamp": "2026-09-20T10:00:00+00:00", "error_type": "E", "error_message": "fine", "traceback_text": "tb"},
    {"nonsense": 1},
    {"crash_id": "bad", "timestamp": "not-a-date", "error_type": "E", "error_message": "", "traceback_text": ""},
    {"crash_id": "old-format", "timestamp": "2026-09-19T10:00:00", "error_type": "OldE", "error_message": "no kind/count fields", "traceback_text": ""},
]}))
(TMP / "crash2.json").write_text(json.dumps(good), encoding="utf-8")
m_cl.CRASH_LOG_PATH = TMP / "crash2.json"
install_capture()
s2 = CrashLogStore()
check("a crash log with malformed entries loads the good ones (the constructor used to raise, blocking start-up)", sorted(e.crash_id for e in s2.entries), ["ok1", "old-format"])
check("...old-format entries get sane defaults", (s2.entries[-1].kind, s2.entries[-1].count), (KIND_CRASH, 1))
check("...and the skipped entries were reported once", len(problems()), 1)
(TMP / "crash3.json").write_text("{corrupt", encoding="utf-8")
m_cl.CRASH_LOG_PATH = TMP / "crash3.json"
check("a corrupt crash log starts empty instead of crashing", len(CrashLogStore().entries), 0)

# dedupe + trim
m_cl.CRASH_LOG_PATH = TMP / "crash4.json"
s4 = CrashLogStore()
for _ in range(5):
    s4.record_event(KIND_RECOVERED, "TimeoutError", "Refresh: read timed out", operation="Refreshing")
check("5 identical recovered failures -> ONE entry with a counter (no flood)", (len(s4.entries), s4.entries[0].count), (1, 5))
s4.record_event(KIND_RECOVERED, "TimeoutError", "Refresh: a DIFFERENT message", operation="Refreshing")
check("a different problem gets its own entry", len(s4.entries), 2)
m_cl.MAX_ENTRIES = 6
s5 = CrashLogStore()
for i in range(3):
    s5.record(CrashReport(crash_id=f"crash{i}", timestamp=dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=30 - i), error_type="E", error_message=f"c{i}", traceback_text=""))
for i in range(12):
    s5.record_event(KIND_RECOVERED, "E", f"noise {i}", operation=f"op{i}")
check("background noise can never push real crashes out of a full log", {e.crash_id for e in s5.entries if e.kind == KIND_CRASH}, {"crash0", "crash1", "crash2"})
check("...and the log stays bounded", len(s5.entries), 6)
m_cl.MAX_ENTRIES = 200

# =============================================================== stores never crash on bad files
print("\n--- every store survives damaged files ---")
def write(path, text): pathlib.Path(path).write_text(text, encoding="utf-8")

# AppConfig
write(m_config.CONFIG_PATH, "{truncated")
install_capture()
c = AppConfig.load()
check("AppConfig: corrupt file -> defaults, recorded", (c.upload_videos_per_day, len(problems()) >= 1), (2, True))
write(m_config.CONFIG_PATH, json.dumps({"upload_videos_per_day": "lots", "developer_mode": "maybe", "daily_quota_budget_units": None,
                                        "shorts_folder_path": ["not", "text"], "new_video_detection_window_seconds": -30, "quality_info_expanded": 7}))
install_capture()
c = AppConfig.load()
check("AppConfig: wrong types/ranges -> safe defaults", (c.upload_videos_per_day, c.developer_mode, c.daily_quota_budget_units, c.shorts_folder_path, c.new_video_detection_window_seconds, c.quality_info_expanded),
      (2, False, 9000, "", 1, False))
check("...recorded as ONE problem listing every fix", (len(problems()), "upload_videos_per_day" in problems()[0]["message"] and "developer_mode" in problems()[0]["message"]), (1, True))
write(m_config.CONFIG_PATH, json.dumps({"upload_videos_per_day": 5, "developer_mode": True, "an_unknown_future_field": 1}))
install_capture()
c = AppConfig.load()
check("AppConfig: good values kept, unknown fields ignored, nothing reported", (c.upload_videos_per_day, c.developer_mode, problems()), (5, True, []))
write(m_config.CONFIG_PATH, "[]")
check("AppConfig: a JSON list instead of an object", AppConfig.load().upload_videos_per_day, 2)

# AutoScheduleConfig
write(m_asc.AUTO_SCHEDULE_CONFIG_PATH, json.dumps({
    "enabled": "yes", "target_mode": "monthly", "daily_target_views": "lots", "normal_videos_per_day": 99, "max_videos_per_day": 0,
    "window_start_hour": 30, "window_end_hour": 3, "estimated_views_per_short": 0,
    "slot_presets": {"3": ["07:00", "06:00", "08:00"], "2": ["06:00", "09:00"], "x": [], "4": ["6:00"], "1": "nope"},
}))
install_capture()
a = AutoScheduleConfig.load()
check("AutoSchedule: modes/ranges sanitised", (a.enabled, a.target_mode, a.daily_target_views, a.normal_videos_per_day, a.max_videos_per_day, a.estimated_views_per_short), (False, "daily", 3250, 6, 1, 1))
check("...an impossible window (hour 30 / end before start) falls back to the default window", (a.window_start.strftime("%H:%M"), a.window_end.strftime("%H:%M")), ("06:00", "14:00"))
check("...only well-formed presets survive (right count, valid HH:MM, increasing)", a.slot_presets, {"2": ["06:00", "09:00"]})
check("...get_slot_times still works for a dropped preset (default spacing)", len(a.get_slot_times(3)), 3)
write(m_asc.AUTO_SCHEDULE_CONFIG_PATH, "not json at all")
check("AutoSchedule: corrupt file -> defaults", AutoScheduleConfig.load().normal_videos_per_day, 2)

# upload activity
now_iso = dt.datetime.now(dt.timezone.utc).isoformat()
write(m_ua.UPLOAD_ACTIVITY_PATH, json.dumps({"entries": [
    {"filename": "good.mp4", "uploaded_at": now_iso, "status": "verified", "video_id": "V1"},
    {"filename": "no-timestamp.mp4", "status": "done"},
    {"filename": "bad-timestamp.mp4", "uploaded_at": "yesterday-ish", "status": "done"},
    {"filename": "bad-schedule.mp4", "uploaded_at": now_iso, "status": "done", "scheduled_at": "soon"},
    {"filename": "bad-number.mp4", "uploaded_at": now_iso, "status": "done", "attempts": "many"},
    "not even an object",
]}))
install_capture()
log = UploadActivityLog.load()
check("UploadActivityLog: only the well-formed entry loads (each bad one skipped, not fatal)", [e.filename for e in log.entries], ["good.mp4"])
check("...today_entries()/_prune() (which parse timestamps on every render) can no longer hit a bad one", (len(log.today_entries()), log.record(UploadActivityEntry(filename="n", uploaded_at=now_iso, status="uploading")) is not None), (1, True))
check("...the skipped entries were reported", len(problems()) >= 1, True)
write(m_ua.UPLOAD_ACTIVITY_PATH, "\x00\x00\x00")
check("UploadActivityLog: garbage file -> empty log", UploadActivityLog.load().entries, [])

# history / auto schedule history / error log / registry / cache / quota
write(m_hist.HISTORY_PATH, json.dumps({"entries": [{"entry_id": "e1", "timestamp": "2026-09-20T10:00:00+00:00", "description": "ok", "changes": []},
                                                    {"entry_id": "e2"}, {"timestamp": "x"}, 5]}))
check("HistoryStore: malformed entries skipped", [e.entry_id for e in HistoryStore().entries], ["e1"])
write(m_hist.HISTORY_PATH, "[[[")
check("HistoryStore: corrupt file", HistoryStore().entries, [])

write(m_ash.AUTO_SCHEDULE_HISTORY_PATH, json.dumps({"decisions": [{"decision_id": "d1"}, {"garbage": True}]}))
check("AutoScheduleHistoryStore: malformed decisions skipped, no crash", len(AutoScheduleHistoryStore().decisions), 0)

write(m_el.ERROR_LOG_PATH, json.dumps({"entries": [
    {"report_id": "r1", "timestamp": "2026-09-20T10:00:00+00:00", "item_id": "0.9.6.3", "page": "Upload", "function": "f", "error_message": "m"},
    {"only": "junk"}, {"report_id": "r3", "timestamp": "nope", "item_id": "x", "page": "p", "function": "f", "error_message": "m"},
    {"report_id": "r4", "timestamp": "2026-09-19T10:00:00", "item_id": "i", "page": "p", "function": "f", "error_message": "m", "brand_new_field": 1}]}))
check("ErrorLogStore: bad entries skipped, unknown fields tolerated (it used to raise on both)", sorted(e.report_id for e in ErrorLogStore().entries), ["r1", "r4"])

write(m_sf.REGISTRY_PATH, json.dumps({"entries": {"5": {"q_number": "5", "filename": "q5.mov", "file_size": 10, "status": "uploaded"}, "6": {"q_number": "6"}, "7": 3}}))
reg = ShortsFolderRegistry.load()
check("ShortsFolderRegistry: bad entries skipped, the good one kept", (sorted(reg.entries), reg.is_uploaded("5")), (["5"], True))
write(m_sf.REGISTRY_PATH, '{"entries": [1,2]}')
check("ShortsFolderRegistry: wrong shape -> empty", ShortsFolderRegistry.load().entries, {})

class NoClient: pass
write(m_vr.CACHE_PATH, json.dumps({"last_refreshed": "garbage", "videos": [{"video_id": "V1", "title": "ok", "publish_at": "2026-12-01T06:00:00+00:00"},
                                                                          {"title": "no id"}, {"video_id": "V2", "publish_at": "not a date"}, 7]}))
repo = VideoRepository(NoClient())
check("VideoRepository: cache with bad entries/timestamp loads the good videos", ([v.video_id for v in repo.videos], repo.last_refreshed), (["V1"], None))
write(m_vr.CACHE_PATH, "{")
check("VideoRepository: corrupt cache", VideoRepository(NoClient()).videos, [])

write(m_q.QUOTA_PATH, "[1, 2]")
check("QuotaTracker: a JSON list (raw.get used to raise AttributeError)", QuotaTracker.load().units_used, 0)
write(m_q.QUOTA_PATH, json.dumps({"day": dt.date.today().isoformat(), "units_used": "many"}))
check("QuotaTracker: non-numeric count", QuotaTracker.load().units_used, 0)
write(m_q.QUOTA_PATH, json.dumps({"day": dt.date.today().isoformat(), "units_used": -50}))
check("QuotaTracker: negative count clamped", QuotaTracker.load().units_used, 0)

# quota recording from many threads never corrupts the file
qt = QuotaTracker.load()
ts = [threading.Thread(target=lambda: [qt.record(1) for _ in range(30)]) for _ in range(8)]
[t.start() for t in ts]; [t.join() for t in ts]
check("QuotaTracker: 8 threads recording -> file still valid JSON", isinstance(json.loads(m_q.QUOTA_PATH.read_text())["units_used"], int), True)

# =============================================================== crash history rows
print("\n--- crash history view ---")
m_cl.CRASH_LOG_PATH = TMP / "crash5.json"
m_el.ERROR_LOG_PATH = TMP / "errors5.json"
cs = CrashLogStore(); es = ErrorLogStore()
old = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=10)
cs.record(CrashReport(crash_id="oldcrash", timestamp=old, error_type="OldError", error_message="ancient", traceback_text="tb-old"))
cs.record(build_crash_report(RuntimeError, RuntimeError("boom ya29.SECRETSECRETSECRETSECRET123"), None, FakeState()))
es.record("0.9.6.3", "Upload", 'Uploading "x"', "[SSL: WRONG_VERSION_NUMBER] wrong version number", operation="One Upload Page -- uploading q7.mov")
cs.record_event(KIND_RECOVERED, "TimeoutError", "Refreshing: read timed out", operation="Refreshing the schedule", page="Calendar")
rows = crash_history.build_rows(cs, es)
check("rows from BOTH stores, newest first", ([r.timestamp for r in rows] == sorted([r.timestamp for r in rows], reverse=True), len(rows)), (True, 4))
import re
line_re = re.compile(r"^\d\d/\d\d/\d{4} \d\d:\d\d \| [^|]+ \| [^|]+ \| .+ \| .+")
check("every line follows: date/time | page | Item ID | operation | short error", all(line_re.match(r.line()) for r in rows), True)
err_row = next(r for r in rows if r.kind == KIND_ERROR)
check("the example from the spec: an upload SSL error line", ("Upload" in err_row.line() and "0.9.6.3" in err_row.line() and "WRONG_VERSION_NUMBER" in err_row.line()), True)
crash_row = next(r for r in rows if r.kind == KIND_CRASH and r.key != "oldcrash")
check("a crash row carries page / item id / operation from the app state", (crash_row.page, crash_row.item_id, crash_row.operation), ("Upload", "0.9.6.3", "Uploading q25.mov (attempt 2/2)"))
check("each row's report is ONLY that entry", ("ancient" not in crash_row.report_text and "SSL" not in crash_row.report_text, "boom" in crash_row.report_text), (True, True))
check("credentials are masked in the row summary AND report", ("SECRETSECRET" not in crash_row.summary and "SECRETSECRET" not in crash_row.report_text and "SECRETSECRET" not in crash_row.error_text), True)
check("the error-log row's report is the standard ERROR REPORT", err_row.report_text.startswith("ERROR REPORT"), True)
check("COPY ERROR text = the error only (no traceback/context)", (crash_row.error_text.startswith("RuntimeError: boom"), "Traceback" in crash_row.error_text), (True, False))
removed = crash_history.clear_older_than(cs, es, 7)
check("clear_older_than(7 days) removes only the old entry", (removed, [r.key for r in crash_history.build_rows(cs, es) if r.key == "oldcrash"]), (1, []))
check("...leaving the recent ones", len(crash_history.build_rows(cs, es)), 3)
crash_history.clear_all(cs, es)
check("clear_all empties both stores (and the files)", (crash_history.build_rows(cs, es), json.loads(m_cl.CRASH_LOG_PATH.read_text())["entries"], json.loads(m_el.ERROR_LOG_PATH.read_text())["entries"]), ([], [], []))

# =============================================================== session marker: unexpected exits
print("\n--- session marker ---")
m_ss.SESSION_PATH = TMP / "session_a.json"
check("first ever start: nothing to report", session_state.begin_session(), None)
session_state.note(page="Upload", operation="Uploading q9.mov")
session_state._write()
dead = subprocess.Popen([sys.executable, "-c", "pass"]); dead.wait()
marker = json.loads(m_ss.SESSION_PATH.read_text()); marker["pid"] = dead.pid
m_ss.SESSION_PATH.write_text(json.dumps(marker))
prev = session_state.begin_session()
check("a previous session that was never closed cleanly (process gone) is reported, with its page/operation", (prev is not None, prev and prev["page"], prev and prev["operation"]), (True, "Upload", "Uploading q9.mov"))
session_state.end_session()
check("a cleanly closed session is not reported next time", session_state.begin_session(), None)
alive = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
try:
    marker = json.loads(m_ss.SESSION_PATH.read_text()); marker["pid"] = alive.pid; marker["clean"] = False
    m_ss.SESSION_PATH.write_text(json.dumps(marker))
    check("an unclean marker whose process is STILL RUNNING (another instance) is not called a crash", session_state.begin_session(), None)
    check("...and checking it did not kill that process", alive.poll() is None, True)
finally:
    alive.kill()
m_ss.SESSION_PATH.write_text("garbage")
check("a corrupt marker never blocks start-up", session_state.begin_session(), None)

# =============================================================== error classification
print("\n--- error classification ---")
from google.auth.exceptions import RefreshError
class FakeHttpError(Exception):
    def __init__(self, status, text=""):
        super().__init__(text)
        self.resp = type("R", (), {"status": status})()
info = classify_exception(RefreshError("invalid_grant: Token has been expired or revoked.", {"error": "invalid_grant"}))
check("expired/revoked sign-in -> AUTH, a retry can't help", (info.kind, info.retry_can_help), (KIND_AUTH, False))
check("...message says what to do AND keeps the real error", ("Sign Out" in info.message() and "invalid_grant" in info.message()), True)
check("an SSL error is NETWORK (retryable)", (classify_exception(ssl.SSLError("[SSL: WRONG_VERSION_NUMBER] wrong version number")).kind, classify_exception(ssl.SSLError("x")).retry_can_help), (KIND_NETWORK, True))
check("timeouts / connection errors are NETWORK", (classify_exception(socket.timeout("timed out")).kind, classify_exception(ConnectionResetError("reset")).kind, classify_exception(TimeoutError("The read operation timed out")).kind), (KIND_NETWORK,) * 3)
check("HTTP 500 is NETWORK (transient server error)", classify_exception(FakeHttpError(503, "backend error")).kind, KIND_NETWORK)
check("HTTP 403 quotaExceeded -> QUOTA, not retryable", (classify_exception(FakeHttpError(403, "quotaExceeded")).kind, classify_exception(FakeHttpError(403, "quotaExceeded")).retry_can_help), (KIND_QUOTA, False))
check("HTTP 401 -> AUTH", classify_exception(FakeHttpError(401, "Unauthorized")).kind, KIND_AUTH)
check("a missing video file -> FILE, not retryable", (classify_exception(FileNotFoundError("q9.mov")).kind, classify_exception(FileNotFoundError("q9.mov")).retry_can_help), (KIND_FILE, False))
check("anything else -> OTHER (retryable, message unchanged)", (classify_exception(ValueError("weird")).kind, classify_exception(ValueError("weird")).message()), (KIND_OTHER, "weird"))

if errors:
    print("\nFAILURES:", errors)
    sys.exit(1)
print("\nALL CHECKS PASSED")

import sys, json, datetime as dt, tempfile, pathlib, threading
sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent.parent))
import data.queue_order as _qo_isolation, tempfile as _tf_isolation, pathlib as _pl_isolation; _qo_isolation.QUEUE_ORDER_PATH = _pl_isolation.Path(_tf_isolation.mkdtemp()) / "queue_order.json"  # a test must NEVER write the real order ledger (data/queue_order.json)

import data.upload_activity as ua
ua.UPLOAD_ACTIVITY_PATH = pathlib.Path(tempfile.mkdtemp()) / "upload_activity.json"
from data.upload_activity import (
    STATUS_DONE, STATUS_FAILED, STATUS_UPLOAD_COMPLETE, STATUS_UPLOADING, STATUS_VERIFICATION_FAILED,
    STATUS_VERIFIED, STATUS_VERIFYING, UploadActivityEntry, UploadActivityLog,
)
import youtube.upload_verifier as uv
from youtube.upload_verifier import run_verification
from youtube.client import YouTubeClient, VideoVerification
from youtube.models import Video

errors = []
def check(name, actual, expected=True):
    ok = actual == expected
    print(("PASS" if ok else "FAIL"), name, "->" if not ok else "", (actual, expected) if not ok else "")
    if not ok:
        errors.append(name)

NOW = dt.datetime.now(dt.timezone.utc).isoformat()

# ============================ data/upload_activity.py ============================
log = UploadActivityLog()
e1 = log.record(UploadActivityEntry(filename="Q7.mp4", uploaded_at=NOW, status=STATUS_UPLOADING, title="Bridge Push #BF5"))
check("record() returns the entry", isinstance(e1, UploadActivityEntry), True)
check("every entry gets a stable unique id", len({e1.entry_id}) == 1 and bool(e1.entry_id), True)
e2 = log.record(UploadActivityEntry(filename="Q6.mp4", uploaded_at=NOW, status=STATUS_VERIFIED, title="Bren", video_id="VID6"))
check("get() finds by id", log.get(e1.entry_id) is e1, True)

log.update(e1, status=STATUS_VERIFYING, video_id="VID7", verification_attempts=2, last_verification_at=NOW, verification_detail="exists=yes")
reloaded = UploadActivityLog.load()
r1 = reloaded.get(e1.entry_id)
check("update() persists immediately (status)", r1.status, STATUS_VERIFYING)
check("update() persists immediately (video id / attempts / detail)", (r1.video_id, r1.verification_attempts, r1.verification_detail), ("VID7", 2, "exists=yes"))
check("entry ids survive a save/load round trip", r1.entry_id, e1.entry_id)

try:
    log.update(e1, not_a_field=1)
    check("update() rejects unknown fields", False)
except AttributeError:
    check("update() rejects unknown fields", True)

labels = {
    STATUS_UPLOADING: "UPLOADING", STATUS_UPLOAD_COMPLETE: "VERIFYING", STATUS_VERIFYING: "VERIFYING",
    STATUS_VERIFIED: "VERIFIED", STATUS_VERIFICATION_FAILED: "VERIFICATION FAILED", STATUS_FAILED: "UPLOAD FAILED",
    STATUS_DONE: "UNVERIFIED",
}
for status, label in labels.items():
    check(f"display label for {status}", UploadActivityEntry(filename="x", uploaded_at=NOW, status=status).display_status, label)
check("legacy 'done' is never presented as VERIFIED", UploadActivityEntry(filename="x", uploaded_at=NOW, status=STATUS_DONE).display_status != "VERIFIED", True)

legacy_json = {"entries": [{
    "filename": "old.mp4", "uploaded_at": NOW, "status": "done", "title": "Old", "q_number": "3",
    "scheduled_at": NOW, "schedule_label": "Scheduled", "error": "", "attempts": 1,
}]}
ua.UPLOAD_ACTIVITY_PATH.write_text(json.dumps(legacy_json), encoding="utf-8")
legacy = UploadActivityLog.load().entries[0]
check("an entry saved before the verification layer existed still loads", legacy.filename, "old.mp4")
check("...with sane defaults for the new fields", (legacy.video_id, legacy.verification_attempts, legacy.last_verification_at), ("", 0, ""))
check("...and stays UNVERIFIED", legacy.display_status, "UNVERIFIED")

log = UploadActivityLog()
a = log.record(UploadActivityEntry(filename="a", uploaded_at=NOW, status=STATUS_UPLOADING))
b = log.record(UploadActivityEntry(filename="b", uploaded_at=NOW, status=STATUS_UPLOAD_COMPLETE, video_id="B"))
c = log.record(UploadActivityEntry(filename="c", uploaded_at=NOW, status=STATUS_VERIFYING, video_id="C"))
d = log.record(UploadActivityEntry(filename="d", uploaded_at=NOW, status=STATUS_VERIFIED, video_id="D"))
e = log.record(UploadActivityEntry(filename="e", uploaded_at=NOW, status=STATUS_VERIFICATION_FAILED, video_id="E"))
f = log.record(UploadActivityEntry(filename="f", uploaded_at=NOW, status=STATUS_FAILED))
g = log.record(UploadActivityEntry(filename="g", uploaded_at=NOW, status=STATUS_DONE))
check("pending_verification = exactly the interrupted stages", {x.filename for x in log.pending_verification()}, {"a", "b", "c"})
check("known_video_ids collects only real ids", log.known_video_ids(), {"B", "C", "D", "E"})

rep = UploadActivityEntry(
    filename="Q6.mp4", uploaded_at=NOW, status=STATUS_VERIFICATION_FAILED, title="Bren Gun Ambush", video_id="VID6",
    verification_attempts=5, last_verification_at=NOW, verification_detail="YouTube does not list this video yet.",
    scheduled_at=NOW, attempts=2, error="",
).format_report()
for needle in ("Q6.mp4", "VID6", "VERIFICATION FAILED", "Verification attempts: 5", "Last verification attempt", "YouTube does not list this video yet.", "Upload timestamp", "Bren Gun Ambush"):
    check(f"report contains {needle!r}", needle in rep, True)
other = UploadActivityEntry(filename="Q9.mp4", uploaded_at=NOW, status=STATUS_VERIFIED, title="Unrelated", video_id="VID9").format_report()
check("a report never contains another entry's data", "Q9.mp4" not in rep and "VID9" not in rep and "Q6.mp4" not in other, True)
check("a report carries no credential/token wording", not any(w in rep.lower() for w in ("token", "secret", "password", "bearer")), True)

# ============================ youtube/upload_verifier.py ============================
def ok_check():
    return VideoVerification(exists=True, channel_matches=True, title_matches=True, in_uploads_playlist=True, upload_status="uploaded", privacy_status="private")
def missing():
    return VideoVerification(exists=False)

class FakeVerifyClient:
    def __init__(self, checks, title_results=None):
        self.checks = list(checks)
        self.title_results = list(title_results or [])
        self.check_calls = []
        self.find_calls = []
    def check_video_registered(self, video_id, expected_title):
        self.check_calls.append((video_id, expected_title))
        item = self.checks.pop(0) if len(self.checks) > 1 else self.checks[0]
        if isinstance(item, Exception):
            raise item
        return item
    def find_recent_upload_by_title(self, title, lookback=5, exclude_ids=(), expected_publish_at=None, require_publish_match=False):
        self.find_calls.append((title, lookback, set(exclude_ids)))
        self.last_expected_publish_at = expected_publish_at
        return self.title_results.pop(0) if self.title_results else None

def run(client, **kw):
    sleeps = []
    attempts = []
    outcome = run_verification(
        client, initial_delay=kw.pop("initial_delay", 3.0), retry_delay=kw.pop("retry_delay", 6.0),
        max_attempts=kw.pop("max_attempts", 5), sleep=sleeps.append,
        on_attempt=lambda n, detail, vid: attempts.append((n, detail, vid)), **kw,
    )
    return outcome, sleeps, attempts

o, sleeps, atts = run(FakeVerifyClient([ok_check()]), video_id="V1", title="T")
check("found first try -> VERIFIED", o.verified, True)
check("...after exactly 1 attempt", o.attempts_in_run, 1)
check("...having waited the initial delay first (\"wait briefly\")", sleeps, [3.0])
check("...and reported that attempt", [a[0] for a in atts], [1])

o, sleeps, atts = run(FakeVerifyClient([missing(), missing(), ok_check()]), video_id="V1", title="T")
check("not found twice, then found -> VERIFIED on the retry", (o.verified, o.attempts_in_run, o.last_attempt_number), (True, 3, 3))
check("...waited initial then retry interval between attempts", sleeps, [3.0, 6.0, 6.0])
check("...every attempt reported, in order", [a[0] for a in atts], [1, 2, 3])

client = FakeVerifyClient([missing()])
o, sleeps, atts = run(client, video_id="V1", title="T", max_attempts=4)
check("never found -> NOT verified", o.verified, False)
check("...bounded: exactly max_attempts checks, never indefinite", len(client.check_calls), 4)
check("...and not flagged as definitive (it was just never visible)", o.definitive, False)
check("...detail says how many attempts were made", "after 4 attempts" in o.detail, True)

lag = VideoVerification(exists=True, channel_matches=True, title_matches=True, in_uploads_playlist=False, upload_status="uploaded")
o, _, _ = run(FakeVerifyClient([lag, ok_check()]), video_id="V1", title="T")
check("exists but not yet in the uploads playlist -> retried, then VERIFIED", (o.verified, o.attempts_in_run), (True, 2))

client = FakeVerifyClient([VideoVerification(exists=True, channel_matches=True, title_matches=True, in_uploads_playlist=True, upload_status="rejected", rejection_reason="duplicate")])
o, sleeps, _ = run(client, video_id="V1", title="T")
check("YouTube says rejected -> stops immediately", (o.verified, o.definitive, len(client.check_calls)), (False, True, 1))
check("...and the reason reaches the detail", "rejectionReason=duplicate" in o.detail, True)

client = FakeVerifyClient([VideoVerification(exists=True, channel_matches=False, title_matches=True, in_uploads_playlist=False, upload_status="uploaded", channel_id="UC_OTHER")])
o, _, _ = run(client, video_id="V1", title="T")
check("video belongs to a DIFFERENT channel -> definitive failure, no retries", (o.verified, o.definitive, len(client.check_calls)), (False, True, 1))

client = FakeVerifyClient([VideoVerification(exists=True, channel_matches=True, title_matches=False, in_uploads_playlist=True, upload_status="uploaded", found_title="Something else")])
o, _, _ = run(client, video_id="V1", title="T")
check("title mismatch -> definitive failure, and YouTube's actual title is in the detail", (o.verified, o.definitive, "Something else" in o.detail), (False, True, True))

o, _, _ = run(FakeVerifyClient([RuntimeError("SSL: WRONG_VERSION_NUMBER"), ok_check()]), video_id="V1", title="T")
check("an API/network error is a failed ATTEMPT to retry, not a verdict", (o.verified, o.attempts_in_run), (True, 2))
client = FakeVerifyClient([RuntimeError("boom")])
o, _, _ = run(client, video_id="V1", title="T", max_attempts=3)
check("persistent API errors end as failed, honestly quoting the error", (o.verified, "RuntimeError: boom" in o.detail, len(client.check_calls)), (False, True, 3))

found_video = Video(video_id="FOUND1", title="T", description="", thumbnail_url="", duration_seconds=1)
client = FakeVerifyClient([ok_check()], title_results=[None, found_video])
o, _, atts = run(client, video_id="", title="T", exclude_ids={"OTHER"})
check("no video id yet: looked up by title, retried until found", (o.verified, o.attempts_in_run), (True, 2))
check("...the EXISTING video's id is attached", (o.video_id, client.check_calls[-1][0]), ("FOUND1", "FOUND1"))
check("...other entries' ids were excluded from the title search", client.find_calls[0][2], {"OTHER"})
check("...and the search looks well beyond the last few uploads", client.find_calls[0][1], uv.TITLE_LOOKUP_LOOKBACK)
check("...attempts before it was found reported with no id", atts[0][2], "")
when = dt.datetime(2026, 12, 16, 10, 0, tzinfo=dt.timezone.utc)
client = FakeVerifyClient([ok_check()], title_results=[found_video])
run(client, video_id="", title="T", expected_publish_at=when)
check("the entry's scheduled time is passed to the by-title search (to tell same-titled videos apart)", client.last_expected_publish_at, when)

client = FakeVerifyClient([missing()], title_results=[None])
o, _, _ = run(client, video_id="", title="T", max_attempts=3)
check("no id and never found by title -> not verified, no video attached", (o.verified, o.video_id), (False, ""))

o, _, atts = run(FakeVerifyClient([ok_check()]), video_id="V1", title="T", attempt_offset=5)
check("attempt numbering continues across runs (retry/resume)", ([a[0] for a in atts], o.last_attempt_number), ([6], 6))

o, sleeps, _ = run(FakeVerifyClient([ok_check()]), video_id="V1", title="T", initial_delay=0)
check("initial_delay=0 (retry/resume) doesn't wait before the first check", sleeps, [])

uv.VERIFY_MAX_ATTEMPTS = 2
client = FakeVerifyClient([missing()])
o = run_verification(client, video_id="V1", title="T", initial_delay=0, retry_delay=0)
check("module-level limits are honoured at call time", len(client.check_calls), 2)
uv.VERIFY_MAX_ATTEMPTS = 5

# ============================ YouTubeClient.check_video_registered ============================
class Chain:
    def __init__(self, service, name):
        self.service, self.name = service, name
    def list(self, **kw):
        self.service.calls.append((self.name, kw))
        return Exec(self.service.responses[self.name])
class Exec:
    def __init__(self, resp):
        self.resp = resp
    def execute(self):
        return self.resp
class FakeService:
    def __init__(self, responses):
        self.responses = responses
        self.calls = []
    def videos(self): return Chain(self, "videos")
    def playlistItems(self): return Chain(self, "playlistItems")
    def channels(self): return Chain(self, "channels")
class FakeQuota:
    def __init__(self): self.units = 0
    def record(self, n): self.units += n

def make_client(responses):
    c = YouTubeClient.__new__(YouTubeClient)
    c._thread_local = threading.local()
    c._service = FakeService(responses)
    c.quota = FakeQuota()
    c._channel_id = None
    c._uploads_playlist_id = None
    return c

CHANNELS = {"items": [{"id": "UC_ME", "contentDetails": {"relatedPlaylists": {"uploads": "UU_ME"}}}]}
def video_item(title="Bridge Push", channel="UC_ME", upload_status="uploaded", **status_extra):
    return {"items": [{"id": "VID1", "snippet": {"title": title, "channelId": channel},
                       "status": {"uploadStatus": upload_status, "privacyStatus": "private", "publishAt": "2026-09-30T06:00:00Z", **status_extra}}]}

c = make_client({"channels": CHANNELS, "videos": video_item(), "playlistItems": {"items": [{"id": "PLI"}]}})
v = c.check_video_registered("VID1", "Bridge Push")
check("healthy video verifies", v.verified, True)
check("...reports channel/title/playlist individually", (v.exists, v.channel_matches, v.title_matches, v.in_uploads_playlist), (True, True, True, True))
check("...and the scheduling/upload status where available", (v.upload_status, v.privacy_status, v.publish_at.isoformat()), ("uploaded", "private", "2026-09-30T06:00:00+00:00"))
pl_call = [kw for name, kw in c._service.calls if name == "playlistItems"][0]
check("the second, independent endpoint is queried filtered to THIS video in the uploads playlist", (pl_call["playlistId"], pl_call["videoId"]), ("UU_ME", "VID1"))
check("quota is recorded for every call made", c.quota.units > 0, True)

c.check_video_registered("VID1", "Bridge Push")
check("the channel/uploads-playlist lookup is cached (one channels.list total)", sum(1 for n, _ in c._service.calls if n == "channels"), 1)

c = make_client({"channels": CHANNELS, "videos": {"items": []}, "playlistItems": {"items": []}})
v = c.check_video_registered("VID1", "T")
check("YouTube doesn't have it -> exists False, not verified, and not a definitive failure", (v.exists, v.verified, v.definitive_failure), (False, False, False))
check("...no wasted second-endpoint call", [n for n, _ in c._service.calls if n == "playlistItems"], [])

c = make_client({"channels": CHANNELS, "videos": video_item(channel="UC_SOMEONE_ELSE"), "playlistItems": {"items": [{"id": "x"}]}})
v = c.check_video_registered("VID1", "Bridge Push")
check("another channel's video -> channel_matches False, definitive", (v.channel_matches, v.verified, v.definitive_failure), (False, False, True))
check("...and no playlist call is spent on someone else's video", [n for n, _ in c._service.calls if n == "playlistItems"], [])

c = make_client({"channels": CHANNELS, "videos": video_item(title="Different Title"), "playlistItems": {"items": [{"id": "x"}]}})
v = c.check_video_registered("VID1", "Bridge Push")
check("different title -> title_matches False", (v.title_matches, v.verified), (False, False))

c = make_client({"channels": CHANNELS, "videos": video_item(title="Bridge   Push "), "playlistItems": {"items": [{"id": "x"}]}})
check("whitespace-only title differences don't cause a false failure", c.check_video_registered("VID1", "Bridge Push").verified, True)

c = make_client({"channels": CHANNELS, "videos": video_item(), "playlistItems": {"items": []}})
v = c.check_video_registered("VID1", "Bridge Push")
check("not (yet) in the uploads playlist -> not verified, but retryable (not definitive)", (v.in_uploads_playlist, v.verified, v.definitive_failure), (False, False, False))

c = make_client({"channels": CHANNELS, "videos": video_item(upload_status="failed", failureReason="codec"), "playlistItems": {"items": [{"id": "x"}]}})
v = c.check_video_registered("VID1", "Bridge Push")
check("uploadStatus=failed -> never VERIFIED, definitive, reason captured", (v.verified, v.definitive_failure, v.failure_reason), (False, True, "codec"))

# find_recent_upload_by_title / list_recent_uploaded_video_ids -- the uploads playlist is NEWEST FIRST
def mk_video(vid, title, publish=None):
    return Video(video_id=vid, title=title, description="", thumbnail_url="", duration_seconds=1, publish_at=publish)

T1 = dt.datetime(2026, 12, 16, 10, 0, tzinfo=dt.timezone.utc)
T2 = dt.datetime(2026, 12, 17, 10, 0, tzinfo=dt.timezone.utc)
vids = {
    "NEWEST": mk_video("NEWEST", "Same Title", T2),   # e.g. q10
    "MIDDLE": mk_video("MIDDLE", "Something else", None),
    "OLDER": mk_video("OLDER", "Same  Title", T1),   # e.g. q8 -- same title, different slot (whitespace differs too)
}
c = make_client({})
requested = []
c.list_recent_uploaded_video_ids = lambda n: (requested.append(n) or ["NEWEST", "MIDDLE", "OLDER"][:n])
c.get_videos = lambda ids: [vids[i] for i in ids]
check("by-title lookup returns the NEWEST match first", c.find_recent_upload_by_title("Same Title").video_id, "NEWEST")
check("...only asking for the most recent `lookback` uploads (not the whole channel)", requested[-1], 5)
check("...never an already-accounted-for video (exclude_ids)", c.find_recent_upload_by_title("Same Title", exclude_ids={"NEWEST"}).video_id, "OLDER")
check("...None when every match is excluded", c.find_recent_upload_by_title("Same Title", exclude_ids={"NEWEST", "OLDER"}), None)
check("same-titled videos are told apart by scheduled time (the OLDER slot wins over the newer video)", c.find_recent_upload_by_title("Same Title", expected_publish_at=T1).video_id, "OLDER")
check("...and the newer slot picks the newer video", c.find_recent_upload_by_title("Same Title", expected_publish_at=T2).video_id, "NEWEST")
check("a rescheduled video (no time match) still falls back to the newest title match", c.find_recent_upload_by_title("Same Title", expected_publish_at=dt.datetime(2027, 1, 1, tzinfo=dt.timezone.utc)).video_id, "NEWEST")
check("...but with require_publish_match a different-time video is NOT adopted (duplicate protection stays precise)", c.find_recent_upload_by_title("Same Title", expected_publish_at=dt.datetime(2027, 1, 1, tzinfo=dt.timezone.utc), require_publish_match=True), None)
check("...and require_publish_match accepts the exact slot", c.find_recent_upload_by_title("Same Title", expected_publish_at=T1, require_publish_match=True).video_id, "OLDER")
check("title differences are still not matches", c.find_recent_upload_by_title("Different Title"), None)

# the real API call: newest-first page(s) only
pl_items = [{"contentDetails": {"videoId": f"V{i:03d}"}} for i in range(120)]  # V000 = newest
class PagingChain:
    def __init__(self, service): self.service = service
    def list(self, **kw):
        self.service.calls.append(("playlistItems", kw))
        start = int(kw.get("pageToken") or 0)
        n = kw["maxResults"]
        resp = {"items": pl_items[start:start + n]}
        if start + n < len(pl_items):
            resp["nextPageToken"] = str(start + n)
        return Exec(resp)
svc = FakeService({"channels": CHANNELS})
svc.playlistItems = lambda: PagingChain(svc)
c = make_client({"channels": CHANNELS})
c._service = svc
got = c.list_recent_uploaded_video_ids(25)
check("list_recent_uploaded_video_ids returns the FIRST (newest) items, in order", (got[0], got[-1], len(got)), ("V000", "V024", 25))
pl_calls = [kw for n, kw in svc.calls if n == "playlistItems"]
check("...with a single API call for 25 videos (not a walk over the whole channel)", len(pl_calls), 1)
check("...against the uploads playlist", pl_calls[0]["playlistId"], "UU_ME")
got = c.list_recent_uploaded_video_ids(120)
check("larger requests page (50 at a time) until satisfied", (len(got), got[-1]), (120, "V119"))

# ============================ per-thread API service ============================
import youtube.client as client_module
built = []
class _FakeCreds: pass
orig_build = client_module.build
client_module.build = lambda *a, **k: (built.append(threading.get_ident()) or object())
try:
    real = YouTubeClient.__new__(YouTubeClient)
    real._credentials = _FakeCreds()
    real._thread_local = threading.local()
    main_a = real._service
    main_b = real._service
    check("the same thread reuses its one service (no rebuilding per call)", (main_a is main_b, len(built)), (True, 1))
    box = {}
    t = threading.Thread(target=lambda: box.update(svc=real._service, svc2=real._service))
    t.start(); t.join()
    check("a different thread gets its OWN service (never shares a connection across threads)", box["svc"] is not main_a, True)
    check("...which it then also reuses", box["svc"] is box["svc2"], True)
    check("exactly one build per thread", len(built), 2)
finally:
    client_module.build = orig_build

if errors:
    print("\nFAILURES:", errors)
    sys.exit(1)
print("\nALL CHECKS PASSED")

"""SCHEDULE COMPLETENESS: the range engine Auto Boost still uses (Shorts only, queue end before/after), and the
complete-schedule discovery (client merge + repository) every scheduling feature relies on.
(The Daily Upload Frequency page, its engine and the queue order are tested in test_daily_frequency.py and test_frequency_page.py.)"""
import sys, os, json, time, tempfile, pathlib, threading, datetime as dt, collections
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import data.queue_order as _qo_isolation, tempfile as _tf_isolation, pathlib as _pl_isolation; _qo_isolation.QUEUE_ORDER_PATH = _pl_isolation.Path(_tf_isolation.mkdtemp()) / "queue_order.json"  # a test must NEVER write the real order ledger (data/queue_order.json)

TMP = pathlib.Path(tempfile.mkdtemp())
import data.config as m_config, data.auto_schedule_config as asc, data.upload_activity as ua
import data.error_log as m_el, data.crash_log as m_cl, data.shorts_folder as m_sf, data.session_state as m_ss, data.history as m_hist
import youtube.video_repo as m_vr
asc.AUTO_SCHEDULE_CONFIG_PATH = TMP / "asc.json"; m_config.CONFIG_PATH = TMP / "config.json"
ua.UPLOAD_ACTIVITY_PATH = TMP / "ua.json"; m_el.ERROR_LOG_PATH = TMP / "el.json"; m_cl.CRASH_LOG_PATH = TMP / "cl.json"
m_sf.REGISTRY_PATH = TMP / "sf.json"; m_ss.SESSION_PATH = TMP / "ss.json"; m_hist.HISTORY_PATH = TMP / "hist.json"
m_vr.CACHE_PATH = TMP / "video_cache.json"

from data.auto_schedule_config import AutoScheduleConfig
from scheduling.increase_slots import compute_increase_slots_schedule, validate_request
from scheduling.ordering import validate_order_preserved
from youtube.client import YouTubeClient
from youtube.models import Video, SHORT_MAX_SECONDS
from youtube.video_repo import VideoRepository

errors = []
def check(name, actual, expected=True):
    ok = actual == expected
    print(("PASS" if ok else "FAIL"), name, "->" if not ok else "", (actual, expected) if not ok else "")
    if not ok: errors.append(name)

today = dt.date.today(); tomorrow = today + dt.timedelta(days=1)
def day(n): return tomorrow + dt.timedelta(days=n)
def at(d, hh, mm=0): return dt.datetime.combine(d, dt.time(hh, mm)).astimezone()
def vid(i, d=None, hh=6, secs=30, **kw):
    return Video(video_id=kw.pop("video_id", f"V{i:03d}"), title=f"t{i}", description="", thumbnail_url="", duration_seconds=secs,
                 publish_at=at(d, hh) if d is not None else None, **kw)

def make_cfg():
    c = AutoScheduleConfig(normal_videos_per_day=2, max_videos_per_day=6)
    c.save_slot_preset(1, [dt.time(6, 0)]); c.save_slot_preset(2, [dt.time(6, 0), dt.time(10, 0)])
    c.save_slot_preset(3, [dt.time(6, 0), dt.time(8, 0), dt.time(10, 0)])
    c.save_slot_preset(4, [dt.time(6, 0), dt.time(8, 0), dt.time(10, 0), dt.time(12, 0)])
    return c
cfg = make_cfg()
now = dt.datetime.now(dt.timezone.utc)

# =========================================================== Video.is_short
print("\n--- what counts as a Short ---")
check("30s, exactly 180s and an unknown duration (0) are Shorts", [vid(1, secs=s).is_short for s in (30, SHORT_MAX_SECONDS, 0)], [True, True, True])
check("181s and a 460s montage are not", [vid(1, secs=s).is_short for s in (SHORT_MAX_SECONDS + 1, 460)], [False, False])

# =========================================================== gaps
print("\n--- gap analysis ---")
sched = []
for d in range(0, 3): sched += [vid(len(sched), day(d), 6), vid(len(sched) + 1, day(d), 10)]      # days 0-2 full (2/day)
sched += [vid(len(sched), day(3), 6)]                                                              # day 3: one video
# days 4-5 empty
sched += [vid(len(sched), day(6), 6), vid(len(sched) + 1, day(6), 10)]                             # day 6 full = the tail
solid = [vid(i, day(i // 2), 6 if i % 2 == 0 else 10) for i in range(8)]
past_only = [vid(0, today - dt.timedelta(days=3))]
montage = [vid(100, day(9), 12, secs=460)]
thin_plus_montage = [vid(1, day(0), 6), vid(2, day(0), 12, secs=460), vid(3, day(1), 6), vid(4, day(1), 10)]

# =========================================================== engine
print("\n--- the engine: gaps filled by moving later Shorts up ---")
r = compute_increase_slots_schedule(sched, day(3), day(6), 2, cfg, now)
new = {a.video_id: a.new_publish_at for a in r.result.assignments}
after = {v.video_id: new.get(v.video_id, v.publish_at).astimezone() for v in sched}
per_day = collections.Counter(t.date() for t in after.values())
check("from the first gap on, every day holds 2 and nothing is left empty", [per_day.get(day(n), 0) for n in range(0, 5)], [2, 2, 2, 2, 1])
check("the last Short moves UP: the old tail was day 6, the new one day 4", (r.queue_end_before, r.queue_end_after), (day(6), day(4)))
check("publish ORDER is unchanged", [k for k, _ in sorted(after.items(), key=lambda kv: kv[1])] == [v.video_id for v in sorted(sched, key=lambda v: v.publish_at)], True)
check("the order guard sees no violation", validate_order_preserved(sched, r.result), [])
check("only Shorts that actually change are sent to YouTube", all(a.new_publish_at != a.old_publish_at for a in r.result.assignments), True)
check("nothing before the start date is touched", all(a.old_publish_at.astimezone().date() >= day(3) for a in r.result.assignments), True)

r2 = compute_increase_slots_schedule(solid, day(0), day(3), 2, cfg, now)
check("an already-solid schedule: nothing changes, queue end stays", (r2.changed_count, r2.queue_end_before, r2.queue_end_after), (0, day(3), day(3)))
r3 = compute_increase_slots_schedule([], day(0), day(3), 2, cfg, now)
check("an empty queue has no end", (r3.queue_size, r3.queue_end_before, r3.queue_end_after), (0, None, None))
r4 = compute_increase_slots_schedule(solid, day(0), day(3), 4, cfg, now)
check("raising to 4/day pulls the queue's end well forward (day 3 -> day 1)", (r4.queue_end_before, r4.queue_end_after), (day(3), day(1)))

print("\n--- a long video is never moved, and its minute is skipped ---")
mixed = [vid(0, day(0), 6), vid(1, day(1), 6), vid(2, day(2), 6), vid(3, day(2), 10), vid(9, day(1), 10, secs=460, video_id="LONG")]
rm = compute_increase_slots_schedule(mixed, day(0), day(5), 2, cfg, now)
check("the long video gets no assignment", "LONG" in {a.video_id for a in rm.result.assignments}, False)
check("...and is not part of the queue (3 Shorts + 1 more = 4 Shorts)", rm.queue_size, 4)
new = {r_.video_id: r_.new_publish_at.astimezone() for r_ in rm.preview_rows}
check("no Short lands on the long video's own minute (day 1 at 10:00)", at(day(1), 10) in new.values(), False)
check("the Shorts are still laid out in their original order", [k for k, _ in sorted(new.items(), key=lambda kv: kv[1])], ["V000", "V001", "V002", "V003"])
check("the order guard ignores the long video", validate_order_preserved(mixed, rm.result), [])

print("\n--- validation ---")
check("a past start date is refused", validate_request(today - dt.timedelta(days=1), day(3), 2, today) is not None, True)
check("an end before the start is refused", validate_request(day(3), day(1), 2, today) is not None, True)
check("a normal request passes", validate_request(day(0), day(3), 2, today), None)

# =========================================================== client: merging the sources
print("\n--- the client: scheduled videos = listing + remembered IDs, each asked once ---")
class Quota:
    def __init__(self): self.units = 0
    def record(self, n): self.units += n
class Exec:
    def __init__(self, fn): self.fn = fn
    def execute(self): return self.fn()
class Svc:
    """playlistItems -> `listing`; search pages -> `search_pages`; videos.list answers from `catalogue`."""
    def __init__(self, listing, catalogue, search_pages=()):
        self.listing, self.catalogue, self.search_pages = listing, catalogue, list(search_pages)
        self.videos_calls, self.search_calls = [], []
    def playlistItems(self):
        outer = self
        class P:
            def list(self, **kw): return Exec(lambda: {"items": [{"contentDetails": {"videoId": i}} for i in outer.listing]})
        return P()
    def search(self):
        outer = self
        class S:
            def list(self, **kw):
                outer.search_calls.append(kw)
                n = len(outer.search_calls) - 1
                page = outer.search_pages[n] if n < len(outer.search_pages) else []
                resp = {"items": [{"id": {"videoId": i}} for i in page]}
                if n + 1 < len(outer.search_pages): resp["nextPageToken"] = f"T{n + 1}"
                return Exec(lambda: resp)
        return S()
    def videos(self):
        outer = self
        class V:
            def list(self, **kw):
                ids = kw["id"].split(",")
                outer.videos_calls.append(ids)
                return Exec(lambda: {"items": [outer.catalogue[i] for i in ids if i in outer.catalogue]})
        return V()
def api_item(vid_id, privacy="private", publish_at="2099-01-01T06:00:00Z", duration="PT30S"):
    status = {"privacyStatus": privacy, "uploadStatus": "uploaded"}
    if publish_at: status["publishAt"] = publish_at
    return {"id": vid_id, "snippet": {"title": vid_id}, "contentDetails": {"duration": duration}, "status": status}
def make_client(svc):
    c = YouTubeClient.__new__(YouTubeClient)
    c._thread_local = threading.local(); c._service = svc; c.quota = Quota(); c._channel_id = "UC"; c._uploads_playlist_id = "UU"
    return c

cat = {i: api_item(i) for i in ("A", "B", "C", "D")}
cat["PUB"] = api_item("PUB", privacy="public")                       # published: keeps its publishAt, must NOT count as scheduled
svc = Svc(listing=["A", "B"], catalogue=cat)
c = make_client(svc)
got = [v.video_id for v in c.get_scheduled_videos(extra_ids=["B", "C", "D", "PUB", "GONE"])]
check("the listing's videos AND the remembered IDs are all returned", sorted(got), ["A", "B", "C", "D"])
check("...a published video (publishAt still set) is not 'scheduled'", "PUB" in got, False)
check("...and an ID YouTube no longer knows simply drops out", "GONE" in got, False)
flat = [i for call in svc.videos_calls for i in call]
check("each ID is asked for once even when two sources named it (B)", flat.count("B"), 1)
check("with no extra IDs it behaves exactly as before", [v.video_id for v in make_client(Svc(["A", "B"], cat)).get_scheduled_videos()], ["A", "B"])
many = {f"X{i}": api_item(f"X{i}") for i in range(120)}
svc = Svc(listing=[], catalogue=many)
make_client(svc).get_scheduled_videos(extra_ids=list(many))
check("videos.list is batched 50 at a time (120 IDs -> 3 calls)", [len(x) for x in svc.videos_calls], [50, 50, 20])

print("\n--- the client: search-based discovery ---")
svc = Svc([], {}, search_pages=[["S1", "S2"], ["S3"], ["S4"]])
c = make_client(svc)
check("discovers across pages, newest first", c.discover_recent_video_ids(2), ["S1", "S2", "S3"])
check("...asks search.list for MY videos, newest first", (svc.search_calls[0]["forMine"], svc.search_calls[0]["order"], svc.search_calls[0]["type"]), (True, "date", "video"))
check("...and counts 100 quota units a page (2 pages = 200)", c.quota.units, 200)
svc = Svc([], {}, search_pages=[["S1"]])
c = make_client(svc)
check("stops early when YouTube has no further page", (c.discover_recent_video_ids(8), len(svc.search_calls)), (["S1"], 1))
svc = Svc([], {}, search_pages=[])
check("an empty channel yields nothing without error", make_client(svc).discover_recent_video_ids(3), [])

# =========================================================== repository
print("\n--- the repository: remembered videos, rationed discovery ---")
class FakeClient:
    def __init__(self, scheduled=(), discovered=()):
        self.scheduled = {v.video_id: v for v in scheduled}   # what YouTube says is scheduled right now
        self.discovered = list(discovered)
        self.discover_calls, self.get_calls = [], []
        self.fail_discovery = False
    def discover_recent_video_ids(self, pages):
        self.discover_calls.append(pages)
        if self.fail_discovery: raise RuntimeError("quotaExceeded")
        return list(self.discovered)
    def get_scheduled_videos(self, use_uploads_playlist=True, extra_ids=()):
        ids = list(dict.fromkeys(extra_ids))
        self.get_calls.append(ids)
        return [self.scheduled[i] for i in ids if i in self.scheduled]
def fresh_repo(client):
    try: m_vr.CACHE_PATH.unlink()
    except FileNotFoundError: pass
    return VideoRepository(client)

a, b, c_, d_ = (vid(i, day(i), 6, video_id=n) for i, n in enumerate("ABCD"))
flag_client = FakeClient(scheduled=[a, b], discovered=["A", "B"])
flag_repo = fresh_repo(flag_client)
check("a new repository has not been refreshed in this run yet", flag_repo.refreshed_this_session, False)
flag_repo.refresh()
check("...and has once a refresh succeeded", flag_repo.refreshed_this_session, True)
repo_cached = VideoRepository(FakeClient())
check("a repository restarted from a saved cache is still 'not refreshed this run' (the cache may be stale)", (len(repo_cached.videos), repo_cached.refreshed_this_session), (2, False))
class DownClient(FakeClient):
    def get_scheduled_videos(self, **kw): raise RuntimeError("offline")
down_repo = VideoRepository(DownClient())
try: down_repo.refresh()
except RuntimeError: pass
check("a refresh that fails does not count", down_repo.refreshed_this_session, False)

client = FakeClient(scheduled=[a, b], discovered=["A", "B", "C"])
repo = fresh_repo(client)
repo.refresh()
check("first ever refresh spends a DEEP discovery (8 pages)", client.discover_calls, [m_vr.DEEP_DISCOVERY_PAGES])
check("...and records it", (repo.last_discovery_at is not None, repo.last_deep_discovery_at is not None), (True, True))
check("only what is really scheduled is kept (C was discovered but isn't scheduled)", [v.video_id for v in repo.videos], ["A", "B"])

time.sleep(m_vr.RECENT_REFRESH_SECONDS + 0.1)
client.scheduled["C"] = c_      # C gets scheduled elsewhere (e.g. YouTube Studio) and is in the next discovery
repo.refresh()
check("a refresh right after needs no discovery (shallow is rationed to 12h)", len(client.discover_calls), 1)
check("...but still asks about every video it has already seen (A, B) by ID", set(client.get_calls[-1]), {"A", "B"})
check("...so C (scheduled elsewhere, not yet discovered) waits for the next discovery rather than costing a search", "C" in [v.video_id for v in repo.videos], False)

client.scheduled.pop("B")       # B published (or deleted): it just stops being returned
time.sleep(m_vr.RECENT_REFRESH_SECONDS + 0.1)
repo.refresh()
check("a video that is no longer scheduled drops out of the cache", "B" in [v.video_id for v in repo.videos], False)

print("\n--- remembered IDs keep videos the listing alone would lose ---")
client = FakeClient(scheduled=[a, b, c_, d_], discovered=["A", "B", "C", "D"])
repo = fresh_repo(client); repo.refresh()
check("(setup) all four known", [v.video_id for v in repo.videos], ["A", "B", "C", "D"])
# now discovery finds nothing and the playlist listing would show none -- only remembered IDs remain
client.discovered = []
time.sleep(m_vr.RECENT_REFRESH_SECONDS + 0.1)
repo.refresh()
check("remembered scheduled videos are still found with no discovery help", [v.video_id for v in repo.videos], ["A", "B", "C", "D"])

print("\n--- the upload record's IDs are included ---")
client = FakeClient(scheduled=[a, b, c_, d_], discovered=[])
repo = fresh_repo(client); repo.known_id_provider = lambda: ["C", "D"]
repo.refresh()
check("videos named only by the upload record are looked up (C, D)", [v.video_id for v in repo.videos], ["C", "D"])
def boom(): raise RuntimeError("record unreadable")
time.sleep(m_vr.RECENT_REFRESH_SECONDS + 0.1)
repo.known_id_provider = boom
repo.refresh()
check("a failing provider can't break a refresh (the cache still works)", [v.video_id for v in repo.videos], ["C", "D"])

print("\n--- discovery schedule ---")
client = FakeClient(scheduled=[a], discovered=["A"])
repo = fresh_repo(client); repo.refresh()
repo.last_discovery_at = dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=13)
time.sleep(m_vr.RECENT_REFRESH_SECONDS + 0.1); repo.refresh()
check("13h after the last discovery a SHALLOW one (3 pages) runs", client.discover_calls[-1], m_vr.SHALLOW_DISCOVERY_PAGES)
repo.last_deep_discovery_at = dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=7)
time.sleep(m_vr.RECENT_REFRESH_SECONDS + 0.1); n = len(client.discover_calls); repo.refresh(deep=True)
check("deep=True, 7h after the last deep one: a DEEP discovery runs", client.discover_calls[-1], m_vr.DEEP_DISCOVERY_PAGES)
repo.last_deep_discovery_at = dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=1)
n = len(client.discover_calls); repo.refresh(deep=True)
check("deep=True again within 6h: no discovery (pressing the button repeatedly is safe)", len(client.discover_calls), n)
repo.last_deep_discovery_at = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=8)
repo.last_discovery_at = dt.datetime.now(dt.timezone.utc) - dt.timedelta(minutes=5)
time.sleep(m_vr.RECENT_REFRESH_SECONDS + 0.1); repo.refresh()
check("a deep discovery happens by itself after 7 days", client.discover_calls[-1], m_vr.DEEP_DISCOVERY_PAGES)

print("\n--- a failing discovery never stops a refresh ---")
client = FakeClient(scheduled=[a, b], discovered=["A", "B"])
repo = fresh_repo(client); repo.refresh()
client.fail_discovery = True
repo.last_discovery_at = dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=20)
time.sleep(m_vr.RECENT_REFRESH_SECONDS + 0.1)
before_cache = [v.video_id for v in repo.videos]
repo.refresh()
check("refresh still succeeds and keeps the schedule (quota exhausted)", [v.video_id for v in repo.videos], before_cache)
check("...the failed discovery did not count as one", repo.last_discovery_at < dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=19), True)
n = len(client.discover_calls)
time.sleep(m_vr.RECENT_REFRESH_SECONDS + 0.1); repo.refresh()
check("...and it isn't retried straight away (30 min back-off)", len(client.discover_calls), n)

print("\n--- timestamps survive a restart ---")
client = FakeClient(scheduled=[a], discovered=["A"])
repo = fresh_repo(client); repo.refresh()
stamps = (repo.last_discovery_at, repo.last_deep_discovery_at)
repo2 = VideoRepository(FakeClient(scheduled=[a], discovered=["A"]))
check("discovery times are loaded back from the cache", (repo2.last_discovery_at, repo2.last_deep_discovery_at), stamps)
time.sleep(m_vr.RECENT_REFRESH_SECONDS + 0.1); repo2.refresh()
check("...so the restarted app does NOT redo the expensive deep search", repo2._client.discover_calls, [])
check("the cached videos keep their duration (Short vs long survives a restart)", all(v.duration_seconds == 30 for v in repo2.videos), True)

print("\n--- concurrent refreshes run one at a time ---")
class SlowClient(FakeClient):
    def __init__(self, *a, **k):
        super().__init__(*a, **k); self.active = 0; self.max_active = 0; self.lock = threading.Lock()
    def get_scheduled_videos(self, **kw):
        with self.lock: self.active += 1; self.max_active = max(self.max_active, self.active)
        time.sleep(0.15)
        try: return super().get_scheduled_videos(**kw)
        finally:
            with self.lock: self.active -= 1
client = SlowClient(scheduled=[a], discovered=["A"])
repo = fresh_repo(client)
ts = [threading.Thread(target=repo.refresh) for _ in range(4)]
[t.start() for t in ts]; [t.join() for t in ts]
check("never more than one refresh talks to YouTube at once", client.max_active, 1)
check("...and the threads that queued behind it reuse its result instead of repeating it", len(client.get_calls) < 4, True)

if errors:
    print("\nFAILURES:", errors); os._exit(1)
print("\nALL CHECKS PASSED"); os._exit(0)

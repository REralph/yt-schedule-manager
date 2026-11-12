"""DAILY UPLOAD FREQUENCY: the permanent sequence numbers, the engine that walks them, the order recovery, and the
safe apply path (checked against a fake YouTube). The queue used throughout is Q100..Q105 and its order must NEVER change."""
import sys, os, json, time, tempfile, pathlib, threading, dataclasses, datetime as dt, collections
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

TMP = pathlib.Path(tempfile.mkdtemp())
import data.upload_activity as ua, data.history as m_hist, data.error_log as m_el, data.crash_log as m_cl, data.queue_order as qo
ua.UPLOAD_ACTIVITY_PATH = TMP / "ua.json"; m_el.ERROR_LOG_PATH = TMP / "el.json"; m_cl.CRASH_LOG_PATH = TMP / "cl.json"
m_hist.HISTORY_PATH = TMP / "hist.json"; qo.QUEUE_ORDER_PATH = TMP / "queue_order.json"

from data.auto_schedule_config import AutoScheduleConfig
from data.queue_order import QueueOrderStore, QueueOrderError, SEQ_STEP
from data.history import HistoryChange, HistoryEntry, HistoryStore
from scheduling import daily_frequency as df
from scheduling.order_recovery import (recover_order, RecoveryBlocked, RecoveryIncomplete, registry_items,
                                       STATUS_CERTAIN, STATUS_RESOLVED, STATUS_DECIDED, OPT_UPLOAD, OPT_SCHEDULE, KIND_TIE)
from youtube.models import Video

errors = []
def check(name, actual, expected=True):
    ok = actual == expected
    print(("PASS" if ok else "FAIL"), name, "->" if not ok else "", (actual, expected) if not ok else "")
    if not ok: errors.append(name)

NOW = dt.datetime(2030, 1, 2, 0, 0).astimezone()
def day(n): return dt.date(2030, 1, 2) + dt.timedelta(days=n)       # day(0) = the first valid day (every slot of it is still ahead of NOW)
def at(d, hh, mm=0): return dt.datetime.combine(d, dt.time(hh, mm)).astimezone()
def hhmm(t): return t.astimezone().strftime("%H:%M")
def dstr(t): return t.astimezone().strftime("%d %H:%M")

def make_cfg():
    return AutoScheduleConfig(normal_videos_per_day=2, slot_presets={
        "1": ["10:00"], "2": ["06:00", "10:00"], "3": ["06:00", "09:00", "12:00"], "4": ["06:00", "08:00", "10:00", "12:00"],
        "5": ["06:00", "07:30", "09:00", "10:30", "12:00"], "6": ["06:00", "07:30", "09:00", "10:30", "12:00", "13:30"]})
cfg = make_cfg()

def q(n, when=None, secs=30, created=None, privacy="private"):
    return Video(video_id=f"Q{n}", title=f"Q{n}", description="", thumbnail_url="", duration_seconds=secs, privacy_status=privacy,
                 publish_at=when, published_at=created)

def registry_for(ids, path):
    reg = QueueOrderStore(path)
    reg.adopt([(i, i, "test", "recovered") for i in ids])
    return reg

def layout(per_day, ids=("Q100", "Q101", "Q102", "Q103", "Q104", "Q105"), start=0, hours=(6, 9, 12, 13)):
    """ids laid out `per_day` at a time from day(start), in the given order."""
    out = []
    for k, vid in enumerate(ids):
        out.append(q(int(vid[1:]), at(day(start + k // per_day), hours[k % per_day])))
    return out

def plan_for(videos, freq, reg, now=NOW):
    return df.compute_frequency_plan(df.build_queue(videos, reg, now), freq, cfg, now)

def rows_by_day(plan):
    d = collections.OrderedDict()
    for r in plan.rows: d.setdefault(r.new_publish_at.astimezone().date(), []).append(r.video_id)
    return [(k, v) for k, v in d.items()]

IDS = ["Q100", "Q101", "Q102", "Q103", "Q104", "Q105"]

# =================================================================== the permanent registry
print("\n--- the registry: numbers are permanent ---")
reg = QueueOrderStore(TMP / "r1.json")
check("a new registry is empty and not adopted", (len(reg), reg.is_adopted), (0, False))
check("append does nothing before an order is adopted (recovery will find the video by its upload time)", reg.append("Q999"), None)
n = reg.adopt([(i, i, "e", "recovered") for i in IDS])
check("adopting records every video, spaced SEQ_STEP apart", (n, [reg.seq_of(i) for i in IDS]), (6, [SEQ_STEP * k for k in range(1, 7)]))
check("ordered_ids is the sequence", reg.ordered_ids(), IDS)
try: reg.adopt([("X", "", "", "")]); replaced = True
except QueueOrderError: replaced = False
check("a recorded order can never be replaced", (replaced, reg.ordered_ids()), (False, IDS))
before = {i: reg.seq_of(i) for i in IDS}
rec = reg.append("Q106", "Q106", "uploaded")
check("a new upload goes to the END with the next number", (rec.seq, reg.ordered_ids()[-1]), (7 * SEQ_STEP, "Q106"))
again = reg.append("Q101")
check("append for a video that already has a number returns it UNCHANGED", (again.seq, reg.seq_of("Q101")), (before["Q101"], before["Q101"]))
check("no existing number was ever altered", all(reg.seq_of(i) == s for i, s in before.items()), True)
reg.insert_between("QX", "Q101", "QX", "placed", "inserted")
check("insert_between places a video after a named one without touching any number", (reg.ordered_ids()[:4], all(reg.seq_of(i) == s for i, s in before.items())), (["Q100", "Q101", "QX", "Q102"], True))
check("...and the same video inserted twice keeps its first number", (reg.insert_between("QX", "Q105").seq, reg.seq_of("QX")), (reg.seq_of("QX"), reg.seq_of("QX")))
try: reg.insert_between("QY", "NOPE"); ok_ = True
except QueueOrderError: ok_ = False
check("inserting after an unknown video is refused", ok_, False)
reg2 = QueueOrderStore(TMP / "r1.json")
check("everything survives a restart (same numbers, same evidence)", ([reg2.seq_of(i) for i in IDS], reg2.get("Q100").evidence, reg2.is_adopted), ([before[i] for i in IDS], "e", True))
check("a sound registry reports no integrity problems", reg2.integrity_problems(), [])
raw = json.loads((TMP / "r1.json").read_text()); raw["records"].append(dict(raw["records"][0], video_id="DUP", seq=raw["records"][0]["seq"]))
(TMP / "r2.json").write_text(json.dumps(raw))
check("two videos sharing a sequence number is reported as ambiguous", any("shared by 2" in p for p in QueueOrderStore(TMP / "r2.json").integrity_problems()), True)
raw = json.loads((TMP / "r1.json").read_text()); raw["records"].append(dict(raw["records"][0]))
(TMP / "r3.json").write_text(json.dumps(raw))
check("a video recorded twice is reported (first record kept)", any("more than once" in p for p in QueueOrderStore(TMP / "r3.json").integrity_problems()), True)
(TMP / "r4.json").write_text("{not json")
check("a damaged file is not trusted (empty, not adopted) rather than crashing", (len(QueueOrderStore(TMP / "r4.json")), QueueOrderStore(TMP / "r4.json").is_adopted), (0, False))

# =================================================================== the engine on Q100..Q105
print("\n--- 3 per day -> 2 per day ---")
reg = registry_for(IDS, TMP / "e1.json")
vids = layout(3)
plan = plan_for(vids, 3, reg)
check("(setup) the current schedule is 3/day and the plan at 3/day changes nothing", (plan.current_frequency, plan.frequency, len(plan.changed_rows)), (3, 3, 0))
plan = plan_for(vids, 2, reg)
check("order is exactly Q100..Q105", plan.order_after, IDS)
check("the first two go in the first day's slots, the third moves to the next day", rows_by_day(plan),
      [(day(0), ["Q100", "Q101"]), (day(1), ["Q102", "Q103"]), (day(2), ["Q104", "Q105"])])
check("slots come from the 2-per-day preset", [hhmm(r.new_publish_at) for r in plan.rows], ["06:00", "10:00"] * 3)
check("the timeline is extended (it now ends a day later than before)", (plan.timeline_end_before, plan.timeline_end_after), (day(1), day(2)))
check("the plan is valid", plan.errors, [])
check("the number of changed videos is reported (every time moves)", len(plan.changed_rows), sum(1 for v, r in zip(vids, plan.rows) if v.publish_at.astimezone() != r.new_publish_at.astimezone()))

print("\n--- 2 per day -> 4 per day ---")
vids2 = layout(2)
plan = plan_for(vids2, 4, reg)
check("four slots a day are filled and later videos are pulled forward", rows_by_day(plan),
      [(day(0), ["Q100", "Q101", "Q102", "Q103"]), (day(1), ["Q104", "Q105"])])
check("order is still Q100..Q105", plan.order_after, IDS)
check("the timeline is shorter", (plan.timeline_end_before, plan.timeline_end_after, plan.timeline_end_after < plan.timeline_end_before), (day(2), day(1), True))
check("the 4-per-day preset times are used", [hhmm(r.new_publish_at) for r in plan.rows], ["06:00", "08:00", "10:00", "12:00", "06:00", "08:00"])
check("valid", plan.errors, [])

print("\n--- every frequency from 1 to 6 ---")
for n_ in range(1, 7):
    p = plan_for(vids2, n_, reg)
    perday = [len(v) for _, v in rows_by_day(p)]
    check(f"{n_}/day: order kept, no day over {n_}, all six scheduled, valid", (p.order_after, max(perday) <= n_, sum(perday), p.errors), (IDS, True, 6, []))

print("\n--- gaps and an irregular, OUT-OF-ORDER schedule ---")
irregular = [q(100, at(day(1), 10)), q(101, at(day(5), 6)), q(102, at(day(2), 6)), q(103, at(day(2), 13)), q(104, at(day(9), 9)), q(105, at(day(0), 8))]
plan = plan_for(irregular, 2, reg)
check("the schedule's own dates are out of order (Q105 first, Q102 before Q101) -- the plan still follows the SEQUENCE", plan.order_after, IDS)
check("the dates it replaces are shown in their date order too (so the disorder is visible)", plan.order_before, ["Q105", "Q100", "Q102", "Q103", "Q101", "Q104"])
check("...and 4 videos are reported as being out of place now", plan.out_of_order_now > 0, True)
check("the new dates are strictly increasing in queue order", all(a.new_publish_at < b.new_publish_at for a, b in zip(plan.rows, plan.rows[1:])), True)
check("no gaps: two a day from the first valid day", [len(v) for _, v in rows_by_day(plan)], [2, 2, 2])
check("valid", plan.errors, [])
check("the dates are never used to decide the order: reversing every date gives the SAME queue order",
      plan_for([q(int(v.video_id[1:]), at(day(20 - k), 6 + k)) for k, v in enumerate(irregular)], 2, reg).order_after, IDS)

print("\n--- running it twice ---")
def apply_plan(videos, plan):
    new = {r.video_id: r.new_publish_at for r in plan.rows}
    return [q(int(v.video_id[1:]), new.get(v.video_id, v.publish_at)) for v in videos]
after1 = apply_plan(irregular, plan)
plan2 = plan_for(after1, 2, reg)
check("the second run finds nothing left to change", (len(plan2.changed_rows), plan2.order_after), (0, IDS))
after2 = apply_plan(after1, plan2)
check("...and the schedule is identical after the second run", [(v.video_id, v.publish_at) for v in after2], [(v.video_id, v.publish_at) for v in after1])
back = plan_for(after1, 3, reg); again = plan_for(apply_plan(after1, back), 2, reg)
check("2 -> 3 -> 2 returns to exactly the same schedule", [(r.video_id, r.new_publish_at) for r in again.rows], [(r.video_id, r.new_publish_at) for r in plan.rows])

print("\n--- who is (and is not) part of the queue ---")
published = q(90, at(day(-3), 6), privacy="public")
past = q(91, NOW - dt.timedelta(hours=3))
long_v = q(92, at(day(0), 10), secs=460)
soon = q(93, NOW + dt.timedelta(minutes=10))
unscheduled = q(94, None)
mixed = layout(2) + [published, past, long_v, soon, unscheduled]
snap = df.build_queue(mixed, reg, NOW)
check("only the six future Shorts are in the queue", [v.video_id for v in snap.queue], IDS)
check("a Short about to publish is locked, a long video is kept apart", ([v.video_id for v in snap.locked], [v.video_id for v in snap.long_videos]), (["Q93"], ["Q92"]))
p = plan_for(mixed, 2, reg)
check("published / past / unscheduled videos are never in the plan", {"Q90", "Q91", "Q94"} & set(p.order_after), set())
check("the long video's slot (day 0 10:00) and the imminent Short's slot are skipped, not reused", all(r.new_publish_at.astimezone() != long_v.publish_at.astimezone() for r in p.rows), True)
check("the long video and the locked Short get no change", {"Q92", "Q93"} & {r.video_id for r in p.changed_rows}, set())
check("valid, with notes about what was left alone", (p.errors, len(p.issues)), ([], 2))

print("\n--- the safety checks refuse a bad plan ---")
good = plan_for(layout(2), 3, reg)
def tampered(fn):
    p_ = plan_for(layout(2), 3, reg)
    fn(p_)
    return df.validate_plan(p_, cfg, NOW)
codes = lambda issues: {i.code for i in issues if i.is_error}
check("a lost video is caught", df.CODE_COVERAGE in codes(tampered(lambda p_: p_.rows.pop())), True)
check("a duplicated video is caught", df.CODE_COVERAGE in codes(tampered(lambda p_: p_.rows.append(p_.rows[0]))), True)
def swap(p_):
    p_.rows[1], p_.rows[2] = p_.rows[2], p_.rows[1]
check("two videos swapped in the plan is caught as an order violation", df.CODE_ORDER in codes(tampered(swap)), True)
def swap_times(p_):
    a, b = p_.rows[0], p_.rows[1]
    p_.rows[0] = dataclasses.replace(a, new_publish_at=b.new_publish_at); p_.rows[1] = dataclasses.replace(b, new_publish_at=a.new_publish_at)
check("times swapped between two videos is caught", df.CODE_ORDER in codes(tampered(swap_times)), True)
def same_minute(p_): p_.rows[1] = dataclasses.replace(p_.rows[1], new_publish_at=p_.rows[0].new_publish_at + dt.timedelta(seconds=30))
check("two Shorts in the same minute (or <15 min apart) is a conflict", bool(codes(tampered(same_minute)) & {df.CODE_CONFLICT, df.CODE_SLOT, df.CODE_ORDER}), True)
def early(p_): p_.rows[0] = dataclasses.replace(p_.rows[0], new_publish_at=at(day(0), 5, 0))
check("a time before 06:00 is outside the publishing window", df.CODE_SLOT in codes(tampered(early)), True)
def late(p_): p_.rows[5] = dataclasses.replace(p_.rows[5], new_publish_at=p_.rows[5].new_publish_at.replace(hour=15))
check("a time after 14:00 is outside the window", df.CODE_SLOT in codes(tampered(late)), True)
def past_(p_): p_.rows[0] = dataclasses.replace(p_.rows[0], new_publish_at=NOW - dt.timedelta(days=1))
check("a time in the past is refused", bool(codes(tampered(past_))), True)
def foreign(p_): p_.rows.append(dataclasses.replace(p_.rows[0], video_id="Q999", seq=10 ** 9, title="Q999"))
check("a video that is not an eligible queued Short is refused", df.CODE_INELIGIBLE in codes(tampered(foreign)), True)
bad_cfg = AutoScheduleConfig(slot_presets={"2": ["06:00", "06:05"]})
check("a slot preset that breaks the window/spacing rules blocks applying", df.CODE_SLOT_PRESET in codes(df.validate_plan(df.compute_frequency_plan(df.build_queue(layout(2), reg, NOW), 2, bad_cfg, NOW), bad_cfg, NOW)), True)
off = AutoScheduleConfig(slot_presets={"2": ["05:00", "10:00"]})
check("...including a slot before 06:00", df.CODE_SLOT_PRESET in codes(df.compute_frequency_plan(df.build_queue(layout(2), reg, NOW), 2, off, NOW).issues), True)
partial = registry_for(IDS[:4], TMP / "e2.json")
p_ = df.compute_frequency_plan(df.build_queue(layout(2), partial, NOW), 2, cfg, NOW)
check("Shorts with no sequence number block everything and are named", (df.CODE_UNREGISTERED in codes(p_.issues), [v.video_id for v in p_.snapshot.unregistered], p_.ok), (True, ["Q104", "Q105"], False))
check("...and they are NOT slotted in anywhere (the plan holds only the numbered ones)", p_.order_after, IDS[:4])
dup = QueueOrderStore(TMP / "r2.json")
p_ = df.compute_frequency_plan(df.build_queue(layout(2), dup, NOW), 2, cfg, NOW)
check("a registry with two videos on one number blocks everything", df.CODE_REGISTRY in codes(p_.issues), True)

print("\n--- the same result with the numbers deliberately out of step with titles, ids and dates ---")
odd = ["Z9", "A1", "M5", "B2"]
reg_odd = QueueOrderStore(TMP / "e3.json"); reg_odd.adopt([(i, i, "", "recovered") for i in odd])
vs = [Video(video_id=i, title=t, description="", thumbnail_url="", duration_seconds=20, privacy_status="private", publish_at=at(day(k), 6))
      for k, (i, t) in enumerate(zip(["A1", "B2", "M5", "Z9"], ["zebra", "apple", "mango", "kiwi"]))]
p_ = plan_for(vs, 2, reg_odd)
check("the order follows the sequence numbers -- not ids (A1<B2<M5<Z9), not titles, not dates", p_.order_after, odd)

# =================================================================== the order recovery
print("\n--- order recovery: evidence, not guesses ---")
def rv(i, created, sched, secs=30, title=None):
    return Video(video_id=i, title=title or i, description="", thumbnail_url="", duration_seconds=secs, privacy_status="private",
                 publish_at=sched, published_at=created)
def hist(ts, desc, pairs):
    return HistoryEntry(entry_id=f"e{ts.timestamp():.0f}", timestamp=ts, description=desc, changes=[HistoryChange(v, v, o, n) for v, o, n in pairs])
T = lambda d, hh=12: at(dt.date(2029, 6, 1) + dt.timedelta(days=d), hh)   # upload times in the past
S = lambda d, hh=6: at(dt.date(2030, 3, 1) + dt.timedelta(days=d), hh)    # scheduled times in the future

clean = [rv("c1", T(0), S(0)), rv("c2", T(1), S(1)), rv("c3", T(2), S(2))]
rep = recover_order(clean)
check("when every record agrees nothing needs deciding and every position is certain", (rep.is_clean, [i.status for i in rep.build()], [i.video_id for i in rep.build()]), (True, [STATUS_CERTAIN] * 3, ["c1", "c2", "c3"]))
check("(positions are 1-based and carry the upload time and the current date)", (rep.build()[0].position, rep.build()[1].created_at == T(1), rep.build()[2].current_at == S(2)), (1, True, True))

# a blind operation moved only SOME videos into the middle of the others
op_time = at(dt.date(2029, 9, 1), 12)
mid = [rv("a", T(0), S(0)), rv("b", T(1), S(5)), rv("c", T(2), S(6)), rv("d", T(3), S(2)), rv("e", T(4), S(3))]
h = hist(op_time, "Increase Daily Slots: blind", [("d", S(7), S(2)), ("e", S(8), S(3))])
rep = recover_order(mid, [h])
check("a disagreement explained by an EARLIER recorded arrangement is resolved to upload order, with the reason stored",
      (rep.is_clean, [i.video_id for i in rep.build()], rep.build()[3].status, "had them in upload order" in rep.build()[3].evidence_text),
      (True, ["a", "b", "c", "d", "e"], STATUS_RESOLVED, True))
rep_no_history = recover_order(mid)
check("...but WITHOUT that history the same disagreement is not explained and needs a decision (no guessing)",
      (rep_no_history.is_clean, len(rep_no_history.decisions_needed())), (False, 1))
try: rep_no_history.build(); raised = False
except RecoveryIncomplete: raised = True
check("...and building without a decision is refused", raised, True)
g = rep_no_history.decisions_needed()[0]
check("the group names its members and offers both orders, recommending the upload order", (sorted(g.members), set(g.options), g.recommended), (["b", "c", "d", "e"], {OPT_UPLOAD, OPT_SCHEDULE}, OPT_UPLOAD))
check("choosing upload order gives the upload sequence", [i.video_id for i in rep_no_history.build({g.block_id: OPT_UPLOAD})], ["a", "b", "c", "d", "e"])
sched_items = rep_no_history.build({g.block_id: OPT_SCHEDULE})
check("choosing schedule order gives that order, re-ordering ONLY the group's own places", [i.video_id for i in sched_items], ["a", "d", "e", "b", "c"])
check("decided videos are marked as decided, with the choice in their evidence", {i.video_id: i.status for i in sched_items}["d"], STATUS_DECIDED)
check("an unknown choice is refused", rep_no_history.missing_choices({g.block_id: "bogus"}), [g.block_id])

# the placement bug: first upload at 10:00, second at 06:00 the same day
swap_day = [rv("s1", T(0), S(0, 10)), rv("s2", T(1), S(0, 6)), rv("s3", T(2), S(1, 10)), rv("s4", T(3), S(1, 6))]
rep = recover_order(swap_day)
check("same-day 10:00-before-06:00 inversions are explained by the known placement bug and keep upload order",
      (rep.is_clean, [i.video_id for i in rep.build()], rep.build()[0].status, "same-day swap" in rep.build()[0].evidence_text), (True, ["s1", "s2", "s3", "s4"], STATUS_RESOLVED, True))
cross_day = [rv("x1", T(0), S(3)), rv("x2", T(1), S(0))]
check("the same inversion across DIFFERENT days is not explained", len(recover_order(cross_day).decisions_needed()), 1)

# a video uploaded after an operation that had already moved an earlier video
op2 = at(dt.date(2029, 9, 1), 12)
after_op = [rv("o1", T(0), S(4)), rv("o2", T(1), S(5)), rv("w", at(dt.date(2029, 10, 1), 12), S(1))]
h2 = hist(op2, "faulty", [("o1", S(0), S(4)), ("o2", S(1), S(5))])
rep = recover_order(after_op, [h2])
check("a video added AFTER an operation moved earlier videos is kept at the end (its slot was assigned in a displaced schedule)",
      (rep.is_clean, [i.video_id for i in rep.build()], "uploaded after" in rep.build()[2].evidence_text), (True, ["o1", "o2", "w"], True))

# ties
tie = [rv("t1", T(0), S(2)), rv("t2", T(0), S(1)), rv("t3", T(1), S(3))]
rep = recover_order(tie)
tb = rep.decisions_needed()
check("equal upload times are always a decision, never silently ordered", (len(tb), tb[0].kind, set(tb[0].options)), (1, KIND_TIE, {OPT_SCHEDULE}))

# blocking problems
nocreate = [rv("n1", T(0), S(0)), rv("n2", None, S(1))]
rep = recover_order(nocreate)
check("a video with no upload time blocks the recovery and is named", (bool(rep.blocking_issues), rep.blocking_issues[0].video_ids), (True, ["n2"]))
try: rep.build(); raised = False
except RecoveryBlocked: raised = True
check("...and nothing is built from incomplete evidence", raised, True)
same = [rv("m1", S(0), S(0))]
check("an upload time identical to the scheduled time is rejected as not independent evidence", bool(recover_order(same).blocking_issues), True)
check("no scheduled Shorts at all blocks too", bool(recover_order([]).blocking_issues), True)
dups = [rv("d1", T(0), S(0)), rv("d1", T(1), S(1))]
check("the same video twice in the data is reported", any(i.kind == "duplicate_video" for i in recover_order(dups).issues), True)

# which videos are part of the queue; entries that don't count
mix = [rv("k1", T(0), S(0)), rv("k2", T(1), S(1), secs=460), rv("k3", T(2), S(2)), Video(video_id="p1", title="p", description="", thumbnail_url="", duration_seconds=20, privacy_status="public", publish_at=None, published_at=T(3))]
check("long videos and published videos are not part of the queue", [i.video_id for i in recover_order(mix).build()], ["k1", "k3"])
undone = hist(op_time, "undone op", [("d", S(7), S(2))]); undone.undone = True
rep = recover_order(mid, [undone])
check("an operation that was undone is ignored as evidence", len(rep.decisions_needed()), 1)
moved_again = [rv("a", T(0), S(0)), rv("b", T(1), S(5)), rv("c", T(2), S(6)), rv("d", T(3), S(9)), rv("e", T(4), S(3))]
h3 = hist(op_time, "op", [("d", S(7), S(2)), ("e", S(8), S(3))])
rep = recover_order(moved_again, [h3])
check("a video changed again by something with no record is left out of the earlier arrangements, not guessed (d's earlier place is unknown)", "d" not in rep.arrangements[0].times, True)

print("\n--- a decided order becomes the permanent registry, and the engine uses only that ---")
rep = recover_order(mid)
choices = {rep.decisions_needed()[0].block_id: OPT_UPLOAD}
items = rep.build(choices)
reg_r = QueueOrderStore(TMP / "rr.json")
reg_r.adopt(registry_items(items))
check("adopting the recovered order records every video with its evidence", (reg_r.ordered_ids(), "decided" in reg_r.get("d").evidence, reg_r.get("a").source), (["a", "b", "c", "d", "e"], True, "recovered"))
p_ = plan_for(mid, 2, reg_r, now=NOW)
check("the frequency plan then follows the recovered sequence even though the CURRENT dates put d and e before b and c", (p_.order_after, p_.order_before), (["a", "b", "c", "d", "e"], ["a", "d", "e", "b", "c"]))

# =================================================================== applying against a fake YouTube
print("\n--- dry run, then apply against a fake YouTube, then verify what YouTube holds ---")
from ui.schedule_apply import apply_schedule_result
from youtube.client import VideoNotModifiableError

class FakeYouTube:
    def __init__(self, videos):
        self.store = {v.video_id: dataclasses.replace(v, raw_status={"privacyStatus": v.privacy_status}) for v in videos}
        self.writes = []
        self.fail = {}
        self.publish_now = set()
    def get_videos(self, ids):
        return [dataclasses.replace(self.store[i]) for i in ids if i in self.store]
    def update_publish_time_with_status(self, vid, raw, new_time):
        if vid in self.fail: raise VideoNotModifiableError(vid, self.fail[vid], "refused")
        self.writes.append((vid, new_time))
        self.store[vid] = dataclasses.replace(self.store[vid], publish_at=new_time)
class FakeRepo:
    def __init__(self, yt): self.yt = yt
    @property
    def videos(self): return [v for v in self.yt.store.values() if v.is_scheduled]
    def refresh_ids(self, ids): pass
_states = [0]
class FakeState:
    def __init__(self, yt):
        _states[0] += 1
        m_hist.HISTORY_PATH = TMP / f"hist_{_states[0]}.json"        # every scenario gets its own history file
        self.client = yt; self.repo = FakeRepo(yt); self.history = HistoryStore()

def run_apply(state, plan, freq_desc="freq"):
    done = []; evt = threading.Event()
    def done_cb(ok, total, failed): done.append((ok, total, failed)); evt.set()
    def order_check():
        fresh = df.compute_frequency_plan(df.build_queue(state.repo.videos, reg_a, NOW), plan.frequency, cfg, NOW)
        return [i.message for i in fresh.errors]
    err = apply_schedule_result(state, plan.to_result(), freq_desc, lambda s: None, done_cb, order_check=order_check, require_scheduled=True)
    if err: return err, None
    evt.wait(10)
    return None, done[0]

reg_a = registry_for(IDS, TMP / "a1.json")
start = layout(3)
yt = FakeYouTube(start)
state = FakeState(yt)
plan = plan_for(state.repo.videos, 2, reg_a)
check("(dry run) the plan was calculated without a single write to YouTube", (len(yt.writes), len(plan.changed_rows) > 0), (0, True))
err, res = run_apply(state, plan, "Daily Upload Frequency: 3/day -> 2/day")
check("apply succeeds for every changed video", (err, res[0], res[1], res[2]), (None, len(plan.changed_rows), len(plan.changed_rows), []))
check("only videos whose time changed were written", sorted(v for v, _ in yt.writes), sorted(r.video_id for r in plan.changed_rows))
check("YouTube now holds exactly the validated schedule, in the validated order", df.verify_remote(plan, yt.get_videos(IDS)), [])
sched_now = sorted(yt.store.values(), key=lambda v: v.publish_at)
check("the order they will publish in is Q100..Q105", [v.video_id for v in sched_now], IDS)
entries = state.history.entries
check("the change was recorded in the persistent history with previous and new times", (len(entries), entries[0].description, all(c.old_publish_at is not None and c.new_publish_at is not None for c in entries[0].changes), len(entries[0].changes)), (1, "Daily Upload Frequency: 3/day -> 2/day", True, len(plan.changed_rows)))
check("...and it is on disk", len(HistoryStore().entries), 1)   # (HISTORY_PATH still points at this scenario's file)
w_before = len(yt.writes)
plan_b = plan_for(state.repo.videos, 2, reg_a)
check("running the same operation again changes nothing and writes nothing", (len(plan_b.changed_rows), run_apply(state, plan_b)[0]), (0, "Nothing to apply."))
check("(no further writes)", len(yt.writes), w_before)

# 2 -> 4 end to end
yt = FakeYouTube(layout(2)); state = FakeState(yt)
plan = plan_for(state.repo.videos, 4, reg_a)
err, res = run_apply(state, plan)
check("2 -> 4 applied: YouTube matches the plan and the order is Q100..Q105", (err, df.verify_remote(plan, yt.get_videos(IDS)), [v.video_id for v in sorted(yt.store.values(), key=lambda v: v.publish_at)]), (None, [], IDS))

# an out-of-order live schedule is put right
yt = FakeYouTube(irregular); state = FakeState(yt)
plan = plan_for(state.repo.videos, 2, reg_a)
err, res = run_apply(state, plan)
check("a scrambled live schedule is rebuilt in sequence order", (err, df.verify_remote(plan, yt.get_videos(IDS)), [v.video_id for v in sorted(yt.store.values(), key=lambda v: v.publish_at)]), (None, [], IDS))

# aborts: nothing is written when validation fails
yt = FakeYouTube(layout(2)); state = FakeState(yt)
plan = plan_for(state.repo.videos, 4, reg_a)
other = registry_for(["Q105", "Q104", "Q103", "Q102", "Q101", "Q100"], TMP / "a2.json")
def bad_check():
    fresh = df.compute_frequency_plan(df.build_queue(state.repo.videos, other, NOW), 4, cfg, NOW)
    expected = [r.video_id for r in plan.rows]
    return [] if fresh.order_after == expected else ["the sequence numbers changed since the preview"]
err = apply_schedule_result(state, plan.to_result(), "x", lambda s: None, lambda *a: None, order_check=bad_check, require_scheduled=True)
check("if the order check fails at the last moment nothing is sent to YouTube and no history is written", (bool(err), len(yt.writes), len(state.history.entries)), (True, 0, 0))

# a video published between preview and apply is not touched
yt = FakeYouTube(layout(2)); state = FakeState(yt)
plan = plan_for(state.repo.videos, 4, reg_a)
yt.store["Q101"] = dataclasses.replace(yt.store["Q101"], privacy_status="public")
err, res = run_apply(state, plan)
check("a video published since the preview is skipped and reported, never rescheduled", ("Q101" in [v for v, _ in yt.writes], any("Q101" in f for f in res[2]), yt.store["Q101"].privacy_status), (False, True, "public"))
check("...and the history does not claim a change that never happened", all(c.video_id != "Q101" for e in state.history.entries for c in e.changes), True)

# a rejected update leaves a truthful history
yt = FakeYouTube(layout(2)); state = FakeState(yt)
plan = plan_for(state.repo.videos, 4, reg_a)
yt.fail["Q102"] = "forbidden"
err, res = run_apply(state, plan)
check("a refused update is reported", (res[0] == len(plan.changed_rows) - 1, any("Q102" in f for f in res[2])), (True, True))
check("...and is removed from the history", all(c.video_id != "Q102" for e in state.history.entries for c in e.changes), True)
check("verification against YouTube then reports exactly that video", [p for p in df.verify_remote(plan, yt.get_videos(IDS))][0].startswith('"Q102"'), True)

# quota running out
yt = FakeYouTube(layout(2)); state = FakeState(yt)
plan = plan_for(state.repo.videos, 4, reg_a)
yt.fail["Q103"] = "quotaExceeded"
err, res = run_apply(state, plan)
check("when the quota runs out it stops and says nothing else was changed", (any("quota" in f.lower() for f in res[2]), all(vid not in {v for v, _ in yt.writes} for vid in ("Q104", "Q105"))), (True, True))
yt.fail.clear()
plan_c = plan_for(state.repo.videos, 4, reg_a)
err, res = run_apply(state, plan_c)
check("running it again later finishes the job and YouTube then matches the plan", (err, df.verify_remote(plan_c, yt.get_videos(IDS))), (None, []))

print("\n--- if the run stops part-way (quota), everything due before the stopping point is already exactly right ---")
import random
def partial_state(videos, plan, order, k):
    """The schedule after the first k updates of `order` have been applied (everything else still at its old time)."""
    new = {r.video_id: r.new_publish_at for r in plan.rows}
    done = {a.video_id for a in order[:k]}
    return {v.video_id: (new[v.video_id] if v.video_id in done else v.publish_at) for v in videos}, done
def matters_at(a): return min(a.new_publish_at.astimezone(), a.old_publish_at.astimezone() if a.old_publish_at else a.new_publish_at.astimezone())
def prefix_is_safe(videos, plan, order, k):
    """T = when the first un-updated video starts to matter. Before T the live schedule must equal the plan."""
    if k >= len(order): return True
    T = matters_at(order[k])
    state, done = partial_state(videos, plan, order, k)
    new = {r.video_id: r.new_publish_at for r in plan.rows}
    live_before_T = sorted((t, vid) for vid, t in state.items() if t.astimezone() < T)
    plan_before_T = sorted((t, vid) for vid, t in new.items() if t.astimezone() < T)
    return live_before_T == plan_before_T
rng = random.Random(20261024)
all_safe = True; seq_order_unsafe = 0; trials = 0
for trial in range(25):
    n = rng.randint(8, 40)
    ids = [f"R{200 + i}" for i in range(n)]
    reg_r = registry_for(ids, TMP / f"rand{trial}.json")
    slots = [at(day(d), h) for d in range(0, n) for h in (6, 9, 12)]
    times = rng.sample(slots, n)                                     # every video at a random, unrelated slot: a thoroughly scrambled schedule
    vs = [Video(video_id=i, title=i, description="", thumbnail_url="", duration_seconds=30, privacy_status="private", publish_at=t) for i, t in zip(ids, times)]
    pl = plan_for(vs, rng.choice([1, 2, 3, 4]), reg_r)
    assert len(pl.rows) == n and len(pl.changed_rows) > n // 2, "the fixture must produce a real plan"
    order = pl.to_result().assignments
    for k in range(0, len(order) + 1):
        trials += 1
        if not prefix_is_safe(vs, pl, order, k): all_safe = False
    by_seq = sorted(order, key=lambda a: reg_r.seq_of(a.video_id))
    for k in range(1, len(by_seq)):
        if not prefix_is_safe(vs, pl, by_seq, k): seq_order_unsafe += 1; break
check(f"in the urgency order, stopping after ANY number of updates (checked {trials} stopping points on 25 scrambled schedules) leaves everything due before that point exactly as planned", all_safe, True)
check("(and it matters: updating in plain queue order leaves old-time videos publishing ahead of correct ones on most of those schedules)", seq_order_unsafe >= 15, True)
plan_u = plan_for(irregular, 2, reg)
firsts = [matters_at(a) for a in plan_u.to_result().assignments]
check("the updates really are in the order of when they matter", firsts, sorted(firsts))
yt = FakeYouTube(irregular); state = FakeState(yt)
plan = plan_for(state.repo.videos, 2, reg_a)
stop_after = 2
orig_update = yt.update_publish_time_with_status
count = {"n": 0}
def limited(vid_, raw, t):
    count["n"] += 1
    if count["n"] > stop_after: raise VideoNotModifiableError(vid_, "quotaExceeded", "quota")
    return orig_update(vid_, raw, t)
yt.update_publish_time_with_status = limited
err, res = run_apply(state, plan)
live = {v.video_id: v.publish_at for v in yt.store.values()}
T = matters_at(plan.to_result().assignments[stop_after])
check("end to end with YouTube refusing after 2 updates: it stops, says why, and the 2 updates it made are in the history",
      (res[0], any("quota" in f.lower() for f in res[2]), sum(len(e.changes) for e in state.history.entries)), (2, True, 2))
check("...and everything due before the stopping point is exactly as planned",
      sorted((t, v) for v, t in live.items() if t.astimezone() < T) == sorted((r.new_publish_at, r.video_id) for r in plan.rows if r.new_publish_at.astimezone() < T), True)

print("\n--- no test may write the real order ledger ---")
tests_dir = pathlib.Path(__file__).resolve().parent
offenders = [f.name for f in sorted(tests_dir.glob("test_*.py"))
             if "UPLOAD_ACTIVITY_PATH" in f.read_text(encoding="utf-8") and "QUEUE_ORDER_PATH" not in f.read_text(encoding="utf-8")]
check("every test that can record an upload redirects data/queue_order.json (a test once added fake uploads to the real ledger)", offenders, [])

if errors:
    print("\nFAILURES:", errors); os._exit(1)
print("\nALL CHECKS PASSED"); os._exit(0)

"""Upload scheduling order: with N uploads per day, a day's videos must take its N times in
order (first upload earliest), and a new upload must never publish before one already on its day.
Regression test for the bug where, at 2 per day, the first upload landed at 10:00 and the
second at 06:00."""
import sys, tempfile, pathlib, datetime as dt
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import data.auto_schedule_config as asc
asc.AUTO_SCHEDULE_CONFIG_PATH = pathlib.Path(tempfile.mkdtemp()) / "auto_schedule_config.json"
from data.auto_schedule_config import AutoScheduleConfig
from scheduling.next_slot import find_upload_slots_at_rate
from youtube.models import Video

errors = []
def check(name, actual, expected=True):
    ok = actual == expected
    print(("PASS" if ok else "FAIL"), name, "->" if not ok else "", (actual, expected) if not ok else "")
    if not ok:
        errors.append(name)

TODAY = dt.date.today()
def day(n): return TODAY + dt.timedelta(days=n)
def at(d, hhmm): return dt.datetime.combine(d, dt.datetime.strptime(hhmm, "%H:%M").time()).astimezone()
def vid(i, when): return Video(video_id=f"v{i}", title=f"t{i}", description="", thumbnail_url="", duration_seconds=1, publish_at=when)
def label(s): return f"{s.astimezone().date()} {s.astimezone():%H:%M}"

def upload_sequence(rate, n, existing=None):
    """Simulates n sequential single-video uploads exactly as the Upload page does it."""
    videos = list(existing or [])
    out = []
    for i in range(n):
        slot = find_upload_slots_at_rate(videos, 1, rate)[0]
        out.append(slot)
        videos.append(vid(1000 + i, slot))
    return out

cfg = AutoScheduleConfig()          # default window 06:00-14:00
cfg.save_slot_preset(2, [dt.time(6, 0), dt.time(10, 0)])
cfg.save_slot_preset(3, [dt.time(6, 0), dt.time(9, 0), dt.time(12, 0)])

print("\n--- the reported bug: 2 uploads per day ---")
seq = upload_sequence(2, 6)
check("1st upload of the day -> 06:00 (was 10:00)", label(seq[0]), f"{day(1)} 06:00")
check("2nd upload of the day -> 10:00 (was 06:00)", label(seq[1]), f"{day(1)} 10:00")
check("3rd upload starts the next day at 06:00", label(seq[2]), f"{day(2)} 06:00")
check("...and 4th at 10:00", label(seq[3]), f"{day(2)} 10:00")
check("six uploads publish in strictly the order they were uploaded", seq == sorted(seq) and len(set(seq)) == 6, True)
check("...exactly two per day", [s.astimezone().date() for s in seq].count(day(1)), 2)

print("\n--- other rates ---")
check("1 per day: the single time every day", [label(s) for s in upload_sequence(1, 3)], [f"{day(1)} 10:00", f"{day(2)} 10:00", f"{day(3)} 10:00"])
check("3 per day: 06:00, 09:00, 12:00 in order, then next day", [label(s) for s in upload_sequence(3, 4)],
      [f"{day(1)} 06:00", f"{day(1)} 09:00", f"{day(1)} 12:00", f"{day(2)} 06:00"])
for rate in (4, 5, 6):
    seq = upload_sequence(rate, rate * 2)
    per_day = [s.astimezone().date() for s in seq]
    check(f"{rate} per day: {rate} videos a day, strictly in upload order", (seq == sorted(seq), per_day.count(day(1)), per_day.count(day(2))), (True, rate, rate))

print("\n--- appending to a partly-filled day ---")
check("a day holding one video at 06:00 -> the next goes to 10:00 the same day", label(find_upload_slots_at_rate([vid(1, at(day(5), "06:00"))], 1, 2)[0]), f"{day(5)} 10:00")
check("a full day (2/2) -> next day", label(find_upload_slots_at_rate([vid(1, at(day(5), "06:00")), vid(2, at(day(5), "10:00"))], 1, 2)[0]), f"{day(6)} 06:00")
check("a day whose only video is already at the LAST time (10:00) is never gone back into: the new video rolls to the next day instead of publishing before it",
      label(find_upload_slots_at_rate([vid(1, at(day(5), "10:00"))], 1, 2)[0]), f"{day(6)} 06:00")
check("a day with a video at a non-table time (08:00): the next table time after it", label(find_upload_slots_at_rate([vid(1, at(day(5), "08:00"))], 1, 2)[0]), f"{day(5)} 10:00")
check("changing the rate 2 -> 3 mid-day: a day with 06:00, 09:00 gets 12:00", label(find_upload_slots_at_rate([vid(1, at(day(5), "06:00")), vid(2, at(day(5), "09:00"))], 1, 3)[0]), f"{day(5)} 12:00")
check("changing the rate 3 -> 2 when a day already has 2: it's full, next day", label(find_upload_slots_at_rate([vid(1, at(day(5), "06:00")), vid(2, at(day(5), "09:00"))], 1, 2)[0]), f"{day(6)} 06:00")
check("starts from the FURTHEST scheduled day, never backfilling an earlier gap",
      label(find_upload_slots_at_rate([vid(1, at(day(3), "06:00")), vid(2, at(day(9), "06:00"))], 1, 2)[0]), f"{day(9)} 10:00")
check("nothing scheduled at all: starts tomorrow at the first time", label(find_upload_slots_at_rate([], 1, 2)[0]), f"{day(1)} 06:00")

print("\n--- several at once ---")
check("two uploads requested in one call -> both of a fresh day, in order", [label(s) for s in find_upload_slots_at_rate([], 2, 2)], [f"{day(1)} 06:00", f"{day(1)} 10:00"])
check("three requested at rate 2 -> two on day one, one on day two", [label(s) for s in find_upload_slots_at_rate([], 3, 2)], [f"{day(1)} 06:00", f"{day(1)} 10:00", f"{day(2)} 06:00"])

print("\n--- the real tail of the current schedule (2027-01-22 full, 2027-01-23 holds one video at 10:00) ---")
tail = [vid(1, at(dt.date(2027, 1, 22), "06:00")), vid(2, at(dt.date(2027, 1, 22), "10:00")), vid(3, at(dt.date(2027, 1, 23), "10:00"))]
seq = upload_sequence(2, 4, tail)
check("the next upload does not publish before the video already at 2027-01-23 10:00; the new days then fill 06:00 -> 10:00 in order",
      [label(s) for s in seq], ["2027-01-24 06:00", "2027-01-24 10:00", "2027-01-25 06:00", "2027-01-25 10:00"])

if errors:
    print("\nFAILURES:", errors); sys.exit(1)
print("\nALL CHECKS PASSED")

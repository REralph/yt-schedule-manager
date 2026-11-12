"""Persisted configuration for AUTO BOOST (this feature was called Auto Schedule; the module,
class and file names keep the old name so nothing saved is lost) -- nothing about how it
decides the next day's video count is hard-coded; every number here is meant to be
user-editable via the Auto Schedule settings page, not changed in code.

v2 design: there is no user-configurable "evaluation period" or "view calculation mode"
anymore -- the system automatically evaluates the most recently completed publishing
day(s) (see scheduling/auto_schedule.py's group_by_published_day/evaluate_daily_performance)
and always combines every Short published on a given day into one total. There's also no
persistent "last scheduled count" to compound off of -- each decision is computed fresh
from real recent performance against the target, which is what makes it self-correct back
toward the normal count once performance recovers (spec section 10), rather than needing a
separate step-down rule.

SET-0012.1 (Upload Time Presets): slot_presets holds one independent, user-editable,
auto-saved list of clock times per possible daily count (1-6) -- see get_slot_times().
This is the one place the whole app now looks up "what times does a day with N uploads
get" (scheduling/slot_rules.py delegates here), replacing the old hardcoded
06:00/08:00/10:00/12:00 table that couldn't go past 4 and wasn't editable at all.

AS-001 revised logic: estimated_views_per_short is now a single flat number the user sets
directly -- the old Automatic/Manual system (a trimmed-mean estimate off recent published
Shorts, with a manual fallback) is gone. It only ever fed a planning calculation (how many
Shorts are needed to close a shortfall); a plain user-set figure is simpler and no less
accurate for that purpose.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, time
from pathlib import Path
from typing import Optional

from data import safe_io

DATA_DIR = Path(__file__).resolve().parent
AUTO_SCHEDULE_CONFIG_PATH = DATA_DIR / "auto_schedule_config.json"

TARGET_MODE_DAILY = "daily"
TARGET_MODE_WEEKLY = "weekly"

MIN_SLOT_COUNT = 1
MAX_SLOT_COUNT = 6
MAX_BOOST_EXTRA = 3  # the "extra videos when below target" choices offered: Calculated (0), +1, +2, +3
MIN_SLOT_SPACING_MINUTES = 15  # matches scheduling/increase_slots.py's own SAFETY_BUFFER -- this app's existing precedent for "how close together is too close"


def validate_slot_times(times: list[time], window_start: time, window_end: time) -> Optional[str]:
    """Returns an error message if `times` isn't a valid slot preset, else None. Checks:
    every time falls inside the publishing window, they're in strictly chronological
    order (which structurally also rules out duplicates -- two equal times can't be
    "strictly" increasing), and consecutive slots are at least MIN_SLOT_SPACING_MINUTES
    apart."""
    for t in times:
        if t < window_start or t > window_end:
            return f"All slot times must be between {window_start.strftime('%H:%M')} and {window_end.strftime('%H:%M')}."
    for i in range(1, len(times)):
        if times[i] <= times[i - 1]:
            return "Slot times must be in chronological order, with no duplicate times."
        gap_minutes = (datetime.combine(datetime.min, times[i]) - datetime.combine(datetime.min, times[i - 1])).total_seconds() / 60
        if gap_minutes < MIN_SLOT_SPACING_MINUTES:
            return f"Slot times must be at least {MIN_SLOT_SPACING_MINUTES} minutes apart."
    return None


def _valid_presets(raw, s: "safe_io.Sanitizer") -> dict:
    """Keeps only slot presets that are well-formed: key "1".."6", exactly that many valid
    HH:MM times in strictly increasing order. Anything else is dropped -- get_slot_times()
    then falls back to the evenly-spaced default for that count."""
    if not isinstance(raw, dict):
        if raw not in (None, {}):
            s.note(f"slot_presets was {type(raw).__name__}, not a mapping -> defaults")
        return {}
    good: dict[str, list[str]] = {}
    for key, times in raw.items():
        try:
            count = int(key)
            if not (MIN_SLOT_COUNT <= count <= MAX_SLOT_COUNT) or not isinstance(times, list) or len(times) != count:
                raise ValueError("wrong number of times for that slot count")
            parsed = [datetime.strptime(t, "%H:%M").time() for t in times]
            if any(parsed[i] >= parsed[i + 1] for i in range(len(parsed) - 1)):
                raise ValueError("times are not in increasing order")
            good[str(count)] = [t.strftime("%H:%M") for t in parsed]
        except (TypeError, ValueError) as e:
            s.note(f"slot preset {key!r} is invalid ({e}) -> default times for that count")
    return good


@dataclass
class AutoScheduleConfig:
    enabled: bool = False

    target_mode: str = TARGET_MODE_DAILY
    daily_target_views: int = 3250
    weekly_target_views: int = 22750

    normal_videos_per_day: int = 2
    max_videos_per_day: int = 6
    minimum_daily_views: int = 2000

    boost_extra_videos: int = 0  # AUTO BOOST: extra videos added to tomorrow when the last completed day missed its target. 0 = "Calculated" (worked out from how far short the views were, using estimated_views_per_short); 1..MAX_BOOST_EXTRA = always exactly that many.
    estimated_views_per_short: int = 800  # AS-001 point 7 -- a single flat planning figure, replacing the old Automatic/Manual estimate system

    window_start_hour: int = 6
    window_start_minute: int = 0
    window_end_hour: int = 14
    window_end_minute: int = 0

    slot_presets: dict[str, list[str]] = field(default_factory=dict)  # "1".."6" -> ["HH:MM", ...] -- see get_slot_times()

    @property
    def window_start(self) -> time:
        return time(self.window_start_hour, self.window_start_minute)

    @property
    def window_end(self) -> time:
        return time(self.window_end_hour, self.window_end_minute)

    def get_slot_times(self, count: int) -> list[time]:
        """The times used for a day with `count` uploads: the user's own saved preset for
        that exact count if one exists, else times evenly distributed across
        [window_start, window_end] (the same even-spacing this app's other schedulers
        already used -- see scheduling/engine.py's ideal_times()). Every caller in the app
        that needs "what time does the Nth upload of the day get" goes through this."""
        count = max(MIN_SLOT_COUNT, min(MAX_SLOT_COUNT, count))
        saved = self.slot_presets.get(str(count))
        if saved:
            try:
                return [datetime.strptime(s, "%H:%M").time() for s in saved]
            except ValueError:
                pass  # a corrupt/malformed saved entry falls back to the computed default rather than breaking scheduling
        return self._default_slot_times(count)

    def _default_slot_times(self, count: int) -> list[time]:
        start_dt = datetime.combine(datetime.min, self.window_start)
        end_dt = datetime.combine(datetime.min, self.window_end)
        if count == 1:
            # A single upload defaults to the middle of the window -- there's no
            # "spread" to compute for one point, and centering it is the least
            # arbitrary placement (unlike pinning it to either edge).
            return [(start_dt + (end_dt - start_dt) / 2).time()]
        step = (end_dt - start_dt) / (count - 1)
        return [(start_dt + step * i).time() for i in range(count)]

    def save_slot_preset(self, count: int, times: list[time]) -> None:
        """SET-0012.1 point 5: "automatically save each preset" -- called the moment a
        slot-time field is edited and validated, not gated behind the page's separate
        Save Settings button. Each count's preset is independent (point 6) since this
        only ever writes the one key for `count`.

        Re-reads the current on-disk state and merges just this one preset into it,
        rather than blindly persisting this whole (possibly stale) in-memory snapshot.
        This matters because multiple pages now each hold their own independently-loaded
        AutoScheduleConfig instance at the same time for as long as the app runs (Auto
        Schedule Settings, Increase Daily Slots, and One Upload Page's own daily-rate
        control -- see "Separate Daily Upload Setting from Auto Schedule"): without this,
        saving a preset from one page's long-lived, now-stale snapshot could silently
        revert a field (e.g. normal_videos_per_day) that a DIFFERENT page had since saved
        from its own snapshot -- exactly the kind of cross-page interference these
        settings are meant to never have."""
        count = max(MIN_SLOT_COUNT, min(MAX_SLOT_COUNT, count))
        fresh = AutoScheduleConfig.load()
        fresh.slot_presets[str(count)] = [t.strftime("%H:%M") for t in times]
        fresh.save()
        self.slot_presets = fresh.slot_presets  # keep this instance consistent if the caller keeps using it

    @property
    def recommended_daily_from_weekly(self) -> float:
        """Spec section 3: weekly ÷ 7, shown next to the weekly target field and kept in
        sync automatically -- not saved separately, always derived live from whatever the
        weekly target field currently holds."""
        return self.weekly_target_views / 7

    def save(self) -> None:
        safe_io.atomic_write_json(AUTO_SCHEDULE_CONFIG_PATH, asdict(self))

    @classmethod
    def load(cls) -> "AutoScheduleConfig":
        """Always returns a usable config -- see AppConfig.load(). The publishing window and
        every slot preset are validated too, since a nonsensical one (hour 99, an end before
        the start, a 3-slot preset holding 2 times) would otherwise break scheduling later."""
        if not AUTO_SCHEDULE_CONFIG_PATH.exists():
            cfg = cls()
            try:
                cfg.save()
            except OSError:
                pass
            return cfg
        raw = safe_io.read_json_object(AUTO_SCHEDULE_CONFIG_PATH, "auto_schedule_config.json")
        if raw is None:
            return cls()
        return cls.from_raw(raw)

    @classmethod
    def from_raw(cls, raw: dict) -> "AutoScheduleConfig":
        d = cls()
        s = safe_io.Sanitizer("auto_schedule_config.json")
        get = raw.get
        cfg = cls(
            enabled=s.bool(get("enabled", d.enabled), d.enabled, "enabled"),
            target_mode=s.choice(get("target_mode", d.target_mode), (TARGET_MODE_DAILY, TARGET_MODE_WEEKLY), d.target_mode, "target_mode"),
            daily_target_views=s.int(get("daily_target_views", d.daily_target_views), d.daily_target_views, 0, 1_000_000_000, "daily_target_views"),
            weekly_target_views=s.int(get("weekly_target_views", d.weekly_target_views), d.weekly_target_views, 0, 1_000_000_000, "weekly_target_views"),
            normal_videos_per_day=s.int(get("normal_videos_per_day", d.normal_videos_per_day), d.normal_videos_per_day, MIN_SLOT_COUNT, MAX_SLOT_COUNT, "normal_videos_per_day"),
            max_videos_per_day=s.int(get("max_videos_per_day", d.max_videos_per_day), d.max_videos_per_day, MIN_SLOT_COUNT, MAX_SLOT_COUNT, "max_videos_per_day"),
            minimum_daily_views=s.int(get("minimum_daily_views", d.minimum_daily_views), d.minimum_daily_views, 0, 1_000_000_000, "minimum_daily_views"),
            boost_extra_videos=s.int(get("boost_extra_videos", d.boost_extra_videos), d.boost_extra_videos, 0, MAX_BOOST_EXTRA, "boost_extra_videos"),
            estimated_views_per_short=s.int(get("estimated_views_per_short", d.estimated_views_per_short), d.estimated_views_per_short, 1, 1_000_000_000, "estimated_views_per_short"),
            window_start_hour=s.int(get("window_start_hour", d.window_start_hour), d.window_start_hour, 0, 23, "window_start_hour"),
            window_start_minute=s.int(get("window_start_minute", d.window_start_minute), d.window_start_minute, 0, 59, "window_start_minute"),
            window_end_hour=s.int(get("window_end_hour", d.window_end_hour), d.window_end_hour, 0, 23, "window_end_hour"),
            window_end_minute=s.int(get("window_end_minute", d.window_end_minute), d.window_end_minute, 0, 59, "window_end_minute"),
        )
        if cfg.window_end <= cfg.window_start:
            s.note(f"publishing window {cfg.window_start:%H:%M}-{cfg.window_end:%H:%M} ends before it starts -> {d.window_start:%H:%M}-{d.window_end:%H:%M}")
            cfg.window_start_hour, cfg.window_start_minute = d.window_start_hour, d.window_start_minute
            cfg.window_end_hour, cfg.window_end_minute = d.window_end_hour, d.window_end_minute
        cfg.slot_presets = _valid_presets(get("slot_presets", {}), s)
        s.report()
        return cfg

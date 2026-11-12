"""Application configuration, persisted to data/config.json."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path

from data import safe_io

DATA_DIR = Path(__file__).resolve().parent
CONFIG_PATH = DATA_DIR / "config.json"


@dataclass
class AppConfig:
    daily_quota_budget_units: int = 9000  # stay under the 10,000/day default with headroom
    use_uploads_playlist: bool = True  # False = fall back to search.list(forMine=true)
    developer_mode: bool = False
    last_playlist_name: str = ""  # remembered across uploads so it doesn't need reselecting each time
    last_made_for_kids: bool = False  # same idea, for the audience setting
    shorts_folder_path: str = ""  # Basic Upload's watched folder for q<number> files; "" = not configured
    new_video_detection_window_seconds: int = 60  # CHECK NEW only selects a file created/modified within this long ago
    upload_videos_per_day: int = 2  # One Upload Page's OWN daily capacity (1-6) -- deliberately separate from AutoScheduleConfig.normal_videos_per_day; see the "Separate Daily Upload Setting from Auto Schedule" spec. The two must never be bound together, though they share the same slot-time presets (data/auto_schedule_config.py's slot_presets) for what clock times a given count actually gets.

    quality_info_expanded: bool = False  # One Upload Page's QUALITY INFORMATION section (QUALITY-001): collapsed by default, remembers the user's last choice

    def save(self) -> None:
        safe_io.atomic_write_json(CONFIG_PATH, asdict(self))

    @classmethod
    def load(cls) -> "AppConfig":
        """Always returns a usable config. A missing/corrupt file gives defaults, and every
        value that is the wrong type or outside its allowed range is replaced by a safe
        default (or clamped) -- with the problem recorded in Crash History -- rather than
        being trusted and crashing a page later."""
        if not CONFIG_PATH.exists():
            cfg = cls()
            try:
                cfg.save()
            except OSError:
                pass
            return cfg
        raw = safe_io.read_json_object(CONFIG_PATH, "config.json")
        if raw is None:
            return cls()
        return cls.from_raw(raw)

    @classmethod
    def from_raw(cls, raw: dict) -> "AppConfig":
        d = cls()  # the defaults
        s = safe_io.Sanitizer("config.json")
        get = raw.get
        cfg = cls(
            daily_quota_budget_units=s.int(get("daily_quota_budget_units", d.daily_quota_budget_units), d.daily_quota_budget_units, 100, 10_000_000, "daily_quota_budget_units"),
            use_uploads_playlist=s.bool(get("use_uploads_playlist", d.use_uploads_playlist), d.use_uploads_playlist, "use_uploads_playlist"),
            developer_mode=s.bool(get("developer_mode", d.developer_mode), d.developer_mode, "developer_mode"),
            last_playlist_name=s.str(get("last_playlist_name", d.last_playlist_name), d.last_playlist_name, "last_playlist_name"),
            last_made_for_kids=s.bool(get("last_made_for_kids", d.last_made_for_kids), d.last_made_for_kids, "last_made_for_kids"),
            shorts_folder_path=s.str(get("shorts_folder_path", d.shorts_folder_path), d.shorts_folder_path, "shorts_folder_path"),
            new_video_detection_window_seconds=s.int(get("new_video_detection_window_seconds", d.new_video_detection_window_seconds), d.new_video_detection_window_seconds, 1, 86_400, "new_video_detection_window_seconds"),
            upload_videos_per_day=s.int(get("upload_videos_per_day", d.upload_videos_per_day), d.upload_videos_per_day, 1, 6, "upload_videos_per_day"),
            quality_info_expanded=s.bool(get("quality_info_expanded", d.quality_info_expanded), d.quality_info_expanded, "quality_info_expanded"),
        )
        s.report()
        return cfg

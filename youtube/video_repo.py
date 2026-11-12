"""Local cache of scheduled videos in front of YouTubeClient, so the UI has something to
show instantly on launch and only hits the API when the user asks to refresh.

COMPLETENESS (why refresh() does more than list the uploads playlist): that listing is
unreliable on this channel -- it left out well over half of the scheduled videos (103 of 228
were visible), which made scheduled days look empty and every schedule-based feature (upload
placement, Increase Daily Slots, Auto Boost, the Calendar) work from a partial picture. So a
refresh now asks YouTube about, by ID, every video the app has EVER seen scheduled (this cache)
plus every video in the upload record -- an ID lookup is reliable and cheap -- and tops that up
with a search-based DISCOVERY of recently-uploaded videos on a schedule (shallow every 12 hours,
deep every 7 days or on request) to catch ones created outside the app, e.g. in YouTube Studio.
A video that is published or deleted simply stops being returned and drops out of the cache."""
from __future__ import annotations

import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Iterable, Optional

from data import safe_io
from youtube.client import YouTubeClient
from youtube.models import Video

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
CACHE_PATH = DATA_DIR / "video_cache.json"

# Discovery costs 100 quota units per page of 50 videos (search.list), so it is rationed.
SHALLOW_DISCOVERY_PAGES = 3  # the 150 newest uploads -- enough to catch anything uploaded since the last discovery
DEEP_DISCOVERY_PAGES = 8  # the 400 newest -- reaches back past every scheduled video on this channel
SHALLOW_MIN_INTERVAL = timedelta(hours=12)
DEEP_MIN_INTERVAL = timedelta(hours=6)  # the fastest a deep discovery may be REQUESTED again
DEEP_REQUIRED_AFTER = timedelta(days=7)  # a deep discovery happens by itself at least this often
RETRY_AFTER_FAILED_DISCOVERY = 30 * 60  # seconds -- don't hammer search while it is failing (e.g. quota)
RECENT_REFRESH_SECONDS = 3  # a refresh that lands right behind another one just reuses its result


def _video_to_dict(v: Video) -> dict:
    return {
        "video_id": v.video_id,
        "title": v.title,
        "description": v.description,
        "thumbnail_url": v.thumbnail_url,
        "duration_seconds": v.duration_seconds,
        "tags": v.tags,
        "privacy_status": v.privacy_status,
        "publish_at": v.publish_at.isoformat() if v.publish_at else None,
        "upload_status": v.upload_status,
        "raw_status": v.raw_status,
        # when it was UPLOADED (YouTube's snippet.publishedAt, which for a scheduled video is its creation time) -- the
        # one per-video fact no scheduling feature can change, so the queue order is recovered from it
        "published_at": v.published_at.isoformat() if v.published_at else None,
    }


def _optional_time(value) -> Optional[datetime]:
    try:
        return datetime.fromisoformat(value) if value else None
    except (TypeError, ValueError):
        return None  # an unreadable stamp is just "not known"; the next refresh fills it in


def _video_from_dict(d: dict) -> Video:
    publish_at = None
    if d.get("publish_at"):
        publish_at = datetime.fromisoformat(d["publish_at"])
    return Video(
        video_id=d["video_id"],
        title=d.get("title", ""),
        description=d.get("description", ""),
        thumbnail_url=d.get("thumbnail_url", ""),
        duration_seconds=d.get("duration_seconds", 0),
        tags=list(d.get("tags", []) or []),
        privacy_status=d.get("privacy_status", "private"),
        publish_at=publish_at,
        upload_status=d.get("upload_status", "uploaded"),
        raw_status=dict(d.get("raw_status", {}) or {}),
        published_at=_optional_time(d.get("published_at")),
    )


class VideoRepository:
    def __init__(self, client: YouTubeClient, use_uploads_playlist: bool = True):
        self._client = client
        self.use_uploads_playlist = use_uploads_playlist
        self.last_refreshed: Optional[datetime] = None
        # False until a refresh has succeeded in THIS run of the app. The cache loaded from disk can
        # be stale or incomplete (an older build only ever cached part of the schedule), so anything
        # that rewrites the schedule waits for this -- see DailyUploadFrequencyPage.
        self.refreshed_this_session = False
        self.last_discovery_at: Optional[datetime] = None
        self.last_deep_discovery_at: Optional[datetime] = None
        self.known_id_provider: Optional[Callable[[], Iterable[str]]] = None  # extra IDs worth asking about (the upload record) -- set by the main window
        self._videos: dict[str, Video] = {}
        self._cache_lock = threading.Lock()  # refresh() runs on several threads (start-up, tab focus, upload preview, Auto Boost) -- saves must not interleave
        self._refresh_lock = threading.Lock()  # ...and refreshes themselves run one at a time (each costs ~50 quota units, a discovery far more)
        self._last_refresh_clock = 0.0
        self._last_failed_discovery_clock = 0.0
        self._load_cache()

    @property
    def videos(self) -> list[Video]:
        return sorted(
            self._videos.values(),
            key=lambda v: v.publish_at or datetime.max.replace(tzinfo=timezone.utc),
        )

    def get(self, video_id: str) -> Optional[Video]:
        return self._videos.get(video_id)

    def refresh(self, deep: bool = False) -> list[Video]:
        """Hits the real API and replaces the local cache with what's actually scheduled now.
        `deep=True` asks for a deep discovery (see the module docstring) if one hasn't run
        within DEEP_MIN_INTERVAL -- for an explicit "refresh everything" button."""
        with self._refresh_lock:
            if not deep and time.monotonic() - self._last_refresh_clock < RECENT_REFRESH_SECONDS and self.last_refreshed:
                return self.videos  # another thread just did this a moment ago

            from data import diagnostics

            now = datetime.now(timezone.utc)
            discovered: list[str] = []
            pages = self._discovery_pages(deep, now)
            discovery_ok = False
            if pages:
                try:
                    discovered = self._client.discover_recent_video_ids(pages)
                    discovery_ok = True
                except Exception as e:  # quota, network...: the ID-based refresh below still works
                    self._last_failed_discovery_clock = time.monotonic()
                    diagnostics.report_recovered("Searching the channel for recently uploaded videos", e, page="Schedule data")

            known = set(self._videos)
            if self.known_id_provider is not None:
                try:
                    known |= set(self.known_id_provider())
                except Exception as e:
                    diagnostics.report_recovered("Reading the upload record's video IDs", e, page="Schedule data")

            fresh = self._client.get_scheduled_videos(
                use_uploads_playlist=self.use_uploads_playlist, extra_ids=[*known, *discovered],
            )
            self._videos = {v.video_id: v for v in fresh}
            self.last_refreshed = now
            self.refreshed_this_session = True
            if discovery_ok:
                self.last_discovery_at = now
                if pages >= DEEP_DISCOVERY_PAGES:
                    self.last_deep_discovery_at = now
            self._last_refresh_clock = time.monotonic()
            self._save_cache()
            return self.videos

    def _discovery_pages(self, deep_requested: bool, now: datetime) -> int:
        """How many search pages this refresh should spend on discovery (0 = none)."""
        if self._last_failed_discovery_clock and time.monotonic() - self._last_failed_discovery_clock < RETRY_AFTER_FAILED_DISCOVERY:
            return 0
        since_deep = (now - self.last_deep_discovery_at) if self.last_deep_discovery_at else None
        if since_deep is None or since_deep >= DEEP_REQUIRED_AFTER:
            return DEEP_DISCOVERY_PAGES
        if deep_requested and since_deep >= DEEP_MIN_INTERVAL:
            return DEEP_DISCOVERY_PAGES
        since_any = (now - self.last_discovery_at) if self.last_discovery_at else None
        if since_any is None or since_any >= SHALLOW_MIN_INTERVAL:
            return SHALLOW_DISCOVERY_PAGES
        return 0

    def refresh_ids(self, video_ids: list[str]) -> list[Video]:
        """Re-fetches just the given video IDs (e.g. right after an Apply, to verify the
        real resulting state) and merges the results into the cache."""
        fresh = self._client.get_videos(video_ids)
        for v in fresh:
            if v.is_scheduled:
                self._videos[v.video_id] = v
            else:
                # no longer scheduled (e.g. published in the meantime, or made public) - drop it
                self._videos.pop(v.video_id, None)
        self._save_cache()
        return fresh

    def _load_cache(self) -> None:
        raw = safe_io.read_json_object(CACHE_PATH, "video_cache.json")
        if raw is None:
            return
        videos = safe_io.parse_entries(raw.get("videos", []), _video_from_dict, "video_cache.json")
        self._videos = {v.video_id: v for v in videos}
        for attr in ("last_refreshed", "last_discovery_at", "last_deep_discovery_at"):
            try:
                setattr(self, attr, datetime.fromisoformat(raw[attr]) if raw.get(attr) else None)
            except (TypeError, ValueError):
                setattr(self, attr, None)  # the cached videos are still usable; the next refresh sets real timestamps

    def _save_cache(self) -> None:
        with self._cache_lock:
            payload = {
                "last_refreshed": self.last_refreshed.isoformat() if self.last_refreshed else None,
                "last_discovery_at": self.last_discovery_at.isoformat() if self.last_discovery_at else None,
                "last_deep_discovery_at": self.last_deep_discovery_at.isoformat() if self.last_deep_discovery_at else None,
                "videos": [_video_to_dict(v) for v in list(self._videos.values())],
            }
            try:
                safe_io.atomic_write_json(CACHE_PATH, payload)
            except OSError as e:
                # The cache is only a start-up convenience: failing to save it must never
                # fail the refresh that just successfully fetched real data from YouTube.
                from data import diagnostics
                diagnostics.report_recovered("Saving the schedule cache", e, page="Schedule data")

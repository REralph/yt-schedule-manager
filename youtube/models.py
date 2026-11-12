"""Plain data model for a YouTube video as the rest of the app sees it."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

_HASHTAG_RE = re.compile(r"(?<!\w)#(\w+)")
# Matches chapter/timestamp lines like "0:00 Intro" or "1:23:45 - Big moment"
_CHAPTER_RE = re.compile(
    r"^\s*(?P<time>\d{1,2}(?::\d{2}){1,2})\s*[-–—:]?\s*(?P<label>.+?)\s*$",
    re.MULTILINE,
)

# ISO 8601 duration like PT1M3S, PT45S, PT1H2M
_DURATION_RE = re.compile(
    r"PT(?:(?P<hours>\d+)H)?(?:(?P<minutes>\d+)M)?(?:(?P<seconds>\d+)S)?"
)


def parse_iso8601_duration(value: str) -> int:
    """Returns total seconds for an ISO 8601 duration string like 'PT1M3S'. Returns 0 if unparseable."""
    if not value:
        return 0
    m = _DURATION_RE.fullmatch(value.strip())
    if not m:
        return 0
    hours = int(m.group("hours") or 0)
    minutes = int(m.group("minutes") or 0)
    seconds = int(m.group("seconds") or 0)
    return hours * 3600 + minutes * 60 + seconds


def format_duration(total_seconds: int) -> str:
    if total_seconds <= 0:
        return "0:00"
    h, rem = divmod(total_seconds, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"


def extract_hashtags(*texts: str) -> list[str]:
    found: list[str] = []
    seen: set[str] = set()
    for text in texts:
        if not text:
            continue
        for match in _HASHTAG_RE.finditer(text):
            tag = match.group(1)
            key = tag.lower()
            if key not in seen:
                seen.add(key)
                found.append(tag)
    return found


def strip_hashtags(text: str) -> str:
    """The title with every #hashtag removed (e.g. for One Upload Page's History display,
    which shows the clean title without the tags used for discovery). Collapses the
    resulting double spaces left behind rather than leaving visible gaps."""
    if not text:
        return ""
    without_tags = _HASHTAG_RE.sub("", text)
    return re.sub(r" {2,}", " ", without_tags).strip()


def extract_chapters(description: str) -> list[tuple[str, str]]:
    """Best-effort parse of description timestamp lines into (timestamp, label) pairs. Read-only/display-only."""
    if not description:
        return []
    chapters: list[tuple[str, str]] = []
    for line in description.splitlines():
        m = _CHAPTER_RE.match(line)
        if m and m.group("label"):
            chapters.append((m.group("time"), m.group("label").strip()))
    # A real chapter list normally has 2+ entries starting near 0:00; a single stray match
    # elsewhere in the description is almost certainly not a chapter list.
    if len(chapters) < 2:
        return []
    return chapters


# YouTube counts a vertical video up to 3 minutes as a Short. Anything longer (e.g. a full-match
# montage) is NOT part of the Shorts queue the scheduling features move around.
SHORT_MAX_SECONDS = 180


@dataclass
class Video:
    video_id: str
    title: str
    description: str
    thumbnail_url: str
    duration_seconds: int
    tags: list[str] = field(default_factory=list)
    privacy_status: str = "private"
    publish_at: Optional[datetime] = None  # tz-aware UTC datetime, or None if not scheduled
    upload_status: str = "uploaded"
    playlist_position: Optional[int] = None
    raw_status: dict = field(default_factory=dict)  # full status part, needed to safely round-trip updates
    view_count: Optional[int] = None  # statistics.viewCount -- None if not fetched, not "zero views"
    published_at: Optional[datetime] = None  # snippet.publishedAt -- when it actually went live (may
    # differ slightly from publish_at, which is when it was *scheduled* to go live); used for
    # Auto Schedule's "N hours since publication" performance windows

    @property
    def is_scheduled(self) -> bool:
        return self.privacy_status == "private" and self.publish_at is not None

    @property
    def is_short(self) -> bool:
        """False only for a video known to be longer than a Short can be. An unknown duration
        (0) counts as a Short, so missing data never makes a Short stop being rescheduled."""
        return self.duration_seconds <= SHORT_MAX_SECONDS

    @property
    def hashtags(self) -> list[str]:
        return extract_hashtags(self.title, self.description)

    @property
    def chapters(self) -> list[tuple[str, str]]:
        return extract_chapters(self.description)

    @property
    def duration_display(self) -> str:
        return format_duration(self.duration_seconds)

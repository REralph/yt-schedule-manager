"""Tracks the user's watched "Shorts folder" for One Upload Page: files named
q<number>.<ext> (q1, q001, q127.mp4, ...) can be picked up without being dragged in.
Detection and uploading are deliberately separate -- finding a file only makes it
selectable, never uploads it by itself (see ui/widgets/basic_upload_panel.py).

There is no continuous folder monitoring here -- list_folder_videos() only ever scans when
explicitly called (selecting the folder, or Most Recent File), never on a timer.
list_folder_videos() raises OSError on failure rather than swallowing it into an empty
list, so a caller can show the user the *real* reason a folder didn't load instead of
silently pretending nothing's there. find_newest_recent_video() only ever returns a file
genuinely created or modified within the caller's detection window, and never falls back
to an older file -- the whole point is picking up the Short just saved, not whatever
happens to be sitting in the folder.

The registry is persisted so a q-number already uploaded doesn't reappear as new after
restarting the app, even though its file is still sitting in the folder. Leading zeros are
normalised away ("q001" and "q1" are the same q-number) so the same clip can't be detected
as two different entries depending on how it happened to be named.
"""
from __future__ import annotations

import datetime as dt
import os
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional

from data import safe_io

DATA_DIR = Path(__file__).resolve().parent
REGISTRY_PATH = DATA_DIR / "shorts_folder_registry.json"

_Q_NUMBER_RE = re.compile(r"^q0*(\d+)", re.IGNORECASE)


def parse_q_number(filename: str) -> Optional[str]:
    """Returns the file's normalised q-number ("127" for "q127.mp4" or "q0127.mp4"), or
    None if the filename doesn't start with q<digits>."""
    stem = Path(filename).stem
    m = _Q_NUMBER_RE.match(stem)
    return m.group(1) if m else None


def _file_touched_at(stat_result: os.stat_result) -> dt.datetime:
    """The more recent of a file's creation/modification moment, local tz-aware. Windows
    reports true file-creation time via st_ctime (unlike POSIX, where st_ctime is "last
    metadata change") -- using max(st_ctime, st_mtime) means a file that was created and
    then briefly re-saved still counts from whichever touch was more recent."""
    return dt.datetime.fromtimestamp(max(stat_result.st_ctime, stat_result.st_mtime)).astimezone()


@dataclass
class FolderVideoEntry:
    q_number: str
    filename: str
    path: str
    file_size: int
    touched_at: dt.datetime  # local tz-aware -- see _file_touched_at
    is_uploaded: bool  # from the registry -- False also covers "never seen before"


def list_folder_videos(
    folder_path: str, extensions: set[str], registry: "ShortsFolderRegistry",
) -> list[FolderVideoEntry]:
    """One on-demand scan (never continuous/background) of every q-numbered video file
    directly inside folder_path, sorted in ascending q-number order -- "q9, q10, q11", not
    the alphabetical "q10, q11, q9". Raises OSError if the folder can't be read (permission
    denied, a disconnected network drive, ...) rather than swallowing the failure into a
    quietly-empty list -- callers must show the user the real reason, never pretend the
    folder loaded when it didn't."""
    results: list[FolderVideoEntry] = []
    with os.scandir(folder_path) as it:  # OSError here is real and propagates to the caller
        for entry in it:
            if not entry.is_file():
                continue
            if Path(entry.name).suffix.lower() not in extensions:
                continue
            q_number = parse_q_number(entry.name)
            if q_number is None:
                continue
            stat = entry.stat()
            results.append(FolderVideoEntry(
                q_number=q_number, filename=entry.name, path=entry.path, file_size=stat.st_size,
                touched_at=_file_touched_at(stat), is_uploaded=registry.is_uploaded(q_number),
            ))
    results.sort(key=lambda e: int(e.q_number))
    return results


@dataclass
class CheckNewResult:
    q_number: str
    filename: str
    path: str
    file_size: int
    created_at: dt.datetime  # local tz-aware -- the more recent of the file's creation/modification time
    age_seconds: float


def pick_newest_recent(
    candidates: list[CheckNewResult], now: dt.datetime, window_seconds: int,
) -> Optional[CheckNewResult]:
    """The pure selection logic behind Most Recent File, split out from the filesystem
    scan so it can be tested with synthetic timestamps -- Windows doesn't let plain Python
    code backdate a real file's creation time, so exercising the "outside the window" and
    "which one is genuinely newest" branches needs fabricated CheckNewResult entries rather
    than real files on disk.

    Returns whichever candidate's age (now - created_at) is smallest, but only if that age
    is within window_seconds -- never the newest-of-what's-available if even the newest
    candidate is actually too old. Returns None rather than an older file when nothing
    qualifies, so a stale video can never get picked up by accident."""
    in_window = [c for c in candidates if 0 <= (now - c.created_at).total_seconds() <= window_seconds]
    if not in_window:
        return None
    return min(in_window, key=lambda c: (now - c.created_at).total_seconds())


def find_newest_recent_video(
    folder_path: str, extensions: set[str], registry: "ShortsFolderRegistry", window_seconds: int,
) -> Optional[CheckNewResult]:
    """Most Recent File's whole implementation, built on list_folder_videos(): whichever
    not-yet-uploaded q-numbered video file was created or modified most recently -- but
    ONLY if that moment falls within the last window_seconds (see pick_newest_recent).
    Returns None (rather than an older file, and rather than raising) both when nothing
    qualifies and when the folder can't be read at all -- callers that care about the
    difference should call list_folder_videos() themselves first."""
    now = dt.datetime.now().astimezone()
    try:
        entries = list_folder_videos(folder_path, extensions, registry)
    except OSError:
        return None
    candidates = [
        CheckNewResult(
            q_number=e.q_number, filename=e.filename, path=e.path, file_size=e.file_size,
            created_at=e.touched_at, age_seconds=(now - e.touched_at).total_seconds(),
        )
        for e in entries if not e.is_uploaded
    ]
    return pick_newest_recent(candidates, now, window_seconds)


@dataclass
class ShortsFolderEntry:
    q_number: str
    filename: str
    file_size: int
    status: str = "new"  # new | uploaded
    video_id: str = ""
    uploaded_at: str = ""  # ISO timestamp, empty until uploaded


@dataclass
class ShortsFolderRegistry:
    entries: dict[str, ShortsFolderEntry] = field(default_factory=dict)  # keyed by q_number

    def is_uploaded(self, q_number: str) -> bool:
        e = self.entries.get(q_number)
        return bool(e and e.status == "uploaded")

    def note_seen(self, q_number: str, filename: str, file_size: int) -> bool:
        """Records a detected file. Returns True if the registry actually changed (a
        brand new q-number, or the file behind an unprocessed one was swapped) and so
        needs saving -- an already-uploaded q-number is left alone even if the file is
        still sitting in the folder."""
        existing = self.entries.get(q_number)
        if existing is None:
            self.entries[q_number] = ShortsFolderEntry(q_number=q_number, filename=filename, file_size=file_size)
            return True
        if existing.status == "new" and (existing.filename != filename or existing.file_size != file_size):
            existing.filename = filename
            existing.file_size = file_size
            return True
        return False

    def mark_uploaded(self, q_number: str, video_id: str) -> None:
        entry = self.entries.get(q_number)
        if entry is None:
            return
        entry.status = "uploaded"
        entry.video_id = video_id
        entry.uploaded_at = dt.datetime.now(dt.timezone.utc).isoformat()
        self.save()

    def save(self) -> None:
        safe_io.atomic_write_json(REGISTRY_PATH, {"entries": {k: asdict(v) for k, v in self.entries.items()}})

    @classmethod
    def load(cls) -> "ShortsFolderRegistry":
        raw = safe_io.read_json_object(REGISTRY_PATH, "shorts_folder_registry.json")
        if raw is None:
            return cls()
        stored = raw.get("entries", {})
        entries = {}
        skipped = 0
        for k, v in (stored.items() if isinstance(stored, dict) else []):
            try:
                known = {f: v[f] for f in ShortsFolderEntry.__dataclass_fields__ if f in v}
                entries[str(k)] = ShortsFolderEntry(**known)
            except Exception:
                skipped += 1
        if skipped:
            from data import diagnostics
            diagnostics.report_data_problem("shorts_folder_registry.json", f"{skipped} unreadable entr{'y' if skipped == 1 else 'ies'} skipped")
        return cls(entries=entries)

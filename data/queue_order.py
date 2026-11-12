"""THE QUEUE ORDER REGISTRY: a permanent sequence number for every Short in the upload queue, stored
separately from its scheduled date/time.

Why it exists: a video's publish time can be changed by many things (a frequency change, Auto Boost, YouTube
Studio, a bug), so the publish time can never be the authority on what order the videos belong in. This file is.
Each video gets a sequence number once -- when its order is first recovered/adopted, or when it is uploaded -- and
that number is never changed or reused. Changing how many Shorts publish per day recalculates dates and times by
walking this sequence; it cannot move a video relative to another.

Only three things can ever write to it, and none of them can alter a number that already exists:
  adopt()           -- records a complete, already-validated order. Only possible once (an adopted registry is final).
  append()          -- a newly uploaded video goes to the end. Does nothing until an order has been adopted.
  insert_between()  -- places a video that was missing from the record, right after a named video.
Numbers are spaced SEQ_STEP apart so insert_between() always has room.

CODES: every sequence number has a short human-readable CODE -- 0001, 0002, ... 0232 -- worked out from the number itself, so it
is just as permanent. It is the code you see next to a video's title in the Upload history; it is never put on YouTube (the
title and description published are exactly what you typed). The ledger keeps the code beside the title and description, so
"which video is 0232, and what was its description?" can always be answered from inside the app.

The file also remembers WHY each video has its position (the evidence the recovery used), so the order can be audited.
"""
from __future__ import annotations

import dataclasses
import datetime as dt
import threading
from pathlib import Path
from typing import Iterable, Optional

from data import safe_io

DATA_DIR = Path(__file__).resolve().parent
QUEUE_ORDER_PATH = DATA_DIR / "queue_order.json"

SEQ_STEP = 1000
FILE_VERSION = 1

SOURCE_RECOVERED = "recovered"  # part of the order recovered and confirmed by the user
SOURCE_DECISION = "decision"  # the position was a conflict the user decided
SOURCE_UPLOAD = "upload"  # added to the end when it was uploaded
SOURCE_INSERTED = "inserted"  # was missing from the record; placed by the user

_SHARED_LOCK = threading.Lock()
_shared: Optional["QueueOrderStore"] = None


class QueueOrderError(RuntimeError):
    """An attempt to do something the registry never allows (re-adopting, renumbering...)."""


def format_code(seq: int) -> str:
    """The display code for a sequence number: 1000 -> '0001', 232000 -> '0232'. A video that was placed between two others
    (see insert_between) gets a fractional code that still sorts correctly: 45500 -> '0045.500'."""
    whole, rest = divmod(int(seq), SEQ_STEP)
    return f"{whole:04d}" if rest == 0 else f"{whole:04d}.{rest:03d}"


@dataclasses.dataclass(frozen=True)
class QueueRecord:
    video_id: str
    seq: int
    title: str = ""
    evidence: str = ""
    source: str = ""
    recorded_at: str = ""
    description: str = ""  # the video's description, kept beside its code (the ledger answers "which video is 0232?")

    @property
    def code(self) -> str:
        return format_code(self.seq)


def _record_from_dict(d: dict) -> QueueRecord:
    seq = d["seq"]
    if isinstance(seq, bool) or not isinstance(seq, int):
        raise ValueError("seq is not an integer")
    video_id = d["video_id"]
    if not isinstance(video_id, str) or not video_id:
        raise ValueError("video_id missing")
    return QueueRecord(
        video_id=video_id, seq=seq, title=str(d.get("title", "")), evidence=str(d.get("evidence", "")),
        source=str(d.get("source", "")), recorded_at=str(d.get("recorded_at", "")), description=str(d.get("description", "") or ""),
    )


class QueueOrderStore:
    def __init__(self, path: Optional[Path] = None):
        self._path = Path(path) if path else None  # None -> the module-level QUEUE_ORDER_PATH, read at call time (tests redirect it)
        self._lock = threading.RLock()
        self._records: dict[str, QueueRecord] = {}
        self.adopted_at: Optional[str] = None
        self.load_problems: list[str] = []  # duplicate sequence numbers / ids found in the file -- the order is then NOT trusted
        self._load()

    # ---- shared instance (every page and the upload flow see the same registry) ----

    @classmethod
    def shared(cls, reload: bool = False) -> "QueueOrderStore":
        global _shared
        with _SHARED_LOCK:
            if _shared is None:
                _shared = cls()
            elif reload:
                _shared._load()
            return _shared

    @classmethod
    def reset_shared(cls) -> None:
        """For tests that redirect the file path."""
        global _shared
        with _SHARED_LOCK:
            _shared = None

    # ---- reading ----

    @property
    def path(self) -> Path:
        return self._path or QUEUE_ORDER_PATH

    @property
    def is_adopted(self) -> bool:
        return self.adopted_at is not None

    @property
    def records(self) -> list[QueueRecord]:
        """Every record, in sequence order."""
        with self._lock:
            return sorted(self._records.values(), key=lambda r: r.seq)

    def has(self, video_id: str) -> bool:
        return video_id in self._records

    def get(self, video_id: str) -> Optional[QueueRecord]:
        return self._records.get(video_id)

    def seq_of(self, video_id: str) -> Optional[int]:
        r = self._records.get(video_id)
        return r.seq if r else None

    def code_of(self, video_id: str) -> str:
        """The display code of a video ('' if it has none)."""
        r = self._records.get(video_id)
        return r.code if r else ""

    def find_by_code(self, code: str) -> Optional[QueueRecord]:
        """The record with this code. Accepts '232', '0232' or '0232.500'."""
        wanted = str(code).strip()
        for r in self._records.values():
            if r.code == wanted:
                return r
        try:
            return next((r for r in self._records.values() if r.seq == int(float(wanted) * SEQ_STEP)), None)
        except ValueError:
            return None

    def next_code(self) -> str:
        """The code the next upload will get ('' until an order has been adopted)."""
        with self._lock:
            if self.adopted_at is None:
                return ""
            return format_code(self._next_whole_seq())

    def _next_whole_seq(self) -> int:
        last = max((r.seq for r in self._records.values()), default=0)
        return (last // SEQ_STEP + 1) * SEQ_STEP

    def ordered_ids(self) -> list[str]:
        return [r.video_id for r in self.records]

    def __len__(self) -> int:
        return len(self._records)

    def integrity_problems(self) -> list[str]:
        """Anything that makes the stored order untrustworthy: two videos sharing a sequence number, or
        problems found while reading the file. Empty when the registry is sound."""
        with self._lock:
            problems = list(self.load_problems)
            by_seq: dict[int, list[str]] = {}
            for r in self._records.values():
                by_seq.setdefault(r.seq, []).append(r.video_id)
            for seq, ids in sorted(by_seq.items()):
                if len(ids) > 1:
                    problems.append(f"Sequence number {seq} is shared by {len(ids)} videos ({', '.join(ids[:4])}): their order is ambiguous.")
            return problems

    # ---- writing (none of these can change an existing number) ----

    def adopt(self, items: Iterable[tuple[str, str, str, str]]) -> int:
        """Records a complete, validated order: `items` is (video_id, title, evidence, source) in sequence
        order. Allowed exactly once -- after that the registry is final and only append()/insert_between() add
        to it. Returns how many videos were recorded."""
        items = list(items)
        with self._lock:
            if self.adopted_at is not None or self._records:
                raise QueueOrderError("A queue order has already been recorded; it is permanent and cannot be replaced.")
            seen: set[str] = set()
            for vid, *_ in items:
                if not vid or vid in seen:
                    raise QueueOrderError(f"Cannot record the order: video {vid!r} is empty or appears more than once.")
                seen.add(vid)
            now = dt.datetime.now(dt.timezone.utc).isoformat()
            for n, (vid, title, evidence, source) in enumerate(items, start=1):
                self._records[vid] = QueueRecord(vid, n * SEQ_STEP, title, evidence, source or SOURCE_RECOVERED, now)
            self.adopted_at = now
            self._save()
            return len(items)

    def append(
        self, video_id: str, title: str = "", evidence: str = "", source: str = SOURCE_UPLOAD, description: str = "",
    ) -> Optional[QueueRecord]:
        """Puts a video at the END of the queue. Does nothing (returns None) before an order has been adopted --
        recovery will then find the video by its upload time like every other. A video that already has a number
        keeps it, untouched."""
        with self._lock:
            if self.adopted_at is None or not video_id:
                return None
            existing = self._records.get(video_id)
            if existing is not None:
                return existing
            rec = QueueRecord(video_id, self._next_whole_seq(), title, evidence, source, dt.datetime.now(dt.timezone.utc).isoformat(), description)
            self._records[video_id] = rec
            self._save()
            return rec

    def set_details(self, video_id: str, title: Optional[str] = None, description: Optional[str] = None) -> bool:
        """Fills in a video's title/description in the ledger. This is the ONE kind of change an existing record accepts, and it
        can never touch the sequence number (or therefore the code)."""
        with self._lock:
            r = self._records.get(video_id)
            if r is None:
                return False
            changes = {}
            if title is not None and title != r.title:
                changes["title"] = title
            if description is not None and description != r.description:
                changes["description"] = description
            if not changes:
                return False
            self._records[video_id] = dataclasses.replace(r, **changes)
            self._save()
            return True

    def backfill_details(self, videos) -> int:
        """Gives every recorded video that has no description yet the description YouTube holds for it (`videos`: Video objects).
        Returns how many records were filled in. Never changes a number or an existing description."""
        n = 0
        with self._lock:
            changed = False
            for v in videos:
                r = self._records.get(v.video_id)
                if r is not None and not r.description and getattr(v, "description", ""):
                    self._records[v.video_id] = dataclasses.replace(r, description=v.description, title=r.title or v.title)
                    n += 1
                    changed = True
            if changed:
                self._save()
        return n

    def insert_between(
        self, video_id: str, after_id: Optional[str], title: str = "", evidence: str = "", source: str = SOURCE_INSERTED,
    ) -> QueueRecord:
        """Places a video that had no record directly AFTER `after_id` (None = at the very start), without
        touching any other number."""
        with self._lock:
            if self.adopted_at is None:
                raise QueueOrderError("No queue order has been recorded yet.")
            if video_id in self._records:
                return self._records[video_id]
            ordered = self.records
            if after_id is None:
                low, nxt = 0, (ordered[0].seq if ordered else SEQ_STEP)
            else:
                if after_id not in self._records:
                    raise QueueOrderError(f"Cannot place the video after {after_id!r}: it has no sequence number.")
                low = self._records[after_id].seq
                later = [r.seq for r in ordered if r.seq > low]
                nxt = later[0] if later else low + 2 * SEQ_STEP
            seq = (low + nxt) // 2
            if seq <= low or seq >= nxt:
                raise QueueOrderError("There is no room between those two videos for another sequence number.")
            rec = QueueRecord(video_id, seq, title, evidence, source, dt.datetime.now(dt.timezone.utc).isoformat())
            self._records[video_id] = rec
            self._save()
            return rec

    # ---- persistence ----

    def _load(self) -> None:
        with self._lock:
            self._records = {}
            self.adopted_at = None
            self.load_problems = []
            raw = safe_io.read_json_object(self.path, "queue_order.json")
            if raw is None:
                return
            adopted = raw.get("adopted_at")
            self.adopted_at = adopted if isinstance(adopted, str) and adopted else None
            seen: set[str] = set()
            dupes: list[str] = []
            for rec in safe_io.parse_entries(raw.get("records", []), _record_from_dict, "queue_order.json"):
                if rec.video_id in seen:
                    dupes.append(rec.video_id)
                    continue  # the first record for a video wins; the repeat is reported, never silently merged
                seen.add(rec.video_id)
                self._records[rec.video_id] = rec
            if dupes:
                self.load_problems.append(f"{len(dupes)} video(s) were recorded more than once in the order file ({', '.join(dupes[:3])}); the first record of each was kept.")

    def _save(self) -> None:
        payload = {
            "version": FILE_VERSION,
            "adopted_at": self.adopted_at,
            "records": [{**dataclasses.asdict(r), "code": r.code} for r in sorted(self._records.values(), key=lambda r: r.seq)],  # "code" is derived from seq -- written so the file reads on its own, ignored when loading
        }
        safe_io.atomic_write_json(self.path, payload)

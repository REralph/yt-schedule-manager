"""One Upload Page's HISTORY (HIST-001): the PERMANENT record of every Short uploaded through
this page, and -- since the "Verify Upload Actually Reached YouTube" spec -- the source of truth
for whether each one was actually CONFIRMED on YouTube. This is NOT the same thing as
data/history.py's reschedule History/Undo trail.

The record is kept forever (it used to be deleted after 7 days, which is how the older uploads
were lost): every upload stays listed, in upload order, so the whole queue can be reviewed and
re-checked later (the History "Refresh" button -> youtube/record_checker.py re-verifies each one
against YouTube, and keeps each entry's current scheduled time in step with the real schedule).
A verified entry is therefore a snapshot, not a promise -- Refresh is what tells you whether a
video is STILL there.

An upload is never treated as successful just because the YouTube API's upload call
returned. Every entry moves through explicit stages, persisted after each transition so an
app restart can pick up where it left off (see ui/widgets/basic_upload_panel.py's
_resume_pending_verifications()):

    UPLOADING -> UPLOAD REQUEST COMPLETE -> VERIFYING -> VERIFIED
                                                      \\-> VERIFICATION FAILED
    UPLOADING -> UPLOAD FAILED                 (the request itself failed, and YouTube
                                                doesn't already have the video either)

"VERIFIED" here means specifically that YouTube itself confirmed it received and registered
the video (it exists, belongs to the authenticated channel, has the expected title, is in the
channel's uploads, and isn't in a failed/rejected/deleted state) -- see
youtube/upload_verifier.py. It says nothing about when the Short is scheduled to publish;
that is scheduled_at, a separate fact shown in the DATE/TIME columns.

"Resets visually each day" (spec) just means today_entries() only returns today's rows --
older entries are kept on disk (pruned after a week) but simply aren't "today" anymore.
"""
from __future__ import annotations

import datetime as dt
import threading
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional

from data import safe_io
from data.diagnostics import redact_secrets

DATA_DIR = Path(__file__).resolve().parent
UPLOAD_ACTIVITY_PATH = DATA_DIR / "upload_activity.json"

MAX_ENTRIES = 20000  # a safety bound only (at ~6 uploads a day that is nine years); the record is otherwise never pruned

STATUS_UPLOADING = "uploading"
STATUS_UPLOAD_COMPLETE = "upload_complete"  # UPLOAD REQUEST COMPLETE -- YouTube's API returned a video ID, but nothing has been confirmed yet
STATUS_VERIFYING = "verifying"
STATUS_VERIFIED = "verified"
STATUS_VERIFICATION_FAILED = "verification_failed"
STATUS_FAILED = "failed"  # UPLOAD FAILED
STATUS_MISSING = "missing"  # NOT ON YOUTUBE -- it was verified once, but a later re-check found it is no longer on the channel (deleted/removed)
STATUS_DONE = "done"  # LEGACY only: recorded before the verification layer existed, so it was never actually confirmed -- shown honestly as UNVERIFIED rather than promoted to VERIFIED

PENDING_STATUSES = (STATUS_UPLOADING, STATUS_UPLOAD_COMPLETE, STATUS_VERIFYING)

STATUS_LABELS = {
    STATUS_UPLOADING: "UPLOADING",
    STATUS_UPLOAD_COMPLETE: "VERIFYING",  # the instant between "request complete" and the first verification attempt -- to the user it's simply "verifying"
    STATUS_VERIFYING: "VERIFYING",
    STATUS_VERIFIED: "VERIFIED",
    STATUS_VERIFICATION_FAILED: "VERIFICATION FAILED",
    STATUS_FAILED: "UPLOAD FAILED",
    STATUS_MISSING: "NOT ON YOUTUBE",
    STATUS_DONE: "UNVERIFIED",
}


_SAVE_LOCK = threading.RLock()  # also guards every in-memory change, so a background re-check and the UI can't interleave
_SHARED_LOCK = threading.Lock()
_shared: Optional["UploadActivityLog"] = None


def _entry_from_dict(v: dict) -> "UploadActivityEntry":
    """One stored record -> an entry, or an exception (the loader then skips just that
    record). The timestamp is validated HERE: today_entries()/_prune() parse it on every
    render, so an unparseable one that got through would break the whole history section."""
    known = {f: v[f] for f in UploadActivityEntry.__dataclass_fields__ if f in v}
    dt.datetime.fromisoformat(known["uploaded_at"])
    if known.get("scheduled_at"):
        dt.datetime.fromisoformat(known["scheduled_at"])
    known["filename"] = str(known.get("filename", "(unknown)"))
    known["status"] = str(known.get("status", STATUS_DONE))
    for numeric in ("attempts", "verification_attempts"):
        if numeric in known:
            known[numeric] = int(known[numeric])
    return UploadActivityEntry(**known)


@dataclass
class UploadActivityEntry:
    filename: str
    uploaded_at: str  # ISO timestamp -- when the upload request actually ran (set when the Upload button is pressed, then updated to when the API upload completed); "time ago" is always relative to this, never to scheduled_at (which can be in the future)
    status: str  # one of the STATUS_* constants above
    title: str = ""  # the raw YouTube title (with hashtags) -- History strips them for display via youtube.models.strip_hashtags
    q_number: str = ""  # empty if this came from drag-and-drop rather than the watched folder
    scheduled_at: str = ""  # ISO timestamp -- when YouTube is supposed to PUBLISH it; empty until a slot is known. Distinct from uploaded_at ("Uploaded to YouTube") -- never confuse the two.
    schedule_label: str = "Scheduled"  # "Scheduled" or "Published" -- decided ONCE, at upload time, by comparing the confirmed publish time against "now" then; never recomputed later, so an entry doesn't relabel itself once its scheduled time elapses
    error: str = ""  # why the upload REQUEST failed (STATUS_FAILED only)
    attempts: int = 1  # automatic-retry (see basic_upload_panel._quick_upload) -- 2 if the first attempt failed and the second one is what actually finished it. Not shown as a History column (would break the one-row layout); surfaced in Developer Diagnostics instead.
    entry_id: str = field(default_factory=lambda: uuid.uuid4().hex)  # stable identity so a verification running in the background can find and update its own row
    video_id: str = ""  # the YouTube video ID, once known (from the upload response, or found on YouTube after an apparently-failed attempt)
    verification_attempts: int = 0  # cumulative across every verification run for this entry, including RETRY VERIFICATION and post-restart resumes
    last_verification_at: str = ""  # ISO timestamp of the most recent verification attempt
    verification_detail: str = ""  # the most recent verification result: what YouTube reported, or the API error hit
    order_code: str = ""  # the video's permanent place in the queue, e.g. "0232" (see data/queue_order.py). Stays in the app -- never published to YouTube -- and is kept here for good so the History always shows which video was which, in order.

    @property
    def display_status(self) -> str:
        return STATUS_LABELS.get(self.status, self.status.upper())

    @property
    def is_pending(self) -> bool:
        return self.status in PENDING_STATUSES

    def _ledger_description(self) -> str:
        """The description kept beside this video's code in the order ledger (data/queue_order.py), if it has one."""
        if not self.video_id:
            return ""
        try:
            from data.queue_order import QueueOrderStore
            rec = QueueOrderStore.shared().get(self.video_id)
            return (rec.description if rec else "").strip()
        except Exception:
            return ""  # a report must never fail because the ledger could not be read

    def format_report(self) -> str:
        """A self-contained technical report for THIS entry only (COPY UPLOAD REPORT) --
        never any other history entry. Contains no credentials or tokens; every field here
        is plain upload/verification metadata."""
        lines = [
            "UPLOAD REPORT",
            f"Local filename: {self.filename}",
            f"Order code: {self.order_code or '(none)'}",
            f"Title: {self.title or '(none)'}",
            f"Description: {self._ledger_description() or '(not recorded)'}",
            f"YouTube video ID: {self.video_id or '(not available)'}",
            f"Current upload state: {self.display_status} ({self.status})",
            f"Upload timestamp: {self.uploaded_at}",
            f"Scheduled to publish: {self.scheduled_at or '(not known)'}",
            f"Upload attempts: {self.attempts}",
            f"Verification attempts: {self.verification_attempts}",
            f"Last verification attempt: {self.last_verification_at or '(none yet)'}",
            f"Verification result / API response: {self.verification_detail or '(none)'}",
        ]
        if self.error:
            lines.append(f"Upload error: {self.error}")
        return redact_secrets("\n".join(lines))


@dataclass
class UploadActivityLog:
    entries: list[UploadActivityEntry] = field(default_factory=list)

    @classmethod
    def shared(cls, reload: bool = False) -> "UploadActivityLog":
        """The ONE log every page uses. Several pages each holding their own copy would
        overwrite each other's changes (each save writes its whole in-memory list), so the
        Upload page, the reschedule pages and the re-check all go through this instance.
        `reload=True` re-reads the file INTO the existing object (so other holders keep
        pointing at the live data) -- the Upload page does that once when it is built."""
        global _shared
        with _SHARED_LOCK:
            if _shared is None:
                _shared = cls.load()
            elif reload:
                _shared.entries[:] = cls.load().entries
            return _shared

    def record(self, entry: UploadActivityEntry) -> UploadActivityEntry:
        with _SAVE_LOCK:
            self.entries.append(entry)
            self._prune()
            self.save()
        return entry

    def update(self, entry: UploadActivityEntry, **fields) -> None:
        """Changes fields on an entry already in this log and persists immediately -- so
        every stage transition (and every verification attempt) survives an app restart."""
        self.update_many([(entry, fields)])

    def update_many(self, updates: list) -> None:
        """Applies many (entry, {field: value}) changes and saves ONCE -- a re-check of the
        whole record touches dozens of entries."""
        with _SAVE_LOCK:
            for entry, fields in updates:
                for name in fields:
                    if name not in UploadActivityEntry.__dataclass_fields__:
                        raise AttributeError(f"UploadActivityEntry has no field {name!r}")
            for entry, fields in updates:
                for name, value in fields.items():
                    setattr(entry, name, value)
            self.save()

    def backfill_order_codes(self, registry) -> int:
        """Writes each recorded upload's code into its history entry (entries made before codes existed, or before the order was
        recorded). Only fills an empty code, never replaces one. `registry` is data/queue_order.QueueOrderStore. Returns how many."""
        with _SAVE_LOCK:
            updates = []
            for e in list(self.entries):
                if e.video_id and not e.order_code:
                    code = registry.code_of(e.video_id)
                    if code:
                        updates.append((e, {"order_code": code}))
            if updates:
                self.update_many(updates)
            return len(updates)

    def newest_first(self) -> list[UploadActivityEntry]:
        """The whole record, most recently uploaded first."""
        return sorted(list(self.entries), key=lambda e: e.uploaded_at, reverse=True)

    def find_by_video_id(self, video_id: str) -> Optional[UploadActivityEntry]:
        return next((e for e in list(self.entries) if video_id and e.video_id == video_id), None)

    def get(self, entry_id: str) -> Optional[UploadActivityEntry]:
        return next((e for e in self.entries if e.entry_id == entry_id), None)

    def pending_verification(self) -> list[UploadActivityEntry]:
        """Entries an app restart interrupted somewhere before VERIFIED/VERIFICATION
        FAILED/UPLOAD FAILED -- see basic_upload_panel._resume_pending_verifications()."""
        return [e for e in self.entries if e.is_pending]

    def known_video_ids(self) -> set[str]:
        """Every YouTube video ID this log already accounts for -- used so that looking a
        video up by title after an apparently-failed upload can never attach a DIFFERENT
        entry's video to this one just because two titles happen to match."""
        return {e.video_id for e in self.entries if e.video_id}

    def today_entries(self) -> list[UploadActivityEntry]:
        today = dt.date.today()
        return sorted(
            (e for e in self.entries if dt.datetime.fromisoformat(e.uploaded_at).astimezone().date() == today),
            key=lambda e: e.uploaded_at,
        )

    def _prune(self) -> None:
        """Only a size bound -- nothing is ever deleted for being old. If the (very large)
        bound is passed, the oldest VERIFIED entries go first."""
        overflow = len(self.entries) - MAX_ENTRIES
        if overflow <= 0:
            return
        by_age = sorted(self.entries, key=lambda e: e.uploaded_at)
        expendable = [e for e in by_age if e.status == STATUS_VERIFIED][:overflow]
        if len(expendable) < overflow:
            expendable += [e for e in by_age if e not in expendable][: overflow - len(expendable)]
        gone = {id(e) for e in expendable}
        self.entries[:] = [e for e in self.entries if id(e) not in gone]

    def save(self) -> None:
        with _SAVE_LOCK:
            safe_io.atomic_write_json(UPLOAD_ACTIVITY_PATH, {"entries": [asdict(e) for e in list(self.entries)]})

    @classmethod
    def load(cls) -> "UploadActivityLog":
        raw = safe_io.read_json_object(UPLOAD_ACTIVITY_PATH, "upload_activity.json")
        if raw is None:
            return cls()
        return cls(entries=safe_io.parse_entries(raw.get("entries", []), _entry_from_dict, "upload_activity.json"))

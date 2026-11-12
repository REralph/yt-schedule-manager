"""RE-CHECK THE UPLOAD RECORD (History -> Refresh).

Verification at upload time (youtube/upload_verifier.py) proves a video reached YouTube at that
moment -- it says nothing about tomorrow. Videos get deleted, rejected after processing, or
rescheduled by hand in YouTube Studio, and until now the record kept showing the old VERIFIED
tick (a video that had been deleted from the channel was still marked VERIFIED). This module
compares the whole record against what YouTube reports NOW, in a handful of batched calls (50
videos per request), and works out what each entry should say:

  * still on the channel and healthy        -> VERIFIED, and its scheduled time is brought in
                                               step with the real one (so a Studio edit or a
                                               reschedule shows up in the record)
  * not on YouTube any more                 -> NOT ON YOUTUBE (it keeps its place in the record)
  * YouTube says it failed / was rejected   -> VERIFICATION FAILED, with YouTube's reason
  * belongs to a different channel          -> VERIFICATION FAILED
  * no video ID recorded (an interrupted    -> looked up among the channel's newest uploads by
    upload, an old record, a failed upload)    title + scheduled time; found => the ID is attached
                                               (so an upload that "failed" but actually went
                                               through is recovered, never duplicated)

Nothing here writes to YouTube, and nothing is changed if YouTube couldn't be reached: an API or
network error never downgrades an entry -- "I couldn't check" is not "it's gone". The function
only PLANS the changes (a list of (entry, fields)); the caller applies them in one save.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from typing import Iterable, Optional

from data.upload_activity import (
    PENDING_STATUSES,
    STATUS_DONE,
    STATUS_FAILED,
    STATUS_MISSING,
    STATUS_VERIFICATION_FAILED,
    STATUS_VERIFIED,
    UploadActivityEntry,
)
from youtube.client import FAILED_UPLOAD_STATUSES, _normalize_title
from youtube.errors import classify_exception

SCAN_NEWEST_UPLOADS = 300  # how many of the channel's newest uploads an ID-less entry is searched for among
SCHEDULE_TOLERANCE_SECONDS = 60


@dataclass
class RecordCheckSummary:
    checked: int = 0
    verified: int = 0
    missing: list[str] = field(default_factory=list)  # filenames now NOT ON YOUTUBE
    failed: list[str] = field(default_factory=list)  # filenames YouTube reports a problem with
    problems: list[str] = field(default_factory=list)  # other things worth a look, e.g. "q3.mov: private with no publish time"
    rescheduled: int = 0  # entries whose recorded time was out of step with YouTube's and was corrected
    recovered: int = 0  # entries that had no video ID and were found on the channel
    retitled: int = 0
    unmatched: list[str] = field(default_factory=list)  # ID-less entries that could not be found (left unchanged)
    skipped: int = 0  # still uploading / being verified -- not touched
    error: str = ""  # set => YouTube couldn't be reached and NOTHING was changed

    @property
    def all_good(self) -> bool:
        return not (self.error or self.missing or self.failed or self.problems)

    def text(self) -> str:
        if self.error:
            return f"Couldn't check: {self.error}"
        parts = [f"Checked {self.checked} upload{'s' if self.checked != 1 else ''}: {self.verified} on YouTube"]
        if self.missing:
            parts.append(f"{len(self.missing)} NOT ON YOUTUBE ({_names(self.missing)})")
        if self.failed:
            parts.append(f"{len(self.failed)} with a problem ({_names(self.failed)})")
        if self.problems:
            parts.append(f"{len(self.problems)} to look at ({_names([p.split(':')[0] for p in self.problems])})")
        if self.rescheduled:
            parts.append(f"{self.rescheduled} schedule time{'s' if self.rescheduled != 1 else ''} updated")
        if self.recovered:
            parts.append(f"{self.recovered} found on YouTube")
        if self.unmatched:
            parts.append(f"{len(self.unmatched)} couldn't be matched")
        return " — ".join(parts)


def _names(names: list[str], limit: int = 3) -> str:
    shown = ", ".join(names[:limit])
    return shown + (f" +{len(names) - limit} more" if len(names) > limit else "")


def _parse_when(value: Optional[str]) -> Optional[dt.datetime]:
    if not value:
        return None
    try:
        return dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _same_moment(a: Optional[dt.datetime], b: Optional[dt.datetime]) -> bool:
    return a is not None and b is not None and abs((a.astimezone(dt.timezone.utc) - b.astimezone(dt.timezone.utc)).total_seconds()) <= SCHEDULE_TOLERANCE_SECONDS


def _describe(item: dict) -> str:
    status = item.get("status", {})
    publish_at = _parse_when(status.get("publishAt"))
    when = f", scheduled for {publish_at.astimezone():%d %b %Y %H:%M}" if publish_at else ""
    return f"{status.get('privacyStatus', 'unknown')}{when}, {status.get('uploadStatus', 'unknown')}"


def _fields_for_found(entry: UploadActivityEntry, item: dict, channel_id: str, now_iso: str, summary: RecordCheckSummary) -> dict:
    """The new field values for an entry whose video YouTube still has."""
    status = item.get("status", {})
    snippet = item.get("snippet", {})
    fields: dict = {"last_verification_at": now_iso}

    if snippet.get("channelId") and channel_id and snippet["channelId"] != channel_id:
        fields.update(status=STATUS_VERIFICATION_FAILED, verification_detail=f"Re-checked: this video belongs to a different channel ({snippet['channelId']}).")
        summary.failed.append(entry.filename)
        return fields

    upload_status = status.get("uploadStatus", "")
    if upload_status in FAILED_UPLOAD_STATUSES:
        reason = status.get("failureReason") or status.get("rejectionReason") or ""
        fields.update(status=STATUS_VERIFICATION_FAILED, verification_detail=f"Re-checked: YouTube reports uploadStatus={upload_status}" + (f" ({reason})" if reason else "") + ".")
        summary.failed.append(entry.filename)
        return fields

    # ---- it is on the channel and healthy
    fields["status"] = STATUS_VERIFIED
    if entry.error:
        fields["error"] = ""
    notes = []

    publish_at = _parse_when(status.get("publishAt"))
    privacy = status.get("privacyStatus", "")
    if publish_at is not None:
        recorded = _parse_when(entry.scheduled_at)
        if not _same_moment(recorded, publish_at):
            fields["scheduled_at"] = publish_at.isoformat()
            summary.rescheduled += 1
            notes.append(f"scheduled time updated from {recorded.astimezone():%d %b %H:%M}" if recorded else "scheduled time recorded")
        if entry.schedule_label != "Scheduled":
            fields["schedule_label"] = "Scheduled"
    elif privacy == "public":
        published_at = _parse_when(snippet.get("publishedAt"))
        if published_at is not None and not _same_moment(_parse_when(entry.scheduled_at), published_at):
            fields["scheduled_at"] = published_at.isoformat()
        fields["schedule_label"] = "Published"
    elif privacy == "private":
        summary.problems.append(f"{entry.filename}: it is private with NO publish time, so it will never go live")
        notes.append("WARNING: private with no publish time")

    title = snippet.get("title", "")
    if title and _normalize_title(title) != _normalize_title(entry.title):
        fields["title"] = title
        summary.retitled += 1
        notes.append("title changed on YouTube")

    fields["verification_detail"] = f"Re-checked: on YouTube ({_describe(item)})" + (f" -- {'; '.join(notes)}" if notes else "") + "."
    summary.verified += 1
    return fields


def check_records(
    client, entries: Iterable[UploadActivityEntry], *, skip_ids: Iterable[str] = (), scan_newest: int = SCAN_NEWEST_UPLOADS,
    now: Optional[dt.datetime] = None,
) -> tuple[list[tuple[UploadActivityEntry, dict]], RecordCheckSummary]:
    """Plans the re-check. Returns ([(entry, new_fields), ...], summary). Raises nothing for an
    API/network problem: that comes back as summary.error with an empty plan."""
    summary = RecordCheckSummary()
    now_iso = (now or dt.datetime.now(dt.timezone.utc)).isoformat()
    skip = set(skip_ids)
    entries = list(entries)

    candidates: list[UploadActivityEntry] = []
    for e in entries:
        if e.entry_id in skip or e.status in PENDING_STATUSES:
            summary.skipped += 1
        else:
            candidates.append(e)
    with_id = [e for e in candidates if e.video_id]
    without_id = sorted((e for e in candidates if not e.video_id), key=lambda e: e.uploaded_at)

    plan: list[tuple[UploadActivityEntry, dict]] = []
    try:
        channel_id = client.get_authenticated_channel_id()
        items = client.get_video_check_items([e.video_id for e in with_id])

        scanned: dict[str, dict] = {}
        scan_order: list[str] = []
        if without_id:
            scan_order = client.list_recent_uploaded_video_ids(scan_newest)
            scanned = client.get_video_check_items(scan_order)
    except Exception as e:  # noqa: BLE001 -- any API/network failure means "couldn't check", never "it's gone"
        info = classify_exception(e)
        summary.error = info.message()
        return [], summary

    summary.checked = len(with_id)

    for e in with_id:
        item = items.get(e.video_id)
        if item is None:
            plan.append((e, {
                "status": STATUS_MISSING, "last_verification_at": now_iso,
                "verification_detail": f"Re-checked {now_iso[:16].replace('T', ' ')}: this video is no longer on the channel -- it was deleted or removed.",
            }))
            summary.missing.append(e.filename)
        else:
            plan.append((e, _fields_for_found(e, item, channel_id, now_iso, summary)))

    # ---- entries with no video ID: find them among the channel's newest uploads
    claimed = {e.video_id for e in entries if e.video_id}
    for e in without_id:
        summary.checked += 1
        title = _normalize_title(e.title)
        scheduled = _parse_when(e.scheduled_at)
        pool = [i for i in scan_order if i not in claimed and i in scanned and _normalize_title(scanned[i].get("snippet", {}).get("title", "")) == title]
        exact = [i for i in pool if _same_moment(scheduled, _parse_when(scanned[i].get("status", {}).get("publishAt")))]
        # An upload that FAILED must match the exact slot it was aimed at -- a same-titled
        # video elsewhere is a different upload. Other entries may fall back to the newest match.
        chosen = exact[0] if exact else (pool[0] if pool and e.status != STATUS_FAILED else None)
        if chosen is None:
            if e.status != STATUS_FAILED:
                summary.unmatched.append(e.filename)
            continue
        claimed.add(chosen)
        fields = _fields_for_found(e, scanned[chosen], channel_id, now_iso, summary)
        fields["video_id"] = chosen
        if fields["status"] == STATUS_VERIFIED:
            summary.recovered += 1
            fields["verification_detail"] = "Found on the channel by title and re-checked: " + fields["verification_detail"].removeprefix("Re-checked: ")
        plan.append((e, fields))

    return plan, summary

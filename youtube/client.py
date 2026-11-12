"""Thin wrapper over the YouTube Data API v3: read scheduled videos, write publish times.

Every write goes through update_publish_time_with_status() (update_publish_time() is a
convenience wrapper that fetches the status for you first), which always PUTs back the
full `status` object with only publishAt changed -- videos.update replaces the whole
part you send, so sending a partial status object would silently reset fields like
privacyStatus/embeddable/selfDeclaredMadeForKids to defaults.
"""
from __future__ import annotations

import datetime as dt
import threading
import unicodedata
from dataclasses import dataclass
from typing import Callable, Iterable, Optional

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaFileUpload

from youtube.models import Video, parse_iso8601_duration
from youtube.quota import (
    COST_PLAYLIST_ITEMS_INSERT,
    COST_PLAYLIST_ITEMS_LIST,
    COST_PLAYLISTS_LIST,
    COST_SEARCH_LIST,
    COST_VIDEOS_INSERT,
    COST_VIDEOS_LIST,
    COST_VIDEOS_UPDATE,
    QuotaTracker,
)

# Every upload from this app is a Battlefield V Short -- there's no in-app content that
# would ever warrant a different YouTube category, and categoryId is a required field on
# videos.insert, so this is a fixed constant rather than a user-facing setting.
UPLOAD_CATEGORY_ID = "20"  # Gaming


@dataclass
class Playlist:
    playlist_id: str
    title: str


# uploadStatus values that mean YouTube did NOT successfully take the video in -- a
# definitive failure, not something waiting longer can fix.
FAILED_UPLOAD_STATUSES = ("failed", "rejected", "deleted")


@dataclass
class VideoVerification:
    """What YouTube itself reports about one video ID -- see YouTubeClient.
    check_video_registered(). Nothing here is inferred from the upload call's own response."""

    exists: bool = False  # videos.list returned the video at all
    channel_matches: bool = False  # it belongs to the authenticated channel
    title_matches: bool = False  # its title is the one this app uploaded
    in_uploads_playlist: bool = False  # it also shows up via a second, independent endpoint (the channel's uploads playlist)
    upload_status: str = ""  # status.uploadStatus: uploaded | processed | failed | rejected | deleted
    failure_reason: str = ""
    rejection_reason: str = ""
    privacy_status: str = ""
    publish_at: Optional[dt.datetime] = None
    found_title: str = ""
    channel_id: str = ""

    @property
    def verified(self) -> bool:
        return (
            self.exists and self.channel_matches and self.title_matches and self.in_uploads_playlist
            and self.upload_status not in FAILED_UPLOAD_STATUSES
        )

    @property
    def definitive_failure(self) -> bool:
        """True when waiting and re-checking can't change the answer: YouTube reports the
        video failed/was rejected/was deleted, belongs to a different channel, or carries a
        different title. (Not-found and not-yet-in-the-uploads-playlist are NOT definitive
        -- those are exactly the propagation delays a retry exists for.)"""
        if not self.exists:
            return False
        return (
            self.upload_status in FAILED_UPLOAD_STATUSES
            or not self.channel_matches
            or not self.title_matches
        )

    def describe(self) -> str:
        if not self.exists:
            return "YouTube does not list this video yet."
        parts = [
            "exists=yes",
            f"channel={'matches' if self.channel_matches else 'DIFFERENT (' + self.channel_id + ')'}",
            f"title={'matches' if self.title_matches else 'DIFFERENT (YouTube has: ' + repr(self.found_title) + ')'}",
            f"in uploads playlist={'yes' if self.in_uploads_playlist else 'NOT YET'}",
            f"uploadStatus={self.upload_status or 'unknown'}",
            f"privacyStatus={self.privacy_status or 'unknown'}",
        ]
        if self.publish_at is not None:
            parts.append(f"publishAt={self.publish_at.isoformat()}")
        if self.failure_reason:
            parts.append(f"failureReason={self.failure_reason}")
        if self.rejection_reason:
            parts.append(f"rejectionReason={self.rejection_reason}")
        return ", ".join(parts)


def _normalize_title(title: str) -> str:
    return " ".join(unicodedata.normalize("NFC", title or "").split())

API_SERVICE_NAME = "youtube"
API_VERSION = "v3"

BATCH_SIZE = 50
UPLOAD_CHUNK_RETRIES = 3  # googleapiclient's own exponential-backoff retry for a single resumable-upload chunk


class VideoNotModifiableError(RuntimeError):
    """The API rejected an update for a video that can no longer be rescheduled."""

    def __init__(self, video_id: str, reason: str, message: str):
        self.video_id = video_id
        self.reason = reason
        super().__init__(message)


def _parse_publish_at(value: Optional[str]) -> Optional[dt.datetime]:
    if not value:
        return None
    # API returns RFC3339 like 2026-11-25T09:00:00Z
    return dt.datetime.fromisoformat(value.replace("Z", "+00:00"))


def _format_publish_at(value: dt.datetime) -> str:
    if value.tzinfo is None:
        raise ValueError("publish_at datetime must be timezone-aware")
    utc = value.astimezone(dt.timezone.utc)
    return utc.strftime("%Y-%m-%dT%H:%M:%SZ")


def _video_from_api_item(item: dict) -> Video:
    snippet = item.get("snippet", {})
    status = item.get("status", {})
    content_details = item.get("contentDetails", {})
    statistics = item.get("statistics", {})
    thumbnails = snippet.get("thumbnails", {})
    thumb = (
        thumbnails.get("medium")
        or thumbnails.get("high")
        or thumbnails.get("default")
        or {}
    )
    return Video(
        video_id=item["id"],
        title=snippet.get("title", ""),
        description=snippet.get("description", ""),
        thumbnail_url=thumb.get("url", ""),
        duration_seconds=parse_iso8601_duration(content_details.get("duration", "")),
        tags=list(snippet.get("tags", []) or []),
        privacy_status=status.get("privacyStatus", "private"),
        publish_at=_parse_publish_at(status.get("publishAt")),
        upload_status=status.get("uploadStatus", "uploaded"),
        raw_status=dict(status),
        view_count=int(statistics["viewCount"]) if "viewCount" in statistics else None,
        published_at=_parse_publish_at(snippet.get("publishedAt")),
    )


class YouTubeClient:
    def __init__(self, credentials: Credentials):
        self._credentials = credentials
        self._thread_local = threading.local()
        self.quota = QuotaTracker.load()
        # A channel's own ID and its uploads playlist ID never change, so they're looked up
        # once and reused (upload verification needs both on every attempt).
        self._channel_id: Optional[str] = None
        self._uploads_playlist_id: Optional[str] = None

    @property
    def _service(self):
        """One googleapiclient service PER THREAD. Its underlying httplib2.Http -- and the
        TLS connection it keeps open between calls -- is not thread-safe, and this app calls
        the API from many threads at once (uploads, schedule refreshes, playlist loads, and
        now upload verification). Sharing a single connection across them lets requests
        interleave on the same socket, which surfaces as random `[SSL: WRONG_VERSION_NUMBER]`
        and read-timeout errors. A per-thread service means concurrent calls never touch the
        same connection; building one is cheap (googleapiclient uses its bundled discovery
        document, no network)."""
        service = getattr(self._thread_local, "service", None)
        if service is None:
            service = build(API_SERVICE_NAME, API_VERSION, credentials=self._credentials)
            self._thread_local.service = service
        return service

    @_service.setter
    def _service(self, value) -> None:
        self._thread_local.service = value

    # ---- reading ------------------------------------------------------

    def _load_channel_info(self) -> None:
        resp = (
            self._service.channels()
            .list(part="id,contentDetails", mine=True)
            .execute()
        )
        self.quota.record(COST_VIDEOS_LIST)
        items = resp.get("items", [])
        if not items:
            raise RuntimeError("No channel found for the authenticated account.")
        self._channel_id = items[0]["id"]
        self._uploads_playlist_id = items[0]["contentDetails"]["relatedPlaylists"]["uploads"]

    def get_uploads_playlist_id(self) -> str:
        if not self._uploads_playlist_id:
            self._load_channel_info()
        return self._uploads_playlist_id

    def get_authenticated_channel_id(self) -> str:
        if not self._channel_id:
            self._load_channel_info()
        return self._channel_id

    def list_all_uploaded_video_ids(self) -> list[str]:
        """All video IDs in the channel's uploads playlist (owner-authenticated, so this
        includes private/scheduled videos, not just public ones)."""
        playlist_id = self.get_uploads_playlist_id()
        video_ids: list[str] = []
        page_token = None
        while True:
            resp = (
                self._service.playlistItems()
                .list(
                    part="contentDetails",
                    playlistId=playlist_id,
                    maxResults=50,
                    pageToken=page_token,
                )
                .execute()
            )
            self.quota.record(COST_PLAYLIST_ITEMS_LIST)
            for item in resp.get("items", []):
                video_ids.append(item["contentDetails"]["videoId"])
            page_token = resp.get("nextPageToken")
            if not page_token:
                break
        return video_ids

    def list_all_video_ids_via_search(self) -> list[str]:
        """Fallback path if the uploads playlist ever proves to omit scheduled videos
        for this account. Costs 100 quota units per page."""
        video_ids: list[str] = []
        page_token = None
        while True:
            resp = (
                self._service.search()
                .list(
                    part="id",
                    forMine=True,
                    type="video",
                    order="date",
                    maxResults=50,
                    pageToken=page_token,
                )
                .execute()
            )
            self.quota.record(COST_SEARCH_LIST)
            for item in resp.get("items", []):
                vid = item.get("id", {}).get("videoId")
                if vid:
                    video_ids.append(vid)
            page_token = resp.get("nextPageToken")
            if not page_token:
                break
        return video_ids

    def get_videos(self, video_ids: Iterable[str]) -> list[Video]:
        """Fetches full details for the given video IDs, batched 50 at a time. Always
        includes statistics (view count) -- requesting an extra part doesn't change
        videos.list's quota cost (a flat 1 unit/call), and Auto Schedule needs it."""
        ids = list(video_ids)
        videos: list[Video] = []
        for i in range(0, len(ids), BATCH_SIZE):
            chunk = ids[i : i + BATCH_SIZE]
            resp = (
                self._service.videos()
                .list(part="snippet,contentDetails,status,statistics", id=",".join(chunk))
                .execute()
            )
            self.quota.record(COST_VIDEOS_LIST)
            for item in resp.get("items", []):
                videos.append(_video_from_api_item(item))
        return videos

    def discover_recent_video_ids(self, pages: int) -> list[str]:
        """The channel's newest `pages` x 50 video IDs (private/scheduled ones included), newest
        first, via search.list -- 100 quota units a page, so callers use it sparingly (see
        youtube/video_repo.py's discovery schedule).

        Why this exists: the uploads playlist -- what list_all_uploaded_video_ids() reads -- is
        NOT a reliable list on this channel. YouTube returns its entries at distinct positions
        but with the wrong video at many of them (1,196 entries holding only ~1,000 distinct
        videos, a different subset depending on page size), so a large share of scheduled videos
        never appear in it: measured against this search, the app saw 103 of 228 scheduled
        videos. Search does not have that problem."""
        found: list[str] = []
        page_token = None
        for _ in range(max(1, pages)):
            resp = (
                self._service.search()
                .list(part="id", forMine=True, type="video", order="date", maxResults=50, pageToken=page_token)
                .execute()
            )
            self.quota.record(COST_SEARCH_LIST)
            found.extend(i["id"]["videoId"] for i in resp.get("items", []) if i.get("id", {}).get("videoId"))
            page_token = resp.get("nextPageToken")
            if not page_token:
                break
        return found

    def get_scheduled_videos(self, use_uploads_playlist: bool = True, extra_ids: Iterable[str] = ()) -> list[Video]:
        """Every video currently scheduled to publish. The IDs come from the uploads playlist
        (or search) PLUS `extra_ids` -- videos the app already knows about (see
        youtube/video_repo.py) -- because the playlist listing alone drops many scheduled videos
        (see discover_recent_video_ids). Each ID is asked for once, however many sources named it,
        and a video that is no longer scheduled (published, deleted) simply isn't returned."""
        ids = (
            self.list_all_uploaded_video_ids()
            if use_uploads_playlist
            else self.list_all_video_ids_via_search()
        )
        videos = self.get_videos(list(dict.fromkeys([*ids, *extra_ids])))
        return [v for v in videos if v.is_scheduled]

    def get_unscheduled_videos(self, use_uploads_playlist: bool = True) -> list[Video]:
        """Videos already uploaded to the channel but with no publish time set yet --
        the "ready" pool HIT and Upload on Specific Day draw from. Private and no
        publishAt (as opposed to is_scheduled's private-and-has-publishAt)."""
        ids = (
            self.list_all_uploaded_video_ids()
            if use_uploads_playlist
            else self.list_all_video_ids_via_search()
        )
        videos = self.get_videos(ids)
        return [v for v in videos if v.publish_at is None and v.privacy_status == "private"]

    def get_recent_published_videos(self, limit: int = 60, use_uploads_playlist: bool = True) -> list[Video]:
        """Already-public (live) Shorts, most recently published first -- what Auto
        Schedule evaluates for real performance and its automatic views-per-Short
        estimate. The uploads playlist is in upload order, so the tail of the full ID
        list is where the most recently published Shorts are; only that tail's full
        details get fetched (not the whole channel), so this stays cheap even on a
        channel with hundreds of Shorts. Over-fetches the tail a bit since some of the
        most recent uploads may still be private/scheduled rather than live yet."""
        ids = (
            self.list_all_uploaded_video_ids()
            if use_uploads_playlist
            else self.list_all_video_ids_via_search()
        )
        candidate_ids = ids[-(limit * 2) :]
        videos = self.get_videos(candidate_ids)
        published = [v for v in videos if v.privacy_status == "public" and v.published_at is not None]
        published.sort(key=lambda v: v.published_at, reverse=True)
        return published[:limit]

    # ---- writing --------------------------------------------------------

    def update_publish_time(self, video_id: str, new_publish_at: dt.datetime) -> Video:
        """Reschedules a single video, re-fetching its current status first. Convenient
        for one-off changes (e.g. Undo); for a bulk batch, prefer prefetching all videos
        once via get_videos() and calling update_publish_time_with_status() per video --
        this method alone would cost 1 extra quota-unit fetch *per video* in a batch."""
        current = self.get_videos([video_id])
        if not current:
            raise VideoNotModifiableError(video_id, "notFound", f"Video {video_id} not found.")
        return self.update_publish_time_with_status(video_id, current[0].raw_status, new_publish_at)

    def update_publish_time_with_status(
        self, video_id: str, raw_status: dict, new_publish_at: dt.datetime
    ) -> Video:
        """Reschedules a video given an already-fetched `status` object (see
        Video.raw_status), so bulk callers can prefetch every selected video's status
        once via get_videos() instead of re-fetching before each individual update.

        Re-reads (via the caller-supplied raw_status) and PUTs back the *entire* status
        object with only publishAt changed -- videos.update replaces the whole part you
        send, so a partial status object would silently reset fields like
        privacyStatus/embeddable/selfDeclaredMadeForKids to defaults.
        """
        new_status = dict(raw_status)
        new_status["publishAt"] = _format_publish_at(new_publish_at)
        new_status["privacyStatus"] = "private"  # required for publishAt to take effect

        try:
            resp = (
                self._service.videos()
                .update(part="status", body={"id": video_id, "status": new_status})
                .execute()
            )
            self.quota.record(COST_VIDEOS_UPDATE)
        except HttpError as e:
            reason = _extract_error_reason(e)
            raise VideoNotModifiableError(video_id, reason, str(e)) from e

        return _video_from_api_item(resp)

    def clear_publish_time(self, video_id: str, raw_status: dict) -> Video:
        """Reverts a video to unscheduled/private with no publish time -- used to undo an
        operation (like HIT) that scheduled a video which had no previous publish time to
        restore. Omitting publishAt from the PUT (rather than sending some earlier value)
        is what makes it "ready" again instead of merely rescheduled."""
        new_status = dict(raw_status)
        new_status.pop("publishAt", None)
        new_status["privacyStatus"] = "private"

        try:
            resp = (
                self._service.videos()
                .update(part="status", body={"id": video_id, "status": new_status})
                .execute()
            )
            self.quota.record(COST_VIDEOS_UPDATE)
        except HttpError as e:
            reason = _extract_error_reason(e)
            raise VideoNotModifiableError(video_id, reason, str(e)) from e

        return _video_from_api_item(resp)

    # ---- uploading --------------------------------------------------------

    def list_playlists(self) -> list[Playlist]:
        """All playlists owned by the authenticated channel, for the Upload page's
        playlist picker."""
        playlists: list[Playlist] = []
        page_token = None
        while True:
            resp = (
                self._service.playlists()
                .list(part="snippet", mine=True, maxResults=50, pageToken=page_token)
                .execute()
            )
            self.quota.record(COST_PLAYLISTS_LIST)
            for item in resp.get("items", []):
                playlists.append(Playlist(playlist_id=item["id"], title=item["snippet"]["title"]))
            page_token = resp.get("nextPageToken")
            if not page_token:
                break
        return playlists

    def upload_video(
        self,
        file_path: str,
        title: str,
        description: str,
        made_for_kids: bool,
        publish_at: dt.datetime,
        tags: Optional[list[str]] = None,
        progress_callback: Optional[Callable[[float], None]] = None,
    ) -> Video:
        """Uploads a new video and schedules it in one call -- status.publishAt can be set
        directly on insert as long as privacyStatus is "private", so there's no separate
        update afterward. Uses a resumable/chunked upload so progress_callback (0.0-1.0)
        can drive a progress bar without blocking on the whole file transferring at once."""
        body = {
            "snippet": {
                "title": title,
                "description": description,
                "tags": tags or [],
                "categoryId": UPLOAD_CATEGORY_ID,
            },
            "status": {
                "privacyStatus": "private",  # required for publishAt to take effect
                "publishAt": _format_publish_at(publish_at),
                "selfDeclaredMadeForKids": made_for_kids,
            },
        }
        media = MediaFileUpload(file_path, chunksize=4 * 1024 * 1024, resumable=True)
        try:
            return self._run_resumable_upload(media, body, progress_callback)
        finally:
            # MediaFileUpload opens the video file when it is constructed and only closes it
            # when the object is garbage-collected -- until then Windows keeps the file
            # LOCKED (it can't be moved, renamed or deleted, e.g. by the editor exporting
            # over it). Close it as soon as the request has finished, success or failure.
            try:
                media.stream().close()
            except Exception:
                pass

    def _run_resumable_upload(self, media, body: dict, progress_callback) -> Video:
        request = self._service.videos().insert(part="snippet,status", body=body, media_body=media)

        response = None
        while response is None:
            # num_retries makes googleapiclient itself retry a single chunk transfer on a
            # transient error (dropped connection, SSL hiccup, 5xx) with backoff, resuming
            # the SAME resumable session rather than starting a new upload -- this is what
            # keeps a mid-transfer blip from ever needing a whole separate upload attempt
            # (see UPLOAD_CHUNK_RETRIES and the outer, attempt-level retry in
            # ui/widgets/basic_upload_panel.py's _quick_upload for the case where the
            # failure happens outside this window, e.g. during the initial request).
            status, response = request.next_chunk(num_retries=UPLOAD_CHUNK_RETRIES)
            if status is not None and progress_callback is not None:
                progress_callback(status.progress())
        if progress_callback is not None:
            progress_callback(1.0)
        self.quota.record(COST_VIDEOS_INSERT)
        return _video_from_api_item(response)

    def list_recent_uploaded_video_ids(self, count: int) -> list[str]:
        """The channel's `count` most recently uploaded video IDs, NEWEST FIRST. The uploads
        playlist is returned newest-first by the API (confirmed against this channel: the
        first items are the latest uploads, the last are the oldest videos on the channel),
        so only the first page(s) are needed -- 1 quota unit for up to 50 videos, instead of
        paging through the whole channel."""
        playlist_id = self.get_uploads_playlist_id()
        ids: list[str] = []
        page_token = None
        while len(ids) < count:
            resp = (
                self._service.playlistItems()
                .list(
                    part="contentDetails", playlistId=playlist_id,
                    maxResults=min(50, count - len(ids)), pageToken=page_token,
                )
                .execute()
            )
            self.quota.record(COST_PLAYLIST_ITEMS_LIST)
            ids.extend(item["contentDetails"]["videoId"] for item in resp.get("items", []))
            page_token = resp.get("nextPageToken")
            if not page_token:
                break
        return ids[:count]

    def find_recent_upload_by_title(
        self, title: str, lookback: int = 5, exclude_ids: Iterable[str] = (),
        expected_publish_at: Optional[dt.datetime] = None, require_publish_match: bool = False,
    ) -> Optional[Video]:
        """Best-effort "did this upload actually reach YouTube?" lookup, used both for the
        duplicate-upload protection (see ui/widgets/basic_upload_panel.py's _quick_upload:
        after an upload call raises -- e.g. the connection dropped while reading the
        response to an insert the server had already accepted) and for verifying history
        entries that have no video ID. It looks at the channel's `lookback` most recent
        uploads (newest first, any privacy status -- a fresh upload is private) for one
        whose title matches. The YouTube Data API has no upload idempotency key to check
        instead, so this is the best available signal -- callers should treat "not found"
        as merely inconclusive, not as proof nothing was created.

        `exclude_ids` are videos already accounted for elsewhere (other upload-history
        entries' video IDs): a same-titled video that is provably a DIFFERENT upload must
        never be mistaken for this one. `expected_publish_at` disambiguates same-titled
        videos by their scheduled time (this channel really does have same-titled Shorts):
        a title match at that exact time wins; with `require_publish_match` nothing else is
        accepted at all (right after an upload, the video MUST carry the slot that was
        requested), otherwise the newest title match is the fallback (a video may have been
        rescheduled since). Titles are compared after normalising whitespace/unicode, since
        YouTube may tidy those without it meaning a different video."""
        ids = self.list_recent_uploaded_video_ids(lookback)
        if not ids:
            return None
        excluded = set(exclude_ids)
        by_id = {v.video_id: v for v in self.get_videos(ids)}
        wanted = _normalize_title(title)
        matches = [
            by_id[i] for i in ids  # newest first
            if i not in excluded and i in by_id and _normalize_title(by_id[i].title) == wanted
        ]
        if expected_publish_at is not None:
            target = expected_publish_at.astimezone()
            exact = [v for v in matches if v.publish_at is not None and v.publish_at.astimezone() == target]
            if exact:
                return exact[0]
            if require_publish_match:
                return None
        return matches[0] if matches else None

    def get_video_check_items(self, video_ids: Iterable[str]) -> dict[str, dict]:
        """Raw videos.list items (snippet + status + processingDetails) for whichever of the
        given IDs YouTube still HAS, keyed by ID -- an ID that is absent from the result is a
        video YouTube no longer lists. 50 IDs per call (1 quota unit each), which is what makes
        re-checking the whole upload record cheap. Raises on an API/network error: callers must
        treat that as "couldn't check", never as "the videos are gone"."""
        ids = list(dict.fromkeys(i for i in video_ids if i))  # de-duplicated, order kept
        found: dict[str, dict] = {}
        for i in range(0, len(ids), BATCH_SIZE):
            chunk = ids[i : i + BATCH_SIZE]
            resp = (
                self._service.videos()
                .list(part="snippet,status,processingDetails", id=",".join(chunk))
                .execute()
            )
            self.quota.record(COST_VIDEOS_LIST)
            for item in resp.get("items", []):
                found[item["id"]] = item
        return found

    def check_video_registered(self, video_id: str, expected_title: str) -> VideoVerification:
        """UPLOAD VERIFICATION (HIST-001): asks YouTube -- independently of whatever the
        upload call itself returned -- whether it actually has this video. Confirms, via two
        separate endpoints, that it exists (videos.list), belongs to the authenticated
        channel, carries the expected title, is present in the channel's uploads playlist
        (playlistItems.list filtered by videoId), and reports a healthy uploadStatus.
        Raises on API/network errors -- the caller (youtube/upload_verifier.py) treats those
        as a failed attempt worth retrying, not as proof the video is missing. Costs about
        2 quota units per call (the channel/uploads-playlist IDs are cached after the first)."""
        resp = (
            self._service.videos()
            .list(part="snippet,status", id=video_id)
            .execute()
        )
        self.quota.record(COST_VIDEOS_LIST)
        items = resp.get("items", [])
        if not items:
            return VideoVerification(exists=False)

        item = items[0]
        snippet = item.get("snippet", {})
        status = item.get("status", {})
        result = VideoVerification(
            exists=True,
            upload_status=status.get("uploadStatus", ""),
            failure_reason=status.get("failureReason", ""),
            rejection_reason=status.get("rejectionReason", ""),
            privacy_status=status.get("privacyStatus", ""),
            publish_at=_parse_publish_at(status.get("publishAt")),
            found_title=snippet.get("title", ""),
            channel_id=snippet.get("channelId", ""),
        )
        result.channel_matches = bool(result.channel_id) and result.channel_id == self.get_authenticated_channel_id()
        result.title_matches = _normalize_title(result.found_title) == _normalize_title(expected_title)

        if result.channel_matches:  # no point spending a call on the second endpoint for someone else's video
            playlist_resp = (
                self._service.playlistItems()
                .list(part="id", playlistId=self.get_uploads_playlist_id(), videoId=video_id, maxResults=1)
                .execute()
            )
            self.quota.record(COST_PLAYLIST_ITEMS_LIST)
            result.in_uploads_playlist = bool(playlist_resp.get("items"))
        return result

    def get_file_details(self, video_id: str) -> Optional[dict]:
        """UPLOAD QUALITY PROTECTION: reads back what YouTube itself recorded about the
        originally received file (part=fileDetails: container, resolution, fps, codec,
        and bitrate for each stream) -- an independent, YouTube-side confirmation used to
        verify nothing changed in transit (see ui/widgets/basic_upload_panel.py's
        _verify_upload_quality() and youtube/video_tech_info.py). Returns None if
        YouTube hasn't finished associating file details with this video yet, which is
        normal right after a brand-new upload, rather than raising -- this is a
        best-effort verification, never something upload success depends on."""
        resp = (
            self._service.videos()
            .list(part="fileDetails", id=video_id)
            .execute()
        )
        self.quota.record(COST_VIDEOS_LIST)
        items = resp.get("items", [])
        if not items:
            return None
        return items[0].get("fileDetails")

    def add_video_to_playlist(self, video_id: str, playlist_id: str) -> None:
        self._service.playlistItems().insert(
            part="snippet",
            body={
                "snippet": {
                    "playlistId": playlist_id,
                    "resourceId": {"kind": "youtube#video", "videoId": video_id},
                }
            },
        ).execute()
        self.quota.record(COST_PLAYLIST_ITEMS_INSERT)


def _extract_error_reason(error: HttpError) -> str:
    try:
        details = error.error_details
        if details:
            return details[0].get("reason", "unknown")
    except Exception:
        pass
    return "unknown"

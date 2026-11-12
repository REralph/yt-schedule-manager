"""UPLOAD VERIFICATION (HIST-001): the retry loop that turns "the upload call returned" into
"YouTube actually has this video". No UI in here -- it runs on a worker thread and reports
back through a callback, so it's straightforwardly testable.

The flow, per the "Verify Upload Actually Reached YouTube" spec: wait briefly for YouTube to
register the upload, ask YouTube (YouTubeClient.check_video_registered) whether the video
really exists on this channel with the expected title, and if it can't be confirmed, wait a
short interval and check again -- a LIMITED number of times (never indefinitely). Not being
able to see the video yet is exactly what YouTube's propagation delay looks like, so
"not found" alone isn't final until the attempts are used up; but a definitive answer
(YouTube reports the upload failed/was rejected/was deleted, or it's someone else's video,
or the title differs) stops immediately, since waiting can't change it.

Nothing here ever uploads anything -- verifying and (re-)uploading are deliberately
separate, so a verification problem can never turn into a duplicate Short.

If no video ID is known (an upload call raised, so the response that would have carried the
ID never arrived -- or the app was closed mid-upload), each attempt looks for the video by
title among the channel's recent uploads instead, and attaches its ID if it finds it."""
from __future__ import annotations

import datetime as dt
import time
from dataclasses import dataclass
from typing import Callable, Iterable, Optional

from youtube.errors import classify_exception

VERIFY_MAX_ATTEMPTS = 5
VERIFY_INITIAL_DELAY_SECONDS = 3.0  # "wait briefly for YouTube to process/register the upload"
VERIFY_RETRY_DELAY_SECONDS = 6.0
TITLE_LOOKUP_LOOKBACK = 25  # how many of the channel's most recent uploads a by-title search considers


@dataclass
class VerificationOutcome:
    verified: bool
    detail: str  # what YouTube reported (or the API error hit) on the last attempt -- goes into the history entry and the error report
    video_id: str = ""  # the video ID in play at the end (attached if it had to be found by title)
    attempts_in_run: int = 0
    last_attempt_number: int = 0  # cumulative numbering, continuing from attempt_offset
    definitive: bool = False  # True if it stopped early because waiting couldn't help


def run_verification(
    client,
    *,
    video_id: str,
    title: str,
    exclude_ids: Iterable[str] = (),
    expected_publish_at: Optional[dt.datetime] = None,
    attempt_offset: int = 0,
    initial_delay: Optional[float] = None,
    retry_delay: Optional[float] = None,
    max_attempts: Optional[int] = None,
    on_attempt: Optional[Callable[[int, str, str], None]] = None,
    sleep: Callable[[float], None] = time.sleep,
) -> VerificationOutcome:
    """Runs up to `max_attempts` checks. `on_attempt(attempt_number, detail, video_id)` fires
    after every attempt (from whichever thread this runs on) so progress can be persisted
    as it happens -- an app restart mid-verification then resumes from a real record.
    `expected_publish_at` (the entry's scheduled time) lets a by-title search tell same-titled
    videos apart. `attempt_offset` continues the numbering across separate runs (RETRY VERIFICATION, a
    post-restart resume) so "Verification attempt" always reflects the true total. Defaults
    are resolved at call time, not import time, so the module constants stay adjustable."""
    max_attempts = VERIFY_MAX_ATTEMPTS if max_attempts is None else max_attempts
    initial_delay = VERIFY_INITIAL_DELAY_SECONDS if initial_delay is None else initial_delay
    retry_delay = VERIFY_RETRY_DELAY_SECONDS if retry_delay is None else retry_delay
    excluded = set(exclude_ids)

    detail = ""
    attempt_number = attempt_offset
    used = 0
    for n in range(1, max_attempts + 1):
        delay = initial_delay if n == 1 else retry_delay
        if delay > 0:
            sleep(delay)
        used = n
        attempt_number = attempt_offset + n
        verified = False
        definitive = False
        try:
            if not video_id:
                found = client.find_recent_upload_by_title(
                    title, lookback=TITLE_LOOKUP_LOOKBACK, exclude_ids=excluded, expected_publish_at=expected_publish_at,
                )
                if found is None:
                    detail = "No video with this title was found among the channel's recent uploads."
                else:
                    video_id = found.video_id  # the upload DID reach YouTube -- attach the existing video rather than ever uploading again
            if video_id:
                check = client.check_video_registered(video_id, title)
                detail = check.describe()
                verified = check.verified
                definitive = check.definitive_failure
        except Exception as e:  # a network/API hiccup is a failed attempt to retry, not proof the video is missing
            detail = f"API error: {type(e).__name__}: {e}"
            info = classify_exception(e)
            if not info.retry_can_help:
                # e.g. the YouTube sign-in expired: checking again in a few seconds can't
                # change that, so stop and say what to do rather than hammering the API.
                detail = f"{info.friendly} {detail}".strip()
                definitive = True

        if on_attempt is not None:
            on_attempt(attempt_number, detail, video_id)
        if verified:
            return VerificationOutcome(
                True, f"Confirmed by YouTube -- {detail}", video_id, used, attempt_number,
            )
        if definitive:
            return VerificationOutcome(
                False, f"YouTube reports a problem with this video -- {detail}", video_id, used, attempt_number, definitive=True,
            )

    return VerificationOutcome(
        False,
        f"Could not be confirmed on YouTube after {used} attempt{'s' if used != 1 else ''}. Last result: {detail}",
        video_id, used, attempt_number,
    )

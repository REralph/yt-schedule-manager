"""Shared apply-with-history logic for any page that computes a ScheduleResult and needs
to write it to YouTube: prefetch every affected video's current status once, record the
change to History before touching anything, then update each video's publish time on a
background thread. Used by the Daily Upload Frequency tab (was Increase Daily Slots / Push) and Auto Boost.

This module has no widget of its own: status_cb/done_cb must already be marshaled onto
the main thread by the caller (e.g. via self.after(0, ...)), the same background-thread
callback pattern used throughout this app. That's also why state.notify_videos_changed()
/notify_history_changed() are deliberately NOT called here -- AppState's listeners are
called synchronously wherever notify_*() is invoked, and some (e.g. a page rebuilding its
own preview table) touch Tk widgets, which must never happen off the main thread. Call
them yourself from inside done_cb once it's actually run on the main thread (see
ui/pages/daily_upload_frequency_page.py's _apply_done()).
"""
from __future__ import annotations

import datetime as dt
import threading
from typing import Callable, Optional

from data import diagnostics
from data.history import HistoryChange
from data.upload_activity import UploadActivityLog
from scheduling.engine import ScheduleResult
from scheduling.ordering import validate_order_preserved
from ui.app_state import AppState
from ui.threading_utils import safe_after, start_worker
from youtube.client import VideoNotModifiableError


def _sync_upload_record(assignments, applied_ids: set) -> None:
    """Keeps the upload record (History) in step with the schedule: every video that was just
    moved has its recorded scheduled time set to the new one, so the record always shows where
    each Short really is now -- not where it was when it was uploaded. Never raises: failing to
    update the record must not turn a successful reschedule into an error."""
    try:
        log = UploadActivityLog.shared()
        updates = []
        for a in assignments:
            if a.video_id not in applied_ids:
                continue
            entry = log.find_by_video_id(a.video_id)
            if entry is not None:
                updates.append((entry, {"scheduled_at": a.new_publish_at.isoformat(), "schedule_label": "Scheduled"}))
        if updates:
            log.update_many(updates)
    except Exception as e:
        diagnostics.report_recovered("Updating the upload record after a reschedule", e, page="Schedule")


def _forget_changes(state: AppState, entry, video_ids: set) -> None:
    """The history entry is written BEFORE anything is sent (so a crash mid-way still leaves a record). Anything that
    then did not happen is taken back out, so the history only ever claims changes YouTube really received -- it is
    evidence the order recovery relies on. Never raises."""
    if entry is None or not video_ids:
        return
    try:
        state.history.remove_changes(entry.entry_id, video_ids)
    except Exception as e:
        diagnostics.report_recovered("Correcting the change history after a failed update", e, page="Schedule")


def apply_schedule_result(
    state: AppState,
    result: ScheduleResult,
    description: str,
    status_cb: Callable[[str], None],
    done_cb: Callable[[int, int, list[str]], None],
    order_check: Optional[Callable[[], list[str]]] = None,
    require_scheduled: bool = False,
) -> Optional[str]:
    """Validates order preservation, then writes every assignment to YouTube on a
    background thread. Returns an error string immediately (nothing started) if the
    order-preservation safety net rejects the result; otherwise returns None and the
    background thread eventually calls done_cb(succeeded_count, total, failed_reasons).

    `order_check` replaces the default check for a caller whose order is defined by something other than the
    current dates (Daily Upload Frequency: the queue's sequence numbers). It runs right before anything is
    sent and returns the reasons to abort (empty = fine). `require_scheduled` re-reads each video just before
    it is written and skips any that is no longer a private, future-scheduled video (e.g. published since the
    preview), so a published video can never be rescheduled."""
    if not result.assignments:
        return "Nothing to apply."
    if not state.client or not state.repo:
        return "Not connected to YouTube."

    # Highest-priority rule, checked one last time right before anything is sent to
    # YouTube: video order must never change.
    if order_check is not None:
        violations = order_check()
    else:
        violations = validate_order_preserved(state.repo.videos, result)
    if violations:
        return (
            "This would change the order videos publish in, which this app never allows. "
            "Nothing was sent to YouTube.\n\n" + "\n".join(violations[:5])
        )

    assignments = result.assignments
    n = len(assignments)
    ids = [a.video_id for a in assignments]

    succeeded: list[str] = []
    failed: list[str] = []
    failed_ids: set[str] = set()
    finished = threading.Event()

    def finish(succeeded_count: int, total: int, failed_reasons: list[str]) -> None:
        """done_cb exactly once, however the worker ends -- a normal finish, an early
        return, or the last-resort failure handler below."""
        if finished.is_set():
            return
        finished.set()
        done_cb(succeeded_count, total, failed_reasons)


    def worker():
        changes = [HistoryChange(a.video_id, a.title, a.old_publish_at, a.new_publish_at) for a in assignments]
        entry = None
        try:
            entry = state.history.record(description, changes)
        except Exception as e:
            # Nothing has been sent to YouTube yet, so stopping here is safe -- and it must
            # finish through done_cb, or the page stays on "Applying..." forever.
            diagnostics.report_recovered("Saving the change history before applying", e, page="Schedule")
            finish(0, n, [f"Could not save the change history, so nothing was applied: {e}"])
            return

        try:
            fetched = {v.video_id: v for v in state.client.get_videos(ids)}
        except Exception as e:
            _forget_changes(state, entry, {a.video_id for a in assignments})
            finish(0, n, [f"Could not read current video details: {e}"])
            return
        raw_statuses = {vid: v.raw_status for vid, v in fetched.items()}
        now = dt.datetime.now(dt.timezone.utc)

        stopped_at = None
        for i, a in enumerate(assignments, start=1):
            status_cb(f"Applying {i}/{n}...")
            raw_status = raw_statuses.get(a.video_id)
            if raw_status is None:
                failed.append(f"{a.title}: video not found (it may have been deleted or made public)")
                failed_ids.add(a.video_id)
                continue
            if require_scheduled:
                v = fetched[a.video_id]
                if not (v.is_scheduled and v.publish_at is not None and v.publish_at > now):
                    failed.append(f"{a.title}: no longer scheduled (it has been published or changed), so it was left alone")
                    failed_ids.add(a.video_id)
                    continue
            try:
                state.client.update_publish_time_with_status(a.video_id, raw_status, a.new_publish_at)
                succeeded.append(a.video_id)
            except VideoNotModifiableError as e:
                failed.append(f"{a.title}: {e.reason}")
                failed_ids.add(a.video_id)
                if e.reason == "quotaExceeded":
                    stopped_at = i
                    break
            except Exception as e:
                failed.append(f"{a.title}: {e}")
                failed_ids.add(a.video_id)

        if stopped_at is not None:
            # the loop stopped early: everything it never reached was not applied either
            failed_ids.update(a.video_id for a in assignments[stopped_at:])
            failed.append(f"Stopped after {stopped_at - 1} of {n}: YouTube's daily quota ran out. Nothing else was changed -- run it again later and only the remaining videos are updated.")
        _forget_changes(state, entry, failed_ids)

        if succeeded:
            try:
                state.repo.refresh_ids(succeeded)
            except Exception:
                pass
            _sync_upload_record(assignments, set(succeeded))
        finish(len(succeeded), n, failed)

    def on_worker_failed(e: BaseException) -> None:
        # An unexpected error mid-way: report what we know so the page unlocks instead of
        # waiting on a callback that will now never come.
        finish(len(succeeded), n, failed + [f"Unexpected error while applying: {type(e).__name__}: {e}"])

    start_worker(worker, name="Apply schedule changes", page="Schedule", operation=description, on_error=on_worker_failed)
    return None

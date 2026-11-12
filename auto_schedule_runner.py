"""AUTO BOOST's (formerly Auto Schedule) headless daily entry point -- meant to be invoked by Windows Task
Scheduler once a day, completely independent of whether the GUI app is open. Can also be
run manually (`python auto_schedule_runner.py`) for testing.

Each run:
  1. Fetches recently-published Shorts and groups their views by the calendar day they
     went live (scheduling/auto_schedule.py's group_by_published_day) -- there is no
     configurable "evaluation period" anymore; a day only counts once every Short on it
     has had 24h to accumulate views.
  2. Evaluates whether the channel is on pace for its daily or weekly target (whichever
     mode is configured), using config.estimated_views_per_short as the flat planning
     figure (AS-001 point 7), deciding how many Shorts TOMORROW should have in total --
     the exact same decision function the GUI settings page's live preview uses, so
     there's one source of truth.
  3. Pulls however many Shorts are needed forward out of the EXISTING future schedule to
     bring tomorrow up to that total (scheduling/auto_schedule.py's
     compute_auto_schedule_pull_forward -- AS-001 points 11-13), cascading the remaining
     queue forward while preserving its exact order, with NO confirmation prompt. That's
     intentional: the ON/OFF switch in Auto Boost settings is the actual gate on this
     running at all; once it's on, this is meant to run unattended. This never uploads a
     new video (point 15) -- it only ever reschedules Shorts that already exist. Every
     decision, applied or not, is still logged to data/auto_schedule_history.json (for
     the GUI) and data/auto_schedule_run.log (for troubleshooting), so nothing that
     happened unattended is invisible after the fact.

Guards against double-scheduling the same target date if the task fires more than once
(e.g. a missed run retried, or manual + scheduled runs overlapping).
"""
from __future__ import annotations

import datetime as dt
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from auth import oauth_manager
from data.auto_schedule_config import TARGET_MODE_WEEKLY, AutoScheduleConfig
from data.auto_schedule_history import AutoScheduleDecision, AutoScheduleHistoryStore
from data.history import HistoryChange, HistoryStore
from scheduling.auto_schedule import (
    compute_auto_schedule_pull_forward,
    compute_week_progress,
    decide_video_count,
    evaluate_daily_performance,
    group_by_published_day,
)
from scheduling.ordering import validate_order_preserved
from youtube.client import YouTubeClient
from youtube.video_repo import VideoRepository

LOG_PATH = Path(__file__).resolve().parent / "data" / "auto_schedule_run.log"
log = logging.getLogger("auto_schedule")


def _setup_logging() -> None:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    handler = logging.FileHandler(str(LOG_PATH), encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    log.addHandler(handler)
    # Task Scheduler is meant to run this with pythonw.exe (no console window), where
    # sys.stdout is None -- StreamHandler(None) would raise on the very first log call
    # and silently kill an unattended run before it even starts. Only attach a console
    # handler when one genuinely exists (e.g. running this manually from a terminal).
    if sys.stdout is not None:
        log.addHandler(logging.StreamHandler(sys.stdout))
    log.setLevel(logging.INFO)


def run() -> int:
    log.info("=== Auto Boost run starting ===")
    config = AutoScheduleConfig.load()
    if not config.enabled:
        log.info("Auto Boost is OFF -- nothing to do.")
        return 0

    creds = oauth_manager.load_cached_credentials()
    if not creds:
        log.error("No valid cached credentials -- cannot run unattended. Open the app and sign in first.")
        return 1

    client = YouTubeClient(creds)
    repo = VideoRepository(client, use_uploads_playlist=True)
    history = AutoScheduleHistoryStore()

    now = dt.datetime.now(dt.timezone.utc)
    today = now.astimezone().date()
    scheduling_target_date = today + dt.timedelta(days=1)
    decision_id = AutoScheduleDecision.new_id()

    already = history.latest_applied_for_date(scheduling_target_date)
    if already:
        log.info(f"Already scheduled for {scheduling_target_date} (decision {already.decision_id}) -- skipping to avoid a duplicate run.")
        return 0

    # ---- Step 1: evaluate recent real performance ----
    try:
        recent_published = client.get_recent_published_videos(limit=60)
    except Exception as e:
        log.error(f"Could not fetch recently published videos: {e}")
        return 1

    by_day = group_by_published_day(recent_published, now)
    expected_per_short = config.estimated_views_per_short  # AS-001 point 7 -- a flat, user-set planning figure, not an automatic estimate
    log.info(f"Estimated views/Short: {expected_per_short} (configured directly, see AS-001)")

    daily_performance = evaluate_daily_performance(by_day, today, config.daily_target_views)
    week_progress = compute_week_progress(by_day, today, config.weekly_target_views) if config.target_mode == TARGET_MODE_WEEKLY else None

    # ---- Step 2: decide tomorrow's total ----
    decision = decide_video_count(config, daily_performance, week_progress, expected_per_short)
    log.info(f"Decision for {scheduling_target_date}: {decision.video_count} video(s) total. {decision.explanation}")

    def record(scheduled_ids, applied, error, previous_count):
        target_views_used = week_progress.required_average if week_progress is not None else config.daily_target_views
        history.record(AutoScheduleDecision(
            decision_id=decision_id,
            timestamp=dt.datetime.now(dt.timezone.utc),
            target_date=scheduling_target_date,
            target_mode=config.target_mode,
            target_views_used=round(target_views_used),
            actual_views=daily_performance.actual_views if daily_performance.ready else None,
            target_reached=decision.target_reached,
            expected_views_per_short=expected_per_short,
            previous_daily_count=previous_count,
            new_daily_count=decision.video_count,
            explanation=decision.explanation,
            scheduled_video_ids=scheduled_ids,
            applied=applied,
            error=error or "",
        ))

    # ---- Step 3: pull Shorts forward out of the existing future queue (never a
    # separate "ready" pool, never a new upload -- see AS-001 points 11-15) ----
    try:
        repo.refresh()
    except Exception as e:
        log.error(f"Could not refresh channel data: {e}")
        record([], False, str(e), 0)
        return 1

    previous_count = sum(
        1 for v in repo.videos if v.publish_at and v.publish_at.astimezone().date() == scheduling_target_date
    )

    pull_forward = compute_auto_schedule_pull_forward(repo.videos, scheduling_target_date, decision.video_count, config, now)
    result = pull_forward.result

    if not result.assignments:
        log.info(f"Nothing to change for {scheduling_target_date} -- already satisfies the decision ({decision.video_count}/day).")
        record([], True, "", previous_count)
        return 0

    # ---- Step 4: validate before ever touching YouTube ----
    violations = validate_order_preserved(repo.videos, result)
    if violations:
        msg = "Order-preservation check failed: " + "; ".join(violations[:5])
        log.error(msg)
        record([], False, msg, previous_count)
        return 1

    # ---- Step 5: apply -- no confirmation prompt, by design (see module docstring) ----
    scheduled_ids: list[str] = []
    write_errors: list[str] = []
    for a in result.assignments:
        try:
            client.update_publish_time(a.video_id, a.new_publish_at)
            scheduled_ids.append(a.video_id)
        except Exception as e:
            write_errors.append(f"{a.video_id}: {e}")

    if scheduled_ids:
        applied_changes = [a for a in result.assignments if a.video_id in scheduled_ids]
        HistoryStore().record(
            f"Auto Boost: {len(scheduled_ids)} video(s) adjusted from {scheduling_target_date.strftime('%d %b %Y')} onward",
            [HistoryChange(a.video_id, a.title, a.old_publish_at, a.new_publish_at) for a in applied_changes],
        )
        try:
            repo.refresh_ids(scheduled_ids)
        except Exception:
            pass

    if write_errors:
        log.error("Some videos failed to schedule: " + "; ".join(write_errors))

    # ---- Step 6: re-verify against the real, refreshed data -- report a failure
    # instead of claiming success if what actually happened doesn't match what was meant ----
    verify_videos = [v for v in repo.videos if v.video_id in scheduled_ids]
    verify_issue = ""
    if len(verify_videos) != len(scheduled_ids):
        verify_issue = "Some scheduled videos could not be re-confirmed after applying."
    if verify_issue:
        log.error(verify_issue)

    applied = bool(scheduled_ids) and not verify_issue
    record(scheduled_ids, applied, "; ".join(write_errors) if write_errors else verify_issue, previous_count)

    log.info(f"=== Auto Boost run finished: {len(scheduled_ids)}/{len(result.assignments)} video(s) adjusted, target {scheduling_target_date} ===")
    return 0 if applied or not write_errors else 1


if __name__ == "__main__":
    _setup_logging()
    try:
        sys.exit(run())
    except Exception:
        log.exception("Auto Boost run crashed with an unhandled exception.")
        sys.exit(1)

"""DAILY UPLOAD FREQUENCY (was INCREASE DAILY SLOTS, and before that Push): change how many Shorts publish per day.

This page changes DATES AND TIMES ONLY. It never reorders the queue. The order comes from permanent sequence numbers
(data/queue_order.py), and every new date is produced by walking those numbers (scheduling/daily_frequency.py) -- the
current dates are not trusted as the order, so a schedule that is already out of order cannot pass its disorder on.

Flow
  1. The queue order must be RECORDED first. If it has not been, "Recover & review the order" shows the order rebuilt from
     the evidence (upload times, the schedule-change history), settles anything the evidence cannot settle WITH YOU, and
     records it. Nothing on YouTube changes in that step.
  2. Choose 1-6 Shorts per day. The preview shows the current vs requested frequency, every Short's current vs new date
     and time, its place in the order before (by date) and after (the sequence), how many change, and anything invalid.
  3. Confirm. Right before sending, the schedule is re-read from YouTube and the plan re-validated; if anything differs
     (or a check fails) nothing is sent. Only Shorts whose date/time actually changes are updated, each change is
     recorded in the schedule-change history (previous and new time), and afterwards YouTube is read back and compared
     with the validated plan.

Recomputes from the local cache whenever the frequency changes (no network hit per edit); YouTube is only contacted on
load, tab focus, Refresh Calculation, and when Confirm is pressed.
"""
from __future__ import annotations

import datetime as dt
from tkinter import messagebox
from typing import Optional

import customtkinter as ctk

from data import diagnostics
from data.auto_schedule_config import AutoScheduleConfig
from data.order_codes import backfill_order_codes
from data.queue_order import QueueOrderStore, format_code
from data.upload_activity import UploadActivityLog
from scheduling import daily_frequency as df
from scheduling.order_recovery import recover_order
from scheduling.queue_registration import append_new_uploads, plan_registration
from ui import thumbnail_cache
from ui.app_state import AppState
from ui.dev_reporting import report_error
from ui.schedule_apply import apply_schedule_result
from ui.threading_utils import safe_after, start_worker
from ui.widgets.dev_tag import DevTag
from ui.widgets.order_review_dialog import OrderReviewDialog
from ui.widgets.slot_selector import SlotCountControl
from youtube.quota import COST_VIDEOS_UPDATE

GOOD = "#7fd17f"
BAD = "#e05a5a"
WARN = "#e0a04a"
MUTED = "#888888"
CHANGED_COLOR = "#5b9bd5"
THUMB_SIZE = (64, 36)
PREVIEW_PAGE = 60
FRESHNESS_NOTE_PREFIX = "Still loading your schedule"


def _now() -> dt.datetime:
    """The one place this page reads the clock (tests replace it)."""
    return dt.datetime.now(dt.timezone.utc)


def _fmt(d: Optional[dt.datetime]) -> str:
    if d is None:
        return "Not previously scheduled"
    return d.astimezone().strftime("%d %b %Y, %H:%M")


def _plural(n: int, word: str) -> str:
    return f"{n} {word}{'' if n == 1 else 's'}"


class DailyUploadFrequencyPage(ctk.CTkFrame):
    def __init__(self, master, state: AppState, **kwargs):
        super().__init__(master, fg_color="transparent", **kwargs)
        self.state = state
        self._applying = False
        self._plan: Optional[df.FrequencyPlan] = None
        self._applied_plan: Optional[df.FrequencyPlan] = None
        self._shown_rows = PREVIEW_PAGE
        self._review_window: Optional[OrderReviewDialog] = None

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(5, weight=1)

        header = ctk.CTkFrame(self, fg_color="transparent")
        header.grid(row=0, column=0, sticky="w", padx=16, pady=(16, 4))
        ctk.CTkLabel(header, text="DAILY UPLOAD FREQUENCY", font=ctk.CTkFont(size=20, weight="bold")).pack(side="left")
        DevTag(header, state, "0.13").show(side="left", padx=(8, 0))

        ctk.CTkLabel(
            self, text="Choose how many Shorts publish per day. This changes only the dates and times -- never the order of your videos. "
            "The order comes from permanent sequence numbers, and every new date is worked out by walking them.",
            text_color=MUTED, font=ctk.CTkFont(size=11), anchor="w", justify="left", wraplength=900,
        ).grid(row=1, column=0, sticky="w", padx=16, pady=(0, 8))

        # ---- the recorded queue order ----
        order_card = ctk.CTkFrame(self, fg_color="#1a1a1a", corner_radius=8)
        order_card.grid(row=2, column=0, sticky="ew", padx=16, pady=(0, 8))
        order_row = ctk.CTkFrame(order_card, fg_color="transparent")
        order_row.pack(fill="x", padx=16, pady=10)
        order_row.grid_columnconfigure(2, weight=1)
        ctk.CTkLabel(order_row, text="QUEUE ORDER", font=ctk.CTkFont(size=12, weight="bold")).grid(row=0, column=0, padx=(0, 10))
        DevTag(order_row, state, "0.13.6").show_grid(row=0, column=1, padx=(0, 8))
        self.order_label = ctk.CTkLabel(order_row, text="", anchor="w", justify="left", wraplength=620)
        self.order_label.grid(row=0, column=2, sticky="ew")
        DevTag(order_row, state, "0.13.6.1").show_grid(row=0, column=3, padx=(6, 0))
        self.order_btn = ctk.CTkButton(order_row, text="", width=210, command=self._open_order_review)
        self.order_btn.grid(row=0, column=4, padx=(8, 0))
        DevTag(order_row, state, "0.13.6.2").show_grid(row=0, column=5, padx=(6, 0))

        # ---- frequency + status ----
        status_card = ctk.CTkFrame(self, fg_color="#1a1a1a", corner_radius=8)
        status_card.grid(row=3, column=0, sticky="ew", padx=16, pady=(0, 8))
        inner = ctk.CTkFrame(status_card, fg_color="transparent")
        inner.pack(fill="x", padx=16, pady=12)

        top = ctk.CTkFrame(inner, fg_color="transparent")
        top.pack(fill="x")
        self.current_label = ctk.CTkLabel(top, text="", anchor="w", justify="left", wraplength=640)
        self.current_label.pack(side="left")
        DevTag(top, state, "0.13.1.5").show(side="left", padx=(8, 0))
        self.time_label = ctk.CTkLabel(top, text="", text_color=MUTED, font=ctk.CTkFont(size=11))
        self.time_label.pack(side="right")
        DevTag(top, state, "0.13.1.1").show(side="right", padx=(0, 8))

        slots_row = ctk.CTkFrame(inner, fg_color="transparent")
        slots_row.pack(fill="x", pady=(12, 0))
        ctk.CTkLabel(slots_row, text="SHORTS PER DAY", font=ctk.CTkFont(size=12, weight="bold")).pack(side="left", padx=(0, 10))
        self.config = AutoScheduleConfig.load()
        # The selection is local to this page: it never writes normal_videos_per_day. Only the shared gear-icon preset
        # editor (SET-0012.1) persists anything, and that is global by design.
        self.slot_control = SlotCountControl(
            slots_row, state, self.config, initial_count=self.config.normal_videos_per_day,
            on_count_changed=lambda _count: self._on_frequency_changed(),
            selector_dev_id="0.13.5", gear_dev_id="0.13.5.1", panel_dev_id="0.13.5.2",
        )
        self.slot_control.pack(side="left")
        self.affected_label = ctk.CTkLabel(slots_row, text="", text_color=MUTED)
        self.affected_label.pack(side="right")
        DevTag(slots_row, state, "0.13.1.2").show(side="right", padx=(0, 8))

        self.summary_label = ctk.CTkLabel(inner, text="", font=ctk.CTkFont(size=13), anchor="w", justify="left", wraplength=900)
        self.summary_label.pack(anchor="w", fill="x", pady=(10, 0))
        DevTag(inner, state, "0.13.1.3").show(anchor="w", pady=(2, 0))
        self.order_line_label = ctk.CTkLabel(inner, text="", font=ctk.CTkFont(size=12), anchor="w", justify="left", wraplength=900, text_color=MUTED)
        self.order_line_label.pack(anchor="w", fill="x", pady=(4, 0))
        self.issues_label = ctk.CTkLabel(inner, text="", font=ctk.CTkFont(size=12), anchor="w", justify="left", wraplength=900)
        self.issues_label.pack(anchor="w", fill="x", pady=(4, 0))
        DevTag(inner, state, "0.13.1.6").show(anchor="w", pady=(2, 0))
        self.cost_label = ctk.CTkLabel(inner, text="", font=ctk.CTkFont(size=11), anchor="w", justify="left", wraplength=900, text_color=MUTED)
        self.cost_label.pack(anchor="w", fill="x", pady=(4, 0))
        DevTag(inner, state, "0.13.1.7").show(anchor="w", pady=(2, 0))
        self.verify_label = ctk.CTkLabel(inner, text="", font=ctk.CTkFont(size=12), anchor="w", justify="left", wraplength=900)
        self.verify_label.pack(anchor="w", fill="x", pady=(4, 0))
        DevTag(inner, state, "0.13.1.8").show(anchor="w", pady=(2, 0))

        # ---- preview table ----
        columns = ctk.CTkFrame(self, fg_color="#151515", corner_radius=6)
        columns.grid(row=4, column=0, sticky="ew", padx=16, pady=(0, 0))
        for c, w in enumerate((0, 0, 0, 2, 1, 1)):
            columns.grid_columnconfigure(c, weight=w)
        ctk.CTkLabel(columns, text="Code", width=56, font=ctk.CTkFont(weight="bold")).grid(row=0, column=0, padx=(10, 0), pady=6)
        ctk.CTkLabel(columns, text="Was #", width=50, font=ctk.CTkFont(weight="bold"), text_color=MUTED).grid(row=0, column=1, padx=(0, 0), pady=6)
        ctk.CTkLabel(columns, text="", width=THUMB_SIZE[0] + 12).grid(row=0, column=2, padx=6, pady=6)
        ctk.CTkLabel(columns, text="Video", font=ctk.CTkFont(weight="bold")).grid(row=0, column=3, sticky="w", padx=10, pady=6)
        ctk.CTkLabel(columns, text="Current", font=ctk.CTkFont(weight="bold")).grid(row=0, column=4, sticky="w", padx=10, pady=6)
        ctk.CTkLabel(columns, text="New", font=ctk.CTkFont(weight="bold")).grid(row=0, column=5, sticky="w", padx=10, pady=6)

        self.table = ctk.CTkScrollableFrame(self, fg_color="transparent")
        self.table.grid(row=5, column=0, sticky="nsew", padx=16, pady=(0, 4))
        for c, w in enumerate((0, 0, 0, 2, 1, 1)):
            self.table.grid_columnconfigure(c, weight=w)
        DevTag(self.table, state, "0.13.2").show_grid(row=0, column=0, columnspan=6, sticky="w")

        self.status_label = ctk.CTkLabel(self, text="", anchor="w", text_color=MUTED, justify="left", wraplength=1000)
        self.status_label.grid(row=6, column=0, sticky="ew", padx=16, pady=(4, 0))

        # ---- action bar ----
        btn_row = ctk.CTkFrame(self, fg_color="transparent")
        btn_row.grid(row=7, column=0, sticky="e", padx=16, pady=16)
        self.refresh_btn = ctk.CTkButton(
            btn_row, text="Refresh Calculation", width=150, fg_color="transparent", border_width=1,
            command=lambda: self.refresh_from_network(deep=True),
        )
        self.refresh_btn.pack(side="right", padx=(8, 0))
        DevTag(btn_row, state, "0.13.3.2").show(side="right", padx=(0, 8))
        self.confirm_btn = ctk.CTkButton(
            btn_row, text="Confirm & Apply", width=190, fg_color="#2f7a3d", hover_color="#256030",
            font=ctk.CTkFont(weight="bold"), command=self._confirm,
        )
        self.confirm_btn.pack(side="right")
        DevTag(btn_row, state, "0.13.3.1").show(side="right", padx=(0, 8))

        state.on_videos_changed(self.recompute)
        self.recompute()

    # ---- tab-focus hook, called by MainWindow's CTkTabview command= ----

    def on_tab_focus(self) -> None:
        self.refresh_from_network()

    def _on_frequency_changed(self) -> None:
        self._shown_rows = PREVIEW_PAGE
        self.verify_label.configure(text="")
        self.recompute()

    # ---- network ----

    def refresh_from_network(self, deep: bool = False) -> None:
        """Re-fetches the real schedule from YouTube before recomputing -- on initial load, tab focus and Refresh
        Calculation, never on a frequency change. `deep=True` (the button) also asks for a deep search of the channel for
        videos scheduled outside this app -- rationed in youtube/video_repo.py, so pressing it repeatedly is safe."""
        if not self.state.repo:
            self.recompute()
            return

        def worker():
            try:
                self.state.repo.refresh(deep=deep)
            except Exception as e:
                diagnostics.report_recovered("Refreshing the schedule (Daily Upload Frequency)", e, page="Daily Upload Frequency")
            safe_after(self, self._after_refresh)

        start_worker(worker, name="Daily Upload Frequency refresh", page="Daily Upload Frequency", on_error=lambda e: safe_after(self, self.recompute))

    def _after_refresh(self) -> None:
        if not self.winfo_exists():
            return
        self._register_new_uploads()
        self.recompute()

    def _register_new_uploads(self) -> None:
        """Shorts uploaded since the order was recorded go to the END of the queue (the app's own rule for uploads).
        Only the clear-cut ones are done here; a Short that is older than numbered ones needs your decision."""
        reg = QueueOrderStore.shared()
        if not (reg.is_adopted and self.state.repo and getattr(self.state.repo, "refreshed_this_session", True)):
            return
        try:
            n = append_new_uploads(reg, plan_registration(reg, self.state.repo.videos))
        except Exception as e:
            diagnostics.report_recovered("Adding new uploads to the queue order", e, page="Daily Upload Frequency")
            return
        if n:
            self.status_label.configure(text=f"{_plural(n, 'new upload')} added to the end of the queue order.", text_color=MUTED)

    # ---- the recorded queue order ----

    def _schedule_is_fresh(self) -> bool:
        """The schedule on screen was loaded from YouTube in this run of the app. Until then it is only the copy saved on
        disk, which may be out of date or incomplete -- and working from an incomplete picture is exactly how videos end up
        stacked on top of each other. A repository without the flag (a stand-in) counts as fresh."""
        return bool(getattr(self.state.repo, "refreshed_this_session", True))

    def _update_order_panel(self, reg: QueueOrderStore, videos) -> None:
        problems = reg.integrity_problems() if reg.is_adopted else []
        if not reg.is_adopted:
            self.order_label.configure(
                text="The queue order has not been recorded yet. Frequency changes only follow a recorded order, so nothing can be changed "
                "until you review and record it. (Reviewing changes nothing on YouTube.)", text_color=WARN)
            self.order_btn.configure(text="Recover & review the order…", state="normal" if videos and self._schedule_is_fresh() else "disabled")
            self.order_btn.grid()
            return
        reg_plan = plan_registration(reg, videos)
        if problems:
            self.order_label.configure(text="The recorded order has problems: " + " ".join(problems[:2]), text_color=BAD)
            self.order_btn.grid_remove()
        elif reg_plan.older or reg_plan.no_time:
            n = len(reg_plan.older) + len(reg_plan.no_time)
            self.order_label.configure(
                text=f"{_plural(n, 'scheduled Short')} ha{'s' if n == 1 else 've'} no sequence number and can't simply go to the end, "
                "so nothing can be changed until they are placed.", text_color=WARN)
            self.order_btn.configure(text="Place them…", state="normal" if not reg_plan.no_time else "disabled")
            self.order_btn.grid()
        else:
            recorded = ""
            try:
                recorded = f" on {dt.datetime.fromisoformat(reg.adopted_at).astimezone():%d %b %Y}"
            except (TypeError, ValueError):
                pass
            self.order_label.configure(
                text=f"✓ Queue order recorded{recorded}: {len(reg)} Shorts have a permanent sequence number. This page only ever changes dates and times.",
                text_color=GOOD)
            self.order_btn.grid_remove()

    def _open_order_review(self) -> None:
        if self._review_window is not None and self._review_window.winfo_exists():
            self._review_window.focus_set()
            return
        if not self.state.repo:
            return
        reg = QueueOrderStore.shared()
        videos = self.state.repo.videos
        try:
            if not reg.is_adopted:
                report = recover_order(videos, self.state.history.entries, UploadActivityLog.shared().newest_first())
                self._review_window = OrderReviewDialog(self, self.state, self._order_saved, report=report)
            else:
                self._review_window = OrderReviewDialog(self, self.state, self._order_saved, placement=plan_registration(reg, videos))
        except Exception as e:
            diagnostics.report_recovered("Opening the queue order review", e, page="Daily Upload Frequency")
            messagebox.showerror("Could not open the order review", f"{type(e).__name__}: {e}")

    def _order_saved(self) -> None:
        # every recorded video now has a code: write it into the upload History and keep each description beside its code
        try:
            backfill_order_codes(self.state.repo.videos if self.state.repo else [])
            self.state.notify_history_changed()
        except Exception as e:
            diagnostics.report_recovered("Giving recorded videos their order codes", e, page="Daily Upload Frequency")
        self.status_label.configure(text="The queue order was recorded. Each video now has its code in the Upload history. Nothing on YouTube was changed.", text_color=GOOD)
        self.recompute()

    # ---- calculation ----

    def recompute(self) -> None:
        if not self.winfo_exists():
            return
        now = _now()
        self.time_label.configure(text=f"Now: {now.astimezone().strftime('%d %b %Y, %H:%M')}")
        videos = self.state.repo.videos if self.state.repo else []
        freq = self.slot_control.selected_count()
        reg = QueueOrderStore.shared()
        self._update_order_panel(reg, videos)

        if not reg.is_adopted:
            self._plan = None
            eligible = [v for v in videos if df.is_eligible(v, now)]
            cur, dist = df.current_frequency(eligible)
            self.current_label.configure(text=self._current_text(cur, dist, freq), text_color="#dddddd")
            self.affected_label.configure(text=f"{_plural(len(eligible), 'scheduled Short')}")
            self.summary_label.configure(text="Record the queue order first -- then the preview of your new schedule appears here.", text_color=MUTED)
            self.order_line_label.configure(text="")
            self.issues_label.configure(text="")
            self.cost_label.configure(text="")
            self.confirm_btn.configure(state="disabled")
            self._render_table()
            return

        plan = df.compute_frequency_plan(df.build_queue(videos, reg, now), freq, self.config, now)
        self._plan = plan
        total, changed = len(plan.rows), len(plan.changed_rows)
        self.current_label.configure(text=self._current_text(plan.current_frequency, plan.current_distribution, freq), text_color="#dddddd")
        self.affected_label.configure(text=f"{_plural(total, 'Short')} in the queue")

        if total == 0:
            self.summary_label.configure(text="There are no scheduled Shorts to change.", text_color=MUTED)
        elif changed == 0:
            self.summary_label.configure(text=f"Nothing would change -- the {_plural(total, 'Short')} already follow the queue order at {freq} per day.", text_color=MUTED)
        else:
            text = f"At {freq} per day, {changed} of {total} Short(s) get a new date or time; the other {total - changed} stay exactly as they are."
            before, after = plan.timeline_end_before, plan.timeline_end_after
            if before and after:
                if after < before:
                    text += f" The last Short moves up from {before:%d %b %Y} to {after:%d %b %Y} ({(before - after).days} day(s) sooner)."
                elif after > before:
                    text += f" The last Short moves back from {before:%d %b %Y} to {after:%d %b %Y} ({(after - before).days} day(s) later)."
                else:
                    text += f" The last Short stays on {before:%d %b %Y}."
            self.summary_label.configure(text=text, text_color=GOOD if plan.ok else MUTED)

        if total:
            out = plan.out_of_order_now
            self.order_line_label.configure(text=(
                f"Order after: the queue's sequence, #1 to #{total}, exactly as recorded -- no video moves relative to another. "
                + (f"Right now {_plural(out, 'Short')} sit out of sequence by date; this puts them back in order." if out else "The current dates are already in sequence.")))
        else:
            self.order_line_label.configure(text="")

        lines = []
        for i in plan.issues:
            lines.append(("✗ " if i.is_error else "• ") + i.message)
        self.issues_label.configure(text="\n".join(lines), text_color=BAD if plan.errors else MUTED)
        self.cost_label.configure(text=self._cost_text(changed))
        self.confirm_btn.configure(state="normal" if (changed and plan.ok and self._schedule_is_fresh() and not self._applying) else "disabled")
        self._show_freshness_note()
        self._render_table()

    @staticmethod
    def _current_text(cur: Optional[int], dist: dict, requested: int) -> str:
        if cur is None:
            return f"Current daily frequency: no scheduled Shorts yet.   Requested: {requested} per day"
        spread = ", ".join(f"{n}×{_plural(days, 'day')}" for n, days in list(dist.items())[:6])
        return f"Current daily frequency: {cur} per day (most days).   Requested: {requested} per day\nDays in the schedule hold: {spread}"

    def _cost_text(self, changed: int) -> str:
        if not changed:
            return ""
        cost = changed * COST_VIDEOS_UPDATE
        text = f"YouTube API cost: about {cost:,} units ({COST_VIDEOS_UPDATE} per changed Short). "
        quota = getattr(self.state.client, "quota", None)
        if quota is not None:
            try:
                left = quota.remaining(self.state.config.daily_quota_budget_units)
                text += f"The app has counted {self.state.config.daily_quota_budget_units - left:,} of today's {self.state.config.daily_quota_budget_units:,}-unit budget. "
            except Exception:
                pass
        return text + ("If YouTube stops part-way, nothing is lost: the Shorts due soonest are updated first, so everything already updated "
                       "is correct and in order; run it again later and only the remaining Shorts are updated.")

    def _show_freshness_note(self) -> None:
        if self._applying:
            return
        if self._schedule_is_fresh():
            if self.status_label.cget("text").startswith(FRESHNESS_NOTE_PREFIX):
                self.status_label.configure(text="")
            return
        self.status_label.configure(
            text=f"{FRESHNESS_NOTE_PREFIX} -- nothing can be applied until your full schedule has loaded from YouTube "
            "(it loads automatically; press Refresh Calculation if this stays).", text_color=WARN)

    # ---- the preview table ----

    def _render_table(self) -> None:
        for w in self.table.winfo_children():
            if not isinstance(w, DevTag):
                w.destroy()
        plan = self._plan
        if plan is None or not plan.rows:
            msg = ("Record the queue order to see the preview." if plan is None else "Nothing to preview -- no scheduled Shorts.")
            ctk.CTkLabel(self.table, text=msg, text_color=MUTED).grid(row=1, column=0, columnspan=6, pady=30)
            return
        by_id = {v.video_id: v for v in (self.state.repo.videos if self.state.repo else [])}
        shown = plan.rows[: self._shown_rows]
        for i, row in enumerate(shown, start=1):
            out_of_place = row.position_by_date != row.position
            ctk.CTkLabel(self.table, text=format_code(row.seq), width=56, text_color="#5b9bd5", font=ctk.CTkFont(weight="bold")).grid(row=i, column=0, padx=(10, 0), pady=3)
            ctk.CTkLabel(self.table, text=f"#{row.position_by_date}", width=50, text_color=BAD if out_of_place else MUTED).grid(row=i, column=1, pady=3)
            thumb = ctk.CTkLabel(self.table, text="", width=THUMB_SIZE[0], height=THUMB_SIZE[1], fg_color="#2a2a2a", corner_radius=4)
            thumb.grid(row=i, column=2, padx=6, pady=3)
            video = by_id.get(row.video_id)
            if video and video.thumbnail_url:
                thumbnail_cache.get_async(self, video.thumbnail_url, THUMB_SIZE, lambda img, lbl=thumb: self._apply_thumb(lbl, img))
            title = row.title if len(row.title) <= 55 else row.title[:52] + "..."
            ctk.CTkLabel(self.table, text=title, anchor="w").grid(row=i, column=3, sticky="ew", padx=10, pady=3)
            ctk.CTkLabel(self.table, text=_fmt(row.old_publish_at), anchor="w", text_color=MUTED).grid(row=i, column=4, sticky="ew", padx=10, pady=3)
            ctk.CTkLabel(self.table, text=_fmt(row.new_publish_at), anchor="w", text_color=CHANGED_COLOR if row.changed else MUTED).grid(row=i, column=5, sticky="ew", padx=10, pady=3)
        if len(plan.rows) > len(shown):
            more = min(PREVIEW_PAGE, len(plan.rows) - len(shown))
            ctk.CTkButton(self.table, text=f"Show {more} more ({len(plan.rows) - len(shown)} not shown)", fg_color="transparent", border_width=1,
                          command=self._show_more).grid(row=len(shown) + 1, column=0, columnspan=6, pady=10)

    def _show_more(self) -> None:
        self._shown_rows += PREVIEW_PAGE
        self._render_table()

    def _apply_thumb(self, label: ctk.CTkLabel, image: ctk.CTkImage) -> None:
        if not label.winfo_exists():
            return
        label.configure(image=image, text="")

    # ---- apply ----

    @staticmethod
    def _signature(plan: df.FrequencyPlan) -> tuple:
        return (plan.frequency, tuple((r.video_id, r.new_publish_at.astimezone().isoformat()) for r in plan.rows))

    def _confirm(self) -> None:
        plan = self._plan
        if self._applying or plan is None or not plan.ok or not plan.changed_rows or not self._schedule_is_fresh():
            return
        total, changed = len(plan.rows), len(plan.changed_rows)
        before, after = plan.timeline_end_before, plan.timeline_end_after
        move = ""
        if before and after and before != after:
            move = f"\n• The last Short moves from {before:%d %b %Y} to {after:%d %b %Y}."
        if not messagebox.askyesno(
            "Apply this daily frequency?",
            f"Change the schedule from {plan.current_frequency or '?'} to {plan.frequency} Short(s) per day?\n\n"
            f"• {changed} of {total} Short(s) get a new date or time; the rest are untouched.\n"
            f"• The order of all {total} Shorts does not change (queue #1 to #{total}).{move}\n"
            f"• About {changed * COST_VIDEOS_UPDATE:,} YouTube API units.\n\n"
            "Every change is recorded in the schedule-change history with its previous time. Before anything is sent, your schedule is "
            "re-read from YouTube and re-checked; if anything differs, nothing is sent.\n\nApply it?",
        ):
            return

        self._applying = True
        self.confirm_btn.configure(state="disabled", text="Checking YouTube...")
        self.refresh_btn.configure(state="disabled")
        self.verify_label.configure(text="")
        self.status_label.configure(text="Re-reading your schedule from YouTube before changing anything...", text_color=MUTED)
        confirmed = self._signature(plan)
        freq = plan.frequency

        def worker():
            err = None
            try:
                self.state.repo.refresh()
            except Exception as e:
                err = f"Could not re-read your schedule from YouTube ({type(e).__name__}: {e}). Nothing was changed."
            safe_after(self, lambda: self._start_apply(confirmed, freq, err))

        start_worker(worker, name="Daily Upload Frequency pre-apply refresh", page="Daily Upload Frequency",
                     on_error=lambda e: safe_after(self, lambda: self._abort_apply(f"Could not re-read your schedule: {e}. Nothing was changed.")))

    def _abort_apply(self, message: str) -> None:
        self._applying = False
        self.confirm_btn.configure(text="Confirm & Apply")
        self.refresh_btn.configure(state="normal")
        self.status_label.configure(text=message, text_color=WARN)
        self.recompute()

    def _start_apply(self, confirmed: tuple, freq: int, err: Optional[str]) -> None:
        if not self.winfo_exists():
            return
        if err:
            self._abort_apply(err)
            return
        now = _now()
        reg = QueueOrderStore.shared(reload=True)
        fresh = df.compute_frequency_plan(df.build_queue(self.state.repo.videos, reg, now), freq, self.config, now)
        if not fresh.ok or self._signature(fresh) != confirmed:
            self._plan = fresh
            self._abort_apply(
                "Your schedule on YouTube changed (or no longer passes the checks) since the preview, so nothing was sent. "
                "The preview has been updated -- please review it and confirm again.")
            return
        self._applied_plan = fresh
        total = len(fresh.rows)
        n = len(fresh.changed_rows)
        self.confirm_btn.configure(text="Applying...")
        self.status_label.configure(text=f"Preparing to apply {n} update(s)...", text_color=MUTED)

        def status_cb(text: str) -> None:
            safe_after(self, lambda: self.status_label.configure(text=text))

        def done_cb(succeeded: int, tot: int, failed: list[str]) -> None:
            safe_after(self, lambda: self._apply_done(succeeded, tot, failed))

        def order_check() -> list[str]:
            """Runs right before anything is sent: the plan must still be exactly the validated one."""
            check_now = _now()
            again = df.compute_frequency_plan(df.build_queue(self.state.repo.videos, QueueOrderStore.shared(reload=True), check_now), freq, self.config, check_now)
            problems = [i.message for i in again.errors]
            if again.order_after != fresh.order_after:
                problems.append("The queue changed since the preview was validated.")
            return problems

        description = f"Daily Upload Frequency: {fresh.current_frequency or '?'}/day -> {freq}/day ({n} of {total} Short(s) rescheduled)"
        error = apply_schedule_result(self.state, fresh.to_result(), description, status_cb, done_cb, order_check=order_check, require_scheduled=True)
        if error:
            self._applied_plan = None
            self._applying = False
            self.confirm_btn.configure(text="Confirm & Apply")
            self.refresh_btn.configure(state="normal")
            self.status_label.configure(text="", text_color=MUTED)
            self.recompute()
            report_error(self, self.state, "0.13.3.1", "Applying Daily Upload Frequency", error,
                         operation=f"Daily Upload Frequency -- {freq}/day")

    def _apply_done(self, succeeded: int, total: int, failed: list[str]) -> None:
        self._applying = False
        self.confirm_btn.configure(text="Confirm & Apply")
        self.refresh_btn.configure(state="normal")
        self.status_label.configure(text="")
        # apply_schedule_result() deliberately doesn't call these itself (its worker is a background thread and these
        # listeners touch Tk widgets) -- done_cb is already on the main thread by now, so it is safe to fire them here.
        self.state.notify_videos_changed()
        self.state.notify_history_changed()
        self.recompute()
        plan = self._applied_plan
        if failed:
            messagebox.showwarning(
                "Some Shorts could not be updated",
                f"Applied {succeeded}/{total}. {len(failed)} problem(s):\n" + "\n".join(failed[:10])
                + "\n\nNothing else was changed. Run it again when the problem is fixed -- only the Shorts that still differ are updated.")
        if succeeded and plan is not None:
            self._verify_after_apply(plan, partial=bool(failed))

    def _verify_after_apply(self, plan: df.FrequencyPlan, partial: bool) -> None:
        """Reads the schedule back from YouTube and compares it with the validated plan."""
        self.verify_label.configure(text="Checking what YouTube now holds against the validated plan...", text_color=MUTED)
        ids = [r.video_id for r in plan.rows]

        def worker():
            try:
                remote = self.state.client.get_videos(ids)
                problems = df.verify_remote(plan, remote)
                result = (problems, None)
            except Exception as e:
                result = ([], f"{type(e).__name__}: {e}")
            safe_after(self, lambda: self._show_verification(plan, result[0], result[1], partial))

        start_worker(worker, name="Daily Upload Frequency verification", page="Daily Upload Frequency",
                     on_error=lambda e: safe_after(self, lambda: self._show_verification(plan, [], str(e), partial)))

    def _show_verification(self, plan: df.FrequencyPlan, problems: list[str], error: Optional[str], partial: bool) -> None:
        if not self.winfo_exists():
            return
        if error:
            self.verify_label.configure(text=f"Could not read YouTube back to verify ({error}). Press Refresh Calculation to check.", text_color=WARN)
        elif problems:
            self.verify_label.configure(
                text=f"✗ YouTube does not match the plan for {_plural(len(problems), 'Short')}: " + " | ".join(problems[:3]) + (" ..." if len(problems) > 3 else ""),
                text_color=BAD)
        else:
            self.verify_label.configure(
                text=f"✓ Verified on YouTube: all {len(plan.rows)} Shorts are scheduled exactly as planned, in queue order." if not partial
                else "Everything that was applied matches the plan on YouTube.", text_color=GOOD)

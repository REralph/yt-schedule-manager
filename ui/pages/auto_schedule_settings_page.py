"""AS-001 -- AUTO BOOST (formerly Auto Schedule) settings tab: keeps the channel on pace for a
daily or weekly view target not by merely recommending more Shorts, but by actually
PULLING Shorts forward out of the existing future schedule to fill tomorrow's shortfall,
then applying that change to YouTube -- see scheduling/auto_schedule.py's
compute_auto_schedule_pull_forward() and the headless runner (auto_schedule_runner.py),
which does the same thing unattended once Automation is ON.

SET-0012.1 (Upload Time Presets) now lives at the TOP of this page: the publishing
window, how many slots/day, and each count's exact times are all defined there, and Auto
Schedule reads them directly (scheduling/slot_rules.py) -- there is no second, separate
copy of "normal videos/day" or "publishing window" anywhere else on this page anymore.

The old Automatic/Manual "expected views per Short" estimate (a trimmed mean off recent
published Shorts) is gone -- ESTIMATED VIEWS PER SHORT is now a single number the user
sets directly, used only for planning how many Shorts a shortfall requires. It never
changes what a video's real view count is or pretends a Short will get that many views.

This page renders a live PREVIEW (current schedule vs. what Auto Boost would do right
now) from a background fetch of recently-published Shorts and the real future schedule --
read-only until Apply Recommended Changes is clicked. That fetch only happens on page
load, Refresh, or right after Save -- never on a timer, to avoid burning API quota
silently."""
from __future__ import annotations

import datetime as dt
import threading
from tkinter import messagebox
from typing import Optional

import customtkinter as ctk

from data import diagnostics
from data.auto_schedule_config import (
    MAX_BOOST_EXTRA,
    MAX_SLOT_COUNT,
    TARGET_MODE_DAILY,
    TARGET_MODE_WEEKLY,
    AutoScheduleConfig,
)
from data.auto_schedule_history import AutoScheduleDecision, AutoScheduleHistoryStore
from scheduling.auto_schedule import (
    DayPerformance,
    DecisionResult,
    PerformanceResult,
    WeekProgress,
    compute_auto_schedule_pull_forward,
    compute_week_progress,
    decide_daily,
    decide_video_count,
    evaluate_daily_performance,
    group_by_published_day,
)
from scheduling.increase_slots import IncreaseSlotsResult
from ui import thumbnail_cache
from ui.app_state import AppState
from ui.dev_reporting import report_error
from ui.schedule_apply import apply_schedule_result
from ui.threading_utils import safe_after, start_worker
from ui.widgets.dev_tag import DevTag
from ui.widgets.slot_selector import SlotCountControl

GOOD = "#7fd17f"
BAD = "#e05a5a"
MUTED = "#888888"
CHANGED_COLOR = "#5b9bd5"
DAY_NAMES = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
THUMB_SIZE = (48, 27)
PREVIEW_PAGE = 30  # rows of the recommended-changes preview drawn at a time (each row is ~4 widgets; the box only shows ~6 anyway)


def _fmt_compact(n: int) -> str:
    if n >= 1000:
        return f"{n / 1000:.1f}k"
    return str(n)


def _fmt_dt(d: Optional[dt.datetime]) -> str:
    if d is None:
        return "Not previously scheduled"
    return d.astimezone().strftime("%d %b %Y, %H:%M")


BOOST_CHOICES = ["Calculated"] + [f"+{n}" for n in range(1, MAX_BOOST_EXTRA + 1)]


def boost_label(extra: int) -> str:
    return "Calculated" if extra <= 0 else f"+{min(extra, MAX_BOOST_EXTRA)}"


def boost_value(label: str) -> int:
    return 0 if label == "Calculated" else int(label.lstrip("+"))


class AutoScheduleSettingsPage(ctk.CTkFrame):
    def __init__(self, master, state: AppState, **kwargs):
        super().__init__(master, fg_color="transparent", **kwargs)
        self.state = state
        self.config = AutoScheduleConfig.load()

        self._by_day: dict[dt.date, DayPerformance] = {}
        self._decision: Optional[DecisionResult] = None
        self._week_progress: Optional[WeekProgress] = None
        self._pull_forward: Optional[IncreaseSlotsResult] = None
        self._preview_shown = PREVIEW_PAGE
        self._target_date: Optional[dt.date] = None
        self._selected_day: Optional[dt.date] = None
        self._day_buttons: dict[dt.date, ctk.CTkButton] = {}
        self._applying = False

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(0, weight=1)
        self.scroll = ctk.CTkScrollableFrame(self, fg_color="transparent")
        self.scroll.grid(row=0, column=0, sticky="nsew")
        self.scroll.grid_columnconfigure(0, weight=1)

        header = ctk.CTkFrame(self.scroll, fg_color="transparent")
        header.pack(fill="x", padx=16, pady=(16, 4))
        ctk.CTkLabel(header, text="AUTO BOOST", font=ctk.CTkFont(size=18, weight="bold")).pack(side="left")
        DevTag(header, state, "0.11").show(side="left", padx=(8, 0))
        ctk.CTkLabel(
            self.scroll,
            text="Checks how many views your Shorts got on the last completed day. If that is below your target, "
                 "tomorrow is boosted with extra video(s) pulled forward from the schedule, and every later video "
                 "moves up to fill the gap -- order kept. It only reschedules Shorts you already uploaded; it "
                 "never uploads anything new.",
            text_color=MUTED, font=ctk.CTkFont(size=11), anchor="w", justify="left", wraplength=640,
        ).pack(fill="x", padx=16, pady=(0, 10))

        self._build_slot_presets_panel()
        self._build_settings_panel()
        self._build_preview_card()
        self._build_weekly_card()

        self._refresh_preview()

    def _row(self, parent, text: str) -> None:
        ctk.CTkLabel(parent, text=text, font=ctk.CTkFont(size=12, weight="bold")).pack(anchor="w", padx=14, pady=(10, 2))

    def _field(self, parent, label: str, var: ctk.StringVar, dev_id: str) -> None:
        row = ctk.CTkFrame(parent, fg_color="transparent")
        row.pack(fill="x", padx=14, pady=(0, 10))
        ctk.CTkLabel(row, text=label, width=230, anchor="w").pack(side="left")
        ctk.CTkEntry(row, textvariable=var, width=100).pack(side="left")
        DevTag(row, self.state, dev_id).show(side="left", padx=(10, 0))

    # ---- SET-0012.1: Upload Time Presets (moved to the top -- point 1) ----

    def _build_slot_presets_panel(self) -> None:
        panel = ctk.CTkFrame(self.scroll, fg_color="#1a1a1a", corner_radius=8, border_width=1, border_color="#3a5a72")
        panel.pack(fill="x", padx=16, pady=(0, 12))

        header_row = ctk.CTkFrame(panel, fg_color="transparent")
        header_row.pack(fill="x", padx=14, pady=(12, 2))
        ctk.CTkLabel(header_row, text="SET-0012.1 — UPLOAD TIME PRESETS", font=ctk.CTkFont(size=13, weight="bold"), text_color="#8fb0cc").pack(side="left")
        DevTag(header_row, self.state, "0.11.22").show(side="left", padx=(8, 0))
        ctk.CTkLabel(
            panel, text="The one source of truth for publishing times -- Auto Boost reads these directly, "
            "there is no separate copy of the window or daily rate anywhere else.",
            text_color=MUTED, font=ctk.CTkFont(size=11), anchor="w", justify="left", wraplength=600,
        ).pack(fill="x", padx=14, pady=(0, 10))

        self._row(panel, "Publishing Window")
        window_row = ctk.CTkFrame(panel, fg_color="transparent")
        window_row.pack(fill="x", padx=14, pady=(0, 10))
        self.window_start_var = ctk.StringVar(value=self.config.window_start.strftime("%H:%M"))
        self.window_end_var = ctk.StringVar(value=self.config.window_end.strftime("%H:%M"))
        start_entry = ctk.CTkEntry(window_row, textvariable=self.window_start_var, width=70)
        start_entry.pack(side="left")
        ctk.CTkLabel(window_row, text=" to ").pack(side="left")
        end_entry = ctk.CTkEntry(window_row, textvariable=self.window_end_var, width=70)
        end_entry.pack(side="left")
        ctk.CTkLabel(window_row, text="  (24-hour, e.g. 06:00)", text_color=MUTED).pack(side="left")
        DevTag(window_row, self.state, "0.11.22.1").show(side="left", padx=(10, 0))
        for entry in (start_entry, end_entry):
            entry.bind("<Return>", lambda _e: self._save_window())
            entry.bind("<FocusOut>", lambda _e: self._save_window())
        self.window_status_label = ctk.CTkLabel(panel, text="", font=ctk.CTkFont(size=11))
        self.window_status_label.pack(anchor="w", padx=14, pady=(0, 8))

        self._row(panel, "How Many Slots Per Day?")
        # Default 2/day unless the user already saved a different preference -- that's
        # just AutoScheduleConfig's own field default (see data/auto_schedule_config.py),
        # so nothing extra is needed here to honor "saved preference takes priority".
        self.slot_control = SlotCountControl(
            panel, self.state, self.config, initial_count=self.config.normal_videos_per_day,
            on_count_changed=self._on_slot_count_changed,
            selector_dev_id="0.11.22.2", gear_dev_id="0.11.22.4", panel_dev_id="0.11.22.3",
        )
        self.slot_control.pack(fill="x", padx=14, pady=(0, 12))

    def _save_window(self) -> None:
        try:
            window_start = dt.datetime.strptime(self.window_start_var.get().strip(), "%H:%M").time()
            window_end = dt.datetime.strptime(self.window_end_var.get().strip(), "%H:%M").time()
        except ValueError:
            self.window_status_label.configure(text="Publishing window times must be in 24-hour HH:MM format, e.g. 06:00.", text_color=BAD)
            return
        if window_end <= window_start:
            self.window_status_label.configure(text="Publishing window end must be after the start.", text_color=BAD)
            return
        self.config.window_start_hour = window_start.hour
        self.config.window_start_minute = window_start.minute
        self.config.window_end_hour = window_end.hour
        self.config.window_end_minute = window_end.minute
        self.config.save()
        self.window_status_label.configure(text="Publishing window saved.", text_color=GOOD)
        self.after(2000, lambda: self.window_status_label.configure(text="") if self.window_status_label.winfo_exists() else None)

    def _on_slot_count_changed(self, count: int) -> None:
        """Selecting a different slot count persists immediately -- the selected count
        must survive a restart on its own, not only via a separate save step (point 1:
        the saved preference takes priority over the default from then on)."""
        self.config.normal_videos_per_day = count
        self.config.save()

    # ---- settings panel (targets + planning figures) ----

    def _build_settings_panel(self) -> None:
        panel = ctk.CTkFrame(self.scroll, fg_color="#1a1a1a", corner_radius=8)
        panel.pack(fill="x", padx=16, pady=(0, 12))

        self._row(panel, "Automation")
        toggle_row = ctk.CTkFrame(panel, fg_color="transparent")
        toggle_row.pack(fill="x", padx=14, pady=(0, 10))
        self.enabled_var = ctk.BooleanVar(value=self.config.enabled)
        ctk.CTkSwitch(toggle_row, text="ON / OFF", variable=self.enabled_var).pack(side="left")
        DevTag(toggle_row, self.state, "0.11.1").show(side="left", padx=(10, 0))

        self._row(panel, "Target")
        mode_row = ctk.CTkFrame(panel, fg_color="transparent")
        mode_row.pack(fill="x", padx=14, pady=(0, 8))
        self.mode_var = ctk.StringVar(value="Weekly Target" if self.config.target_mode == TARGET_MODE_WEEKLY else "Daily Target")
        ctk.CTkSegmentedButton(mode_row, variable=self.mode_var, values=["Daily Target", "Weekly Target"]).pack(side="left")
        DevTag(mode_row, self.state, "0.11.13").show(side="left", padx=(10, 0))

        self.daily_target_var = ctk.StringVar(value=str(self.config.daily_target_views))
        self._field(panel, "Daily Target Views", self.daily_target_var, "0.11.2")

        weekly_row = ctk.CTkFrame(panel, fg_color="transparent")
        weekly_row.pack(fill="x", padx=14, pady=(0, 2))
        ctk.CTkLabel(weekly_row, text="Weekly Target Views", width=230, anchor="w").pack(side="left")
        self.weekly_target_var = ctk.StringVar(value=str(self.config.weekly_target_views))
        weekly_entry = ctk.CTkEntry(weekly_row, textvariable=self.weekly_target_var, width=100)
        weekly_entry.pack(side="left")
        weekly_entry.bind("<KeyRelease>", lambda e: self._update_recommended_daily())
        DevTag(weekly_row, self.state, "0.11.14").show(side="left", padx=(10, 0))
        self.recommended_daily_label = ctk.CTkLabel(panel, text="", text_color=MUTED, font=ctk.CTkFont(size=11))
        self.recommended_daily_label.pack(anchor="w", padx=14, pady=(0, 10))
        DevTag(panel, self.state, "0.11.15").show(anchor="w", padx=(14, 0))
        self._update_recommended_daily()

        self._row(panel, "Boost")
        boost_row = ctk.CTkFrame(panel, fg_color="transparent")
        boost_row.pack(fill="x", padx=14, pady=(0, 2))
        ctk.CTkLabel(boost_row, text="Extra videos when below target", width=230, anchor="w").pack(side="left")
        self.boost_var = ctk.StringVar(value=boost_label(self.config.boost_extra_videos))
        ctk.CTkSegmentedButton(boost_row, variable=self.boost_var, values=BOOST_CHOICES, command=lambda _v: self._update_boost_hint()).pack(side="left")
        DevTag(boost_row, self.state, "0.11.25").show(side="left", padx=(10, 0))
        self.boost_hint_label = ctk.CTkLabel(panel, text="", text_color=MUTED, font=ctk.CTkFont(size=11), anchor="w", justify="left", wraplength=640)
        self.boost_hint_label.pack(anchor="w", padx=14, pady=(2, 10))
        self._update_boost_hint()

        self.estimated_views_var = ctk.StringVar(value=str(self.config.estimated_views_per_short))
        self._field(panel, "Estimated Views Per Short", self.estimated_views_var, "0.11.23")

        self.max_var = ctk.StringVar(value=str(self.config.max_videos_per_day))
        self._field(panel, "Maximum Shorts / Day", self.max_var, "0.11.5")

        self.minimum_var = ctk.StringVar(value=str(self.config.minimum_daily_views))
        self._field(panel, "Minimum Daily Views Trigger", self.minimum_var, "0.11.16")

        save_row = ctk.CTkFrame(panel, fg_color="transparent")
        save_row.pack(fill="x", padx=14, pady=(6, 14))
        self.save_btn = ctk.CTkButton(save_row, text="Save Settings", height=32, fg_color="#2f7a3d", hover_color="#256030", command=self._save)
        self.save_btn.pack(side="left")
        self.save_status_label = ctk.CTkLabel(save_row, text="", text_color=GOOD)
        self.save_status_label.pack(side="left", padx=(12, 0))
        DevTag(save_row, self.state, "0.11.10").show(side="left", padx=(10, 0))

    def _update_boost_hint(self) -> None:
        extra = boost_value(self.boost_var.get())
        if extra == 0:
            text = ("Calculated: works out how many extra videos tomorrow needs from how far short the views were, "
                    "using Estimated Views Per Short below.")
        else:
            text = (f"If the last completed day is below target, tomorrow gets exactly +{extra} extra video{'s' if extra != 1 else ''} "
                    f"(never more than Maximum Shorts / Day). Every later video then moves up one step to fill the gap.")
        self.boost_hint_label.configure(text=text)

    def _update_recommended_daily(self) -> None:
        try:
            weekly = int(self.weekly_target_var.get().strip())
        except ValueError:
            self.recommended_daily_label.configure(text="Recommended Daily: --")
            return
        self.recommended_daily_label.configure(text=f"{weekly:,} ÷ 7 = Recommended Daily: {weekly / 7:,.0f}")

    # ---- preview card: what Auto Boost would do right now ----

    def _build_preview_card(self) -> None:
        card = ctk.CTkFrame(self.scroll, fg_color="#1a1a1a", corner_radius=8)
        card.pack(fill="x", padx=16, pady=(0, 12))
        header_row = ctk.CTkFrame(card, fg_color="transparent")
        header_row.pack(fill="x", padx=14, pady=(12, 4))
        ctk.CTkLabel(header_row, text="RECOMMENDED CHANGES", font=ctk.CTkFont(size=14, weight="bold")).pack(side="left")
        DevTag(header_row, self.state, "0.11.18").show(side="left", padx=(8, 0))
        ctk.CTkButton(header_row, text="Refresh", width=80, height=24, command=self._refresh_preview).pack(side="right")
        DevTag(header_row, self.state, "0.11.20").show(side="right", padx=(0, 8))

        self.estimate_summary_label = ctk.CTkLabel(card, text="Calculating...", justify="left", anchor="w", font=ctk.CTkFont(size=12))
        self.estimate_summary_label.pack(fill="x", padx=14)
        DevTag(card, self.state, "0.11.24.1").show(anchor="w", padx=(14, 0))

        self.recommendation_label = ctk.CTkLabel(card, text="", text_color=MUTED, justify="left", anchor="w", wraplength=640)
        self.recommendation_label.pack(fill="x", padx=14, pady=(6, 10))
        DevTag(card, self.state, "0.11.18.2").show(anchor="w", padx=(14, 0))

        columns = ctk.CTkFrame(card, fg_color="#151515", corner_radius=6)
        columns.pack(fill="x", padx=14)
        columns.grid_columnconfigure(0, weight=0)
        columns.grid_columnconfigure(1, weight=2)
        columns.grid_columnconfigure(2, weight=1)
        columns.grid_columnconfigure(3, weight=1)
        ctk.CTkLabel(columns, text="", width=THUMB_SIZE[0] + 12).grid(row=0, column=0, padx=8, pady=4)
        ctk.CTkLabel(columns, text="Video", font=ctk.CTkFont(size=11, weight="bold")).grid(row=0, column=1, sticky="w", padx=8, pady=4)
        ctk.CTkLabel(columns, text="Current", font=ctk.CTkFont(size=11, weight="bold")).grid(row=0, column=2, sticky="w", padx=8, pady=4)
        ctk.CTkLabel(columns, text="New", font=ctk.CTkFont(size=11, weight="bold")).grid(row=0, column=3, sticky="w", padx=8, pady=4)

        self.preview_table = ctk.CTkScrollableFrame(card, fg_color="transparent", height=160)
        self.preview_table.pack(fill="x", padx=14, pady=(0, 4))
        self.preview_table.grid_columnconfigure(0, weight=0)
        self.preview_table.grid_columnconfigure(1, weight=2)
        self.preview_table.grid_columnconfigure(2, weight=1)
        self.preview_table.grid_columnconfigure(3, weight=1)
        DevTag(self.preview_table, self.state, "0.11.24.2").show(anchor="w")

        apply_row = ctk.CTkFrame(card, fg_color="transparent")
        apply_row.pack(fill="x", padx=14, pady=(4, 14))
        self.apply_btn = ctk.CTkButton(
            apply_row, text="Apply Recommended Changes", width=220, fg_color="#2f7a3d", hover_color="#256030",
            font=ctk.CTkFont(weight="bold"), command=self._apply_pull_forward, state="disabled",
        )
        self.apply_btn.pack(side="left")
        DevTag(apply_row, self.state, "0.11.24.3").show(side="left", padx=(8, 0))
        self.apply_status_label = ctk.CTkLabel(apply_row, text="", text_color=MUTED)
        self.apply_status_label.pack(side="left", padx=(12, 0))

    def _render_preview_table(self) -> None:
        for w in self.preview_table.winfo_children():
            w.destroy()
        if self._pull_forward is None or not self._pull_forward.preview_rows:
            ctk.CTkLabel(self.preview_table, text="Nothing to preview yet.", text_color=MUTED).grid(row=0, column=0, columnspan=4, pady=16)
            return
        by_id = {v.video_id: v for v in (self.state.repo.videos if self.state.repo else [])}
        ordered = sorted(self._pull_forward.preview_rows, key=lambda r: r.new_publish_at)
        total = len(ordered)
        ordered = ordered[: self._preview_shown]
        for i, row in enumerate(ordered):
            video = by_id.get(row.video_id)
            thumb_label = ctk.CTkLabel(self.preview_table, text="", width=THUMB_SIZE[0], height=THUMB_SIZE[1], fg_color="#2a2a2a", corner_radius=4)
            thumb_label.grid(row=i, column=0, padx=8, pady=2)
            if video and video.thumbnail_url:
                thumbnail_cache.get_async(self, video.thumbnail_url, THUMB_SIZE, lambda img, lbl=thumb_label: self._apply_thumb(lbl, img))
            title_txt = row.title if len(row.title) <= 50 else row.title[:47] + "..."
            ctk.CTkLabel(self.preview_table, text=title_txt, anchor="w", font=ctk.CTkFont(size=11)).grid(row=i, column=1, sticky="ew", padx=8, pady=2)
            ctk.CTkLabel(self.preview_table, text=_fmt_dt(row.old_publish_at), anchor="w", text_color=MUTED, font=ctk.CTkFont(size=11)).grid(row=i, column=2, sticky="ew", padx=8, pady=2)
            new_color = CHANGED_COLOR if row.changed else MUTED
            ctk.CTkLabel(self.preview_table, text=_fmt_dt(row.new_publish_at), anchor="w", text_color=new_color, font=ctk.CTkFont(size=11)).grid(row=i, column=3, sticky="ew", padx=8, pady=2)
        if total > len(ordered):
            more = min(PREVIEW_PAGE, total - len(ordered))
            ctk.CTkButton(
                self.preview_table, text=f"Show {more} more ({total - len(ordered)} not shown)", fg_color="transparent", border_width=1,
                command=self._show_more_preview,
            ).grid(row=len(ordered), column=0, columnspan=4, pady=8)

    def _show_more_preview(self) -> None:
        self._preview_shown += PREVIEW_PAGE
        self._render_preview_table()

    def _apply_thumb(self, label: ctk.CTkLabel, image: ctk.CTkImage) -> None:
        if not label.winfo_exists():
            return
        label.configure(image=image, text="")

    # ---- weekly performance card ----

    def _build_weekly_card(self) -> None:
        card = ctk.CTkFrame(self.scroll, fg_color="#1a1a1a", corner_radius=8)
        card.pack(fill="x", padx=16, pady=(0, 20))
        header_row = ctk.CTkFrame(card, fg_color="transparent")
        header_row.pack(fill="x", padx=14, pady=(12, 4))
        ctk.CTkLabel(header_row, text="WEEKLY PERFORMANCE", font=ctk.CTkFont(size=14, weight="bold")).pack(side="left")
        DevTag(header_row, self.state, "0.11.19").show(side="left", padx=(8, 0))

        self.weekly_summary_label = ctk.CTkLabel(card, text="", text_color=MUTED, justify="left", anchor="w")
        self.weekly_summary_label.pack(fill="x", padx=14, pady=(0, 8))
        DevTag(card, self.state, "0.11.19.1").show(anchor="w", padx=(14, 0))

        self.week_row = ctk.CTkFrame(card, fg_color="transparent")
        self.week_row.pack(fill="x", padx=14, pady=(0, 4))
        for i in range(7):
            self.week_row.grid_columnconfigure(i, weight=1, uniform="day")

        self.detail_frame = ctk.CTkFrame(card, fg_color="#141414", corner_radius=6)
        self.detail_frame.pack(fill="x", padx=14, pady=(8, 14))
        self.detail_label = ctk.CTkLabel(
            self.detail_frame, text="Click a day above for a plain-language explanation.",
            text_color=MUTED, justify="left", anchor="w",
        )
        self.detail_label.pack(fill="x", padx=10, pady=8)
        DevTag(self.detail_frame, self.state, "0.11.19.3").show(anchor="w", padx=(10, 0))

    def _render_week_row(self, today: dt.date) -> None:
        for w in self.week_row.winfo_children():
            w.destroy()
        self._day_buttons.clear()

        week_start = today - dt.timedelta(days=today.weekday())
        daily_target = self.config.daily_target_views
        for i, name in enumerate(DAY_NAMES):
            day = week_start + dt.timedelta(days=i)
            col = ctk.CTkFrame(self.week_row, fg_color="#141414", corner_radius=6)
            col.grid(row=0, column=i, sticky="nsew", padx=2)
            ctk.CTkLabel(col, text=name, font=ctk.CTkFont(size=11, weight="bold")).pack(pady=(6, 0))
            perf = self._by_day.get(day)
            if perf is None:
                ctk.CTkLabel(col, text="—", text_color=MUTED, font=ctk.CTkFont(size=11)).pack()
                ctk.CTkLabel(col, text="—", text_color=MUTED, font=ctk.CTkFont(size=10)).pack(pady=(0, 6))
                DevTag(col, self.state, "0.11.19.2").show(pady=(0, 4))
                continue
            reached = perf.combined_views >= daily_target
            ctk.CTkLabel(col, text=f"{perf.video_count}", font=ctk.CTkFont(size=11)).pack()
            ctk.CTkLabel(col, text=_fmt_compact(perf.combined_views), font=ctk.CTkFont(size=10)).pack()
            btn = ctk.CTkButton(
                col, text=("✓" if reached else "✗"), width=28, height=20,
                fg_color=(GOOD if reached else BAD), hover_color=(GOOD if reached else BAD),
                text_color="#111111", command=lambda d=day: self._select_day(d),
            )
            btn.pack(pady=(2, 6))
            self._day_buttons[day] = btn
            DevTag(col, self.state, "0.11.19.2").show(pady=(0, 2))

    def _select_day(self, day: dt.date) -> None:
        self._selected_day = day
        self._render_day_detail()

    def _render_day_detail(self) -> None:
        day = self._selected_day
        if day is None or day not in self._by_day:
            self.detail_label.configure(text="Click a day above for a plain-language explanation.")
            return
        perf = self._by_day[day]
        daily_target = self.config.daily_target_views
        reached = perf.combined_views >= daily_target
        expected = self.config.estimated_views_per_short

        # Recomputed the same way the real decision would be, treating this day as the
        # most recently completed one -- gives a live, always-available "what would Auto
        # Boost have done next" explanation without needing a separate stored log entry
        # per day.
        performance = evaluate_daily_performance(self._by_day, day + dt.timedelta(days=1), daily_target)
        decision = decide_daily(performance, self.config, expected)

        lines = [
            day.strftime("%d %B").upper(),
            "",
            f"Shorts uploaded:       {perf.video_count}",
            f"Combined views:        {perf.combined_views:,}",
            f"Target:                {daily_target:,}",
            f"Target reached:        {'✓' if reached else '✗'}",
        ]
        if reached:
            lines.append("")
            lines.append(f"Tomorrow:              Return to {self.config.normal_videos_per_day} Short(s)")
        else:
            lines.append("Estimated additional")
            lines.append(f"Shorts required:       {decision.additional_shorts}")
            lines.append("")
            lines.append("Reason:")
            lines.append("This day's performance was below the target, so Auto")
            lines.append(f"Boost would pull {decision.additional_shorts} more Short(s) forward for the following day.")
        self.detail_label.configure(text="\n".join(lines))

    # ---- live preview (background, read-only until Apply is clicked) ----

    def _refresh_preview(self) -> None:
        if not self.state.client or not self.state.repo:
            self.estimate_summary_label.configure(text="Sign in to YouTube to compute a live preview.")
            self.recommendation_label.configure(text="")
            self.weekly_summary_label.configure(text="")
            return

        self.estimate_summary_label.configure(text="Calculating...")
        self.apply_btn.configure(state="disabled")

        def worker():
            try:
                recent = self.state.client.get_recent_published_videos(limit=60)
                self.state.repo.refresh()
            except Exception as e:
                # Capture the message now -- Python deletes the "as e" binding the moment
                # this except block exits, so a lambda that closes over `e` itself would
                # find it gone by the time Tk actually invokes it via self.after().
                error_message = str(e)
                diagnostics.report_recovered("Refreshing the Auto Boost preview", e, page="Auto Boost")
                safe_after(self, lambda: self._on_preview_error(error_message))
                return
            now = dt.datetime.now(dt.timezone.utc)
            today = now.astimezone().date()
            target_date = today + dt.timedelta(days=1)
            by_day = group_by_published_day(recent, now)
            expected = self.config.estimated_views_per_short
            daily_perf = evaluate_daily_performance(by_day, today, self.config.daily_target_views)
            week_progress = (
                compute_week_progress(by_day, today, self.config.weekly_target_views)
                if self.config.target_mode == TARGET_MODE_WEEKLY else None
            )
            decision = decide_video_count(self.config, daily_perf, week_progress, expected)
            pull_forward = compute_auto_schedule_pull_forward(self.state.repo.videos, target_date, decision.video_count, self.config, now)
            safe_after(self, lambda: self._apply_preview(by_day, daily_perf, decision, week_progress, today, target_date, pull_forward))

        # The calculation after the API calls used to sit outside any try/except: an
        # unexpected error there killed the worker and left this page on "Calculating..."
        # with Apply disabled forever. on_error puts the page back into a usable state.
        start_worker(
            worker, name="Auto Boost preview", page="Auto Boost",
            on_error=lambda e: safe_after(self, lambda: self._on_preview_error(f"Unexpected error: {e}")),
        )

    def _on_preview_error(self, error_message: str) -> None:
        if not self.winfo_exists():
            return
        self.estimate_summary_label.configure(text=f"Couldn't compute a live preview: {error_message}")

    def _apply_preview(
        self, by_day: dict, daily_performance: PerformanceResult, decision: DecisionResult,
        week_progress: Optional[WeekProgress], today: dt.date, target_date: dt.date,
        pull_forward: IncreaseSlotsResult,
    ) -> None:
        if not self.winfo_exists():
            return
        self._by_day = by_day
        self._decision = decision
        self._week_progress = week_progress
        self._target_date = target_date
        self._pull_forward = pull_forward
        self._preview_shown = PREVIEW_PAGE  # a fresh calculation starts from the top again

        if week_progress is not None:
            # Weekly mode (point 18): target/collected/remaining are the week's totals.
            target_display, performance_display, shortfall_display = week_progress.target, week_progress.collected, week_progress.remaining
        else:
            # Daily mode (point 10/17): target/performance/shortfall are the most
            # recently completed day's own numbers.
            target_display = self.config.daily_target_views
            performance_display = daily_performance.actual_views if daily_performance.ready else None
            shortfall_display = decision.shortfall

        performance_line = f"{performance_display:,}" if performance_display is not None else "not enough data yet"
        self.estimate_summary_label.configure(
            text=(
                f"Target:                      {target_display:,}\n"
                f"Current Performance:         {performance_line}\n"
                f"Shortfall:                   {shortfall_display:,}\n"
                + (f"Boost Setting:               +{self.config.boost_extra_videos} per missed day\n" if self.config.boost_extra_videos > 0
                   else f"Estimated Views/Short:       {decision.expected_views_per_short:,}\n") +
                f"Recommended Next-Day Slots:  {decision.video_count}\n"
                f"Videos To Pull Forward:      {pull_forward.changed_count}"
            )
        )
        self.recommendation_label.configure(text=decision.explanation, text_color=(MUTED if decision.target_reached is not False else BAD))
        self.apply_btn.configure(state="normal" if pull_forward.result.assignments else "disabled")
        self._render_preview_table()

        if week_progress is not None:
            self.weekly_summary_label.configure(
                text=(
                    f"Target: {week_progress.target:,}   Collected: {week_progress.collected:,}   "
                    f"Remaining: {week_progress.remaining:,}   Days remaining: {week_progress.days_remaining}   "
                    f"Required average: {week_progress.required_average:,.0f}/day   "
                    f"Estimated Shorts likely required: {decision.additional_shorts}"
                )
            )
        else:
            self.weekly_summary_label.configure(text="")

        self._render_week_row(today)
        self._render_day_detail()

    # ---- apply: pull forward for real ----

    def _apply_pull_forward(self) -> None:
        if self._applying or self._pull_forward is None or not self._pull_forward.result.assignments:
            return
        result = self._pull_forward.result
        decision = self._decision
        n = len(result.assignments)
        target_date = self._target_date
        previous_count = sum(
            1 for v in (self.state.repo.videos if self.state.repo else [])
            if v.publish_at and v.publish_at.astimezone().date() == target_date
        )

        self._applying = True
        self.apply_btn.configure(state="disabled", text="Applying...")
        self.apply_status_label.configure(text=f"Preparing to apply {n} update(s)...", text_color=MUTED)

        def status_cb(text: str) -> None:
            safe_after(self, lambda: self.apply_status_label.configure(text=text))

        def done_cb(succeeded: int, total: int, failed: list[str]) -> None:
            safe_after(self, lambda: self._apply_done(succeeded, total, failed, decision, target_date, previous_count))

        description = (
            f"Auto Boost: {target_date.strftime('%d %b %Y')} -- {previous_count} -> {decision.video_count}/day "
            f"({n} Short(s) pulled forward). {decision.explanation}"
        )
        error = apply_schedule_result(self.state, result, description, status_cb, done_cb)
        if error:
            self._applying = False
            self.apply_btn.configure(state="normal", text="Apply Recommended Changes")
            self.apply_status_label.configure(text="", text_color=MUTED)
            report_error(
                self, self.state, "0.11.24.3", "Applying Auto Boost's pull-forward", error,
                operation=f"Auto Boost -- pulling Shorts forward for {target_date}",
            )

    def _apply_done(
        self, succeeded: int, total: int, failed: list[str], decision: DecisionResult,
        target_date: dt.date, previous_count: int,
    ) -> None:
        self._applying = False
        self.apply_btn.configure(text="Apply Recommended Changes")
        self.apply_status_label.configure(text="")

        # apply_schedule_result() deliberately doesn't call these itself (its worker runs
        # on a background thread, and these listeners touch Tk widgets) -- done_cb is
        # already marshaled onto the main thread by the time this method runs.
        self.state.notify_videos_changed()
        self.state.notify_history_changed()

        AutoScheduleHistoryStore().record(AutoScheduleDecision(
            decision_id=AutoScheduleDecision.new_id(),
            timestamp=dt.datetime.now(dt.timezone.utc),
            target_date=target_date,
            target_mode=self.config.target_mode,
            target_views_used=round(self._week_progress.required_average) if self._week_progress else self.config.daily_target_views,
            actual_views=None,
            target_reached=decision.target_reached,
            expected_views_per_short=decision.expected_views_per_short,
            previous_daily_count=previous_count,
            new_daily_count=decision.video_count,
            explanation=decision.explanation,
            scheduled_video_ids=[],
            applied=succeeded > 0,
            error="; ".join(failed) if failed else "",
        ))

        self._refresh_preview()

        if failed:
            messagebox.showwarning(
                "Some videos could not be updated",
                f"Applied {succeeded}/{total}. {len(failed)} failed:\n" + "\n".join(failed[:10]),
            )
        else:
            messagebox.showinfo("Auto Boost applied", f"Successfully pulled {succeeded} Short(s) forward.")

    # ---- save ----

    def _save(self) -> None:
        try:
            daily_target = int(self.daily_target_var.get().strip())
            weekly_target = int(self.weekly_target_var.get().strip())
            estimated_views = int(self.estimated_views_var.get().strip())
            maximum = int(self.max_var.get().strip())
            minimum = int(self.minimum_var.get().strip())
            normal = self.slot_control.selected_count()
            if min(daily_target, weekly_target, maximum, minimum) < 0 or estimated_views <= 0 or maximum == 0:
                raise ValueError
            if maximum < normal:
                messagebox.showerror("Invalid settings", "Maximum Shorts / Day must be at least the selected slot count (SET-0012.1).")
                return
            if maximum > MAX_SLOT_COUNT:
                messagebox.showerror(
                    "Invalid settings",
                    f"Maximum Shorts / Day can't exceed {MAX_SLOT_COUNT} -- that's as many Upload Time Presets (SET-0012.1) as exist.",
                )
                return
        except ValueError:
            messagebox.showerror("Invalid settings", "Target Views, Estimated Views Per Short, Maximum Shorts Per Day, and Minimum Daily Views must be whole numbers.")
            return

        self.config.enabled = self.enabled_var.get()
        self.config.target_mode = TARGET_MODE_WEEKLY if self.mode_var.get() == "Weekly Target" else TARGET_MODE_DAILY
        self.config.daily_target_views = daily_target
        self.config.weekly_target_views = weekly_target
        self.config.estimated_views_per_short = estimated_views
        self.config.boost_extra_videos = boost_value(self.boost_var.get())
        self.config.max_videos_per_day = maximum
        self.config.minimum_daily_views = minimum
        self.config.save()

        self.save_status_label.configure(text="Saved.")
        safe_after(self, lambda: self.save_status_label.configure(text=""), 2000)
        self._refresh_preview()

"""CALENDAR: a monthly grid view of the schedule -- publishing density at a glance
(colour-coded video-count pills), today highlighted, click any day to see exactly what's
scheduled on it. Read-only: this tab never writes to YouTube, it's purely a lens onto
state.repo.videos (grouped by data/calendar_aggregator.py), grouped by each video's LOCAL
publish date (publish_at.astimezone().date()) so a UTC-boundary-crossing time never lands
on the wrong day.

Also refreshes from YouTube on tab focus (see MainWindow's CTkTabview command=), not just
on state.on_videos_changed: the in-memory video cache only updates when something in THIS
running app writes to it, so a video scheduled by a separate process -- most notably
auto_schedule_runner.py, which runs unattended via Task Scheduler as its own process --
wouldn't otherwise show up here until something else happened to trigger a refresh.
"""
from __future__ import annotations

import calendar
import datetime as dt
import threading

import customtkinter as ctk

from data import diagnostics
from data.calendar_aggregator import TIER_EMPTY, TIER_FULL, TIER_PARTIAL, count_for_month, group_by_date, tier_for_count
from ui import thumbnail_cache
from ui.app_state import AppState
from ui.threading_utils import safe_after, start_worker
from ui.widgets.dev_tag import DevTag
from youtube.models import Video

MONTH_NAMES = [
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
]
WEEKDAY_LABELS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]

TIER_COLORS = {
    TIER_EMPTY: ("#2a2a2a", "#666666"),
    TIER_PARTIAL: ("#5a4a1a", "#e0b04a"),
    TIER_FULL: ("#1f4a2f", "#6fd18a"),
}
TODAY_BORDER = "#5b9bd5"
OUT_OF_MONTH_TEXT = "#4a4a4a"
IN_MONTH_TEXT = "#e5e5e5"
SELECTED_BG = "#22303f"
CELL_BG = "#1a1a1a"
CHIP_BG = "#242424"
CHIP_TEXT = "#c8c8c8"
MORE_BG = "#2f3a4a"
MORE_TEXT = "#8fb0cc"
THUMB_SIZE = (72, 40)
MAX_CHIPS_PER_CELL = 3
CELL_HEIGHT = 108


def _fmt_time(t: dt.datetime) -> str:
    return t.astimezone().strftime("%I:%M %p").lstrip("0")


class CalendarPage(ctk.CTkFrame):
    def __init__(self, master, state: AppState, **kwargs):
        super().__init__(master, fg_color="transparent", **kwargs)
        self.state = state
        today = dt.date.today()
        self._view_year, self._view_month = today.year, today.month
        self._selected_date: dt.date = today
        self._cal = calendar.Calendar(firstweekday=0)  # Monday first, matches DatePickerButton elsewhere

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(2, weight=1)

        header = ctk.CTkFrame(self, fg_color="transparent")
        header.grid(row=0, column=0, sticky="w", padx=16, pady=(16, 4))
        ctk.CTkLabel(header, text="Calendar", font=ctk.CTkFont(size=20, weight="bold")).pack(side="left")
        DevTag(header, state, "0.14").show(side="left", padx=(8, 0))

        self._build_month_nav()

        body = ctk.CTkFrame(self, fg_color="transparent")
        body.grid(row=2, column=0, sticky="nsew", padx=16, pady=(0, 16))
        body.grid_columnconfigure(0, weight=3)
        body.grid_columnconfigure(1, weight=2)
        body.grid_rowconfigure(0, weight=1)

        grid_scroll = ctk.CTkScrollableFrame(body, fg_color="transparent")
        grid_scroll.grid(row=0, column=0, sticky="nsew", padx=(0, 12))
        self.grid_area = grid_scroll
        DevTag(self.grid_area, state, "0.14.2").show(anchor="w")

        self.detail_panel = ctk.CTkScrollableFrame(body, fg_color="#161616", corner_radius=8)
        self.detail_panel.grid(row=0, column=1, sticky="nsew")

        self._refreshing = False
        state.on_videos_changed(self._render)
        self._render()

    # ---- tab-focus hook, called by MainWindow's CTkTabview command= ----

    def on_tab_focus(self) -> None:
        if self._refreshing or not self.state.repo:
            return
        self._refreshing = True

        def worker():
            try:
                self.state.repo.refresh()
            except Exception as e:
                diagnostics.report_recovered("Refreshing the schedule (Calendar)", e, page="Calendar")
            safe_after(self, self._refresh_done)

        # on_error matters here: _refreshing stays True until _refresh_done runs, so a
        # worker that died without it would have blocked every later refresh.
        start_worker(worker, name="Calendar refresh", page="Calendar", on_error=lambda e: safe_after(self, self._refresh_done))

    def _refresh_done(self) -> None:
        self._refreshing = False
        self.state.notify_videos_changed()

    # ---- header: month nav + today + summary ----

    def _build_month_nav(self) -> None:
        row = ctk.CTkFrame(self, fg_color="transparent")
        row.grid(row=1, column=0, sticky="ew", padx=16, pady=(0, 10))

        nav = ctk.CTkFrame(row, fg_color="transparent")
        nav.pack(side="left")
        ctk.CTkButton(nav, text="< Previous Month", width=130, height=28, command=self._prev_month).pack(side="left")
        DevTag(nav, self.state, "0.14.1.1").show(side="left", padx=(6, 0))
        self.month_label = ctk.CTkLabel(nav, text="", font=ctk.CTkFont(size=15, weight="bold"), width=160)
        self.month_label.pack(side="left", padx=10)
        DevTag(nav, self.state, "0.14.1.2").show(side="left", padx=(0, 10))
        ctk.CTkButton(nav, text="Next Month >", width=130, height=28, command=self._next_month).pack(side="left")
        DevTag(nav, self.state, "0.14.1.3").show(side="left", padx=(6, 0))
        ctk.CTkButton(nav, text="Today", width=70, height=28, fg_color="transparent", border_width=1, command=self._jump_to_today).pack(side="left", padx=(16, 0))
        DevTag(nav, self.state, "0.14.1.4").show(side="left", padx=(6, 0))

        self.summary_badge = ctk.CTkLabel(row, text="", text_color="#a0a0a0")
        self.summary_badge.pack(side="right")
        DevTag(row, self.state, "0.14.1.5").show(side="right", padx=(0, 8))

    def _prev_month(self) -> None:
        self._view_month -= 1
        if self._view_month == 0:
            self._view_month = 12
            self._view_year -= 1
        self._render()

    def _next_month(self) -> None:
        self._view_month += 1
        if self._view_month == 13:
            self._view_month = 1
            self._view_year += 1
        self._render()

    def _jump_to_today(self) -> None:
        today = dt.date.today()
        self._view_year, self._view_month = today.year, today.month
        self._selected_date = today
        self._render()

    # ---- rendering ----

    def _render(self) -> None:
        by_date = group_by_date(self.state.repo.videos if self.state.repo else [])
        self.month_label.configure(text=f"{MONTH_NAMES[self._view_month - 1]} {self._view_year}")
        total_this_month = count_for_month(by_date, self._view_year, self._view_month)
        self.summary_badge.configure(text=f"{total_this_month} video(s) scheduled this month")

        self._render_grid(by_date)
        self._render_detail(by_date)

    def _render_grid(self, by_date: dict) -> None:
        for w in self.grid_area.winfo_children():
            w.destroy()

        for col in range(7):
            self.grid_area.grid_columnconfigure(col, weight=1, uniform="cal")
            ctk.CTkLabel(self.grid_area, text=WEEKDAY_LABELS[col], text_color="#888888", font=ctk.CTkFont(size=11, weight="bold")).grid(
                row=0, column=col, sticky="ew", pady=(0, 4)
            )

        weeks = self._cal.monthdatescalendar(self._view_year, self._view_month)
        today = dt.date.today()
        for row_idx, week in enumerate(weeks, start=1):
            self.grid_area.grid_rowconfigure(row_idx, weight=0, minsize=CELL_HEIGHT + 6)
            for col_idx, day in enumerate(week):
                self._build_cell(day, row_idx, col_idx, today, by_date.get(day, []))

    def _build_cell(self, day: dt.date, row: int, col: int, today: dt.date, videos: list) -> None:
        in_month = day.month == self._view_month
        is_today = day == today
        is_selected = day == self._selected_date
        count = len(videos)

        border_color = TODAY_BORDER if is_today else "#2a2a2a"
        border_width = 2 if is_today else 1
        bg = SELECTED_BG if is_selected else CELL_BG

        cell = ctk.CTkFrame(self.grid_area, fg_color=bg, corner_radius=6, border_width=border_width, border_color=border_color, height=CELL_HEIGHT)
        cell.grid(row=row, column=col, sticky="new", padx=3, pady=3)
        cell.grid_propagate(False)

        # ---- header: date number + a compact tier-coloured count indicator ----
        head = ctk.CTkFrame(cell, fg_color="transparent")
        head.pack(fill="x", padx=6, pady=(5, 2))
        text_color = IN_MONTH_TEXT if in_month else OUT_OF_MONTH_TEXT
        ctk.CTkLabel(head, text=str(day.day), text_color=text_color, font=ctk.CTkFont(size=12, weight="bold" if is_today else "normal")).pack(side="left")
        if in_month and count:
            tier = tier_for_count(count)
            _, pill_fg = TIER_COLORS[tier]
            ctk.CTkLabel(head, text=str(count), text_color=pill_fg, font=ctk.CTkFont(size=10, weight="bold")).pack(side="right")

        if not in_month:
            cell.configure(cursor="arrow")
            DevTag(cell, self.state, "0.14.2.1").show(anchor="se", padx=(0, 4), pady=(0, 2))
            return cell

        cell.configure(cursor="hand2")
        cell.bind("<Button-1>", lambda e, d=day: self._select_date(d))
        head.bind("<Button-1>", lambda e, d=day: self._select_date(d))

        # ---- individual event chips, stacked in document flow (never overlapping) ----
        # Each chip gets its own thin wrapper frame rather than attaching a DevTag
        # straight to the CTkLabel: CTkLabel manages its own internal contents via grid,
        # so packing a DevTag directly into one raises "already has slaves managed by
        # grid" -- a container frame is required as the DevTag's parent.
        shown = videos[:MAX_CHIPS_PER_CELL]
        overflow = count - len(shown)
        for video in shown:
            chip_text = f"{_fmt_time(video.publish_at)} {video.title}"
            if len(chip_text) > 20:
                chip_text = chip_text[:19] + "…"
            chip_wrap = ctk.CTkFrame(cell, fg_color="transparent")
            chip_wrap.pack(fill="x", padx=6, pady=1)
            chip = ctk.CTkLabel(
                chip_wrap, text=chip_text, fg_color=CHIP_BG, text_color=CHIP_TEXT, corner_radius=4,
                font=ctk.CTkFont(size=9), anchor="w", cursor="hand2",
            )
            chip.pack(side="left", fill="x", expand=True, ipady=1)
            for w in (chip_wrap, chip):
                w.bind("<Button-1>", lambda e, d=day: self._select_date(d))
            DevTag(chip_wrap, self.state, "0.14.2.2").show(side="right")

        if overflow > 0:
            more_wrap = ctk.CTkFrame(cell, fg_color="transparent")
            more_wrap.pack(fill="x", padx=6, pady=(1, 4))
            more = ctk.CTkLabel(
                more_wrap, text=f"+{overflow} more", fg_color=MORE_BG, text_color=MORE_TEXT, corner_radius=4,
                font=ctk.CTkFont(size=9, weight="bold"),
            )
            more.pack(side="left", fill="x", expand=True, ipady=1)
            for w in (more_wrap, more):
                w.configure(cursor="hand2")
                w.bind("<Button-1>", lambda e, d=day: self._select_date(d))
            DevTag(more_wrap, self.state, "0.14.2.3").show(side="right")

        DevTag(cell, self.state, "0.14.2.1").show(anchor="se", padx=(0, 4), pady=(0, 2))
        return cell

    def _select_date(self, day: dt.date) -> None:
        self._selected_date = day
        self._render()

    def _render_detail(self, by_date: dict) -> None:
        for w in self.detail_panel.winfo_children():
            w.destroy()

        heading_text = self._selected_date.strftime("%A, %d %B %Y")
        header_row = ctk.CTkFrame(self.detail_panel, fg_color="transparent")
        header_row.pack(fill="x", padx=14, pady=(14, 4))
        ctk.CTkLabel(header_row, text=heading_text, font=ctk.CTkFont(size=14, weight="bold"), anchor="w").pack(side="left")
        DevTag(header_row, self.state, "0.14.3.1").show(side="left", padx=(6, 0))

        videos = by_date.get(self._selected_date, [])
        if not videos:
            ctk.CTkLabel(self.detail_panel, text="Nothing scheduled on this day.", text_color="#888888").pack(padx=14, pady=20)
            return

        for video in videos:
            self._build_detail_row(video)

    def _build_detail_row(self, video: Video) -> None:
        row = ctk.CTkFrame(self.detail_panel, fg_color="#1f1f1f", corner_radius=6)
        row.pack(fill="x", padx=12, pady=4)
        inner = ctk.CTkFrame(row, fg_color="transparent")
        inner.pack(fill="x", padx=10, pady=8)

        thumb = ctk.CTkLabel(inner, text="", width=THUMB_SIZE[0], height=THUMB_SIZE[1], fg_color="#2a2a2a", corner_radius=4)
        thumb.pack(side="left", padx=(0, 10))
        if video.thumbnail_url:
            thumbnail_cache.get_async(self, video.thumbnail_url, THUMB_SIZE, lambda img, lbl=thumb: self._apply_thumb(lbl, img))

        info = ctk.CTkFrame(inner, fg_color="transparent")
        info.pack(side="left", fill="x", expand=True)
        title_txt = video.title if len(video.title) <= 55 else video.title[:52] + "..."
        ctk.CTkLabel(info, text=title_txt, anchor="w", font=ctk.CTkFont(size=12)).pack(anchor="w", fill="x")
        meta = f"{_fmt_time(video.publish_at)}  ·  {video.duration_display}"
        ctk.CTkLabel(info, text=meta, anchor="w", text_color="#a0a0a0", font=ctk.CTkFont(size=11)).pack(anchor="w", fill="x")
        DevTag(info, self.state, "0.14.3.2").show(anchor="w", pady=(2, 0))

    def _apply_thumb(self, label: ctk.CTkLabel, image: ctk.CTkImage) -> None:
        if not label.winfo_exists():
            return
        label.configure(image=image, text="")

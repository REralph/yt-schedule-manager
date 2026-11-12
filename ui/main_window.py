"""Main app window: tab shell for the app's five active workflows -- Upload (add new
videos), Auto Boost (the automation engine, was Auto Schedule), Daily Upload Frequency (change how many Shorts
publish per day without changing their order; was Increase Daily Slots and, before that, "Push" -- see
ui/pages/daily_upload_frequency_page.py), Calendar (monthly overview of publishing
density), and Settings (YouTube connection, Application, Developer Tools). Schedule,
Timeline, Groups, Scheduler, Preview, and History were removed as unused/redundant
navigation; their pages and exclusively-used support modules were deleted rather than
left as dead code."""
from __future__ import annotations

import sys

import customtkinter as ctk

from data import diagnostics
from data.config import AppConfig
from data.crash_log import build_crash_report
from data.order_codes import backfill_order_codes
from data.upload_activity import UploadActivityLog
from ui import ctk_perf
from ui.app_state import AppState
from ui.threading_utils import safe_after, start_worker
from ui.pages.auto_schedule_settings_page import AutoScheduleSettingsPage
from ui.pages.calendar_page import CalendarPage
from ui.pages.daily_upload_frequency_page import DailyUploadFrequencyPage
from ui.pages.settings_page import SettingsPage
from ui.pages.upload_page import UploadPage
from ui.widgets.dev_panel import DevInfoPanel, DevModeBanner
from youtube.client import YouTubeClient
from youtube.video_repo import VideoRepository

TAB_LABELS = {
    "Upload": "Upload",
    "AutoSchedule": "Auto Boost",
    "Push": "Daily Upload Frequency",
    "Calendar": "Calendar",
    "Settings": "Settings",
}
TABS = list(TAB_LABELS.values())


class MainWindow(ctk.CTkFrame):
    def __init__(self, master, credentials, on_sign_out, **kwargs):
        super().__init__(master, fg_color="transparent", **kwargs)

        self.state = AppState(AppConfig.load())
        self.state.client = YouTubeClient(credentials)
        self.state.repo = VideoRepository(
            self.state.client, use_uploads_playlist=self.state.config.use_uploads_playlist
        )
        # Every video the upload record knows about is worth asking YouTube about by ID on each
        # refresh (the uploads-playlist listing alone drops many -- see youtube/video_repo.py).
        self.state.repo.known_id_provider = lambda: UploadActivityLog.shared().known_video_ids()

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)

        self.daily_upload_frequency_page = None  # set once built; a page that failed to build stays None (see _build_page)
        self.calendar_page = None

        DevModeBanner(self, self.state, grid_row=0)

        self.tabview = ctk.CTkTabview(self, anchor="nw", command=self._on_tab_changed)
        self.tabview.grid(row=1, column=0, sticky="nsew", padx=8, pady=8)
        for name in TABS:
            self.tabview.add(name)
            self.tabview.tab(name).grid_columnconfigure(0, weight=1)
            self.tabview.tab(name).grid_rowconfigure(0, weight=1)

        self.state.request_tab = self.tabview.set

        # A tab you are not looking at is not rescaled when the window moves to a monitor with a different display scale -- it
        # is rescaled the moment it is shown (see ui/ctk_perf.py). Every way of showing a tab (a click, tabview.set() from
        # code) ends in _set_grid_current_tab, so that is where the waiting tab is caught up.
        for name in TABS:
            ctk_perf.register_lazy_container(self.tabview.tab(name), lambda n=name: self.tabview.get() == n)
        original_show_tab = self.tabview._set_grid_current_tab

        def show_current_tab() -> None:
            original_show_tab()
            try:
                ctk_perf.activate(self.tabview.tab(self.tabview.get()))
            except Exception as e:  # a tab that cannot be caught up still shows; it is just at the old scale until the next change
                diagnostics.report_recovered("Rescaling a tab after a display-scale change", e, page="Window")

        self.tabview._set_grid_current_tab = show_current_tab

        # Each tab is built independently: a page whose constructor fails (a bad saved
        # value it didn't expect, a bug) is replaced by a short notice and recorded in
        # Crash History, instead of preventing the whole window -- and every other tab --
        # from opening.
        self._build_page(TAB_LABELS["Upload"], lambda tab: UploadPage(tab, self.state))
        self._build_page(TAB_LABELS["AutoSchedule"], lambda tab: AutoScheduleSettingsPage(tab, self.state))
        self.daily_upload_frequency_page = self._build_page(TAB_LABELS["Push"], lambda tab: DailyUploadFrequencyPage(tab, self.state))
        self.calendar_page = self._build_page(TAB_LABELS["Calendar"], lambda tab: CalendarPage(tab, self.state))
        self._build_page(TAB_LABELS["Settings"], lambda tab: SettingsPage(tab, self.state, on_sign_out=on_sign_out))

        self.tabview.set(TAB_LABELS["Upload"])
        self.state.set_current_tab(TAB_LABELS["Upload"])

        DevInfoPanel(self, self.state, grid_row=2)

        # Kick off an initial refresh in the background so Upload On Specific Day's and
        # Push's cascade math have real data as soon as the app opens.
        self.after(200, self._initial_refresh)

    def _build_page(self, tab_name: str, factory):
        tab = self.tabview.tab(tab_name)
        try:
            page = factory(tab)
            page.grid(row=0, column=0, sticky="nsew")
            return page
        except Exception:
            exc_type, exc_value, exc_tb = sys.exc_info()
            try:
                self.state.crash_log.record(build_crash_report(exc_type, exc_value, exc_tb, self.state))
            except Exception:
                pass
            for child in tab.winfo_children():
                try:
                    child.destroy()
                except Exception:
                    pass
            ctk.CTkLabel(
                tab, text=(
                    f"The {tab_name} page couldn't be loaded.\n\n{type(exc_value).__name__}: {exc_value}\n\n"
                    "The rest of the app is unaffected. The details were saved -- see Settings -> Crash History."
                ),
                text_color="#e0a04a", justify="left", wraplength=760,
            ).grid(row=0, column=0, padx=24, pady=24, sticky="nw")
            return None

    def _on_tab_changed(self) -> None:
        active = self.tabview.get()
        self.state.set_current_tab(active)
        if active == TAB_LABELS["Push"] and self.daily_upload_frequency_page is not None:
            self.daily_upload_frequency_page.on_tab_focus()
        elif active == TAB_LABELS["Calendar"] and self.calendar_page is not None:
            self.calendar_page.on_tab_focus()

    def _initial_refresh(self) -> None:
        def worker():
            try:
                self.state.repo.refresh()
            except Exception as e:
                # Best-effort: the cached schedule stays in use, and the pages re-refresh
                # themselves. Logged (not swallowed) so a pattern of failures is diagnosable.
                diagnostics.report_recovered("Start-up schedule refresh", e, page="Startup")
            safe_after(self, self.state.notify_videos_changed)
            safe_after(self, self._backfill_order_codes)

        start_worker(worker, name="Start-up schedule refresh", page="Startup")

    def _backfill_order_codes(self) -> None:
        """Every recorded video gets its description kept beside its code, and every upload in the History gets its code (only fills in
        what is missing; local only)."""
        try:
            _described, coded = backfill_order_codes(self.state.repo.videos if self.state.repo else [])
            if coded:
                self.state.notify_history_changed()
        except Exception as e:
            diagnostics.report_recovered("Giving recorded videos their order codes", e, page="Startup")

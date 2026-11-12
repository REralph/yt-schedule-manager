"""SETTINGS: a container with four sub-areas -- YouTube (connection/quota), Application,
Crash History (every recorded crash/error, each copyable as its own report), and Developer
Tools (Item IDs, copyable references, error reporting). Auto Boost's own
rules live on their own top-level "Auto Boost" tab, not here -- it's the main
automation section, not an advanced setting.

There is no "Scheduling" sub-tab any more (was 0.12 / data/central_config.py): its
Slot Interval / Max HIT Uploads / Default Frequency fields only ever fed HIT, Reset All
Schedules, and Upload On Specific Day, all of which were retired with the 3-tab
navigation, so nothing read them."""
from __future__ import annotations

import customtkinter as ctk

from ui.app_state import AppState
from ui.pages.application_settings_page import ApplicationSettingsPage
from ui.pages.crash_history_page import CrashHistoryPage
from ui.pages.developer_tools_page import DeveloperToolsPage
from ui.pages.youtube_settings_page import YouTubeSettingsPage
from ui.widgets.dev_tag import DevTag

SUB_TABS = ["YouTube", "Application", "Crash History", "Developer Tools"]


class SettingsPage(ctk.CTkFrame):
    def __init__(self, master, state: AppState, on_sign_out, **kwargs):
        super().__init__(master, fg_color="transparent", **kwargs)
        self.state = state

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)

        header = ctk.CTkFrame(self, fg_color="transparent")
        header.grid(row=0, column=0, sticky="w", padx=16, pady=(16, 8))
        ctk.CTkLabel(header, text="Settings", font=ctk.CTkFont(size=20, weight="bold")).pack(side="left")
        DevTag(header, state, "0.8").show(side="left", padx=(8, 0))

        self.sub_tabview = ctk.CTkTabview(self, anchor="nw")
        self.sub_tabview.grid(row=1, column=0, sticky="nsew", padx=16, pady=(0, 16))
        for name in SUB_TABS:
            self.sub_tabview.add(name)
            self.sub_tabview.tab(name).grid_columnconfigure(0, weight=1)
            self.sub_tabview.tab(name).grid_rowconfigure(0, weight=1)

        state.request_settings_tab = self.sub_tabview.set

        YouTubeSettingsPage(self.sub_tabview.tab("YouTube"), state, on_sign_out=on_sign_out).grid(row=0, column=0, sticky="nsew")
        ApplicationSettingsPage(self.sub_tabview.tab("Application"), state).grid(row=0, column=0, sticky="nsew")
        CrashHistoryPage(self.sub_tabview.tab("Crash History"), state).grid(row=0, column=0, sticky="nsew")
        DeveloperToolsPage(self.sub_tabview.tab("Developer Tools"), state).grid(row=0, column=0, sticky="nsew")

        self.sub_tabview.set(SUB_TABS[0])

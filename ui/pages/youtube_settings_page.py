"""SETTINGS -> YouTube sub-tab: connection status, sign out/re-auth, and quota usage.
Developer Mode moved to its own Developer Tools sub-tab."""
from __future__ import annotations

import customtkinter as ctk

from auth import oauth_manager
from ui.app_state import AppState
from ui.widgets.dev_tag import DevTag
from youtube.quota import COST_VIDEOS_UPDATE


class YouTubeSettingsPage(ctk.CTkFrame):
    def __init__(self, master, state: AppState, on_sign_out, **kwargs):
        super().__init__(master, fg_color="transparent", **kwargs)
        self.state = state
        self.on_sign_out = on_sign_out

        self.grid_columnconfigure(0, weight=1)

        card = ctk.CTkFrame(self, fg_color="#1f1f1f", corner_radius=8)
        card.grid(row=0, column=0, sticky="ew", padx=16, pady=(16, 12))
        card.grid_columnconfigure(0, weight=1)

        conn_header = ctk.CTkFrame(card, fg_color="transparent")
        conn_header.grid(row=0, column=0, sticky="w", padx=14, pady=(12, 4))
        ctk.CTkLabel(conn_header, text="YouTube Connection", font=ctk.CTkFont(size=14, weight="bold")).pack(side="left")
        DevTag(conn_header, state, "0.8.1").show(side="left", padx=(8, 0))

        self.connection_label = ctk.CTkLabel(card, text="Connected", text_color="#7fd17f")
        self.connection_label.grid(row=1, column=0, sticky="w", padx=14)

        sign_out_row = ctk.CTkFrame(card, fg_color="transparent")
        sign_out_row.grid(row=2, column=0, sticky="w", padx=14, pady=(8, 14))
        sign_out_btn = ctk.CTkButton(sign_out_row, text="Sign Out", width=120, fg_color="#7a2b2b", command=self._sign_out)
        sign_out_btn.pack(side="left")
        DevTag(sign_out_row, state, "0.8.1.1").show(side="left", padx=(8, 0))

        quota_card = ctk.CTkFrame(self, fg_color="#1f1f1f", corner_radius=8)
        quota_card.grid(row=1, column=0, sticky="ew", padx=16, pady=(0, 12))
        quota_card.grid_columnconfigure(0, weight=1)

        quota_header = ctk.CTkFrame(quota_card, fg_color="transparent")
        quota_header.grid(row=0, column=0, sticky="w", padx=14, pady=(12, 4))
        ctk.CTkLabel(quota_header, text="API Quota (today)", font=ctk.CTkFont(size=14, weight="bold")).pack(side="left")
        DevTag(quota_header, state, "0.8.2").show(side="left", padx=(8, 0))

        self.quota_label = ctk.CTkLabel(quota_card, text="", text_color="#a0a0a0", justify="left")
        self.quota_label.grid(row=1, column=0, sticky="w", padx=14, pady=(0, 14))
        self._refresh_quota_label()

    def _refresh_quota_label(self) -> None:
        budget = self.state.config.daily_quota_budget_units
        used = self.state.client.quota.units_used if self.state.client else 0
        remaining = max(0, budget - used)
        max_reschedules = remaining // COST_VIDEOS_UPDATE
        self.quota_label.configure(
            text=(
                f"{used} / {budget} units used today.\n"
                f"~{max_reschedules} more reschedules available before hitting your configured daily budget."
            )
        )

    def _sign_out(self) -> None:
        oauth_manager.sign_out()
        self.on_sign_out()

"""SETTINGS -> Application sub-tab: app-level options that aren't scheduling or
YouTube-connection related -- the desktop shortcut (data/desktop_shortcut.py) and One
Upload Page's Check New detection window (data/config.py)."""
from __future__ import annotations

import threading
from tkinter import messagebox

import customtkinter as ctk

from data import desktop_shortcut
from ui.app_state import AppState
from ui.threading_utils import safe_after, start_worker
from ui.widgets.dev_tag import DevTag

_STATUS_TEXT = {
    "installed": ("Status: ✓ Installed", "#7fd17f"),
    "not_installed": ("Status: Not Installed", "#a0a0a0"),
    "broken": ("Status: ⚠ Broken — points to a missing file, recreate it", "#e0a04a"),
}
_BUTTON_TEXT = {
    "installed": "RECREATE SHORTCUT",
    "not_installed": "CREATE DESKTOP SHORTCUT",
    "broken": "RECREATE SHORTCUT",
}

_WINDOW_OPTIONS = {"30 seconds": 30, "60 seconds": 60, "2 minutes": 120, "5 minutes": 300}
_WINDOW_OPTIONS_REV = {v: k for k, v in _WINDOW_OPTIONS.items()}


class ApplicationSettingsPage(ctk.CTkFrame):
    def __init__(self, master, state: AppState, **kwargs):
        super().__init__(master, fg_color="transparent", **kwargs)
        self.state = state

        self.grid_columnconfigure(0, weight=1)

        card = ctk.CTkFrame(self, fg_color="#1f1f1f", corner_radius=8)
        card.grid(row=0, column=0, sticky="ew", padx=16, pady=(16, 12))
        card.grid_columnconfigure(0, weight=1)

        header = ctk.CTkFrame(card, fg_color="transparent")
        header.grid(row=0, column=0, sticky="w", padx=14, pady=(12, 4))
        ctk.CTkLabel(header, text="Desktop Shortcut", font=ctk.CTkFont(size=14, weight="bold")).pack(side="left")
        DevTag(header, state, "0.16.1").show(side="left", padx=(8, 0))

        self.status_label = ctk.CTkLabel(card, text="Status: Checking...", text_color="#a0a0a0")
        self.status_label.grid(row=1, column=0, sticky="w", padx=14)

        action_row = ctk.CTkFrame(card, fg_color="transparent")
        action_row.grid(row=2, column=0, sticky="w", padx=14, pady=(8, 14))
        self.action_btn = ctk.CTkButton(action_row, text="CREATE DESKTOP SHORTCUT", width=220, command=self._on_create_clicked)
        self.action_btn.pack(side="left")
        DevTag(action_row, state, "0.16.1.2").show(side="left", padx=(8, 0))

        detection_card = ctk.CTkFrame(self, fg_color="#1f1f1f", corner_radius=8)
        detection_card.grid(row=1, column=0, sticky="ew", padx=16, pady=(0, 12))
        detection_card.grid_columnconfigure(0, weight=1)

        detection_header = ctk.CTkFrame(detection_card, fg_color="transparent")
        detection_header.grid(row=0, column=0, sticky="w", padx=14, pady=(12, 4))
        ctk.CTkLabel(detection_header, text="Upload Detection", font=ctk.CTkFont(size=14, weight="bold")).pack(side="left")
        DevTag(detection_header, state, "0.16.2").show(side="left", padx=(8, 0))

        window_row = ctk.CTkFrame(detection_card, fg_color="transparent")
        window_row.grid(row=1, column=0, sticky="w", padx=14, pady=(0, 14))
        ctk.CTkLabel(window_row, text="New Video Detection Window", width=230, anchor="w").pack(side="left")
        current_seconds = state.config.new_video_detection_window_seconds
        self.window_var = ctk.StringVar(value=_WINDOW_OPTIONS_REV.get(current_seconds, "60 seconds"))
        ctk.CTkOptionMenu(
            window_row, variable=self.window_var, values=list(_WINDOW_OPTIONS.keys()), width=140,
            command=self._on_window_changed,
        ).pack(side="left")
        DevTag(window_row, state, "0.16.2.1").show(side="left", padx=(10, 0))

        self._refresh_status()

    def _on_window_changed(self, value: str) -> None:
        self.state.config.new_video_detection_window_seconds = _WINDOW_OPTIONS.get(value, 60)
        self.state.config.save()

    def _refresh_status(self) -> None:
        def worker():
            status = desktop_shortcut.shortcut_status()
            safe_after(self, lambda: self._apply_status(status))

        start_worker(worker, name="Desktop shortcut status", page="Settings")

    def _apply_status(self, status: str) -> None:
        if not self.winfo_exists():
            return
        text, color = _STATUS_TEXT[status]
        self.status_label.configure(text=text, text_color=color)
        self.action_btn.configure(text=_BUTTON_TEXT[status])

    def _on_create_clicked(self) -> None:
        self.action_btn.configure(state="disabled", text="Working...")

        def worker():
            error = None
            try:
                desktop_shortcut.create_shortcut()
            except Exception as e:
                error = e
            status = desktop_shortcut.shortcut_status()
            safe_after(self, lambda: self._on_create_done(status, error))

        start_worker(worker, name="Create desktop shortcut", page="Settings", on_error=lambda e: safe_after(self, lambda: self._on_create_done("missing", e)))

    def _on_create_done(self, status: str, error: Exception | None) -> None:
        if not self.winfo_exists():
            return
        self.action_btn.configure(state="normal")
        self._apply_status(status)
        if error is not None:
            messagebox.showerror("Desktop Shortcut", f"Couldn't create the desktop shortcut:\n{error}")

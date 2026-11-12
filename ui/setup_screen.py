"""Shown before the main app when there's no valid OAuth session yet: either the user
hasn't created a Google Cloud OAuth client (missing client_secret.json), or they simply
need to sign in / re-authenticate."""
from __future__ import annotations

import threading
import webbrowser

import customtkinter as ctk

from auth import oauth_manager
from ui.threading_utils import safe_after, start_worker

SETUP_STEPS = """1. Go to console.cloud.google.com and create (or select) a project.
2. APIs & Services -> Library -> enable "YouTube Data API v3".
3. APIs & Services -> OAuth consent screen -> External -> Testing,
   and add your own Google account as a test user.
4. APIs & Services -> Credentials -> Create Credentials -> OAuth client ID
   -> Application type "Desktop app" -> Create, then Download JSON.
5. Save that file as:
   credentials\\client_secret.json
   in this app's folder, then click "Check again" below.

The first time you connect, Google will show an "unverified app" warning
screen since this is a personal-use app you created yourself (not published) --
click "Advanced" then "Go to (app name)" to continue. This is expected."""


class SetupScreen(ctk.CTkFrame):
    def __init__(self, master, on_connected, **kwargs):
        super().__init__(master, fg_color="transparent", **kwargs)
        self.on_connected = on_connected

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(0, weight=1)

        box = ctk.CTkFrame(self, fg_color="#1f1f1f", corner_radius=10)
        box.grid(row=0, column=0)

        ctk.CTkLabel(box, text="Connect to YouTube", font=ctk.CTkFont(size=22, weight="bold")).pack(
            padx=32, pady=(28, 6)
        )

        self.status_label = ctk.CTkLabel(box, text="", text_color="#a0a0a0")
        self.status_label.pack(padx=32, pady=(0, 10))

        self.steps_label = ctk.CTkLabel(
            box, text=SETUP_STEPS, justify="left", anchor="w", font=ctk.CTkFont(size=12), text_color="#cccccc"
        )
        self.steps_label.pack(padx=32, pady=(0, 16))

        link_btn = ctk.CTkButton(
            box, text="Open Google Cloud Console", fg_color="transparent", border_width=1,
            command=lambda: webbrowser.open("https://console.cloud.google.com/apis/credentials"),
        )
        link_btn.pack(padx=32, pady=(0, 12))

        btn_row = ctk.CTkFrame(box, fg_color="transparent")
        btn_row.pack(padx=32, pady=(0, 28))

        self.check_btn = ctk.CTkButton(btn_row, text="Check again", width=140, command=self._check)
        self.check_btn.pack(side="left", padx=(0, 10))

        self.connect_btn = ctk.CTkButton(btn_row, text="Connect to YouTube", width=180, command=self._connect)
        self.connect_btn.pack(side="left")

        self._check(initial=True)

    def _check(self, initial: bool = False) -> None:
        present = oauth_manager.client_secret_present()
        if present:
            self.status_label.configure(text="client_secret.json found. Ready to connect.", text_color="#7fd17f")
            self.connect_btn.configure(state="normal")
        else:
            if not initial:
                self.status_label.configure(
                    text="Still no credentials\\client_secret.json found.", text_color="#e0a04a"
                )
            self.connect_btn.configure(state="disabled")

    def _connect(self) -> None:
        self.connect_btn.configure(state="disabled", text="Opening browser...")
        self.status_label.configure(text="A browser window will open for you to sign in and approve access.")

        def worker():
            error = None
            try:
                oauth_manager.run_auth_flow()
            except Exception as e:
                error = str(e)
            safe_after(self, lambda: self._connect_done(error))

        start_worker(worker, name="YouTube sign-in", page="Sign-in", on_error=lambda e: safe_after(self, lambda: self._connect_done(str(e))))

    def _connect_done(self, error: str | None) -> None:
        if error:
            self.connect_btn.configure(state="normal", text="Connect to YouTube")
            self.status_label.configure(text=f"Connection failed: {error}", text_color="#e05a5a")
            return
        self.on_connected()

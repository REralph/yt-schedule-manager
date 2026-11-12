"""UPLOAD: adding brand-new video files to the channel via One Upload Page -- one video at
a time, detected from a watched folder or dragged in. There is no longer a mode switch
here; One Upload Page (ui/widgets/basic_upload_panel.py) is the whole workflow."""
from __future__ import annotations

import customtkinter as ctk

from ui.app_state import AppState
from ui.widgets.basic_upload_panel import BasicUploadPanel
from ui.widgets.dev_tag import DevTag


class UploadPage(ctk.CTkFrame):
    def __init__(self, master, state: AppState, **kwargs):
        super().__init__(master, fg_color="transparent", **kwargs)
        self.state = state

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(0, weight=1)
        self.scroll = ctk.CTkScrollableFrame(self, fg_color="transparent")
        self.scroll.grid(row=0, column=0, sticky="nsew")
        self.scroll.grid_columnconfigure(0, weight=1)

        header = ctk.CTkFrame(self.scroll, fg_color="transparent")
        header.pack(fill="x", padx=16, pady=(16, 8))
        ctk.CTkLabel(header, text="Upload", font=ctk.CTkFont(size=20, weight="bold")).pack(side="left")
        DevTag(header, state, "0.9").show(side="left", padx=(8, 0))

        panel = BasicUploadPanel(self.scroll, state)
        panel.pack(fill="x", padx=16, pady=(0, 20))

"""VIEW ERROR LOG: every error reported through Developer Tools (data/error_log.py),
most recent first, each with its own Copy button so an old report can be handed over
without needing it to still be "the last one"."""
from __future__ import annotations

import customtkinter as ctk

from data.error_log import ErrorLogStore


class ErrorLogDialog(ctk.CTkToplevel):
    def __init__(self, master, error_log: ErrorLogStore):
        super().__init__(master)
        self.title("Error Log")
        self.geometry("560x520")
        self.attributes("-topmost", True)

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)

        entries = error_log.entries
        ctk.CTkLabel(
            self, text=f"{len(entries)} error(s) recorded" if entries else "No errors recorded yet.",
            font=ctk.CTkFont(size=14, weight="bold"),
        ).grid(row=0, column=0, sticky="w", padx=20, pady=(18, 8))

        scroll = ctk.CTkScrollableFrame(self, fg_color="transparent")
        scroll.grid(row=1, column=0, sticky="nsew", padx=20, pady=(0, 8))
        for report in entries:
            self._build_row(scroll, report)

        ctk.CTkButton(self, text="Close", width=100, fg_color="transparent", border_width=1, command=self.destroy).grid(
            row=2, column=0, sticky="e", padx=20, pady=16
        )

        self.update_idletasks()
        self.grab_set()

    def _build_row(self, parent, report) -> None:
        row = ctk.CTkFrame(parent, fg_color="#1f1f1f", corner_radius=6)
        row.pack(fill="x", pady=4)
        inner = ctk.CTkFrame(row, fg_color="transparent")
        inner.pack(fill="x", padx=12, pady=8)

        when = report.timestamp.astimezone().strftime("%d %b %Y, %H:%M")
        ctk.CTkLabel(
            inner, text=f"{when}  ·  {report.item_id}  ·  {report.page}", anchor="w",
            font=ctk.CTkFont(size=12, weight="bold"),
        ).pack(anchor="w")
        ctk.CTkLabel(inner, text=report.error_message, anchor="w", text_color="#a0a0a0", wraplength=460, justify="left").pack(
            anchor="w", pady=(2, 6)
        )
        ctk.CTkButton(inner, text="Copy", width=70, height=22, command=lambda r=report: self._copy(r)).pack(anchor="e")

    def _copy(self, report) -> None:
        try:
            self.clipboard_clear()
            self.clipboard_append(report.format())
        except Exception:
            pass

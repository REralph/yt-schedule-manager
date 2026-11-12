"""One-click error reporting: whenever a real operation fails (an API call, an upload, a
write to YouTube), this shows instead of a plain messagebox -- the Item ID identifying
exactly what failed is always visible, and Copy Error puts the complete developer report
(see data/error_log.py's ErrorReport.format()) on the clipboard with no manual selecting."""
from __future__ import annotations

from typing import Callable, Optional

import customtkinter as ctk

from data.error_log import ErrorReport

BAD = "#e05a5a"


class ErrorDialog(ctk.CTkToplevel):
    def __init__(
        self, master, report: ErrorReport, on_retry: Optional[Callable[[], None]] = None, retry_label: str = "Retry",
    ):
        super().__init__(master)
        self.title("Error")
        self.geometry("440x300" if on_retry else "440x260")
        self.attributes("-topmost", True)
        self._report = report
        self._on_retry = on_retry

        self.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(self, text="ERROR", font=ctk.CTkFont(size=16, weight="bold"), text_color=BAD).grid(
            row=0, column=0, pady=(20, 10)
        )
        ctk.CTkLabel(
            self, text=report.error_message, wraplength=380, justify="center", font=ctk.CTkFont(size=13),
        ).grid(row=1, column=0, padx=24, pady=(0, 14))
        ctk.CTkLabel(self, text=f"Item ID: {report.item_id}", text_color="#8fb0cc", font=ctk.CTkFont(size=12, weight="bold")).grid(
            row=2, column=0, pady=(0, 18)
        )

        btn_row = ctk.CTkFrame(self, fg_color="transparent")
        btn_row.grid(row=3, column=0, pady=(0, 8))
        self.copy_btn = ctk.CTkButton(btn_row, text="Copy Error", width=160, command=self._copy)
        self.copy_btn.pack(side="left")
        if on_retry is not None:
            ctk.CTkButton(
                btn_row, text=retry_label, width=max(100, 8 * len(retry_label) + 24),
                fg_color="#2f7a3d", hover_color="#256030", command=self._retry,
            ).pack(side="left", padx=(8, 0))
        ctk.CTkButton(self, text="Close", width=160, fg_color="transparent", border_width=1, command=self.destroy).grid(
            row=4, column=0, pady=(0, 20)
        )

        self.update_idletasks()
        self.grab_set()

    def _retry(self) -> None:
        self.destroy()
        if self._on_retry is not None:
            self._on_retry()

    def _copy(self) -> None:
        try:
            self.clipboard_clear()
            self.clipboard_append(self._report.format())
        except Exception:
            pass
        self.copy_btn.configure(text="Copied!")
        self.after(1200, lambda: self.copy_btn.configure(text="Copy Error") if self.copy_btn.winfo_exists() else None)

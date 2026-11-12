"""SETTINGS -> Crash History (CRASH-002, APP-OPT-001): a persistent, reviewable record of every
crash and error the app has recorded -- so an unexpected problem can be looked at later, and
handed to whoever is diagnosing it, without needing the app to be in that state again.

Each entry is one line: `23/09/2026 00:42 | Upload | 0.9.6.3 | Uploading q7.mov | SSL:
WRONG_VERSION_NUMBER`. Clicking a line expands it to show that entry's full technical report,
with COPY CRASH REPORT (the complete report for that one entry -- error, exception type, full
traceback, time, page, Item ID, operation, application state, attempt info; never any other
entry, never credentials) and COPY ERROR (just the error text).

What appears here (data/crash_history.py merges the two stores):
  CRASH            an exception that reached a global handler
  ERROR            a failure shown to the user (e.g. an upload that failed)
  RECOVERED        a background operation that failed and was recovered from automatically
  DATA REPAIRED    a damaged/invalid settings or data file was replaced by safe defaults
  UNEXPECTED EXIT  the previous session ended without a normal close
"""
from __future__ import annotations

from tkinter import messagebox

import customtkinter as ctk

from data.crash_history import HistoryRow, build_rows, clear_all, clear_older_than
from data.diagnostics import KIND_CRASH, KIND_DATA, KIND_ERROR, KIND_RECOVERED, KIND_SHUTDOWN
from ui.app_state import AppState
from ui.widgets.dev_tag import DevTag

MAX_ROWS_SHOWN = 150
OLD_AFTER_DAYS = 7
GOOD = "#7fd17f"
MUTED = "#888888"
KIND_COLORS = {
    KIND_CRASH: "#e05a5a",
    KIND_SHUTDOWN: "#e05a5a",
    KIND_ERROR: "#e0a04a",
    KIND_DATA: "#d6c25a",
    KIND_RECOVERED: "#7aa7c9",
}


class CrashHistoryPage(ctk.CTkFrame):
    def __init__(self, master, state: AppState, **kwargs):
        super().__init__(master, fg_color="transparent", **kwargs)
        self.state = state
        self._expanded: set[str] = set()
        self._rows: list[HistoryRow] = []

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(2, weight=1)

        header = ctk.CTkFrame(self, fg_color="transparent")
        header.grid(row=0, column=0, sticky="ew", padx=16, pady=(16, 2))
        ctk.CTkLabel(header, text="Crash History", font=ctk.CTkFont(size=18, weight="bold")).pack(side="left")
        DevTag(header, state, "0.17").show(side="left", padx=(8, 0))
        self.summary_label = ctk.CTkLabel(header, text="", text_color=MUTED, font=ctk.CTkFont(size=11))
        self.summary_label.pack(side="left", padx=(14, 0))

        toolbar = ctk.CTkFrame(self, fg_color="transparent")
        toolbar.grid(row=1, column=0, sticky="ew", padx=16, pady=(4, 8))
        ctk.CTkButton(toolbar, text="Refresh", width=80, height=26, fg_color="transparent", border_width=1, command=self.refresh).pack(side="left")
        self.clear_old_btn = ctk.CTkButton(
            toolbar, text=f"Clear Older Than {OLD_AFTER_DAYS} Days", width=180, height=26, fg_color="transparent", border_width=1,
            command=self._clear_old,
        )
        self.clear_old_btn.pack(side="left", padx=(8, 0))
        self.clear_all_btn = ctk.CTkButton(
            toolbar, text="Clear All", width=90, height=26, fg_color="#5a2a2a", hover_color="#4a2020", command=self._clear_all,
        )
        self.clear_all_btn.pack(side="left", padx=(8, 0))
        DevTag(toolbar, state, "0.17.4").show(side="left", padx=(8, 0))
        self.status_label = ctk.CTkLabel(toolbar, text="", text_color=GOOD, font=ctk.CTkFont(size=11))
        self.status_label.pack(side="left", padx=(12, 0))

        self.list_frame = ctk.CTkScrollableFrame(self, fg_color="#151515", corner_radius=8)
        self.list_frame.grid(row=2, column=0, sticky="nsew", padx=16, pady=(0, 16))
        self.list_frame.grid_columnconfigure(0, weight=1)
        DevTag(self.list_frame, state, "0.17.1").show_grid(row=0, column=0, sticky="w")  # the rows below are gridded, so this must be too

        self.bind("<Map>", lambda *_e: self.refresh())  # refreshed every time the tab is opened, so new entries are always there
        self.refresh()

    # ---- data ------------------------------------------------------------------------

    def refresh(self) -> None:
        if not self.winfo_exists():
            return
        self._rows = build_rows(self.state.crash_log, self.state.error_log)
        self._render()

    def _render(self) -> None:
        for child in list(self.list_frame.winfo_children()):
            if isinstance(child, ctk.CTkFrame) and getattr(child, "_is_history_row", False):
                child.destroy()
        rows = self._rows[:MAX_ROWS_SHOWN]
        crashes = sum(1 for r in self._rows if r.kind in (KIND_CRASH, KIND_SHUTDOWN))
        self.summary_label.configure(
            text=(f"{len(self._rows)} entr{'y' if len(self._rows) == 1 else 'ies'}" + (f" -- {crashes} crash{'es' if crashes != 1 else ''}" if crashes else ""))
            if self._rows else "",
        )
        has_rows = bool(self._rows)
        self.clear_all_btn.configure(state="normal" if has_rows else "disabled")
        self.clear_old_btn.configure(state="normal" if has_rows else "disabled")

        if not rows:
            empty = ctk.CTkFrame(self.list_frame, fg_color="transparent")
            empty._is_history_row = True
            empty.grid(row=1, column=0, sticky="ew")
            ctk.CTkLabel(empty, text="No crashes or errors recorded.", text_color=MUTED).pack(anchor="w", padx=12, pady=12)
            return
        for index, row in enumerate(rows, start=1):
            self._render_row(index, row)

    def _render_row(self, grid_row: int, row: HistoryRow) -> None:
        frame = ctk.CTkFrame(self.list_frame, fg_color="#1c1c1c", corner_radius=6)
        frame._is_history_row = True
        frame.grid(row=grid_row, column=0, sticky="ew", pady=2, padx=2)
        frame.grid_columnconfigure(1, weight=1)

        expanded = row.key in self._expanded
        ctk.CTkLabel(
            frame, text=row.kind_label, width=112, anchor="w", font=ctk.CTkFont(size=10, weight="bold"),
            text_color=KIND_COLORS.get(row.kind, MUTED),
        ).grid(row=0, column=0, padx=(10, 4), pady=4, sticky="w")
        ctk.CTkButton(
            frame, text=("▼ " if expanded else "▶ ") + row.line(), anchor="w", height=24, fg_color="transparent", hover_color="#2a2a2a",
            text_color="#d0d0d0", font=ctk.CTkFont(size=11), command=lambda r=row: self._toggle(r),
        ).grid(row=0, column=1, sticky="ew", padx=(0, 6), pady=2)

        if not expanded:
            return
        details = ctk.CTkFrame(frame, fg_color="transparent")
        details.grid(row=1, column=0, columnspan=2, sticky="ew", padx=10, pady=(0, 8))
        details.grid_columnconfigure(0, weight=1)
        box = ctk.CTkTextbox(details, height=190, wrap="none", font=ctk.CTkFont(family="Consolas", size=11))
        box.grid(row=0, column=0, sticky="ew")
        box.insert("1.0", row.report_text)
        box.configure(state="disabled")
        buttons = ctk.CTkFrame(details, fg_color="transparent")
        buttons.grid(row=1, column=0, sticky="w", pady=(6, 0))
        ctk.CTkButton(buttons, text="Copy Crash Report", width=150, height=26, command=lambda r=row: self._copy(r.report_text, "Copied crash report.")).pack(side="left")
        DevTag(buttons, self.state, "0.17.2").show(side="left", padx=(6, 0))
        ctk.CTkButton(
            buttons, text="Copy Error", width=100, height=26, fg_color="transparent", border_width=1,
            command=lambda r=row: self._copy(r.error_text, "Copied error."),
        ).pack(side="left", padx=(8, 0))
        DevTag(buttons, self.state, "0.17.3").show(side="left", padx=(6, 0))

    # ---- actions ---------------------------------------------------------------------

    def _toggle(self, row: HistoryRow) -> None:
        if row.key in self._expanded:
            self._expanded.discard(row.key)
        else:
            self._expanded.add(row.key)
        self._render()

    def _copy(self, text: str, message: str) -> None:
        """Copies exactly the given text -- the one entry's report/error, nothing else."""
        try:
            self.clipboard_clear()
            self.clipboard_append(text)
        except Exception:
            self._flash("Couldn't access the clipboard.")
            return
        self._flash(message)

    def _flash(self, text: str) -> None:
        self.status_label.configure(text=text)
        self.after(2000, lambda: self.status_label.configure(text="") if self.status_label.winfo_exists() else None)

    def _clear_all(self) -> None:
        if not self._rows:
            return
        if not messagebox.askyesno("Clear Crash History", "Permanently delete every recorded crash and error?"):
            return
        clear_all(self.state.crash_log, self.state.error_log)
        self._expanded.clear()
        self.refresh()
        self._flash("Crash history cleared.")

    def _clear_old(self) -> None:
        if not self._rows:
            return
        if not messagebox.askyesno("Clear Old Records", f"Delete crash/error records older than {OLD_AFTER_DAYS} days?"):
            return
        removed = clear_older_than(self.state.crash_log, self.state.error_log, OLD_AFTER_DAYS)
        self.refresh()
        self._flash(f"Removed {removed} old record{'s' if removed != 1 else ''}.")

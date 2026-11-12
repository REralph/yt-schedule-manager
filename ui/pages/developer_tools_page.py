"""SETTINGS -> Developer Tools: Item IDs, copyable developer references, and error
reporting. Purely diagnostic -- nothing here reads or writes video/schedule data, changes
scheduling behaviour, or affects uploads/Auto Schedule; it only helps identify and report
exactly which part of the app is being discussed. See ui/dev_reporting.py and
data/error_log.py for the actual formats this copies.
"""
from __future__ import annotations

from tkinter import messagebox

import customtkinter as ctk

from data.diagnostics import KIND_CRASH, KIND_SHUTDOWN
from data.error_log import APP_VERSION
from ui.app_state import AppState
from ui.dev_ids import REGISTRY
from ui.dev_reporting import build_full_reference, current_page_dev_id, display_id
from ui.dialogs.error_log_dialog import ErrorLogDialog
from ui.widgets.dev_tag import DevTag

GOOD = "#7fd17f"


class DeveloperToolsPage(ctk.CTkFrame):
    def __init__(self, master, state: AppState, **kwargs):
        super().__init__(master, fg_color="transparent", **kwargs)
        self.state = state

        self.grid_columnconfigure(0, weight=1)

        header = ctk.CTkFrame(self, fg_color="transparent")
        header.grid(row=0, column=0, sticky="w", padx=16, pady=(16, 4))
        ctk.CTkLabel(header, text="Developer Tools", font=ctk.CTkFont(size=18, weight="bold")).pack(side="left")
        DevTag(header, state, "0.15").show(side="left", padx=(8, 0))
        ctk.CTkLabel(
            self, text="Diagnostic only -- these tools never change scheduling behaviour, uploads, or video data.",
            text_color="#888888", font=ctk.CTkFont(size=11), anchor="w",
        ).grid(row=1, column=0, sticky="w", padx=16, pady=(0, 10))

        panel = ctk.CTkFrame(self, fg_color="#1a1a1a", corner_radius=8)
        panel.grid(row=2, column=0, sticky="ew", padx=16, pady=(0, 12))

        # ---- Developer Mode ----
        mode_row = ctk.CTkFrame(panel, fg_color="transparent")
        mode_row.pack(fill="x", padx=14, pady=(14, 10))
        ctk.CTkLabel(mode_row, text="Developer Mode", font=ctk.CTkFont(size=13, weight="bold")).pack(side="left")
        self.mode_var = ctk.StringVar(value="on" if state.config.developer_mode else "off")
        self.mode_switch = ctk.CTkSwitch(
            mode_row, text="", command=self._toggle_dev_mode, variable=self.mode_var, onvalue="on", offvalue="off",
        )
        self.mode_switch.pack(side="right")
        DevTag(mode_row, state, "0.15.1.1").show(side="right", padx=(0, 8))
        ctk.CTkLabel(
            panel, text="Shows an [ID] tag beside pages and features throughout the app. Off by default -- turn it "
            "on when you want to reference something specific.", text_color="#888888", font=ctk.CTkFont(size=11),
            anchor="w", justify="left",
        ).pack(fill="x", padx=14, pady=(0, 12))

        ctk.CTkFrame(panel, height=1, fg_color="#2a2a2a").pack(fill="x", padx=14, pady=(0, 12))

        # ---- reference copying ----
        current_row = ctk.CTkFrame(panel, fg_color="transparent")
        current_row.pack(fill="x", padx=14, pady=(0, 8))
        ctk.CTkLabel(current_row, text="Current Item ID", font=ctk.CTkFont(size=12)).pack(side="left")
        self.current_id_label = ctk.CTkLabel(current_row, text="", text_color="#8fb0cc", font=ctk.CTkFont(size=12, weight="bold"))
        self.current_id_label.pack(side="right")
        DevTag(current_row, state, "0.15.2.1").show(side="right", padx=(0, 8))

        btn_row = ctk.CTkFrame(panel, fg_color="transparent")
        btn_row.pack(fill="x", padx=14, pady=(0, 6))
        self.copy_id_btn = ctk.CTkButton(btn_row, text="Copy ID", width=110, command=self._copy_id)
        self.copy_id_btn.pack(side="left")
        DevTag(btn_row, state, "0.15.2.2").show(side="left", padx=(6, 0))
        self.copy_page_btn = ctk.CTkButton(btn_row, text="Copy Page Reference", width=160, command=self._copy_page_reference)
        self.copy_page_btn.pack(side="left", padx=(8, 0))
        DevTag(btn_row, state, "0.15.2.3").show(side="left", padx=(6, 0))

        current_page_row = ctk.CTkFrame(panel, fg_color="transparent")
        current_page_row.pack(fill="x", padx=14, pady=(0, 14))
        ctk.CTkButton(
            current_page_row, text="Copy Current Page Info", width=180, command=self._copy_current_page_info,
        ).pack(side="left")
        DevTag(current_page_row, state, "0.15.2.4").show(side="left", padx=(6, 0))

        ctk.CTkFrame(panel, height=1, fg_color="#2a2a2a").pack(fill="x", padx=14, pady=(0, 12))

        # ---- error reporting ----
        ctk.CTkLabel(panel, text="Error Reporting", font=ctk.CTkFont(size=13, weight="bold")).pack(anchor="w", padx=14, pady=(0, 8))
        error_row = ctk.CTkFrame(panel, fg_color="transparent")
        error_row.pack(fill="x", padx=14, pady=(0, 14))
        self.copy_last_error_btn = ctk.CTkButton(error_row, text="Copy Last Error", width=150, command=self._copy_last_error)
        self.copy_last_error_btn.pack(side="left")
        DevTag(error_row, state, "0.15.3.1").show(side="left", padx=(6, 0))
        ctk.CTkButton(error_row, text="View Error Log", width=130, fg_color="transparent", border_width=1, command=self._view_error_log).pack(
            side="left", padx=(8, 0)
        )
        DevTag(error_row, state, "0.15.3.2").show(side="left", padx=(6, 0))

        ctk.CTkFrame(panel, height=1, fg_color="#2a2a2a").pack(fill="x", padx=14, pady=(0, 12))

        # ---- error / crash log ----
        crash_header_row = ctk.CTkFrame(panel, fg_color="transparent")
        crash_header_row.pack(fill="x", padx=14, pady=(0, 8))
        ctk.CTkLabel(crash_header_row, text="Error / Crash Log", font=ctk.CTkFont(size=13, weight="bold")).pack(side="left")
        DevTag(crash_header_row, state, "0.15.5").show(side="left", padx=(6, 0))
        ctk.CTkLabel(
            panel, text="Unhandled/fatal errors the app's global exception handling caught -- separate from Error "
            "Reporting above, which is for errors the app already handled gracefully. Survives restarts.",
            text_color="#888888", font=ctk.CTkFont(size=11), anchor="w", justify="left", wraplength=520,
        ).pack(fill="x", padx=14, pady=(0, 8))

        last_error_row = ctk.CTkFrame(panel, fg_color="transparent")
        last_error_row.pack(fill="x", padx=14, pady=(0, 8))
        ctk.CTkLabel(last_error_row, text="LAST APPLICATION ERROR", font=ctk.CTkFont(size=11, weight="bold"), text_color="#8fb0cc").pack(anchor="w")
        self.last_crash_label = ctk.CTkLabel(
            last_error_row, text="", text_color="#a0a0a0", font=ctk.CTkFont(size=11), anchor="w", justify="left", wraplength=520,
        )
        self.last_crash_label.pack(anchor="w", pady=(2, 0))
        DevTag(last_error_row, state, "0.15.5.1").show(anchor="w", pady=(2, 0))

        crash_btn_row = ctk.CTkFrame(panel, fg_color="transparent")
        crash_btn_row.pack(fill="x", padx=14, pady=(6, 14))
        self.copy_crash_error_btn = ctk.CTkButton(crash_btn_row, text="Copy Error", width=110, command=self._copy_crash_error)
        self.copy_crash_error_btn.pack(side="left")
        DevTag(crash_btn_row, state, "0.15.5.2").show(side="left", padx=(6, 0))
        self.copy_crash_diagnostics_btn = ctk.CTkButton(
            crash_btn_row, text="Copy Diagnostics", width=140, fg_color="transparent", border_width=1,
            command=self._copy_crash_diagnostics,
        )
        self.copy_crash_diagnostics_btn.pack(side="left", padx=(8, 0))
        DevTag(crash_btn_row, state, "0.15.5.3").show(side="left", padx=(6, 0))
        self.clear_crash_log_btn = ctk.CTkButton(
            crash_btn_row, text="Clear Log", width=90, fg_color="#5a2a2a", hover_color="#4a2020", command=self._clear_crash_log,
        )
        self.clear_crash_log_btn.pack(side="left", padx=(8, 0))
        DevTag(crash_btn_row, state, "0.15.5.4").show(side="left", padx=(6, 0))

        ctk.CTkFrame(panel, height=1, fg_color="#2a2a2a").pack(fill="x", padx=14, pady=(0, 12))

        version_row = ctk.CTkFrame(panel, fg_color="transparent")
        version_row.pack(fill="x", padx=14, pady=(0, 14))
        ctk.CTkLabel(version_row, text="Application Version", font=ctk.CTkFont(size=12)).pack(side="left")
        ctk.CTkLabel(version_row, text=APP_VERSION, text_color="#a0a0a0", font=ctk.CTkFont(size=12)).pack(side="right")
        DevTag(version_row, state, "0.15.4").show(side="right", padx=(0, 8))

        self.status_label = ctk.CTkLabel(self, text="", text_color=GOOD)
        self.status_label.grid(row=3, column=0, sticky="w", padx=16)

        state.on_dev_selection_changed(self._refresh)
        self._refresh()
        self._refresh_crash_log()

    def _refresh(self) -> None:
        dev_id = self.state.dev_selected_component
        if dev_id and dev_id in REGISTRY:
            self.current_id_label.configure(text=display_id(dev_id))
            self.copy_id_btn.configure(state="normal")
            self.copy_page_btn.configure(state="normal")
        else:
            self.current_id_label.configure(text="(click any [ID] tag first)")
            self.copy_id_btn.configure(state="disabled")
            self.copy_page_btn.configure(state="disabled")
        self.copy_last_error_btn.configure(state="normal" if self.state.last_error else "disabled")

    def _latest_crash(self):
        """LAST APPLICATION ERROR means an actual crash / unexpected exit -- not the
        recovered background issues and repaired data files that Settings -> Crash History
        also lists (those would otherwise crowd out the real thing here)."""
        return self.state.crash_log.latest_of_kinds(KIND_CRASH, KIND_SHUTDOWN)

    def _refresh_crash_log(self) -> None:
        latest = self._latest_crash()
        if latest is None:
            self.last_crash_label.configure(text="No crashes recorded.")
        else:
            self.last_crash_label.configure(text=latest.short_summary())
        has_crash = latest is not None
        self.copy_crash_error_btn.configure(state="normal" if has_crash else "disabled")
        self.copy_crash_diagnostics_btn.configure(state="normal" if has_crash else "disabled")
        self.clear_crash_log_btn.configure(state="normal" if has_crash else "disabled")

    def _toggle_dev_mode(self) -> None:
        self.state.set_developer_mode(self.mode_var.get() == "on")

    def _flash_status(self, text: str) -> None:
        self.status_label.configure(text=text)
        self.after(1500, lambda: self.status_label.configure(text="") if self.status_label.winfo_exists() else None)

    def _copy_to_clipboard(self, text: str) -> None:
        try:
            self.clipboard_clear()
            self.clipboard_append(text)
        except Exception:
            pass

    def _copy_id(self) -> None:
        dev_id = self.state.dev_selected_component
        if not dev_id:
            return
        self._copy_to_clipboard(display_id(dev_id))
        self._flash_status("Copied ID.")

    def _copy_page_reference(self) -> None:
        dev_id = self.state.dev_selected_component
        if not dev_id:
            return
        self._copy_to_clipboard(build_full_reference(dev_id))
        self._flash_status("Copied page reference.")

    def _copy_current_page_info(self) -> None:
        dev_id = current_page_dev_id(self.state)
        if not dev_id:
            messagebox.showinfo("Copy Current Page Info", "Couldn't tell which tab is currently active.")
            return
        self._copy_to_clipboard(build_full_reference(dev_id))
        self._flash_status("Copied current page info.")

    def _copy_last_error(self) -> None:
        if not self.state.last_error:
            return
        self._copy_to_clipboard(self.state.last_error.format())
        self._flash_status("Copied last error report.")

    def _view_error_log(self) -> None:
        ErrorLogDialog(self, self.state.error_log)

    def _copy_crash_error(self) -> None:
        latest = self._latest_crash()
        if latest is None:
            return
        self._copy_to_clipboard(f"{latest.error_type}: {latest.error_message}")
        self._flash_status("Copied crash error.")

    def _copy_crash_diagnostics(self) -> None:
        latest = self._latest_crash()
        if latest is None:
            return
        self._copy_to_clipboard(latest.format())
        self._flash_status("Copied full crash diagnostics.")

    def _clear_crash_log(self) -> None:
        if self._latest_crash() is None:
            return
        if not messagebox.askyesno("Clear Crash Log", "Permanently delete all recorded crash reports?"):
            return
        self.state.crash_log.clear()
        self._refresh_crash_log()
        self._flash_status("Crash log cleared.")

"""Developer Mode chrome: a small "DEVELOPER MODE — ON" header banner, and an info
panel showing the current page/section plus whichever component was last clicked via a
DevTag, with a Copy ID button. Both are entirely inert when Developer Mode is off --
purely informational, never touches app behaviour or data."""
from __future__ import annotations

import customtkinter as ctk

from ui.app_state import AppState
from ui.dev_ids import REGISTRY
from ui.dev_ids import label as dev_label
from ui.dev_ids import page_id_of


class DevModeBanner(ctk.CTkFrame):
    def __init__(self, master, state: AppState, grid_row: int, **kwargs):
        super().__init__(master, fg_color="#5a3a10", corner_radius=0, height=22, **kwargs)
        self.state = state
        self._grid_row = grid_row
        self._shown = False
        self.grid_propagate(False)

        ctk.CTkLabel(
            self, text="DEVELOPER MODE — ON", font=ctk.CTkFont(size=11, weight="bold"), text_color="#f0d080"
        ).pack(pady=1)

        self.state.on_developer_mode_changed(self._sync_visibility)
        self._sync_visibility()

    def _sync_visibility(self) -> None:
        if self.state.config.developer_mode and not self._shown:
            self.grid(row=self._grid_row, column=0, sticky="ew")
            self._shown = True
        elif not self.state.config.developer_mode and self._shown:
            self.grid_remove()
            self._shown = False


class DevInfoPanel(ctk.CTkFrame):
    def __init__(self, master, state: AppState, grid_row: int, **kwargs):
        super().__init__(master, fg_color="#2a2410", corner_radius=0, **kwargs)
        self.state = state
        self._grid_row = grid_row
        self._shown = False
        self.grid_columnconfigure(0, weight=1)

        self.info_label = ctk.CTkLabel(
            self, text="", anchor="w", justify="left", font=ctk.CTkFont(size=11), text_color="#d0b060"
        )
        self.info_label.grid(row=0, column=0, sticky="ew", padx=10, pady=4)

        self.copy_btn = ctk.CTkButton(self, text="Copy ID", width=80, height=22, command=self._copy)
        self.copy_btn.grid(row=0, column=1, padx=10, pady=4)

        self.state.on_dev_selection_changed(self._render)
        self.state.on_developer_mode_changed(self._sync_visibility)
        self._render()
        self._sync_visibility()

    def _render(self) -> None:
        dev_id = self.state.dev_selected_component
        if not dev_id:
            self.info_label.configure(text="Click any [ID] tag to inspect that component.")
            self.copy_btn.configure(state="disabled")
            return

        page_id = page_id_of(dev_id)
        page_node = REGISTRY.get(page_id)
        id_parts = dev_id.split(".")
        section_id = ".".join(id_parts[:3]) if len(id_parts) >= 3 else None
        section_node = REGISTRY.get(section_id) if section_id else None

        parts = [f"Current Page: {page_id}  |  Page Name: {page_node.name if page_node else '?'}"]
        if section_node:
            parts.append(f"Current Section: {section_id}  |  Section Name: {section_node.name}")
        parts.append(f"Selected Component: {dev_label(dev_id)}")
        self.info_label.configure(text="    ".join(parts))
        self.copy_btn.configure(state="normal")

    def _copy(self) -> None:
        dev_id = self.state.dev_selected_component
        if not dev_id:
            return
        try:
            self.clipboard_clear()
            self.clipboard_append(dev_id)
        except Exception:
            pass

    def _sync_visibility(self) -> None:
        if self.state.config.developer_mode and not self._shown:
            self.grid(row=self._grid_row, column=0, sticky="ew")
            self._shown = True
        elif not self.state.config.developer_mode and self._shown:
            self.grid_remove()
            self._shown = False

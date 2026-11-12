"""One reusable "N slots/day" control, used everywhere the app lets you pick a daily slot
count (Auto Schedule Settings' SET-0012.1 panel, Increase Daily Slots' rate picker) -- see
the "Slot Controls -- Default Selection + Collapsible Time Settings" spec. Previously each
page built its own slightly different version of this; this is the one shared component.

Normal view is just the compact segmented buttons plus a small gear:
    1 | 2 | [3] | 4 | 5 | 6 | ⚙
Clicking the gear expands a small panel showing SLOT 1..N (only the currently-selected
count's slots, never all six at once) with editable times, auto-saving on a valid edit.
Clicking the gear again collapses it back to the compact view.

Ownership split, because "which slot count is selected" and "what times a given count's
preset uses" are two different questions with two different owners:
  - The selected COUNT is entirely the host page's business -- this widget only reports a
    change via on_count_changed(count); it never writes normal_videos_per_day itself. Auto
    Schedule Settings wants that selection to become the new normal daily rate; Increase
    Daily Slots wants it to stay a one-off, temporary choice for that operation only.
  - The PRESET TIMES for a given count are unambiguously global (SET-0012.1: "3 slots"
    means the same thing everywhere), so this widget freely reads/writes
    config.slot_presets via config.get_slot_times()/save_slot_preset() -- called on the
    exact AutoScheduleConfig *instance* the host page passed in, never a separate load(),
    so the host's own later config.save() calls can't clobber what this widget just saved.
"""
from __future__ import annotations

import datetime as dt
from typing import Callable

import customtkinter as ctk

from data.auto_schedule_config import (
    MAX_SLOT_COUNT,
    MIN_SLOT_COUNT,
    AutoScheduleConfig,
    validate_slot_times,
)
from ui.app_state import AppState
from ui.widgets.dev_tag import DevTag

BAD = "#e05a5a"
GOOD = "#7fd17f"


def _clamp(count: int) -> int:
    return max(MIN_SLOT_COUNT, min(MAX_SLOT_COUNT, count))


class SlotCountControl(ctk.CTkFrame):
    def __init__(
        self,
        master,
        state: AppState,
        config: AutoScheduleConfig,
        initial_count: int,
        on_count_changed: Callable[[int], None],
        selector_dev_id: str,
        gear_dev_id: str,
        panel_dev_id: str,
        **kwargs,
    ):
        super().__init__(master, fg_color="transparent", **kwargs)
        self.state = state
        self.config = config
        self._on_count_changed = on_count_changed
        self._panel_dev_id = panel_dev_id
        self._expanded = False
        self.slot_time_vars: list[ctk.StringVar] = []

        self.count_var = ctk.StringVar(value=str(_clamp(initial_count)))

        top_row = ctk.CTkFrame(self, fg_color="transparent")
        top_row.pack(fill="x")
        self.segmented_btn = ctk.CTkSegmentedButton(
            top_row, variable=self.count_var,
            values=[str(n) for n in range(MIN_SLOT_COUNT, MAX_SLOT_COUNT + 1)],
            command=self._handle_count_changed,
        )
        self.segmented_btn.pack(side="left")
        DevTag(top_row, state, selector_dev_id).show(side="left", padx=(8, 0))
        self.gear_btn = ctk.CTkButton(
            top_row, text="⚙", width=28, height=28, fg_color="transparent", border_width=1,
            font=ctk.CTkFont(size=13), command=self._toggle_expanded,
        )
        self.gear_btn.pack(side="left", padx=(8, 0))
        DevTag(top_row, state, gear_dev_id).show(side="left", padx=(4, 0))

        self.panel_frame = ctk.CTkFrame(self, fg_color="#141414", corner_radius=6)
        # Deliberately NOT packed yet -- collapsed by default (point 2: the slot buttons
        # stay clean, nothing underneath them until the gear is clicked).
        self._build_panel_contents()

    def selected_count(self) -> int:
        return _clamp(int(self.count_var.get()))

    def set_enabled(self, enabled: bool) -> None:
        """For a host page that needs to lock this control during some other operation
        (e.g. One Upload Page while an upload is in flight) -- disables the count
        selector and the gear icon, and collapses the panel if it happened to be open."""
        state_ = "normal" if enabled else "disabled"
        self.segmented_btn.configure(state=state_)
        self.gear_btn.configure(state=state_)
        if not enabled and self._expanded:
            self._toggle_expanded()

    def _handle_count_changed(self, value: str) -> None:
        self._build_panel_contents()
        self._on_count_changed(_clamp(int(value)))

    def _toggle_expanded(self) -> None:
        self._expanded = not self._expanded
        if self._expanded:
            self._build_panel_contents()
            self.panel_frame.pack(fill="x", pady=(8, 0))
        else:
            self.panel_frame.pack_forget()

    def _build_panel_contents(self) -> None:
        for w in self.panel_frame.winfo_children():
            w.destroy()
        self.slot_time_vars = []

        header_row = ctk.CTkFrame(self.panel_frame, fg_color="transparent")
        header_row.pack(fill="x", padx=10, pady=(8, 4))
        ctk.CTkLabel(header_row, text="CONFIGURE SLOT TIMES", font=ctk.CTkFont(size=11, weight="bold"), text_color="#8fb0cc").pack(side="left")
        DevTag(header_row, self.state, self._panel_dev_id).show(side="left", padx=(6, 0))

        count = self.selected_count()
        for i, t in enumerate(self.config.get_slot_times(count)):
            row = ctk.CTkFrame(self.panel_frame, fg_color="transparent")
            row.pack(fill="x", padx=10, pady=(0, 4))
            ctk.CTkLabel(row, text=f"SLOT {i + 1}", width=60, anchor="w", font=ctk.CTkFont(size=11)).pack(side="left")
            var = ctk.StringVar(value=t.strftime("%H:%M"))
            entry = ctk.CTkEntry(row, textvariable=var, width=80)
            entry.pack(side="left")
            entry.bind("<Return>", lambda _e: self._commit())
            entry.bind("<FocusOut>", lambda _e: self._commit())
            self.slot_time_vars.append(var)

        self.status_label = ctk.CTkLabel(self.panel_frame, text="", font=ctk.CTkFont(size=11))
        self.status_label.pack(anchor="w", padx=10, pady=(0, 8))

    def _commit(self) -> None:
        count = self.selected_count()
        try:
            parsed = [dt.datetime.strptime(v.get().strip(), "%H:%M").time() for v in self.slot_time_vars]
        except ValueError:
            self.status_label.configure(text="Slot times must be in 24-hour HH:MM format, e.g. 06:00.", text_color=BAD)
            return
        error = validate_slot_times(parsed, self.config.window_start, self.config.window_end)
        if error:
            self.status_label.configure(text=error, text_color=BAD)
            return
        self.config.save_slot_preset(count, parsed)
        self.status_label.configure(text=f"Saved {count}-slot preset.", text_color=GOOD)
        self.after(2000, lambda: self.status_label.configure(text="") if self.status_label.winfo_exists() else None)

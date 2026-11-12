"""A small, subtle label that shows a Developer Mode ID (e.g. "[AS-001 — Auto Schedule
Settings]", or "[0.13.3.1 — Confirm & Push Schedule Button]" for nodes without a short
code) next to the page/section/component it identifies. Hidden entirely when Developer
Mode is off, and live-toggles with it -- no page needs to re-render to react to the switch.
Clicking it copies ONLY the bare Item ID/short code (e.g. "AS-001", not "Item ID:
AS-001\\nPage: ...\\nFunction: ...") to the clipboard, and updates the Developer Info
Panel's "selected component" -- Developer Tools' own Copy Page Reference / Copy Current
Page Info buttons (see ui/dev_reporting.py) are what give the fuller Item ID/Page/Function
text when that's actually wanted.

Usage: construct it, then call .show(**pack_kwargs) with the same keyword arguments you
would normally pass to .pack() -- use that instead of calling .pack() directly, so
toggling Developer Mode back on can restore the exact same placement.
"""
from __future__ import annotations

import customtkinter as ctk

from ui import ctk_perf
from ui.app_state import AppState
from ui.dev_ids import label as dev_label
from ui.dev_reporting import display_id

DEV_TAG_COLOR = "#6a8caa"
DEV_TAG_HOVER = "#8fb0cc"


class DevTag(ctk.CTkLabel):
    # Hidden unless Developer Mode is on, and there are ~200 of them: they are not rescaled for a monitor change until they are
    # actually shown (see ui/ctk_perf.py).
    _ctk_perf_defer_until_shown = True

    def __init__(self, master, state: AppState, dev_id: str, **kwargs):
        self.state = state
        self.dev_id = dev_id
        self._pack_kwargs: dict = {}
        self._manager = "pack"
        self._shown = False
        text = f"[{dev_label(dev_id)}]"
        kwargs.setdefault("font", ctk.CTkFont(size=9, slant="italic"))
        kwargs.setdefault("text_color", DEV_TAG_COLOR)
        kwargs.setdefault("cursor", "hand2")
        super().__init__(master, text=text, **kwargs)

        # *_e (not e) on every handler below: Tk can occasionally invoke a bound callback
        # with no substituted event args at all -- observed on <Destroy> specifically when
        # a parent rebuilds many children in a tight loop (e.g.
        # ui/widgets/slot_selector.py's SlotCountControl re-rendering its slot-time
        # fields on every count change). A strict `lambda e: ...` would then raise
        # TypeError, which main.py's global handler would otherwise record as a spurious
        # "crash" -- there's nothing actually wrong here, so tolerate it instead.
        self.bind("<Button-1>", lambda *_e: self._activate())
        self.bind("<Enter>", lambda *_e: self.configure(text_color=DEV_TAG_HOVER))
        self.bind("<Leave>", lambda *_e: self.configure(text_color=DEV_TAG_COLOR))
        self.bind("<Destroy>", lambda *_e: self.state.off_developer_mode_changed(self._sync_visibility))

        self.state.on_developer_mode_changed(self._sync_visibility)

    def show(self, **pack_kwargs) -> "DevTag":
        """Call once after construction instead of .pack() -- remembers the layout.
        Use this when the tag's parent lays its other children out with .pack()."""
        self._manager = "pack"
        self._pack_kwargs = pack_kwargs
        self._sync_visibility()
        return self

    def show_grid(self, **grid_kwargs) -> "DevTag":
        """Same idea as show(), for a parent that lays its other children out with
        .grid() -- a parent can't mix the two geometry managers."""
        self._manager = "grid"
        self._pack_kwargs = grid_kwargs
        self._sync_visibility()
        return self

    def _activate(self) -> None:
        self.state.set_dev_selected_component(self.dev_id)
        try:
            self.clipboard_clear()
            self.clipboard_append(display_id(self.dev_id))
        except Exception:
            pass  # clipboard access can occasionally fail on some platforms; non-critical

    def _sync_visibility(self) -> None:
        if not self.winfo_exists():
            # This instance was destroyed (e.g. its parent's content was rebuilt) but
            # its listener is still registered -- a harmless no-op rather than a crash.
            return
        if self.state.config.developer_mode:
            if not self._shown:
                ctk_perf.apply_pending_single(self)
                if self._manager == "grid":
                    self.grid(**self._pack_kwargs)
                else:
                    self.pack(**self._pack_kwargs)
                self._shown = True
        else:
            if self._shown:
                if self._manager == "grid":
                    self.grid_remove()
                else:
                    self.pack_forget()
                self._shown = False

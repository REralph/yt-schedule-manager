"""AUTO PASTE & ENTER: a small, generic button usable on any page/dialog that has a text
field -- pastes the current clipboard contents into whichever widget currently has
keyboard focus, then sends Enter. Nothing more: it never reads or parses the clipboard
itself (unlike Quick Paste, ui/widgets/basic_upload_panel.py's 0.9.6.18, which splits the
clipboard into Title/Description specifically), never touches the clipboard's contents,
and never looks at what page/field it's on -- it only ever acts on whatever the user last
clicked into, exactly like a manual Ctrl+V then Enter would. Deliberately independent of
Quick Paste and Upload; clicking it never triggers either.

Does nothing if no text field currently has focus (including if focus happens to be on
this button itself, or on nothing at all) -- see _paste_and_enter(). Safe to construct
more than once on the same page or reuse across pages; each instance is stateless."""
from __future__ import annotations

import tkinter as tk

import customtkinter as ctk


class AutoPasteEnterButton(ctk.CTkButton):
    def __init__(self, master, **kwargs):
        kwargs.setdefault("text", "AUTO PASTE & ENTER")
        kwargs.setdefault("width", 170)
        kwargs.setdefault("height", 36)
        super().__init__(master, command=self._paste_and_enter, **kwargs)

    def _paste_and_enter(self) -> None:
        widget = self.focus_get()
        if not isinstance(widget, (tk.Entry, tk.Text)):
            return  # nothing (or something non-text) focused -- do not paste or submit
        widget.event_generate("<<Paste>>")
        widget.event_generate("<Return>")

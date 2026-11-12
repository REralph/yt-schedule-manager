"""Small dark-themed calendar popup for picking a start date. Built custom rather than
using tkcalendar, whose ttk styling doesn't match CustomTkinter's dark theme."""
from __future__ import annotations

import calendar
import datetime as dt
from typing import Callable, Optional

import customtkinter as ctk

MONTH_NAMES = [
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
]


class DatePickerButton(ctk.CTkButton):
    """A button labeled with the currently chosen date; clicking it opens a small
    calendar popup to pick a new one."""

    def __init__(
        self,
        master,
        initial_date: dt.date,
        on_change: Optional[Callable[[dt.date], None]] = None,
        label_prefix: str = "Start date",
        short_format: bool = False,
        **kwargs,
    ):
        self._date = initial_date
        self._on_change = on_change
        self._label_prefix = label_prefix
        self._short_format = short_format
        super().__init__(master, text=self._label(), command=self._open_popup, **kwargs)

    def _label(self) -> str:
        if self._short_format:
            return f"{self._label_prefix}: {self._date.day} {MONTH_NAMES[self._date.month - 1][:3]} {self._date.year}"
        return f"{self._label_prefix}: {self._date.day} {MONTH_NAMES[self._date.month - 1]} {self._date.year}"

    @property
    def value(self) -> dt.date:
        return self._date

    def set_date(self, new_date: dt.date) -> None:
        self._date = new_date
        self.configure(text=self._label())

    def _open_popup(self) -> None:
        _CalendarPopup(self, self._date, self._on_select)

    def _on_select(self, picked: dt.date) -> None:
        self.set_date(picked)
        if self._on_change:
            self._on_change(picked)


class _CalendarPopup(ctk.CTkToplevel):
    def __init__(self, master, initial_date: dt.date, on_pick: Callable[[dt.date], None]):
        super().__init__(master)
        self.title("Select start date")
        self.resizable(False, False)
        self.attributes("-topmost", True)
        self._on_pick = on_pick
        self._view_year = initial_date.year
        self._view_month = initial_date.month
        self._selected_date = initial_date

        header = ctk.CTkFrame(self, fg_color="transparent")
        header.pack(fill="x", padx=8, pady=(8, 4))
        ctk.CTkButton(header, text="<", width=30, command=self._prev_month).pack(side="left")
        self._month_label = ctk.CTkLabel(header, text="", font=ctk.CTkFont(size=13, weight="bold"))
        self._month_label.pack(side="left", expand=True)
        ctk.CTkButton(header, text=">", width=30, command=self._next_month).pack(side="right")

        self._grid_frame = ctk.CTkFrame(self, fg_color="transparent")
        self._grid_frame.pack(padx=8, pady=(0, 8))

        self._render()

        self.update_idletasks()
        try:
            x = master.winfo_rootx()
            y = master.winfo_rooty() + master.winfo_height()
            self.geometry(f"+{x}+{y}")
        except Exception:
            pass
        self.grab_set()

    def _render(self) -> None:
        for w in self._grid_frame.winfo_children():
            w.destroy()

        self._month_label.configure(text=f"{MONTH_NAMES[self._view_month - 1]} {self._view_year}")

        for col, name in enumerate(["Mo", "Tu", "We", "Th", "Fr", "Sa", "Su"]):
            ctk.CTkLabel(self._grid_frame, text=name, width=34, text_color="#888888").grid(
                row=0, column=col, padx=1, pady=1
            )

        cal = calendar.Calendar(firstweekday=0)  # Monday first
        weeks = cal.monthdayscalendar(self._view_year, self._view_month)
        today = dt.date.today()
        for row_idx, week in enumerate(weeks, start=1):
            for col_idx, day in enumerate(week):
                if day == 0:
                    ctk.CTkLabel(self._grid_frame, text="", width=34).grid(row=row_idx, column=col_idx, padx=1, pady=1)
                    continue
                this_date = dt.date(self._view_year, self._view_month, day)
                is_selected = this_date == self._selected_date
                is_today = this_date == today
                if is_selected:
                    fg = "#274b6d"
                elif is_today:
                    fg = "#3a3a1f"
                else:
                    fg = "#2a2a2a"
                btn = ctk.CTkButton(
                    self._grid_frame,
                    text=str(day),
                    width=34,
                    height=28,
                    fg_color=fg,
                    command=lambda d=this_date: self._pick(d),
                )
                btn.grid(row=row_idx, column=col_idx, padx=1, pady=1)

    def _pick(self, picked: dt.date) -> None:
        self._on_pick(picked)
        self.destroy()

    def _prev_month(self) -> None:
        if self._view_month == 1:
            self._view_month = 12
            self._view_year -= 1
        else:
            self._view_month -= 1
        self._render()

    def _next_month(self) -> None:
        if self._view_month == 12:
            self._view_month = 1
            self._view_year += 1
        else:
            self._view_month += 1
        self._render()

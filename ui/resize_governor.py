"""Keeps the window fluid while its edge is being dragged.

Why dragging froze: every size change Windows reports while you drag makes Tk re-lay-out the whole page and CustomTkinter
redraw every widget that changed size -- about a millisecond per widget, per step, with hundreds or thousands on screen
(the Auto Boost tab alone drew ~1,000 labels: one second per step, i.e. one frame per second). The window then stops
answering and Windows paints it blank until it catches up.

What this does: the moment a resize starts, the page is held at the size it had; the window frame follows the mouse
instantly and the page is simply clipped (or the new area shows the window background). When the size has stopped changing
for SETTLE_MS the page is released and laid out ONCE at the final size. Nothing is lost or reordered -- it is purely a
change in WHEN the layout happens.

Implementation: the screen sits in cell (0, 0) of the window's grid, stretching with it (weight 1). Holding it means giving
that cell a fixed size (weight 0, minsize = the page's current size); releasing it restores weight 1. Moving the window
(same size) never holds anything.

If even the held page cannot be repainted fast enough (a heavy tab on a large, high-scale screen: size events arrive more than
SLOW_GAP_MS apart, SLOW_GAPS_TO_HIDE times in a row) the page is hidden until the drag ends, so the window frame still follows
the mouse smoothly; it reappears, once, when you let go.

A second hook lets ui/ctk_perf.py hide the page for the instant it takes to rescale after a move to a monitor with a
different display scale.
"""
from __future__ import annotations

import time
from typing import Callable, Optional

SETTLE_MS = 200  # the page is released this long after the LAST size change (longer if size changes are arriving slowly -- see below)
SETTLE_GAP_FACTOR = 2.5  # ...and never sooner than this many times the recent gap between size changes, so a slow-to-repaint page
MAX_SETTLE_MS = 700  # is not released in the middle of a drag (a release re-lays the whole page out, which is the slow thing we avoid)
SLOW_GAP_MS = 170  # while dragging, size events further apart than this mean the repaint cannot keep up...
SLOW_GAPS_TO_HIDE = 2  # ...and after this many in a row the page is hidden until the drag ends


class ResizeGovernor:
    def __init__(self, window, get_content: Callable[[], Optional[object]], settle_ms: int = SETTLE_MS, clock: Callable[[], float] = time.monotonic):
        self.window = window
        self._get_content = get_content
        self.settle_ms = settle_ms
        self._clock = clock
        self._last_size: Optional[tuple] = None
        self._frozen = False
        self._job = None
        self._hidden_content = None  # hidden for a monitor change (see hide_for_rescale)
        self._drag_hidden = None  # hidden because the drag was too slow to repaint
        self._last_event_at: Optional[float] = None
        self._slow_gaps = 0
        self._real_last: Optional[float] = None  # real time of the previous size change (always time.monotonic: it sizes the release timer)
        self._gap_ms = 0.0  # recent gap between size changes, decaying
        self.freezes = 0  # how many times a resize was held -- for tests/diagnostics
        self.drag_hides = 0
        window.bind("<Configure>", self._on_configure, add="+")
        window.bind("<Destroy>", self._on_destroy, add="+")

    def _on_destroy(self, event) -> None:
        if event.widget is not self.window:
            return
        self._frozen = False
        if self._job is not None:
            try:
                self.window.after_cancel(self._job)  # no timer left behind to fire at a window that no longer exists
            except Exception:
                pass
            self._job = None

    # ---- state ----

    @property
    def is_frozen(self) -> bool:
        return self._frozen

    # ---- resizing ----

    def _on_configure(self, event) -> None:
        if event.widget is not self.window:
            return  # every child's Configure bubbles up to the window's bindings; only the window's own size matters here
        size = (event.width, event.height)
        previous, self._last_size = self._last_size, size
        if previous is None or size == previous:
            return  # the first event, or the window was only moved
        real = time.monotonic()
        if self._real_last is not None:
            gap = (real - self._real_last) * 1000
            self._gap_ms = max(gap, self._gap_ms * 0.6) if gap < 2000 else 0.0  # a long pause is not a slow drag
        self._real_last = real
        now = self._clock()
        if not self._frozen:
            self._freeze()
            self._last_event_at, self._slow_gaps = now, 0
        elif self._last_event_at is not None:
            gap_ms = (now - self._last_event_at) * 1000
            self._last_event_at = now
            self._slow_gaps = self._slow_gaps + 1 if gap_ms > SLOW_GAP_MS else 0
            if self._slow_gaps >= SLOW_GAPS_TO_HIDE and self._drag_hidden is None:
                self._hide_for_drag()
        if self._frozen:
            self._schedule_release()

    def _freeze(self) -> None:
        content = self._get_content()
        try:
            if content is None or not content.winfo_exists() or not content.winfo_ismapped():
                return
            width, height = content.winfo_width(), content.winfo_height()
            if width <= 1 or height <= 1:
                return
            self.window.grid_columnconfigure(0, weight=0, minsize=width)
            self.window.grid_rowconfigure(0, weight=0, minsize=height)
            self._frozen = True
            self.freezes += 1
        except Exception:
            self._frozen = False

    def _schedule_release(self) -> None:
        try:
            if self._job is not None:
                self.window.after_cancel(self._job)
            delay = int(min(MAX_SETTLE_MS, max(self.settle_ms, self._gap_ms * SETTLE_GAP_FACTOR)))
            self._job = self.window.after(delay, self.release)
        except Exception:
            self._job = None

    def release(self) -> None:
        """The size has settled: lay the page out once, at the final size."""
        self._job = None
        if not self._frozen:
            return
        self._frozen = False
        self._last_event_at, self._slow_gaps = None, 0
        self._real_last, self._gap_ms = None, 0.0
        try:
            self.window.grid_columnconfigure(0, weight=1, minsize=0)
            self.window.grid_rowconfigure(0, weight=1, minsize=0)
        except Exception:
            pass
        content, self._drag_hidden = self._drag_hidden, None
        try:
            if content is not None and content.winfo_exists():
                content.grid()  # back where it was, now at the final size
        except Exception:
            pass

    def _hide_for_drag(self) -> None:
        content = self._get_content()
        try:
            if content is not None and content.winfo_exists() and content.winfo_manager() == "grid":
                content.grid_remove()
                self._drag_hidden = content
                self.drag_hides += 1
        except Exception:
            self._drag_hidden = None

    # ---- monitor change (see ui/ctk_perf.py) ----

    def hide_for_rescale(self, window) -> None:
        if window is not self.window:
            return
        content = self._get_content()
        try:
            if content is not None and content.winfo_exists() and content.winfo_manager() == "grid":
                content.grid_remove()
                self._hidden_content = content
        except Exception:
            self._hidden_content = None

    def show_after_rescale(self, window) -> None:
        if window is not self.window:
            return
        content, self._hidden_content = self._hidden_content, None
        try:
            if content is not None and content.winfo_exists():
                content.grid()  # restores the position and stretch it had before grid_remove()
        except Exception:
            pass

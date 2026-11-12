"""Guards against a CustomTkinter fault that produced hundreds of crashes in one evening.

CustomTkinter asks Windows for the display scale (DPI) of the monitor a window is on, and
polls it every half second. When Windows can't answer -- the screen is off or locked, a monitor
is being switched, a remote session changes -- GetDpiForMonitor quietly returns 0 instead of
failing, and CustomTkinter turns that into a window scale of 0. From then on EVERY window resize
or redraw event divides by that zero ("ZeroDivisionError: float division by zero" from
ctk_tk._update_dimensions_event), until Windows answers properly again. The app kept running,
but each event was recorded as a crash (200 in two hours) and drawing could go wrong.

Two layers, both installed once at start-up before any window exists:
  1. A scale of 0 (or nonsense) is never accepted: the tracker falls back to the last scale
     Windows reported properly (or 100%), so the fault never reaches a widget.
  2. If a zero scale somehow got through anyway, converting a size back from it returns the
     size unchanged instead of dividing by zero.
"""
from __future__ import annotations

import math

_installed = False


def install_scaling_guard() -> None:
    global _installed
    if _installed:
        return
    _installed = True
    try:
        from customtkinter.windows.widgets.scaling.scaling_base_class import CTkScalingBaseClass
        from customtkinter.windows.widgets.scaling.scaling_tracker import ScalingTracker
    except Exception:
        return  # a different CustomTkinter layout: nothing to guard, and never block start-up for it

    original = ScalingTracker.get_window_dpi_scaling.__func__
    last_good = {"value": 1.0}

    def safe_get_window_dpi_scaling(cls, window):
        try:
            value = original(cls, window)
        except Exception:
            value = None
        if isinstance(value, (int, float)) and math.isfinite(value) and 0.25 <= value <= 16:
            last_good["value"] = float(value)
            return value
        return last_good["value"]  # Windows couldn't say right now -- keep what it said last

    ScalingTracker.get_window_dpi_scaling = classmethod(safe_get_window_dpi_scaling)

    for name in ("_reverse_window_scaling", "_reverse_widget_scaling"):
        original_reverse = getattr(CTkScalingBaseClass, name, None)
        if original_reverse is None:
            continue

        def make(original_reverse):
            def reverse(self, scaled_value):
                try:
                    return original_reverse(self, scaled_value)
                except ZeroDivisionError:
                    return int(scaled_value)
            return reverse

        setattr(CTkScalingBaseClass, name, make(original_reverse))

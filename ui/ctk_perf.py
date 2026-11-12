"""Makes CustomTkinter fast enough to resize and to move between monitors without freezing or going blank.

Measured on this app (about 1,900 widgets that each rescale, ~5,000 Tk windows):
  * Dragging a window onto a monitor with a different display scale took 7-8 SECONDS. CustomTkinter rescales every widget --
    including the four tabs you are not looking at -- and, on Windows, sets the whole window to 15% opacity for as long as
    that takes (that is the "going blank").
  * CTkScrollbar (and CTkOptionMenu) end every redraw with update_idletasks(), which forces Tk to run ALL pending layout and
    painting for the entire window, synchronously, once per scrollbar update -- many times for a single resize step.

Three changes, all installed once at start-up (like ui/ctk_guard.py), each falling back to stock behaviour if CustomTkinter
looks different from the version this was written against (6.0):

  1. The forced update_idletasks() is removed from scrollbars and option menus. Tk still does the work, once, in its normal
     idle pass, instead of re-entrantly on every redraw.
  2. LAZY RESCALING. A tab that is not visible is not rescaled when the monitor changes; it is rescaled the moment it is
     shown. The Developer Mode tags (hidden unless Developer Mode is on) are rescaled when they are first shown.
     Everything on screen is rescaled immediately, exactly as before. (Measured: treating every off-screen widget this way
     was no faster and made the hide-while-rescaling step slower, so it is deliberately not done.)
  3. The 15%-opacity fade is gone. While the visible content rescales it is hidden for an instant instead (a hook the app
     registers -- see ui/resize_governor.py), and appears once, at the right size.
"""
from __future__ import annotations

import sys
from typing import Callable, Optional

_installed = False

# widgets (tab frames) whose contents are rescaled only when they become visible: container -> is_active()
_containers: dict = {}
# callbacks that were skipped: container -> {callback id -> callback}
_pending: dict = {}
# individual widgets skipped because they are not on screen yet: widget -> callback
_pending_single: dict = {}
_before_rescale: list = []
_after_rescale: list = []


def register_lazy_container(container, is_active: Callable[[], bool]) -> None:
    """From now on, widgets inside `container` are only rescaled for a monitor change while is_active() is True; the rest
    wait until activate(container) is called (when the container is shown)."""
    _containers[container] = is_active


def add_rescale_hooks(before: Optional[Callable] = None, after: Optional[Callable] = None) -> None:
    """Called around the rescale of a window after a monitor change: before(window) / after(window)."""
    if before is not None:
        _before_rescale.append(before)
    if after is not None:
        _after_rescale.append(after)


def _scales(tracker, window):
    if not tracker.deactivate_automatic_dpi_awareness:
        return (tracker.window_dpi_scaling_dict[window] * tracker.widget_scaling,
                tracker.window_dpi_scaling_dict[window] * tracker.window_scaling)
    return tracker.widget_scaling, tracker.window_scaling


def _container_of(widget):
    """The registered lazy container this widget lives in, or None."""
    w = widget
    for _ in range(64):  # a widget tree is never this deep; the bound only guards against a cycle
        if w is None:
            return None
        if w in _containers:
            return w
        w = getattr(w, "master", None)
    return None


def activate(container) -> int:
    """Rescales everything that was skipped inside `container` while it was hidden (call when it is shown). Returns how many."""
    waiting = _pending.pop(container, None)
    if not waiting:
        return 0
    from customtkinter.windows.widgets.scaling.scaling_tracker import ScalingTracker
    try:
        window = ScalingTracker.get_window_root_of_widget(container)
        widget_scale, window_scale = _scales(ScalingTracker, window)
    except Exception:
        return 0
    done = 0
    for callback in list(waiting.values()):
        try:
            callback(widget_scale, window_scale)
            done += 1
        except Exception:
            pass  # the widget was destroyed while it waited
    return done


def apply_pending_single(widget) -> bool:
    """For a widget that was skipped because it was not on screen: rescale it now (call just before showing it)."""
    callback = _pending_single.pop(widget, None)
    if callback is None:
        return False
    from customtkinter.windows.widgets.scaling.scaling_tracker import ScalingTracker
    try:
        window = ScalingTracker.get_window_root_of_widget(widget)
        callback(*_scales(ScalingTracker, window))
        return True
    except Exception:
        return False


def pending_count() -> int:
    return sum(len(v) for v in _pending.values()) + len(_pending_single)


def install() -> None:
    global _installed
    if _installed:
        return
    _installed = True
    try:
        from customtkinter.windows.widgets.ctk_scrollbar import CTkScrollbar
        from customtkinter.windows.widgets.ctk_optionmenu import CTkOptionMenu
        from customtkinter.windows.widgets.scaling.scaling_tracker import ScalingTracker
    except Exception:
        return  # a different CustomTkinter layout: leave it exactly as it is

    # ---- 1. no forced whole-window flush from a scrollbar / option menu redraw
    for cls in (CTkScrollbar, CTkOptionMenu):
        original_init = cls.__init__

        def make(original_init):
            def init(self, *args, **kwargs):
                original_init(self, *args, **kwargs)
                try:
                    self._canvas.update_idletasks = lambda: None
                except Exception:
                    pass
            return init
        cls.__init__ = make(original_init)

    # ---- 2. lazy rescaling
    needed = ("window_widgets_dict", "window_dpi_scaling_dict", "update_scaling_callbacks_for_window", "check_dpi_scaling", "get_window_dpi_scaling")
    if not all(hasattr(ScalingTracker, n) for n in needed):
        return

    def lazy_update_for_window(cls, window):
        widget_scale, window_scale = _scales(cls, window)
        for callback in list(cls.window_widgets_dict.get(window, [])):
            owner = getattr(callback, "__self__", None)
            if owner is not None and _containers:
                container = _container_of(owner)
                if container is not None and not _containers[container]():
                    _pending.setdefault(container, {})[id(callback)] = callback
                    continue
            if owner is not None and getattr(owner, "_ctk_perf_defer_until_shown", False):
                try:
                    shown = bool(owner.winfo_manager())
                except Exception:
                    shown = True
                if not shown:
                    _pending_single[owner] = callback
                    continue
            try:
                callback(widget_scale, window_scale)
            except Exception:
                pass  # one widget that cannot rescale (destroyed mid-way) must not stop the rest

    ScalingTracker.update_scaling_callbacks_for_window = classmethod(lazy_update_for_window)

    # ---- 3. the monitor-change check, without the 15% opacity fade
    def check_dpi_scaling(cls):
        new_scaling_detected = False
        for window in list(cls.window_widgets_dict):
            try:
                if not (window.winfo_exists() and not window.state() == "iconic"):
                    continue
                current = cls.get_window_dpi_scaling(window)
                if current == cls.window_dpi_scaling_dict[window]:
                    continue
                cls.window_dpi_scaling_dict[window] = current
                for hook in _before_rescale:
                    try:
                        hook(window)
                    except Exception:
                        pass
                try:
                    window.block_update_dimensions_event()
                    try:
                        cls.update_scaling_callbacks_for_window(window)
                    finally:
                        window.unblock_update_dimensions_event()
                finally:
                    for hook in _after_rescale:
                        try:
                            hook(window)
                        except Exception:
                            pass
                new_scaling_detected = True
            except Exception:
                continue  # a window that is going away

        for app in list(cls.window_widgets_dict.keys()):
            try:
                if new_scaling_detected:
                    app.after(cls.loop_pause_after_new_scaling, cls.check_dpi_scaling)
                else:
                    app.after(cls.update_loop_interval, cls.check_dpi_scaling)
                return
            except Exception:
                continue
        cls.update_loop_running = False

    ScalingTracker.check_dpi_scaling = classmethod(check_dpi_scaling)

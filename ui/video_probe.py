"""Reads a rough duration and a preview frame directly from a local video file, before it's
uploaded -- entirely local (cv2), no network call and nothing sent to YouTube. The frame is
read at native aspect ratio (Shorts are vertical, 9:16), so callers should pass a portrait
`size` rather than the landscape boxes used for YouTube's own thumbnails elsewhere in the
app.

The cv2 decode runs on a worker thread, but the CTkImage itself is built on the Tk main
thread inside the widget.after(...) callback, not in the worker -- Tk/Tcl's image registry
isn't thread-safe, and constructing a CTkImage off-thread can silently corrupt it (surfacing
much later as a "pyimage doesn't exist" TclError, especially on a widget whose image gets
reassigned more than once, like a single reused thumbnail preview rather than a fresh label
per item)."""
from __future__ import annotations

import threading
from typing import Callable, Optional

import cv2
import customtkinter as ctk
from PIL import Image

from data import diagnostics
from ui.threading_utils import safe_after, start_worker


def probe_async(
    widget, file_path: str, size: tuple[int, int],
    on_ready: Callable[[int, Optional[ctk.CTkImage]], None],
) -> None:
    """on_ready(duration_seconds, thumbnail) is always called on the Tk main thread.
    duration_seconds is 0 and thumbnail is None if the file can't be read (unsupported
    codec, corrupt file, etc.) -- this is only a preview, so callers should degrade
    quietly rather than treat a failed probe as an error."""

    def worker():
        duration_seconds = 0
        frame_image = None
        try:
            cap = cv2.VideoCapture(file_path)
            try:
                if cap.isOpened():
                    fps = cap.get(cv2.CAP_PROP_FPS) or 0.0
                    frame_count = cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0.0
                    if fps > 0:
                        duration_seconds = round(frame_count / fps)
                    if frame_count > 1:
                        cap.set(cv2.CAP_PROP_POS_FRAMES, int(frame_count * 0.1))
                    ok, frame = cap.read()
                    if ok:
                        frame_image = Image.fromarray(frame[:, :, ::-1])  # BGR -> RGB
                        del frame  # the full-resolution decode (~6 MB for a 1080x1920 Short) is not needed past this point
                        # Shrink to twice the display size right here on the worker: a CTkImage
                        # keeps the FULL image it is given, so handing it the whole frame kept
                        # ~6 MB alive per video ever selected.
                        frame_image.thumbnail((size[0] * 2, size[1] * 2))
                else:
                    diagnostics.report_recovered(
                        "Reading a video preview", RuntimeError("the video could not be opened for a preview (unsupported codec or damaged file)"),
                        page="Upload", operation=f"Previewing {file_path.replace(chr(92), '/').rsplit('/', 1)[-1]}",
                    )
            finally:
                cap.release()
        except Exception as e:
            diagnostics.report_recovered("Reading a video preview", e, page="Upload")
        safe_after(widget, lambda: _deliver(widget, duration_seconds, frame_image, size, on_ready))

    start_worker(worker, name="Video preview", page="Upload")


def _deliver(
    widget, duration_seconds: int, frame_image, size: tuple[int, int],
    on_ready: Callable[[int, Optional[ctk.CTkImage]], None],
) -> None:
    """Runs on the Tk main thread -- the only safe place to construct a CTkImage."""
    thumbnail = None
    if frame_image is not None and widget.winfo_exists():
        try:
            thumbnail = ctk.CTkImage(light_image=frame_image, dark_image=frame_image, size=size)
        except Exception:
            thumbnail = None
    on_ready(duration_seconds, thumbnail)


def format_duration(seconds: int) -> str:
    if seconds <= 0:
        return ""
    h, rem = divmod(int(seconds), 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"

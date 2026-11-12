"""Minimal async thumbnail loader shared by any page that shows video thumbnails
(currently Push and Calendar). In-memory only, keyed by (url, size) -- no disk cache, so
a restart re-downloads, but that's a fine trade for how few thumbnails either page shows
at once.
"""
from __future__ import annotations

import io
import threading
from typing import Callable

import customtkinter as ctk
import requests
from PIL import Image

from data import diagnostics
from ui.threading_utils import safe_after, start_worker

MAX_CACHED_THUMBNAILS = 300  # bounded: an unbounded cache grows for as long as the app stays open
_memory_cache: dict[tuple, ctk.CTkImage] = {}

# YouTube has no thumbnail yet for a video that is scheduled but not published (it answers
# 404/403/410), which is the normal state of most of this app's videos -- so those aren't
# problems and aren't logged. They are remembered for the session so the page isn't
# re-requested on every redraw.
_NO_THUMBNAIL_YET = (403, 404, 410)
_unavailable: set[str] = set()


def get_async(widget, url: str, size: tuple[int, int], on_ready: Callable[[ctk.CTkImage], None]) -> None:
    """Fetches (or reuses from cache) a thumbnail as a CTkImage. on_ready is always
    called on the Tk main thread via widget.after(...) -- callers don't need to marshal
    it themselves. Silently does nothing on a failed fetch or an empty url."""
    if not url:
        return
    key = (url, size)
    cached = _memory_cache.get(key)
    if cached is not None:
        on_ready(cached)
        return
    if url in _unavailable:
        return

    def worker():
        try:
            resp = requests.get(url, timeout=10)
            resp.raise_for_status()
            image = Image.open(io.BytesIO(resp.content)).convert("RGB")
        except Exception as e:
            # A thumbnail that can't be fetched just doesn't show. A genuine problem (a
            # timeout, a server error) is logged; the message deliberately leaves the URL
            # out so the same problem on different thumbnails folds into ONE entry with a
            # count instead of one entry per video.
            status = getattr(getattr(e, "response", None), "status_code", None)
            _unavailable.add(url)
            if status not in _NO_THUMBNAIL_YET:
                reason = f"HTTP {status}" if status else type(e).__name__
                diagnostics.report_recovered(
                    f"Downloading a video thumbnail ({reason})", RuntimeError(f"the thumbnail server returned {reason}" if status else f"{type(e).__name__} while downloading"),
                    page="Thumbnails",
                )
            return

        def deliver() -> None:
            # Built on the MAIN thread: Tk image objects must not be created (or, see
            # ui/threading_utils.py, finalised) off it.
            ctk_image = ctk.CTkImage(light_image=image, dark_image=image, size=size)
            _memory_cache[key] = ctk_image
            while len(_memory_cache) > MAX_CACHED_THUMBNAILS:
                _memory_cache.pop(next(iter(_memory_cache)))
            on_ready(ctk_image)

        safe_after(widget, deliver)

    start_worker(worker, name="Thumbnail download", page="Thumbnails")

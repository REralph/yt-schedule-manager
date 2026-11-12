"""Shared state passed to every page: the repo/client and simple pub/sub so pages can
react when the video list or history changes."""
from __future__ import annotations

from typing import Callable, Optional

from data import diagnostics, session_state
from data.config import AppConfig
from data.crash_log import CrashLogStore
from data.error_log import ErrorLogStore, ErrorReport
from data.history import HistoryStore
from youtube.client import YouTubeClient
from youtube.video_repo import VideoRepository


class AppState:
    def __init__(self, config: AppConfig):
        self.config = config
        self.client: Optional[YouTubeClient] = None
        self.repo: Optional[VideoRepository] = None
        self.history = HistoryStore()
        self.error_log = ErrorLogStore()
        self.last_error: Optional[ErrorReport] = None
        self.crash_log = CrashLogStore.shared()  # the ONE process-wide store (see data/crash_log.py) -- main.py's handlers write to the same instance

        # A short breadcrumb of whatever the user last did, plus what upload (if any) is
        # currently in flight -- read by data/crash_log.py's build_crash_report() so a
        # crash report has some idea what was happening, without needing a raw dump of
        # this whole object (which is deliberately never serialized as-is).
        self.last_action: str = ""
        self.current_upload_filename: str = ""
        self.current_upload_attempt: int = 0

        # Lets any page switch the active tab (wired to CTkTabview.set by MainWindow).
        self.request_tab: Callable[[str], None] = lambda name: None

        # Lets any page jump straight to one of Settings' nested sub-tabs (YouTube /
        # Application / Developer Tools), wired to the nested CTkTabview.set by SettingsPage.
        self.request_settings_tab: Callable[[str], None] = lambda name: None

        # The currently-active top-level tab's display name (e.g. "Push"), kept in sync
        # by MainWindow's CTkTabview(command=...) -- lets Developer Tools' "Copy Current
        # Page Info" work out what to copy without the page itself needing to say so.
        self.current_tab: str = ""

        # Currently-selected component in Developer Mode's info panel (dev_id string).
        self.dev_selected_component: Optional[str] = None

        self._videos_listeners: list[Callable[[], None]] = []
        self._history_listeners: list[Callable[[], None]] = []
        self._dev_mode_listeners: list[Callable[[], None]] = []
        self._dev_selection_listeners: list[Callable[[], None]] = []

    # ---- current tab ----

    def set_current_tab(self, name: str) -> None:
        self.current_tab = name
        session_state.note(page=name)  # so an unexplained exit still says which page it was on

    def set_last_action(self, text: str) -> None:
        self.last_action = text
        session_state.note(operation=text)

    # ---- videos ----

    def notify_videos_changed(self) -> None:
        self._notify(self._videos_listeners)

    def on_videos_changed(self, fn: Callable[[], None]) -> None:
        self._videos_listeners.append(fn)

    # ---- history ----

    def notify_history_changed(self) -> None:
        self._notify(self._history_listeners)

    def on_history_changed(self, fn: Callable[[], None]) -> None:
        self._history_listeners.append(fn)

    def off_history_changed(self, fn: Callable[[], None]) -> None:
        try:
            self._history_listeners.remove(fn)
        except ValueError:
            pass

    # ---- developer mode ----
    # Purely informational -- never touches scheduling or data.

    def set_developer_mode(self, enabled: bool) -> None:
        self.config.developer_mode = enabled
        self.config.save()
        self._notify(self._dev_mode_listeners)

    def on_developer_mode_changed(self, fn: Callable[[], None]) -> None:
        self._dev_mode_listeners.append(fn)

    def off_developer_mode_changed(self, fn: Callable[[], None]) -> None:
        """Pairs with on_developer_mode_changed() -- DevTag calls this when it's
        destroyed so a page that rebuilds its contents (e.g. on every re-render) doesn't
        leave a growing pile of listeners pointing at widgets that no longer exist."""
        try:
            self._dev_mode_listeners.remove(fn)
        except ValueError:
            pass

    def set_dev_selected_component(self, dev_id: str) -> None:
        self.dev_selected_component = dev_id
        self._notify(self._dev_selection_listeners)

    def on_dev_selection_changed(self, fn: Callable[[], None]) -> None:
        self._dev_selection_listeners.append(fn)

    @staticmethod
    def _notify(listeners: list[Callable[[], None]]) -> None:
        """Calls every listener, isolating failures: one page's broken/stale listener (e.g.
        a callback for a widget that no longer exists) used to abort the loop, so the pages
        after it never updated -- and, since callers like the upload-complete handler notify
        mid-way through their own work, could abort THAT too, leaving an upload half
        recorded. A failing listener is now logged and the rest still run."""
        for fn in list(listeners):
            try:
                fn()
            except Exception as e:
                diagnostics.report_recovered(
                    f"A page's update listener ({getattr(fn, '__qualname__', repr(fn))})", e, page="UI update",
                )

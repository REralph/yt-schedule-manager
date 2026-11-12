"""ONE UPLOAD PAGE: the single page for adding a new video to the channel. Select a
folder or drag one video in, paste its title/description, and upload -- one video at a
time. There is deliberately only ever one active selection here, never a queue of several
simultaneous cards, and there is no manual date override -- scheduling is always automatic
(see scheduling/next_slot.py's module docstring: it continues from the schedule's furthest
occupied date, never a "first empty date" search).

FOLDER SELECTION: choosing a folder is validated end to end -- the dialog result is
checked, the save is checked, and the folder is scanned immediately so a real access
problem (permission denied, a disconnected drive) surfaces right away as an actual error
message rather than a folder that silently "selected" but shows nothing. The scan result
(data/shorts_folder.py's list_folder_videos()) is what populates the folder's video list,
sorted numerically by q-number (q9, q10, q11 -- never the alphabetical q10, q11, q9) and
tagged New/Uploaded from the registry. There is no background monitoring: the folder is
scanned when selected and again whenever MOST RECENT FILE is clicked, never on a timer.

MOST RECENT FILE (renamed from the old "Check New") re-scans the already-selected folder
and selects the single newest not-yet-uploaded video created or modified within the
configured detection window (Settings -> Application, default 60s) -- see
data/shorts_folder.py's find_newest_recent_video(). If nothing qualifies, nothing gets
selected and the panel says so plainly; it never falls back to an older file. The result is
also highlighted directly in the folder video list.

LAYOUT: four fully independent buttons in a fixed UPLOAD ACTIONS panel, positioned right
after the two detection boxes -- [ Quick Paste ] [ Most Recent File + Quick Paste ]
[ Upload ] [ Auto Paste & Enter ]. This panel never moves, resizes, or gets rebuilt, no
matter what happens elsewhere on the page. None of the four ever triggers another:
standalone Quick Paste (0.9.6.18) only reads the clipboard onto whatever's currently
selected, the combined button (0.9.6.19 + 0.9.6.18) finds/selects the newest eligible
video and then Quick Pastes onto it, Upload never re-runs either of those -- it just
uploads whatever is currently sitting in the Title/Description fields -- and Auto Paste &
Enter (0.9.6.31, ui/widgets/auto_paste_enter_button.py) ignores the selected video and
Title/Description state entirely, acting only on whatever text field currently has
keyboard focus. If the combined button finds nothing new but a video is already selected
(e.g. dragged in manually), it still pastes onto that.

DRAG VIDEO also auto-attempts a SILENT Quick Paste the moment a video is selected (drag or
"click to browse", same drop zone) -- see _set_file_from_drag()/_quick_paste(silent=True):
on any failure (nothing usable on the clipboard, or it doesn't parse) nothing is shown at
all, the video just stays selected with its Title/Description left as they were. Manual
Quick Paste (the standalone button, or the combined button) stays loud on failure, since
those are a deliberate click the user is watching the result of.
Everything that genuinely needs to grow or shrink -- the Selected Video block, the optional
Developer Diagnostics panel -- is packed with `before=self.history_wrap` so it always lands
between the action panel and History, and History always stays at the bottom.

DAILY CAPACITY: "How many videos do you want to upload per day?" (in the fixed UPLOAD
ACTIONS panel) is this page's OWN setting (AppConfig.upload_videos_per_day), deliberately
independent of AutoScheduleConfig.normal_videos_per_day -- see the "Separate Daily Upload
Setting from Auto Schedule" spec. Changing one never touches the other. The two settings
do share the same underlying slot-TIME presets (data/auto_schedule_config.py's
slot_presets) for what clock times a given count actually gets, via the same
ui/widgets/slot_selector.py SlotCountControl used on the Auto Schedule and Increase Daily
Slots pages -- only which COUNT is currently selected is kept separate. Read fresh from
AppConfig every time via _current_daily_rate(), never cached.

RECALIBRATION: selecting a video (via Most Recent File, the folder list, or dragging one
in) always triggers a real refresh of the schedule from YouTube before showing where it
would land -- see _recalibrate_and_preview(). The preview can therefore never drift from a
stale local video_cache.json; YouTube is always the source of truth for "what's the
furthest scheduled date" by the time a preview is actually shown. The lighter
_update_schedule_preview(), called far more often, only re-reads whatever's already cached
locally -- it does not hit the network on every call.

There is no "Upload complete" popup -- every upload is recorded into data/upload_activity.py
from the moment it starts and shown in the HISTORY -- TODAY section at the bottom of the
page: one compact row per upload (FILE | TITLE | DATE | TIME | HOW LONG AGO | STATUS, real
columns, never wrapping to a second line -- see _render_history()), nothing else. HOW LONG
AGO re-renders every 30s on its own (_schedule_history_ago_refresh()) so it stays current
without reopening the page. A failed upload's row
shows the error where the title would go and can be clicked to copy it.

PERMANENT RECORD + REFRESH: every upload stays in the History for good (HISTORY has a Today / All
switch -- All lists the whole record, newest first, 40 at a time). The Refresh button on the right
of the History header re-checks the whole record against YouTube (youtube/record_checker.py): each
video must still be on the channel, its recorded scheduled time is brought in step with the real
one (so edits made in YouTube Studio, or by Increase Daily Slots / Auto Schedule, show up), and
anything that has vanished is marked NOT ON YOUTUBE with a Re-check button. An upload that "failed"
but actually reached YouTube is found and recovered the same way, never re-uploaded.

UPLOAD VERIFICATION (HIST-001): the upload call returning is NOT treated as success. The
entry goes UPLOADING -> (request complete, video ID recorded) -> VERIFYING -> VERIFIED or
VERIFICATION FAILED: after a short wait, YouTube itself is asked whether the video really
exists on this channel with the expected title (see _start_verification() and
youtube/upload_verifier.py), retried a limited number of times while YouTube registers it.
The STATUS column therefore shows whether each upload was actually CONFIRMED -- DATE/TIME
(when it's scheduled to publish) and HOW LONG AGO (when it was uploaded) stay separate
facts. Every stage is persisted, so an app restart resumes anything still VERIFYING
(_resume_pending_verifications()). Verification and RETRY VERIFICATION only ever ask YouTube
-- they never upload -- and before any upload attempt is repeated, YouTube is checked for
the video first, so an apparent failure whose upload really went through is adopted rather
than duplicated.

DEVELOPER DIAGNOSTICS: when Developer Mode is on, a panel shows the folder path/state,
scan results, detection-window value, the currently selected video, the calculated next
schedule position, and the last upload error (with Copy Error) -- see
_build_diagnostics_panel()/_update_diagnostics(). Nothing here is faked: every field
reflects what the last real scan/selection/calculation actually produced.

UPLOAD QUALITY PROTECTION: the upload path never re-encodes, compresses, resizes, or
otherwise modifies the selected file -- _quick_upload()'s worker hands the exact
selected.file_path straight to YouTubeClient.upload_video(), which uploads those bytes
as-is (see youtube/client.py's own docstring); a failed attempt's automatic retry
re-uploads that SAME original path, never a converted fallback copy. A small "QUALITY
INFORMATION" section (0.9.6.30 / QUALITY-001, available for the current selection, not
Developer Mode-only, COLLAPSED BY DEFAULT behind a compact header with an expand arrow --
the last choice is remembered via AppConfig.quality_info_expanded) shows the selected
file's real filename/size/resolution/fps/codecs/bitrates --
read straight off the file via a bundled read-only ffprobe, see youtube/video_tech_info.py
and _probe_tech_info()/_render_tech_info() -- plus a standing note that accepting the
upload isn't the same as YouTube finishing its own background processing. After a
successful upload, _verify_upload_quality() does a best-effort independent check (the
Data API's fileDetails part, i.e. what YouTube itself recorded receiving) against that
same pre-upload reading and surfaces the result in Developer Diagnostics only -- never a
popup, and never something upload success depends on."""
from __future__ import annotations

import datetime as dt
import gc
import os
import threading
import time
import tkinter.font as tkfont
from dataclasses import dataclass
from tkinter import filedialog, messagebox
from typing import Callable, Optional

import customtkinter as ctk
from tkinterdnd2 import DND_FILES

from data import diagnostics
from data.auto_schedule_config import MAX_SLOT_COUNT, MIN_SLOT_COUNT, AutoScheduleConfig
from data.queue_order import QueueOrderStore
from data.shorts_folder import (
    CheckNewResult,
    FolderVideoEntry,
    ShortsFolderRegistry,
    list_folder_videos,
    pick_newest_recent,
)
from data.upload_activity import (
    STATUS_DONE,
    STATUS_FAILED,
    STATUS_MISSING,
    STATUS_UPLOAD_COMPLETE,
    STATUS_UPLOADING,
    STATUS_VERIFICATION_FAILED,
    STATUS_VERIFIED,
    STATUS_VERIFYING,
    UploadActivityEntry,
    UploadActivityLog,
)
from scheduling.next_slot import find_upload_slots_at_rate
from ui.app_state import AppState
from ui.dev_reporting import report_error
from ui.threading_utils import safe_after, start_worker
from ui.upload_text_parser import MAX_DESCRIPTION_LEN, MAX_TITLE_LEN, parse_title_description
from ui.video_probe import format_duration, probe_async
from ui.widgets.auto_paste_enter_button import AutoPasteEnterButton
from ui.widgets.dev_tag import DevTag
from ui.widgets.slot_selector import SlotCountControl
import youtube.record_checker as record_checker
import youtube.upload_verifier as upload_verifier
from youtube.client import Playlist
from youtube.errors import classify_exception
from youtube.models import strip_hashtags
from youtube.video_tech_info import (
    VideoTechInfo,
    compare as compare_tech_info,
    format_bitrate,
    format_size,
    from_youtube_file_details,
    probe_local_file,
)

VIDEO_EXTENSIONS = {".mp4", ".mov", ".m4v", ".avi", ".mkv", ".webm"}
THUMB_SIZE = (64, 114)  # portrait -- Shorts are 9:16, unlike YouTube's own 16:9 thumbnail images

KIDS_NO = "No, it's not made for kids"
KIDS_YES = "Yes, it's made for kids"
NO_PLAYLIST = "(No playlist)"

GOOD = "#7fd17f"
WARN = "#e0a04a"
BAD = "#e05a5a"
SCHEDULED_COLOR = "#a8e6b0"

# HISTORY -- TODAY column layout (HIST-001): one compact row per upload, FILE | TITLE |
# DATE | TIME | HOW LONG AGO, never wrapping to a second line -- see _render_history().
HISTORY_FILE_COL_PX = 90
HISTORY_CODE_COL_PX = 58  # the order code, e.g. "0232"
HISTORY_DATE_COL_PX = 90
HISTORY_TIME_COL_PX = 55
HISTORY_AGO_COL_PX = 110
CODE_COLOR = "#5b9bd5"
HISTORY_STATUS_COL_PX = 160  # wide enough for "VERIFICATION FAILED" in bold
HISTORY_RETRY_BTN_PX = 104  # "Retry Verification" -- on a VERIFICATION FAILED row
HISTORY_COPY_BTN_PX = 76  # "Copy Report" -- on every row, Developer Mode only
HISTORY_TITLE_MAX_PX = 360 + HISTORY_CODE_COL_PX + 8  # (the 360 the title always had, plus the CODE column added in front of it) a representative budget for the TITLE column's available
# space at a typical window width -- truncated once at render time rather than
# re-measured on every resize, so it stays simple; the important guarantee (never wraps
# to a second line) holds regardless of the exact window width.
HISTORY_AGO_REFRESH_MS = 30000  # keeps "How Long Ago" current without a page refresh -- cheap, local-only re-render, no I/O

# UPLOAD RELIABILITY: an upload that fails is retried once, automatically, after a short
# delay -- see _quick_upload(). Not user-configurable; this is a fixed reliability policy,
# not a setting.
UPLOAD_MAX_ATTEMPTS = 2
UPLOAD_RETRY_DELAY_SECONDS = 3

MAX_LEGACY_VERIFICATIONS = 12  # how many of today's pre-verification ("legacy") history entries a single start-up will look up on YouTube
RESUME_VERIFICATION_DELAY_MS = 1500  # after start-up, once the page is on screen, pick up any upload a previous session left unverified

# HIST-001 STATUS column colours -- see data/upload_activity.py's STATUS_LABELS for the text.
HISTORY_PAGE_SIZE = 40  # rows shown in the "All" view before "Show more"
CLICK_SETTLE_MS = 130  # a CTkButton schedules its click animation 100ms after a click

STATUS_COLORS = {
    STATUS_MISSING: BAD,
    STATUS_UPLOADING: "#5b9bd5",
    STATUS_UPLOAD_COMPLETE: WARN,
    STATUS_VERIFYING: WARN,
    STATUS_VERIFIED: GOOD,
    STATUS_VERIFICATION_FAILED: BAD,
    STATUS_FAILED: BAD,
    STATUS_DONE: "#888888",
}

# UPLOAD QUALITY PROTECTION: after a successful upload, poll YouTube's fileDetails a few
# times before giving up -- it isn't always available the instant an upload finishes. Not
# user-configurable; purely a best-effort verification, never something success depends on.
UPLOAD_VERIFY_MAX_ATTEMPTS = 6
UPLOAD_VERIFY_DELAY_SECONDS = 5

_history_row_font: Optional[tkfont.Font] = None


def _get_history_row_font() -> tkfont.Font:
    """A real tkinter.font.Font for measuring text width in pixels (Tk fonts can only be
    constructed once a Tk root exists, hence the lazy singleton). Used to truncate the
    TITLE/error text to fit its column with "..." instead of letting the row wrap."""
    global _history_row_font
    if _history_row_font is None:
        _history_row_font = tkfont.Font(size=12)
    return _history_row_font


def _truncate_to_width(text: str, font: tkfont.Font, max_width_px: int) -> str:
    if font.measure(text) <= max_width_px:
        return text
    lo, hi = 0, len(text)
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if font.measure(text[:mid] + "...") <= max_width_px:
            lo = mid
        else:
            hi = mid - 1
    return (text[:lo] + "...") if lo > 0 else "..."


def _format_window(seconds: int) -> str:
    if seconds % 60 == 0 and seconds >= 60:
        minutes = seconds // 60
        return f"{minutes} minute{'s' if minutes != 1 else ''}"
    return f"{seconds} seconds"


def _format_time_ago(when: dt.datetime) -> str:
    """HOW LONG AGO (HIST-001): raw elapsed time, not calendar-day boundaries -- "3 hours
    ago" always means 3 hours ago, it never flips to "Yesterday" just because a midnight
    happened to pass in between (that would make the same entry's label jump around
    unpredictably depending on what time of day it's viewed). Only switches to
    "Yesterday"/"N days ago" once a full 24/48/... hours have actually elapsed. In
    practice HIST-001 only ever shows today's entries anyway (see
    data/upload_activity.py's today_entries()), so the day-based branches are here for
    correctness/reuse rather than something this page's own list will often reach."""
    seconds = max(0, int((dt.datetime.now(dt.timezone.utc) - when.astimezone(dt.timezone.utc)).total_seconds()))
    if seconds < 60:
        return "just now"
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes} minute{'s' if minutes != 1 else ''} ago"
    hours = minutes // 60
    if hours < 24:
        return f"{hours} hour{'s' if hours != 1 else ''} ago"
    days = hours // 24
    if days == 1:
        return "Yesterday"
    return f"{days} day{'s' if days != 1 else ''} ago"


@dataclass
class SelectedVideo:
    file_path: str
    title: str = ""
    description: str = ""
    made_for_kids: bool = False
    playlist_name: str = ""
    status: str = "pending"  # pending | uploading | done | failed
    error: str = ""
    q_number: str = ""  # set only when this came from the watched Shorts folder

    @property
    def ready(self) -> bool:
        return bool(self.title.strip()) and bool(self.description.strip())


class BasicUploadPanel(ctk.CTkFrame):
    def __init__(self, master, state: AppState, **kwargs):
        super().__init__(master, fg_color="#1a1a1a", corner_radius=8, **kwargs)
        self.state = state
        self._selected: Optional[SelectedVideo] = None
        self._playlists: list[Playlist] = []
        self._registry = ShortsFolderRegistry.load()
        self._activity_log = UploadActivityLog.shared(reload=True)  # the ONE shared record (see data/upload_activity.py) -- the reschedule pages update it too
        self._history_view = "Today"
        self._ago_labels: list = []  # (label, uploaded_at) for the rows on screen -- see _tick_history_ago()
        self._history_limit = HISTORY_PAGE_SIZE
        self._record_check_running = False
        self._last_record_check = ""
        self._uploading = False
        self._folder_videos: list[FolderVideoEntry] = []
        self._folder_scan_error: str = ""
        self._most_recent_result: Optional[CheckNewResult] = None
        self._last_upload_error: str = ""
        self._last_upload_attempts: int = 0
        self._tech_info: Optional[VideoTechInfo] = None
        self._last_quality_check: str = ""
        self._active_upload_entry_id: str = ""  # the HIST-001 entry whose upload is running right now, if any -- a restart-resume must never mistake it for an interrupted one
        self._history_fonts: dict[tuple[int, str], ctk.CTkFont] = {}
        self._resume_queue: list[UploadActivityEntry] = []  # start-up verifications run ONE AT A TIME (see _resume_pending_verifications)
        self._verifying_entry_ids: set[str] = set()  # HIST-001 entries with a verification run in flight right now -- so a resume and a manual RETRY VERIFICATION can never run the same entry twice at once
        # CTkLabel.configure(image=None) doesn't clear the underlying tkinter.Label's own
        # image option -- once the old CTkImage is garbage collected (thumb_label is a
        # single reused label, not a fresh one per selection), that dangling reference
        # surfaces as "TclError: image ... doesn't exist" on the label's next redraw. Every
        # CTkImage this panel ever creates is kept alive here for its whole lifetime so that
        # can't happen.
        self._thumb_keepalive: list = []

        header = ctk.CTkFrame(self, fg_color="transparent")
        header.pack(fill="x", padx=12, pady=(10, 4))
        ctk.CTkLabel(header, text="ONE UPLOAD PAGE", font=ctk.CTkFont(size=14, weight="bold")).pack(side="left")
        DevTag(header, state, "0.9.6").show(side="left", padx=(6, 0))
        ctk.CTkLabel(
            header, text="Select a folder or drag one video, paste its details, and upload -- one at a time",
            text_color="#888888", font=ctk.CTkFont(size=11),
        ).pack(side="left", padx=(12, 0))

        # ---- top row: NEW VIDEO DETECTION (left) / DRAG VIDEO (right) ----
        top_row = ctk.CTkFrame(self, fg_color="transparent")
        top_row.pack(fill="x", padx=12, pady=(0, 8))
        top_row.grid_columnconfigure(0, weight=1, uniform="top")
        top_row.grid_columnconfigure(1, weight=1, uniform="top")

        detect_card = ctk.CTkFrame(top_row, fg_color="#141414", corner_radius=8, height=300)
        detect_card.grid(row=0, column=0, sticky="nsew", padx=(0, 6))
        detect_card.pack_propagate(False)  # folder status / list content changing must never resize this card or shift the action panel below it
        detect_inner = ctk.CTkFrame(detect_card, fg_color="transparent")
        detect_inner.pack(fill="both", expand=True, padx=10, pady=10)
        ctk.CTkLabel(detect_inner, text="NEW VIDEO DETECTION", font=ctk.CTkFont(size=12, weight="bold")).pack(anchor="w")

        select_row = ctk.CTkFrame(detect_inner, fg_color="transparent")
        select_row.pack(fill="x", pady=(8, 2))
        self.folder_select_btn = ctk.CTkButton(select_row, text="Select Folder", height=26, command=self._choose_folder)
        self.folder_select_btn.pack(side="left")
        DevTag(select_row, state, "0.9.6.12").show(side="left", padx=(6, 0))

        self.folder_status_label = ctk.CTkLabel(
            detect_inner, text="No folder selected", font=ctk.CTkFont(size=11), text_color="#888888", anchor="w",
        )
        self.folder_status_label.pack(fill="x", pady=(0, 6))
        DevTag(detect_inner, state, "0.9.6.11").show(anchor="w")

        self.check_result_label = ctk.CTkLabel(
            detect_inner, text="Select a folder, then use Most Recent File to look for a freshly-saved video.",
            font=ctk.CTkFont(size=10), justify="left", anchor="w", text_color="#888888", wraplength=280,
        )
        self.check_result_label.pack(fill="x", pady=(0, 6))
        DevTag(detect_inner, state, "0.9.6.20").show(anchor="w")

        self.folder_video_list = ctk.CTkScrollableFrame(detect_inner, fg_color="transparent", height=140)
        self.folder_video_list.pack(fill="both", expand=True)
        DevTag(detect_inner, state, "0.9.6.24").show(anchor="w")

        drop_card = ctk.CTkFrame(top_row, fg_color="#141414", corner_radius=8)
        drop_card.grid(row=0, column=1, sticky="nsew", padx=(6, 0))
        ctk.CTkLabel(drop_card, text="DRAG VIDEO", font=ctk.CTkFont(size=12, weight="bold")).pack(anchor="w", padx=10, pady=(10, 4))
        self.drop_zone = ctk.CTkFrame(
            drop_card, fg_color="#0f0f0f", corner_radius=6, border_width=2, border_color="#3a3a3a", height=254,
        )
        self.drop_zone.pack(fill="both", expand=True, padx=10, pady=(0, 10))
        self.drop_zone.pack_propagate(False)
        self.drop_zone.bind("<Button-1>", lambda e: self._browse())
        self.drop_zone.drop_target_register(DND_FILES)
        self.drop_zone.dnd_bind("<<Drop>>", self._on_drop)
        DevTag(drop_card, state, "0.9.6.1").show(anchor="w", padx=(10, 0), pady=(0, 4))
        drop_inner = ctk.CTkFrame(self.drop_zone, fg_color="transparent")
        drop_inner.pack(expand=True)
        drop_lbl1 = ctk.CTkLabel(drop_inner, text="DRAG VIDEO FILE HERE", font=ctk.CTkFont(size=15, weight="bold"), text_color="#888888")
        drop_lbl1.pack()
        drop_lbl2 = ctk.CTkLabel(drop_inner, text="or click to browse", font=ctk.CTkFont(size=11), text_color="#666666")
        drop_lbl2.pack()
        for w in (drop_inner, drop_lbl1, drop_lbl2):
            w.bind("<Button-1>", lambda e: self._browse())

        # ---- fixed UPLOAD ACTIONS panel: three fully independent buttons --
        # [ Quick Paste ] [ Most Recent File + Quick Paste ] [ Upload ]. This card is
        # packed once, right after the two detection boxes, and never toggled or resized
        # -- everything that changes size lives below it, so the buttons never move
        # underneath the user's cursor while working through several videos quickly.
        # None of the three ever triggers another: Quick Paste never finds a video or
        # uploads; the combined button never uploads; Upload never re-runs either of the
        # other two -- see _quick_paste()/_most_recent_file_and_paste()/_quick_upload(). ----
        action_card = ctk.CTkFrame(self, fg_color="#141414", corner_radius=8)
        action_card.pack(fill="x", padx=12, pady=(0, 10))
        ctk.CTkLabel(action_card, text="UPLOAD ACTIONS", font=ctk.CTkFont(size=12, weight="bold")).pack(pady=(10, 6))

        # ---- "How many videos do you want to upload per day?" -- ONE UPLOAD PAGE'S OWN
        # daily capacity, always visible here regardless of whether a video is currently
        # selected. Deliberately independent of AutoScheduleConfig.normal_videos_per_day
        # (see the "Separate Daily Upload Setting from Auto Schedule" spec) -- this control
        # only ever writes AppConfig.upload_videos_per_day, never Auto Schedule's own
        # setting. Reuses the same shared SlotCountControl as Auto Schedule Settings and
        # Increase Daily Slots for a consistent compact-selector-plus-gear-icon look; the
        # slot-TIME presets it can view/edit through the gear are the one thing genuinely
        # shared between all three pages -- the selected COUNT itself never is. ----
        rate_row = ctk.CTkFrame(action_card, fg_color="transparent")
        rate_row.pack(pady=(0, 10))
        ctk.CTkLabel(
            rate_row, text="HOW MANY VIDEOS DO YOU WANT TO UPLOAD PER DAY?",
            font=ctk.CTkFont(size=11, weight="bold"), text_color="#a0a0a0",
        ).pack(side="left", padx=(0, 10))
        self.daily_rate_control = SlotCountControl(
            rate_row, state, AutoScheduleConfig.load(), initial_count=self._current_daily_rate(),
            on_count_changed=self._on_daily_rate_changed,
            selector_dev_id="0.9.6.27", gear_dev_id="0.9.6.28", panel_dev_id="0.9.6.29",
        )
        self.daily_rate_control.pack(side="left")

        self.action_row = ctk.CTkFrame(action_card, fg_color="transparent")
        self.action_row.pack(pady=(0, 12))
        self.quick_paste_btn = ctk.CTkButton(
            self.action_row, text="Quick Paste", width=120, height=36, fg_color="transparent", border_width=1,
            command=self._quick_paste,
        )
        self.quick_paste_btn.pack(side="left", padx=(0, 6))
        DevTag(self.action_row, state, "0.9.6.18").show(side="left", padx=(0, 12))
        self.most_recent_file_paste_btn = ctk.CTkButton(
            self.action_row, text="Most Recent File + Quick Paste", width=260, height=36,
            command=self._most_recent_file_and_paste,
        )
        self.most_recent_file_paste_btn.pack(side="left", padx=(0, 6))
        DevTag(self.action_row, state, "0.9.6.19").show(side="left", padx=(0, 20))
        self.upload_btn = ctk.CTkButton(
            self.action_row, text="Upload", width=130, height=36, fg_color="#2f7a3d", hover_color="#256030",
            font=ctk.CTkFont(size=13, weight="bold"), command=self._quick_upload, state="disabled",
        )
        self.upload_btn.pack(side="left")
        DevTag(self.action_row, state, "0.9.6.3").show(side="left", padx=(8, 0))
        self.auto_paste_enter_btn = AutoPasteEnterButton(
            self.action_row, fg_color="transparent", border_width=1,
        )
        self.auto_paste_enter_btn.pack(side="left", padx=(20, 0))
        DevTag(self.action_row, state, "0.9.6.31").show(side="left", padx=(8, 0))

        # ---- history -- today (packed now so it's the fixed anchor everything dynamic
        # below the action panel gets positioned "before") ----
        self.history_wrap = ctk.CTkFrame(self, fg_color="transparent")
        self.history_wrap.pack(fill="x", padx=12, pady=(4, 12), side="bottom")
        ctk.CTkFrame(self.history_wrap, fg_color="#333333", height=1).pack(fill="x", pady=(0, 8))
        history_header = ctk.CTkFrame(self.history_wrap, fg_color="transparent")
        history_header.pack(fill="x")
        ctk.CTkLabel(history_header, text="HISTORY", font=ctk.CTkFont(size=12, weight="bold")).pack(side="left")
        DevTag(history_header, state, "0.9.6.21").show(side="left", padx=(6, 0))
        # Today = just today's uploads; All = the permanent record of every upload (newest
        # first, 40 at a time). Refresh re-checks the WHOLE record against YouTube whichever
        # view is showing -- see _verify_all_records().
        self.history_view_toggle = ctk.CTkSegmentedButton(
            history_header, values=["Today", "All"], width=120, height=22, command=self._on_history_view_changed,
        )
        self.history_view_toggle.set("Today")
        self.history_view_toggle.pack(side="left", padx=(12, 0))
        DevTag(history_header, state, "0.9.6.35").show(side="left", padx=(6, 0))
        # Tags for the verification additions live here, once, rather than repeated on every
        # row -- per-row Developer Mode tags eat horizontal space, and a row that outgrows
        # the page loses its TITLE column first.
        DevTag(history_header, state, "0.9.6.32").show(side="left", padx=(6, 0))
        DevTag(history_header, state, "0.9.6.33").show(side="left", padx=(6, 0))
        DevTag(history_header, state, "0.9.6.34").show(side="left", padx=(6, 0))
        self.history_refresh_btn = ctk.CTkButton(
            history_header, text="↻ Refresh", width=96, height=24, fg_color="#2f7a3d", hover_color="#256030",
            font=ctk.CTkFont(size=11, weight="bold"), command=self._verify_all_records,
        )
        self.history_refresh_btn.pack(side="right")
        DevTag(history_header, state, "0.9.6.36").show(side="right", padx=(0, 6))
        self.history_status_label = ctk.CTkLabel(
            self.history_wrap, text="", anchor="w", justify="left", wraplength=1000, font=ctk.CTkFont(size=11),
        )  # the result of the last Refresh -- packed only while it has something to say
        self.history_list = ctk.CTkFrame(self.history_wrap, fg_color="transparent")
        self.history_list.pack(fill="x", pady=(6, 0))
        self.state.on_history_changed(self._render_history)  # the reschedule pages notify this after moving videos

        # ---- developer diagnostics (Developer Mode only) ----
        self.diagnostics_frame = ctk.CTkFrame(self, fg_color="#1f1f1f", corner_radius=8, border_width=1, border_color="#3a5a72")
        diag_header = ctk.CTkFrame(self.diagnostics_frame, fg_color="transparent")
        diag_header.pack(fill="x", padx=10, pady=(8, 4))
        ctk.CTkLabel(diag_header, text="DEVELOPER DIAGNOSTICS", font=ctk.CTkFont(size=11, weight="bold"), text_color="#6a8caa").pack(side="left")
        self.diagnostics_label = ctk.CTkLabel(
            self.diagnostics_frame, text="", font=ctk.CTkFont(size=10), justify="left", anchor="w", text_color="#a0a0a0",
        )
        self.diagnostics_label.pack(fill="x", padx=10, pady=(0, 6))
        diag_buttons = ctk.CTkFrame(self.diagnostics_frame, fg_color="transparent")
        diag_buttons.pack(anchor="w", padx=10, pady=(0, 8))
        self.diag_copy_error_btn = ctk.CTkButton(
            diag_buttons, text="Copy Error", width=90, height=22, font=ctk.CTkFont(size=10),
            fg_color="transparent", border_width=1, command=self._copy_last_upload_error,
        )
        self.diag_copy_error_btn.pack(side="left")
        self.diag_copy_report_btn = ctk.CTkButton(
            diag_buttons, text="Copy Upload Report", width=130, height=22, font=ctk.CTkFont(size=10),
            fg_color="transparent", border_width=1, command=self._copy_latest_upload_report,
        )
        self.diag_copy_report_btn.pack(side="left", padx=(6, 0))
        DevTag(diag_header, state, "0.9.6.26").show(side="left", padx=(6, 0))

        # ---- the one active selection (Selected Video / Title / Description / Next
        # Scheduled Position) -- dynamic, expands/changes below the fixed action panel
        # above; empty_label and detail_frame are mutually exclusive. Both this and the
        # diagnostics panel are always (re-)packed with before=self.history_wrap, so
        # neither one's toggling can ever disturb History's fixed position. ----
        self.empty_label = ctk.CTkLabel(self, text="Select a folder or drag a video in to begin.", text_color="#666666")
        self.detail_frame = ctk.CTkFrame(self, fg_color="#141414", corner_radius=8)
        self._build_detail_panel()
        self.empty_label.pack(pady=24, before=self.history_wrap)

        self._update_schedule_preview()
        self._refresh_copy_buttons()
        self._refresh_quick_buttons()
        self._update_counts()
        self.refresh_status()
        self._scan_and_display_folder()
        self._render_history()
        self._schedule_history_ago_refresh()
        self._sync_diagnostics_visibility()
        self.state.on_developer_mode_changed(self._sync_diagnostics_visibility)
        self.bind("<Destroy>", lambda e: self.state.off_developer_mode_changed(self._sync_diagnostics_visibility))

        self.state.on_videos_changed(self._update_schedule_preview)
        self._load_playlists()
        self._resume_after_id = self.after(RESUME_VERIFICATION_DELAY_MS, self._resume_pending_verifications)

    # ---- detail panel construction (built once; only its values change) ----

    def _build_detail_panel(self) -> None:
        body = ctk.CTkFrame(self.detail_frame, fg_color="transparent")
        body.pack(fill="x", padx=12, pady=12)

        self.thumb_label = ctk.CTkLabel(
            body, text="...", image=None, width=THUMB_SIZE[0], height=THUMB_SIZE[1],
            fg_color="#1e1e1e", corner_radius=6, font=ctk.CTkFont(size=10), text_color="#666666",
        )
        self.thumb_label.pack(side="left", padx=(0, 12), anchor="n")

        info_col = ctk.CTkFrame(body, fg_color="transparent")
        info_col.pack(side="left", fill="both", expand=True)

        heading_row = ctk.CTkFrame(info_col, fg_color="transparent")
        heading_row.pack(fill="x")
        ctk.CTkLabel(heading_row, text="SELECTED VIDEO", font=ctk.CTkFont(size=11, weight="bold"), text_color="#888888").pack(side="left")
        self.clear_btn = ctk.CTkButton(
            heading_row, text="Clear", width=60, height=22, fg_color="#5a2a2a", hover_color="#4a2020",
            command=self._clear_selection,
        )
        self.clear_btn.pack(side="right")
        DevTag(heading_row, self.state, "0.9.6.10").show(side="right", padx=(0, 6))

        name_row = ctk.CTkFrame(info_col, fg_color="transparent")
        name_row.pack(fill="x", pady=(2, 0))
        self.filename_label = ctk.CTkLabel(name_row, text="", anchor="w", font=ctk.CTkFont(size=16, weight="bold"))
        self.filename_label.pack(side="left")
        self.duration_label = ctk.CTkLabel(name_row, text="", text_color="#a0a0a0", font=ctk.CTkFont(size=12))
        self.duration_label.pack(side="left", padx=(8, 0))
        self.new_video_tag_label = ctk.CTkLabel(name_row, text="", text_color=GOOD, font=ctk.CTkFont(size=12, weight="bold"))
        self.new_video_tag_label.pack(side="left", padx=(8, 0))
        # The code this upload will get (0232, 0233...): kept in the app with its title and description and in the History -- never sent to YouTube.
        self.order_code_label = ctk.CTkLabel(name_row, text="", text_color=CODE_COLOR, font=ctk.CTkFont(size=12, weight="bold"))
        self.order_code_label.pack(side="left", padx=(8, 0))

        title_row = ctk.CTkFrame(info_col, fg_color="transparent")
        title_row.pack(fill="x", pady=(10, 0))
        ctk.CTkLabel(title_row, text="Title + Hashtags:", font=ctk.CTkFont(size=12), width=120, anchor="w").pack(side="left")
        self.title_var = ctk.StringVar(value="")
        self.title_entry = ctk.CTkEntry(
            title_row, textvariable=self.title_var, placeholder_text="Video title + hashtags",
            height=36, font=ctk.CTkFont(size=14),
        )
        self.title_entry.pack(side="left", fill="x", expand=True)
        self.title_var.trace_add("write", self._on_title_edited)
        self.title_count_label = ctk.CTkLabel(title_row, text="", font=ctk.CTkFont(size=10), text_color="#666666", width=56)
        self.title_count_label.pack(side="left", padx=(6, 0))
        DevTag(title_row, self.state, "0.9.6.7").show(side="left", padx=(6, 0))

        desc_row = ctk.CTkFrame(info_col, fg_color="transparent")
        desc_row.pack(fill="x", pady=(8, 0))
        ctk.CTkLabel(desc_row, text="Description:", font=ctk.CTkFont(size=12), width=120, anchor="nw").pack(side="left", anchor="n", pady=(6, 0))
        self.desc_text = ctk.CTkTextbox(desc_row, height=130, font=ctk.CTkFont(size=13))
        self.desc_text.pack(side="left", fill="x", expand=True)
        self.desc_text.bind("<KeyRelease>", self._on_description_edited)
        self.desc_count_label = ctk.CTkLabel(desc_row, text="", font=ctk.CTkFont(size=10), text_color="#666666", width=64, anchor="n")
        self.desc_count_label.pack(side="left", padx=(6, 0), anchor="n", pady=(6, 0))
        DevTag(desc_row, self.state, "0.9.6.8").show(side="left", padx=(6, 0), anchor="n", pady=(6, 0))

        actions_row = ctk.CTkFrame(info_col, fg_color="transparent")
        actions_row.pack(fill="x", pady=(6, 0))
        self.copy_title_btn = ctk.CTkButton(
            actions_row, text="Copy Title", width=88, height=24, fg_color="transparent", border_width=1,
            state="disabled", command=self._copy_title,
        )
        self.copy_title_btn.pack(side="left")
        DevTag(actions_row, self.state, "0.9.6.5").show(side="left", padx=(4, 0))
        self.copy_description_btn = ctk.CTkButton(
            actions_row, text="Copy Description", width=116, height=24, fg_color="transparent", border_width=1,
            state="disabled", command=self._copy_description,
        )
        self.copy_description_btn.pack(side="left", padx=(6, 0))
        DevTag(actions_row, self.state, "0.9.6.6").show(side="left", padx=(4, 0))

        settings_row = ctk.CTkFrame(info_col, fg_color="transparent")
        settings_row.pack(fill="x", pady=(10, 0))
        ctk.CTkLabel(settings_row, text="Playlist", font=ctk.CTkFont(size=12)).pack(side="left")
        self.playlist_var = ctk.StringVar(value=NO_PLAYLIST)
        self.playlist_menu = ctk.CTkOptionMenu(
            settings_row, variable=self.playlist_var, values=[NO_PLAYLIST], width=160, height=26, command=self._on_playlist_changed,
        )
        self.playlist_menu.pack(side="left", padx=(6, 14))
        ctk.CTkLabel(settings_row, text="Audience", font=ctk.CTkFont(size=12)).pack(side="left")
        self.kids_var = ctk.StringVar(value=KIDS_NO)
        self.kids_menu = ctk.CTkOptionMenu(
            settings_row, variable=self.kids_var, values=[KIDS_NO, KIDS_YES], width=200, height=26, command=self._on_kids_changed,
        )
        self.kids_menu.pack(side="left", padx=(6, 14))
        # "How many videos per day" used to live here as a small dropdown bound to Auto
        # Schedule's shared setting -- it's now its own independent control in the fixed
        # UPLOAD ACTIONS panel above (see __init__'s daily_rate_control), so it stays
        # visible/usable regardless of whether a video happens to be selected right now.

        # ---- QUALITY INFORMATION (Upload Quality Protection, 0.9.6.30 / QUALITY-001) --
        # available for the current selection, not gated behind Developer Mode. Read
        # straight off the local file via ffprobe (see _probe_tech_info()); never
        # affects what actually gets uploaded. COLLAPSED BY DEFAULT so it only costs one
        # compact header row of vertical space -- clicking the header or the arrow on its
        # right expands the full details (see _toggle_quality_info()). The probing below
        # keeps running while collapsed, since the post-upload verification needs the
        # pre-upload snapshot either way. ----
        tech_card = ctk.CTkFrame(info_col, fg_color="#101010", corner_radius=6)
        tech_card.pack(fill="x", pady=(10, 0))
        tech_header = ctk.CTkFrame(tech_card, fg_color="transparent", cursor="hand2")
        tech_header.pack(fill="x", padx=10, pady=(4, 4))
        tech_title = ctk.CTkLabel(
            tech_header, text="QUALITY INFORMATION", font=ctk.CTkFont(size=10, weight="bold"),
            text_color="#888888", cursor="hand2",
        )
        tech_title.pack(side="left")
        DevTag(tech_header, self.state, "0.9.6.30").show(side="left", padx=(6, 0))
        self.tech_toggle_btn = ctk.CTkButton(
            tech_header, text="▼", width=26, height=18, fg_color="transparent", hover_color="#2a2a2a",
            text_color="#888888", font=ctk.CTkFont(size=10), command=self._toggle_quality_info,
        )
        self.tech_toggle_btn.pack(side="right")
        for clickable in (tech_header, tech_title):
            clickable.bind("<Button-1>", lambda *_e: self._toggle_quality_info())

        self.tech_body = ctk.CTkFrame(tech_card, fg_color="transparent")  # shown/hidden by _apply_quality_info_state()
        tech_grid = ctk.CTkFrame(self.tech_body, fg_color="transparent")
        tech_grid.pack(fill="x", padx=10, pady=(0, 4))
        self.tech_info_labels: dict[str, ctk.CTkLabel] = {}
        for i, field_name in enumerate((
            "Filename", "File size", "Resolution", "FPS",
            "Video codec", "Video bitrate", "Audio codec", "Audio bitrate",
        )):
            row, col = divmod(i, 2)
            cell = ctk.CTkFrame(tech_grid, fg_color="transparent")
            cell.grid(row=row, column=col, sticky="w", padx=(0, 24), pady=1)
            ctk.CTkLabel(cell, text=f"{field_name}: ", font=ctk.CTkFont(size=10), text_color="#666666").pack(side="left")
            value_label = ctk.CTkLabel(cell, text="—", font=ctk.CTkFont(size=10, weight="bold"), text_color="#a0a0a0")
            value_label.pack(side="left")
            self.tech_info_labels[field_name] = value_label

        ctk.CTkLabel(
            self.tech_body,
            text=(
                "This app uploads your file exactly as it is on disk -- no re-encoding, compression, "
                "resizing, or codec changes. YouTube accepting the upload means it's successfully "
                "queued; YouTube's own processing of the highest-quality version continues afterward "
                "in the background."
            ),
            font=ctk.CTkFont(size=9), text_color="#555555", justify="left", wraplength=420, anchor="w",
        ).pack(fill="x", padx=10, pady=(2, 8))
        self._apply_quality_info_state()

        self.status_label = ctk.CTkLabel(info_col, text="", anchor="w", font=ctk.CTkFont(size=11))
        self.status_label.pack(anchor="w", pady=(8, 0))

        # ---- NEXT SCHEDULED POSITION: the single most prominent thing on this page --
        # large date/time, everything else (daily position, where the schedule currently
        # extends to) demoted to one small subtitle line so it doesn't compete for attention ----
        position_card = ctk.CTkFrame(info_col, fg_color="#12261a", corner_radius=8, border_width=1, border_color="#2f7a3d")
        position_card.pack(fill="x", pady=(12, 0))
        ctk.CTkLabel(
            position_card, text="NEXT SCHEDULED POSITION", font=ctk.CTkFont(size=11, weight="bold"), text_color=GOOD,
        ).pack(anchor="w", padx=14, pady=(10, 0))
        self.position_date_label = ctk.CTkLabel(position_card, text="—", font=ctk.CTkFont(size=24, weight="bold"), anchor="w")
        self.position_date_label.pack(anchor="w", padx=14, pady=(4, 0))
        self.position_time_label = ctk.CTkLabel(
            position_card, text="", font=ctk.CTkFont(size=20, weight="bold"), text_color=SCHEDULED_COLOR, anchor="w",
        )
        self.position_time_label.pack(anchor="w", padx=14, pady=(0, 2))
        self.position_subtitle_label = ctk.CTkLabel(
            position_card, text="", font=ctk.CTkFont(size=10), text_color="#6f9a7a", anchor="w", justify="left",
        )
        self.position_subtitle_label.pack(anchor="w", padx=14, pady=(2, 10))
        DevTag(position_card, self.state, "0.9.6.9").show(anchor="w", padx=(14, 0))

        self.progress_bar = ctk.CTkProgressBar(info_col, height=5)
        self.progress_bar.set(0)
        # only packed while uploading -- see _lock_ui()

    # ---- Shorts folder: select, scan, and display -- no background polling ----

    def _choose_folder(self) -> None:
        """Every step here is validated and observable: a cancelled dialog changes
        nothing, a path that turns out to be inaccessible is reported (not silently
        accepted), a config-save failure is reported (not swallowed), and success always
        ends in an immediate scan so the user sees real folder contents right away rather
        than just a label that claims a folder is selected."""
        self.state.set_last_action("Selecting Shorts folder")
        path = filedialog.askdirectory(title="Select your Shorts folder")
        if not path:
            return
        if not os.path.isdir(path):
            messagebox.showerror("Folder not found", f"This folder doesn't exist or isn't accessible:\n{path}")
            return
        self.state.config.shorts_folder_path = path
        try:
            self.state.config.save()
        except Exception as e:
            messagebox.showerror("Couldn't save folder selection", f"The folder was chosen but couldn't be saved:\n{e}")
            return
        self._scan_and_display_folder()

    def _scan_and_display_folder(self) -> None:
        folder = self.state.config.shorts_folder_path
        if not folder:
            self._folder_videos = []
            self._folder_scan_error = ""
        elif not os.path.isdir(folder):
            self._folder_videos = []
            self._folder_scan_error = "Folder is not accessible."
        else:
            try:
                self._folder_videos = list_folder_videos(folder, VIDEO_EXTENSIONS, self._registry)
                self._folder_scan_error = ""
            except Exception as e:
                # Deliberately broad: an inaccessible drive raises OSError, but a
                # malformed filename or anything else unanticipated should still surface
                # as a real, visible error here rather than crashing the click handler --
                # see the "Upload Reliability + Crash Recovery" spec's folder-scanning item.
                self._folder_videos = []
                self._folder_scan_error = str(e)
        self._render_folder_status()
        self._render_folder_video_list()
        self._update_diagnostics()

    def _render_folder_status(self) -> None:
        folder = self.state.config.shorts_folder_path
        if not folder:
            self.folder_status_label.configure(text="No folder selected", text_color="#888888")
        elif self._folder_scan_error:
            self.folder_status_label.configure(text=f"⚠ {self._folder_scan_error}", text_color=BAD)
        else:
            name = os.path.basename(folder.rstrip("\\/")) or folder
            count = len(self._folder_videos)
            self.folder_status_label.configure(
                text=f"✓ Selected: {name}  ({count} video{'s' if count != 1 else ''})", text_color=GOOD,
            )

    def _render_folder_video_list(self) -> None:
        for w in self.folder_video_list.winfo_children():
            w.destroy()
        if self._folder_scan_error:
            ctk.CTkLabel(
                self.folder_video_list, text=f"⚠ {self._folder_scan_error}", text_color=BAD,
                font=ctk.CTkFont(size=11), wraplength=260, justify="left",
            ).pack(anchor="w", pady=4)
            return
        if not self._folder_videos:
            text = "No folder selected yet." if not self.state.config.shorts_folder_path else "No videos found in this folder."
            ctk.CTkLabel(self.folder_video_list, text=text, text_color="#666666", font=ctk.CTkFont(size=11)).pack(anchor="w", pady=4)
            return

        selected_path = self._selected.file_path if self._selected else None
        highlighted_path = self._most_recent_result.path if self._most_recent_result else None
        for entry in reversed(self._folder_videos):  # newest q-number (most recently added) first
            is_selected = entry.path == selected_path
            is_highlighted = entry.path == highlighted_path
            bg = "#2f5a3a" if is_highlighted else ("#213a2b" if is_selected else "#1c1c1c")
            row = ctk.CTkFrame(self.folder_video_list, fg_color=bg, corner_radius=4)
            row.pack(fill="x", pady=1)
            status_text = "Uploaded" if entry.is_uploaded else "New"
            status_color = "#888888" if entry.is_uploaded else GOOD
            text_color = "#ffffff" if (is_selected or is_highlighted) else status_color
            label_text = f"Q{entry.q_number} — {entry.filename}   {entry.touched_at.strftime('%d %b %H:%M')}   {status_text}"
            lbl = ctk.CTkLabel(row, text=label_text, anchor="w", font=ctk.CTkFont(size=11), text_color=text_color)
            lbl.pack(side="left", fill="x", expand=True, padx=8, pady=4)
            row.bind("<Button-1>", lambda e, p=entry.path, q=entry.q_number: self._select_from_folder_list(p, q))
            lbl.bind("<Button-1>", lambda e, p=entry.path, q=entry.q_number: self._select_from_folder_list(p, q))
            DevTag(row, self.state, "0.9.6.25").show(side="right", padx=(4, 6))

    def _select_from_folder_list(self, path: str, q_number: str) -> None:
        if self._uploading:
            messagebox.showerror("Upload in progress", "Wait for the current upload to finish first.")
            return
        if not os.path.exists(path):
            messagebox.showerror("File not found", "This file is no longer in the folder.")
            self._scan_and_display_folder()
            return
        if self._selected and self._selected.file_path == path:
            return
        if not self._confirm_discard_if_needed():
            return
        self._set_selected(SelectedVideo(
            file_path=path, q_number=q_number,
            playlist_name=self.state.config.last_playlist_name or "",
            made_for_kids=self.state.config.last_made_for_kids,
        ))

    def _most_recent_file(self) -> None:
        self.state.set_last_action("Finding most recent file")
        self._most_recent_result = None  # reset up front so a guard clause below can never leave a stale result from a previous call in place
        if self._uploading:
            messagebox.showerror("Upload in progress", "Wait for the current upload to finish first.")
            return
        folder = self.state.config.shorts_folder_path
        if not folder or not os.path.isdir(folder):
            messagebox.showerror("No folder selected", "Select your Shorts folder first.")
            return

        self._scan_and_display_folder()
        if self._folder_scan_error:
            self._most_recent_result = None
            self._render_check_result(None, self.state.config.new_video_detection_window_seconds, error=self._folder_scan_error)
            return

        window_seconds = self.state.config.new_video_detection_window_seconds
        now = dt.datetime.now().astimezone()
        candidates = [
            CheckNewResult(
                q_number=e.q_number, filename=e.filename, path=e.path, file_size=e.file_size,
                created_at=e.touched_at, age_seconds=(now - e.touched_at).total_seconds(),
            )
            for e in self._folder_videos if not e.is_uploaded
        ]
        result = pick_newest_recent(candidates, now, window_seconds)
        self._most_recent_result = result
        self._render_check_result(result, window_seconds)
        self._render_folder_video_list()
        self._update_diagnostics()
        if result is None:
            return

        if self._selected and self._selected.file_path == result.path:
            return
        if not self._confirm_discard_if_needed():
            return
        self._set_selected(SelectedVideo(
            file_path=result.path, q_number=result.q_number,
            playlist_name=self.state.config.last_playlist_name or "",
            made_for_kids=self.state.config.last_made_for_kids,
        ))

    def _most_recent_file_and_paste(self) -> None:
        """Combined action (0.9.6.19 + 0.9.6.18): find + select the newest eligible video,
        then immediately Quick Paste the clipboard into its Title/Description. Never
        uploads.

        If nothing eligible is found but a video is already selected (e.g. dragged in
        manually), Quick Paste still applies to that -- this is what keeps "drag a video,
        then paste, then upload" working even though Quick Paste no longer has its own
        button. If a newer file WAS found but the user declined to discard an unsaved
        previous selection (so the old one is still what's selected), nothing gets pasted
        over it -- their kept selection is left exactly as it was."""
        self._most_recent_file()
        if self._most_recent_result is not None and (
            self._selected is None or self._selected.file_path != self._most_recent_result.path
        ):
            return
        if self._selected is not None:
            self._quick_paste()

    def _render_check_result(self, result: Optional[CheckNewResult], window_seconds: int, error: str = "") -> None:
        if error:
            self.check_result_label.configure(text=f"⚠ {error}", text_color=BAD)
            return
        if result is None:
            self.check_result_label.configure(
                text=(
                    "No recent video found.\n"
                    f"No video was created in the last {_format_window(window_seconds)}."
                ),
                text_color=WARN,
            )
            return
        self.check_result_label.configure(
            text=(
                "✓ Most recent file found\n"
                f"{result.filename}\n"
                f"Created: {result.created_at.strftime('%H:%M:%S')}   Age: {int(result.age_seconds)}s"
            ),
            text_color=GOOD,
        )

    # ---- drag-and-drop (right side) ----

    def _on_drop(self, event) -> None:
        try:
            paths = self.tk.splitlist(event.data)
        except Exception:
            paths = [event.data] if event.data else []
        if paths:
            self._set_file_from_drag(paths[0])

    def _browse(self) -> None:
        path = filedialog.askopenfilename(
            title="Select a video",
            filetypes=[("Video files", " ".join(f"*{ext}" for ext in VIDEO_EXTENSIONS)), ("All files", "*.*")],
        )
        if path:
            self._set_file_from_drag(path)

    def _set_file_from_drag(self, path: str) -> None:
        """Drag Video -> Select Video -> Automatically Attempt Quick Paste: once the video
        is selected (whether dragged in or picked via the "click to browse" fallback --
        same drop zone, same workflow), this immediately tries a SILENT Quick Paste. If
        the clipboard has nothing usable, nothing is shown at all -- see _quick_paste's
        silent=True behavior -- the video is still selected and the user can fill in the
        title/description by hand or with the standalone QUICK PASTE button."""
        if self._uploading:
            messagebox.showerror("Upload in progress", "Wait for the current upload to finish first.")
            return
        ext = os.path.splitext(path)[1].lower()
        if ext not in VIDEO_EXTENSIONS:
            messagebox.showerror("Unsupported file", f"This doesn't look like a video file:\n{os.path.basename(path)}")
            return
        if self._selected and self._selected.file_path == path:
            return
        if not self._confirm_discard_if_needed():
            return
        self._set_selected(SelectedVideo(
            file_path=path,
            playlist_name=self.state.config.last_playlist_name or "",
            made_for_kids=self.state.config.last_made_for_kids,
        ))
        self._quick_paste(silent=True)

    # ---- the one active selection ----

    def _confirm_discard_if_needed(self) -> bool:
        """True if it's fine to replace whatever's currently selected -- nothing typed
        into it yet, or the user explicitly confirms losing what is typed."""
        if self._selected is None:
            return True
        if not self._selected.title.strip() and not self._selected.description.strip():
            return True
        return messagebox.askyesno(
            "Discard current video?",
            f"You have an unsaved title/description for {os.path.basename(self._selected.file_path)}. "
            f"Switch anyway and lose it?",
        )

    def _set_selected(self, selected: SelectedVideo) -> None:
        self._selected = selected
        self.empty_label.pack_forget()
        self.detail_frame.pack(fill="x", padx=12, pady=(0, 12), before=self.history_wrap)
        self.filename_label.configure(text=os.path.basename(selected.file_path))
        self.duration_label.configure(text="Reading...")
        self.new_video_tag_label.configure(text="✓ New video detected" if selected.q_number else "")
        self._refresh_order_code_label()
        # Deliberately not clearing thumb_label's image here -- see _thumb_keepalive's
        # comment. The previous selection's thumbnail just stays visible until _on_probed
        # replaces it with a real one (or "No preview" if this file has none).
        self.title_var.set(selected.title)
        self.desc_text.delete("1.0", "end")
        if selected.description:
            self.desc_text.insert("1.0", selected.description)
        self.kids_var.set(KIDS_YES if selected.made_for_kids else KIDS_NO)
        self._refresh_playlist_options()
        self._refresh_copy_buttons()
        self._refresh_quick_buttons()
        self._update_counts()
        self.refresh_status()
        self._render_folder_video_list()
        self._update_diagnostics()
        self._recalibrate_and_preview()
        probe_async(self, selected.file_path, THUMB_SIZE, self._on_probed)
        self._tech_info = None
        self._last_quality_check = ""
        self._render_tech_info(None, loading=True)
        self._probe_tech_info(selected)

    def _refresh_order_code_label(self) -> None:
        """Shows the code the selected video will be given when it is uploaded."""
        try:
            code = QueueOrderStore.shared().next_code() if self._selected is not None else ""
            self.order_code_label.configure(text=f"CODE {code}" if code else "")
        except Exception:
            pass  # a label must never get in the way of selecting a video

    def _clear_selection(self) -> None:
        if self._uploading:
            return
        self._selected = None
        self.detail_frame.pack_forget()
        self.empty_label.pack(pady=24, before=self.history_wrap)
        self._refresh_quick_buttons()
        self._render_folder_video_list()
        self._update_diagnostics()

    def _on_probed(self, duration_seconds: int, thumbnail: Optional[ctk.CTkImage]) -> None:
        if not self.winfo_exists() or self._selected is None:
            return
        self.duration_label.configure(text=format_duration(duration_seconds) or "--:--")
        if thumbnail is not None:
            self._thumb_keepalive.append(thumbnail)
            del self._thumb_keepalive[:-6]  # only the newest is on screen -- the old ones no longer need to be pinned (this used to grow with every video ever selected)
            self.thumb_label.configure(image=thumbnail, text="")
        else:
            self.thumb_label.configure(text="No preview")

    # ---- quality information (Upload Quality Protection, 0.9.6.30 / QUALITY-001) ----

    def _toggle_quality_info(self) -> None:
        self.state.config.quality_info_expanded = not self.state.config.quality_info_expanded
        self.state.config.save()  # remembered across restarts; the default (no saved value) is collapsed
        self._apply_quality_info_state()

    def _apply_quality_info_state(self) -> None:
        expanded = self.state.config.quality_info_expanded
        self.tech_toggle_btn.configure(text="▲" if expanded else "▼")
        if expanded:
            self.tech_body.pack(fill="x")
        else:
            self.tech_body.pack_forget()

    def _probe_tech_info(self, selected: SelectedVideo) -> None:
        """Reads the SELECTED file's own real properties off disk, on a worker thread --
        ffprobe can take a moment on a large file, and this must never block the UI. Never
        raises: probe_local_file() itself already reduces any failure to None."""
        def worker():
            info = probe_local_file(selected.file_path)
            self._safe_after(lambda: self._on_tech_info_probed(selected, info))

        self._start_worker(worker)

    def _on_tech_info_probed(self, selected: SelectedVideo, info: Optional[VideoTechInfo]) -> None:
        if not self.winfo_exists() or self._selected is not selected:
            return  # the selection changed while this was probing -- discard the stale result
        self._tech_info = info
        self._render_tech_info(info)

    def _render_tech_info(self, info: Optional[VideoTechInfo], loading: bool = False) -> None:
        if loading:
            for label in self.tech_info_labels.values():
                label.configure(text="Reading...")
            return
        if info is None:
            fallback_name = os.path.basename(self._selected.file_path) if self._selected else "—"
            for field_name, label in self.tech_info_labels.items():
                label.configure(text=fallback_name if field_name == "Filename" else "Unknown")
            return
        self.tech_info_labels["Filename"].configure(text=info.filename)
        self.tech_info_labels["File size"].configure(text=format_size(info.file_size_bytes))
        self.tech_info_labels["Resolution"].configure(text=f"{info.width}x{info.height}" if info.width else "Unknown")
        self.tech_info_labels["FPS"].configure(text=f"{info.fps:g}" if info.fps else "Unknown")
        self.tech_info_labels["Video codec"].configure(text=info.video_codec or "Unknown")
        self.tech_info_labels["Video bitrate"].configure(text=format_bitrate(info.video_bitrate_bps))
        self.tech_info_labels["Audio codec"].configure(text=info.audio_codec or "Unknown")
        self.tech_info_labels["Audio bitrate"].configure(text=format_bitrate(info.audio_bitrate_bps))

    # ---- title / description ----

    def _on_title_edited(self, *_args) -> None:
        if self._selected is None:
            return
        self._selected.title = self.title_var.get()
        self._refresh_copy_buttons()
        self._update_counts()
        self.refresh_status()

    def _on_description_edited(self, _event=None) -> None:
        if self._selected is None:
            return
        self._selected.description = self.desc_text.get("1.0", "end-1c")
        self._refresh_copy_buttons()
        self._update_counts()
        self.refresh_status()

    def _quick_paste(self, silent: bool = False) -> None:
        """Reads and parses the clipboard into Title/Description ONLY -- it never uploads
        or schedules anything, so there's always a chance to review or edit before pressing
        Upload separately.

        `silent=True` is for the automatic attempt this panel makes right after a video is
        dragged/browsed in (see _set_file_from_drag()): on any failure (nothing usable on
        the clipboard, or it doesn't parse) it does absolutely nothing visible -- no
        messagebox, no label change -- and just leaves the Title/Description fields as
        they were. The video stays selected either way; the user can always fall back to
        the standalone QUICK PASTE button or just type things in by hand. Manual clicks
        (that button, or the Most Recent File + Quick Paste combined button) keep the
        existing loud behavior, since those are a deliberate action the user is watching
        the result of."""
        if self._selected is None:
            return
        try:
            text = self.clipboard_get()
        except Exception:
            return
        title, description, error = parse_title_description(text.strip())
        if error:
            if not silent:
                messagebox.showerror("Could not parse title/description", error)
            return
        self.title_var.set(title)
        self.desc_text.delete("1.0", "end")
        self.desc_text.insert("1.0", description)
        self._selected.title = title
        self._selected.description = description
        self._refresh_copy_buttons()
        self._update_counts()
        self.refresh_status()

    def _copy_title(self) -> None:
        if not self._selected or not self._selected.title:
            return
        self.clipboard_clear()
        self.clipboard_append(self._selected.title)

    def _copy_description(self) -> None:
        if not self._selected or not self._selected.description:
            return
        self.clipboard_clear()
        self.clipboard_append(self._selected.description)

    def _refresh_copy_buttons(self) -> None:
        title = self._selected.title if self._selected else ""
        description = self._selected.description if self._selected else ""
        self.copy_title_btn.configure(state="normal" if title.strip() else "disabled")
        self.copy_description_btn.configure(state="normal" if description.strip() else "disabled")

    def _refresh_quick_buttons(self) -> None:
        # Most Recent File + Quick Paste stays enabled with nothing selected -- finding a
        # video is its whole first step. Only Upload needs an actual selection to make sense.
        self.upload_btn.configure(state="normal" if self._selected is not None else "disabled")

    def _update_counts(self) -> None:
        title_len = len(self._selected.title) if self._selected else 0
        self.title_count_label.configure(
            text=f"{title_len}/{MAX_TITLE_LEN}", text_color=BAD if title_len > MAX_TITLE_LEN else "#666666",
        )
        desc_len = len(self._selected.description) if self._selected else 0
        self.desc_count_label.configure(
            text=f"{desc_len}/{MAX_DESCRIPTION_LEN}", text_color=BAD if desc_len > MAX_DESCRIPTION_LEN else "#666666",
        )

    # ---- playlist / audience ----

    def _load_playlists(self) -> None:
        def worker():
            try:
                playlists = self.state.client.list_playlists() if self.state.client else []
            except Exception as e:
                diagnostics.report_recovered("Loading playlists", e, page="Upload")
                playlists = []
            self._safe_after(lambda: self._on_playlists_loaded(playlists))

        self._start_worker(worker)

    def _on_playlists_loaded(self, playlists: list[Playlist]) -> None:
        self._playlists = playlists
        self._refresh_playlist_options()

    def _refresh_playlist_options(self) -> None:
        names = [NO_PLAYLIST] + [p.title for p in self._playlists]
        self.playlist_menu.configure(values=names)
        current = self._selected.playlist_name if self._selected else ""
        self.playlist_var.set(current if current and current in names else NO_PLAYLIST)

    def _on_playlist_changed(self, value: str) -> None:
        if self._selected is None:
            return
        self._selected.playlist_name = "" if value == NO_PLAYLIST else value
        self.state.config.last_playlist_name = self._selected.playlist_name
        self.state.config.save()

    def _on_kids_changed(self, value: str) -> None:
        if self._selected is None:
            return
        self._selected.made_for_kids = value == KIDS_YES
        self.state.config.last_made_for_kids = self._selected.made_for_kids
        self.state.config.save()

    # ---- videos per day (One Upload Page's OWN setting -- see the module docstring's
    # DAILY CAPACITY note; deliberately never touches AutoScheduleConfig) ----

    def _current_daily_rate(self) -> int:
        """This page's own daily capacity (1-6), read fresh from AppConfig every time,
        never cached -- deliberately independent of AutoScheduleConfig.normal_videos_per_day."""
        return max(MIN_SLOT_COUNT, min(MAX_SLOT_COUNT, self.state.config.upload_videos_per_day))

    def _on_daily_rate_changed(self, count: int) -> None:
        self.state.config.upload_videos_per_day = count
        self.state.config.save()
        self._update_schedule_preview()

    # ---- automatic scheduling (no manual override) ----

    def _recalibrate_and_preview(self) -> None:
        """RECALIBRATE SCHEDULE: refreshes the real YouTube schedule before showing where
        a newly-selected video would land, so the preview can never drift from a stale
        local cache -- YouTube is always the source of truth. Shows whatever's cached
        immediately (so the panel isn't blank while this runs), then replaces it with the
        recalibrated result once the refresh completes."""
        self._update_schedule_preview()
        if not self.state.repo:
            return

        def worker():
            try:
                self.state.repo.refresh()
            except Exception as e:
                diagnostics.report_recovered("Refreshing the schedule (Upload page)", e, page="Upload")
            self._safe_after(self._update_schedule_preview)

        self._start_worker(worker)

    def _update_schedule_preview(self) -> None:
        """Refreshes the prominent "next schedule position" card from whatever's already
        cached locally -- no network call (see _recalibrate_and_preview() for the version
        that refreshes first). Never touches the title/description widgets, so it's
        always safe to call from a background poll or an unrelated schedule change
        without disturbing anything being typed.

        Scheduling logic (unchanged): reads the actual future schedule, finds the furthest
        occupied date, tops it up while it has spare capacity against this page's OWN
        videos-per-day rate (see _current_daily_rate() -- deliberately not Auto
        Schedule's), and only then advances to the next date -- see
        scheduling/next_slot.py's find_upload_slots_at_rate()."""
        if not self.winfo_exists():
            return
        rate = self._current_daily_rate()

        if self._selected is None:
            self._update_diagnostics()
            return

        existing = list(self.state.repo.videos) if self.state.repo else []
        slots = find_upload_slots_at_rate(existing, 1, rate)
        if not slots:
            self.position_date_label.configure(text="Calculating...")
            self.position_time_label.configure(text="")
            self.position_subtitle_label.configure(text="")
            self._update_diagnostics()
            return
        slot = slots[0]
        today = dt.date.today()
        day = slot.astimezone().date()
        position = sum(1 for v in existing if v.publish_at and v.publish_at.astimezone().date() == day) + 1
        occupied_dates = [
            v.publish_at.astimezone().date() for v in existing
            if v.publish_at and v.publish_at.astimezone().date() >= today
        ]
        extends_to = max(occupied_dates).strftime("%d %B %Y") if occupied_dates else "nothing scheduled yet"
        self.position_date_label.configure(text=slot.strftime("%d %B %Y").upper())
        self.position_time_label.configure(text=slot.strftime("%I:%M %p"))
        self.position_subtitle_label.configure(
            text=f"Daily allocation {position} / {rate}  •  Schedule extends to {extends_to}"
        )
        self._update_diagnostics()

    # ---- status ----

    def refresh_status(self) -> None:
        if self._selected is None:
            self.status_label.configure(text="")
            return
        if self._selected.status == "uploading":
            text, color = "Uploading...", "#5b9bd5"
        elif self._selected.status == "failed":
            text, color = f"Failed: {self._selected.error}", BAD
        elif self._selected.ready:
            text, color = "Ready", GOOD
        else:
            text, color = "Waiting for title & description", "#888888"
        self.status_label.configure(text=f"Status: {text}", text_color=color)

    def _lock_ui(self, locked: bool) -> None:
        state_ = "disabled" if locked else "normal"
        for w in (
            self.title_entry, self.quick_paste_btn, self.most_recent_file_paste_btn, self.upload_btn,
            self.playlist_menu, self.kids_menu, self.clear_btn, self.folder_select_btn,
        ):
            w.configure(state=state_)
        self.daily_rate_control.set_enabled(not locked)
        self.desc_text.configure(state=state_)
        if locked:
            self.progress_bar.set(0)
            self.progress_bar.pack(fill="x", pady=(6, 0))
        else:
            self.progress_bar.pack_forget()
            self._refresh_copy_buttons()
            self._refresh_quick_buttons()
        self.refresh_status()

    # ---- history -- today ----

    def _schedule_history_ago_refresh(self) -> None:
        self._ago_after_id = self.after(HISTORY_AGO_REFRESH_MS, self._tick_history_ago)

    def destroy(self) -> None:
        """Cancels this page's pending timers before it goes -- the 30s HOW LONG AGO tick
        re-arms itself forever, so a page that is destroyed and rebuilt (e.g. on sign-out and
        sign-in again) would otherwise leave an orphaned timer behind each time."""
        for attr in ("_ago_after_id", "_resume_after_id"):
            after_id = getattr(self, attr, None)
            if after_id:
                try:
                    self.after_cancel(after_id)
                except Exception:
                    pass
                setattr(self, attr, None)
        try:
            self.state.off_history_changed(self._render_history)
        except Exception:
            pass
        super().destroy()

    def _tick_history_ago(self) -> None:
        """Refreshes the HOW LONG AGO texts IN PLACE. It used to rebuild every History row
        each time, which also rebuilt the buttons: CustomTkinter only counts a mouse-up as a
        click if the pointer ENTERED that button first, so a button replaced underneath a
        stationary pointer ignored the next click until the mouse was nudged -- a "Verify
        button that doesn't work". Rows are now only rebuilt when their data actually changes."""
        if not self.winfo_exists():
            return
        try:
            for label, uploaded_at in list(self._ago_labels):
                try:
                    text = _format_time_ago(uploaded_at)
                    if label.cget("text") != text:
                        label.configure(text=text)
                except Exception:
                    pass  # that row was rebuilt/destroyed since -- the next render registers fresh labels
        finally:
            self._schedule_history_ago_refresh()  # re-armed no matter what, so one bad pass can't stop the clock for good

    def _after_click(self, fn) -> None:
        """Runs a History button's action just AFTER its click has finished. Several of these
        actions rebuild the row list -- destroying the very button being clicked, from inside
        its own click handler, while CustomTkinter still has a click-animation scheduled on it
        (which then fires at a widget that no longer exists)."""
        self.after(CLICK_SETTLE_MS, fn)

    def _hfont(self, size: int, weight: str = "normal") -> ctk.CTkFont:
        """One shared CTkFont per (size, weight) for the history rows. _render_history()
        rebuilds every row on each stage change and every 30s, and constructing fresh
        CTkFont objects each time leaves a pile of garbage Tk font objects behind -- see
        _start_worker() for why that matters."""
        key = (size, weight)
        font = self._history_fonts.get(key)
        if font is None:
            font = self._history_fonts[key] = ctk.CTkFont(size=size, weight=weight)
        return font

    def _start_worker(self, target, on_error=None) -> None:
        """Starts a daemon worker thread. Collects garbage FIRST, on the main thread. Tk
        objects (fonts, images, variables) run Tcl calls when they are garbage-collected;
        if that collection happened on the NEW thread while the main thread sits in
        Thread.start() waiting for it to come up, the Tcl call would wait for the main
        thread and the main thread for the new thread -- a permanent freeze (reproduced in
        testing right after a history re-render). Collecting here means there is nothing
        left for the new thread to collect."""
        gc.collect()
        start_worker(target, name="Upload page background task", page="Upload", on_error=on_error)

    def _render_history(self) -> None:
        """HIST-001: one compact row per upload -- FILE | TITLE | DATE | TIME | HOW LONG
        AGO, aligned in real columns (not free-floating text), never more than one line
        each. TITLE is truncated with "..." (see _truncate_to_width()) instead of
        wrapping. HOW LONG AGO is computed fresh from the real upload timestamp every time
        this renders, and re-renders on its own every 30s (see _schedule_history_ago_refresh())
        so it stays current without needing the page reopened. Nothing else is shown here
        -- no description, hashtags, or per-row buttons; a failed upload's row shows the
        error in place of a title, styled red, and can be clicked to copy the error (see
        _copy_history_error()) without adding a second line or a visible extra column."""
        if not self.winfo_exists():
            return
        self._ago_labels = []
        for w in self.history_list.winfo_children():
            w.destroy()
        show_all = self._history_view == "All"
        everything = self._activity_log.newest_first() if show_all else sorted(self._activity_log.today_entries(), key=lambda e: e.uploaded_at, reverse=True)
        entries = everything[: self._history_limit] if show_all else everything
        if not entries:
            ctk.CTkLabel(
                self.history_list, text="No uploads recorded yet." if show_all else "No uploads yet today.",
                text_color="#666666", font=self._hfont(11),
            ).pack(anchor="w")
            return

        dev_mode = self.state.config.developer_mode
        has_retry = any(e.status in (STATUS_VERIFICATION_FAILED, STATUS_MISSING) for e in entries)
        # The actions column (Retry Verification / Copy Report) only reserves the width the
        # current rows actually need, identically in the header and every row so the other
        # columns stay aligned -- and the TITLE budget shrinks by the same amount so the row
        # can never outgrow the page (a squeezed-out title column would simply vanish).
        actions_width = (HISTORY_RETRY_BTN_PX + 4 if has_retry else 0) + (HISTORY_COPY_BTN_PX if dev_mode else 0)
        title_budget = max(120, HISTORY_TITLE_MAX_PX - actions_width)

        header = ctk.CTkFrame(self.history_list, fg_color="transparent")
        header.pack(fill="x", pady=(0, 2))
        header.grid_columnconfigure(2, weight=1)
        header_font = self._hfont(10, "bold")
        ctk.CTkLabel(header, text="FILE", width=HISTORY_FILE_COL_PX, anchor="w", font=header_font, text_color="#888888").grid(row=0, column=0, sticky="w")
        ctk.CTkLabel(header, text="CODE", width=HISTORY_CODE_COL_PX, anchor="w", font=header_font, text_color="#888888").grid(row=0, column=1, sticky="w", padx=(8, 0))
        ctk.CTkLabel(header, text="TITLE", anchor="w", font=header_font, text_color="#888888").grid(row=0, column=2, sticky="w", padx=(8, 8))
        ctk.CTkLabel(header, text="DATE", width=HISTORY_DATE_COL_PX, anchor="w", font=header_font, text_color="#888888").grid(row=0, column=3, sticky="w")
        ctk.CTkLabel(header, text="TIME", width=HISTORY_TIME_COL_PX, anchor="w", font=header_font, text_color="#888888").grid(row=0, column=4, sticky="w")
        ctk.CTkLabel(header, text="HOW LONG AGO", width=HISTORY_AGO_COL_PX, anchor="w", font=header_font, text_color="#888888").grid(row=0, column=5, sticky="w")
        ctk.CTkLabel(header, text="STATUS", width=HISTORY_STATUS_COL_PX, anchor="w", font=header_font, text_color="#888888").grid(row=0, column=6, sticky="w")
        if actions_width:
            ctk.CTkFrame(header, width=actions_width, height=1, fg_color="transparent").grid(row=0, column=7, padx=(4, 0))

        row_font = _get_history_row_font()
        for entry in entries:
            uploaded_at = dt.datetime.fromisoformat(entry.uploaded_at)
            row = ctk.CTkFrame(self.history_list, fg_color="transparent")
            row.pack(fill="x", pady=2)
            row.grid_columnconfigure(2, weight=1)

            ctk.CTkLabel(row, text=entry.filename, width=HISTORY_FILE_COL_PX, anchor="w", font=self._hfont(12)).grid(
                row=0, column=0, sticky="w"
            )
            ctk.CTkLabel(row, text=entry.order_code or "—", width=HISTORY_CODE_COL_PX, anchor="w", font=self._hfont(12, "bold"),
                         text_color=CODE_COLOR if entry.order_code else "#555555").grid(row=0, column=1, sticky="w", padx=(8, 0))

            # DATE/TIME is when YouTube is scheduled to PUBLISH it (never confused with the
            # HOW LONG AGO column, which is when it was actually uploaded); an upload that
            # failed outright was never scheduled, so its DATE/TIME is just when it was tried.
            if entry.status != STATUS_FAILED and entry.scheduled_at:
                when = dt.datetime.fromisoformat(entry.scheduled_at).astimezone()
            else:
                when = uploaded_at.astimezone()

            if entry.status == STATUS_FAILED:
                error_text = _truncate_to_width(f"✗ Failed — {entry.error}", row_font, title_budget)
                error_lbl = ctk.CTkLabel(
                    row, text=error_text, anchor="w", font=self._hfont(12), text_color=BAD, cursor="hand2",
                )
                error_lbl.grid(row=0, column=2, sticky="w", padx=(8, 8))
                error_lbl.bind("<Button-1>", lambda *_e, err=entry.error: self._copy_history_error(err))
                row.bind("<Button-1>", lambda *_e, err=entry.error: self._copy_history_error(err))
            else:
                clean_title = strip_hashtags(entry.title) or "(no title)"
                title_text = _truncate_to_width(clean_title, row_font, title_budget)
                title_lbl = ctk.CTkLabel(row, text=title_text, anchor="w", font=self._hfont(12))
                title_lbl.grid(row=0, column=2, sticky="w", padx=(8, 8))
                if entry.status in (STATUS_VERIFICATION_FAILED, STATUS_MISSING):  # click anywhere on the row to copy THIS entry's report
                    title_lbl.configure(cursor="hand2")
                    title_lbl.bind("<Button-1>", lambda *_e, en=entry: self._copy_upload_report(en))
                    row.bind("<Button-1>", lambda *_e, en=entry: self._copy_upload_report(en))

            ctk.CTkLabel(row, text=when.strftime("%d/%m/%Y"), width=HISTORY_DATE_COL_PX, anchor="w", font=self._hfont(12)).grid(
                row=0, column=3, sticky="w"
            )
            ctk.CTkLabel(row, text=when.strftime("%H:%M"), width=HISTORY_TIME_COL_PX, anchor="w", font=self._hfont(12)).grid(
                row=0, column=4, sticky="w"
            )
            ago_label = ctk.CTkLabel(row, text=_format_time_ago(uploaded_at), width=HISTORY_AGO_COL_PX, anchor="w", font=self._hfont(12))
            ago_label.grid(row=0, column=5, sticky="w")
            self._ago_labels.append((ago_label, uploaded_at))
            ctk.CTkLabel(
                row, text=entry.display_status, width=HISTORY_STATUS_COL_PX, anchor="w",
                font=self._hfont(11, "bold"), text_color=STATUS_COLORS.get(entry.status, "#888888"),
            ).grid(row=0, column=6, sticky="w")

            if actions_width:
                actions = ctk.CTkFrame(row, fg_color="transparent", width=actions_width, height=22)
                actions.grid(row=0, column=7, sticky="e", padx=(4, 0))
                actions.pack_propagate(False)
                if entry.status == STATUS_VERIFICATION_FAILED:
                    ctk.CTkButton(
                        actions, text="Retry Verification", width=HISTORY_RETRY_BTN_PX, height=20, font=self._hfont(10),
                        fg_color="#2f7a3d", hover_color="#256030",
                        command=lambda en=entry: self._after_click(lambda: self._retry_verification(en, show_dialog=True)),
                    ).pack(side="left", padx=(0, 4))
                elif entry.status == STATUS_MISSING:
                    ctk.CTkButton(
                        actions, text="Re-check", width=HISTORY_RETRY_BTN_PX, height=20, font=self._hfont(10),
                        fg_color="#2f7a3d", hover_color="#256030", command=lambda en=entry: self._after_click(lambda: self._recheck_entry(en)),
                    ).pack(side="left", padx=(0, 4))
                if dev_mode:
                    ctk.CTkButton(
                        actions, text="Copy Report", width=HISTORY_COPY_BTN_PX, height=20, font=self._hfont(10),
                        fg_color="transparent", border_width=1, command=lambda en=entry: self._copy_upload_report(en),
                    ).pack(side="left")
            DevTag(row, self.state, "0.9.6.22").show_grid(row=0, column=8, sticky="e", padx=(4, 0))

        if show_all:
            footer = ctk.CTkFrame(self.history_list, fg_color="transparent")
            footer.pack(fill="x", pady=(6, 0))
            ctk.CTkLabel(
                footer, text=f"Showing {len(entries)} of {len(everything)} recorded uploads", text_color="#666666", font=self._hfont(10),
            ).pack(side="left")
            if len(everything) > len(entries):
                ctk.CTkButton(
                    footer, text=f"Show {min(HISTORY_PAGE_SIZE, len(everything) - len(entries))} more", width=110, height=22,
                    fg_color="transparent", border_width=1, font=self._hfont(10), command=lambda: self._after_click(self._show_more_history),
                ).pack(side="left", padx=(10, 0))

    # ---- history: Today / All, and re-checking the record against YouTube ----

    def _on_history_view_changed(self, value: str) -> None:
        self._history_view = value
        self._history_limit = HISTORY_PAGE_SIZE
        self._render_history()

    def _show_more_history(self) -> None:
        self._history_limit += HISTORY_PAGE_SIZE
        self._render_history()

    def _set_history_status(self, text: str, color: str = "#888888") -> None:
        if not self.winfo_exists():
            return
        if text:
            self.history_status_label.configure(text=text, text_color=color)
            if not self.history_status_label.winfo_ismapped():
                self.history_status_label.pack(fill="x", anchor="w", pady=(4, 0), before=self.history_list)
        else:
            self.history_status_label.pack_forget()

    def _verify_all_records(self) -> None:
        """HISTORY -> REFRESH: re-checks EVERY recorded upload against YouTube (not just the
        rows on screen). Confirms each video is still on the channel, brings its scheduled time
        in step with the real schedule, and flags anything missing or failed -- see
        youtube/record_checker.py. A video that was verified at upload time can have been
        deleted or moved since; this is what finds out."""
        self._run_record_check(list(self._activity_log.entries))

    def _recheck_entry(self, entry: UploadActivityEntry) -> None:
        self._run_record_check([entry])

    def _run_record_check(self, entries: list) -> None:
        if self._record_check_running:
            return
        client = self.state.client
        if client is None:
            self._set_history_status("Not connected to YouTube.", BAD)
            return
        if not entries:
            self._set_history_status("Nothing has been uploaded yet, so there is nothing to check.", "#888888")
            return
        self._record_check_running = True
        self.history_refresh_btn.configure(state="disabled", text="Checking…")
        self._set_history_status(f"Checking {len(entries)} upload{'s' if len(entries) != 1 else ''} on YouTube…", WARN)
        self.state.set_last_action("Re-checking the upload record against YouTube")
        skip = set(self._verifying_entry_ids)
        if self._active_upload_entry_id:
            skip.add(self._active_upload_entry_id)

        def worker():
            plan, summary = record_checker.check_records(client, entries, skip_ids=skip)
            self._safe_after(lambda: self._on_records_checked(plan, summary))

        def failed(e: BaseException) -> None:
            summary = record_checker.RecordCheckSummary(error=classify_exception(e).message())
            self._safe_after(lambda: self._on_records_checked([], summary))

        self._start_worker(worker, on_error=failed)

    def _on_records_checked(self, plan: list, summary) -> None:
        self._record_check_running = False
        if not self.winfo_exists():
            return
        self.history_refresh_btn.configure(state="normal", text="↻ Refresh")
        if plan:
            self._activity_log.update_many(plan)
            for entry, fields in plan:
                # An upload found on the channel by title (it had no video ID): make sure the
                # watched folder can't offer that file as "New" again.
                if fields.get("video_id") and entry.q_number and not self._registry.is_uploaded(entry.q_number):
                    self._registry.mark_uploaded(entry.q_number, fields["video_id"])
            if any(fields.get("video_id") for _entry, fields in plan):
                self._scan_and_display_folder()
        stamp = dt.datetime.now().strftime("%H:%M")
        text = f"{summary.text()}   (checked {stamp})"
        if summary.error or summary.missing or summary.failed:
            color = BAD
        elif summary.problems or summary.unmatched:
            color = WARN
        else:
            color = GOOD
        self._last_record_check = text
        self._set_history_status(text, color)
        if summary.problems:
            self._last_record_check += "\n" + "\n".join(summary.problems)
        self._render_history()
        self._update_diagnostics()

    def _copy_history_error(self, error: str) -> None:
        self.clipboard_clear()
        self.clipboard_append(error)

    def _copy_upload_report(self, entry: UploadActivityEntry) -> None:
        """COPY UPLOAD REPORT: this ONE entry's technical report -- never any other history
        entry (see UploadActivityEntry.format_report())."""
        self.clipboard_clear()
        self.clipboard_append(entry.format_report())

    # ---- developer diagnostics ----

    def _sync_diagnostics_visibility(self) -> None:
        if not self.winfo_exists():
            return
        if self.state.config.developer_mode:
            self.diagnostics_frame.pack(fill="x", padx=12, pady=(0, 10), before=self.history_wrap)
            self._update_diagnostics()
        else:
            self.diagnostics_frame.pack_forget()

    def _copy_last_upload_error(self) -> None:
        if not self._last_upload_error:
            return
        self.clipboard_clear()
        self.clipboard_append(self._last_upload_error)

    def _latest_activity_entry(self) -> Optional[UploadActivityEntry]:
        return max(self._activity_log.entries, key=lambda e: e.uploaded_at, default=None)

    def _copy_latest_upload_report(self) -> None:
        latest = self._latest_activity_entry()
        if latest is not None:
            self._copy_upload_report(latest)

    def _update_diagnostics(self) -> None:
        if not self.state.config.developer_mode or not self.diagnostics_frame.winfo_exists():
            return
        folder = self.state.config.shorts_folder_path
        lines = [
            f"Selected folder: {folder or '(none)'}",
            f"Folder selection: {'OK' if folder and not self._folder_scan_error else ('FAILED — ' + self._folder_scan_error if self._folder_scan_error else 'not selected')}",
            f"Videos found: {len(self._folder_videos)}",
            f"Most recent file: {self._most_recent_result.filename if self._most_recent_result else '(none)'}",
            f"Detection window: {self.state.config.new_video_detection_window_seconds}s",
        ]
        if self._most_recent_result:
            lines.append(f"  created: {self._most_recent_result.created_at.strftime('%Y-%m-%d %H:%M:%S')}")
        selected_desc = os.path.basename(self._selected.file_path) if self._selected else "(none)"
        lines.append(f"Currently selected video: {selected_desc}")
        pos_date = self.position_date_label.cget("text") if hasattr(self, "position_date_label") else "—"
        pos_time = self.position_time_label.cget("text") if hasattr(self, "position_time_label") else ""
        lines.append(f"Calculated next schedule: {pos_date} {pos_time}".strip())
        lines.append(f"Last upload error: {self._last_upload_error or '(none)'}")
        if self._last_upload_attempts:
            lines.append(f"Last upload attempts: {self._last_upload_attempts}")
        if self._last_quality_check:
            lines.append(f"Last upload quality check: {self._last_quality_check}")
        if self._last_record_check:
            lines.append(f"Last record re-check: {self._last_record_check}")
        latest = self._latest_activity_entry()
        if latest is not None:
            # HIST-001 upload verification -- everything needed to diagnose an upload that
            # didn't reach YouTube, straight from the persisted history entry.
            lines += [
                f"Latest upload (HIST-001): {latest.filename} -- current state {latest.display_status}",
                f"  YouTube video ID: {latest.video_id or '(not available)'}",
                f"  Upload timestamp: {latest.uploaded_at}",
                f"  Verification attempt: {latest.verification_attempts}   Last verification attempt: {latest.last_verification_at or '(none yet)'}",
                f"  Verification result / API response: {latest.verification_detail or '(none)'}",
            ]
        self.diagnostics_label.configure(text="\n".join(lines))
        self.diag_copy_error_btn.configure(state="normal" if self._last_upload_error else "disabled")
        self.diag_copy_report_btn.configure(state="normal" if latest is not None else "disabled")

    # ---- upload (one video, never a batch) ----

    def _quick_upload(self) -> None:
        """"Upload" always means: take this one video and put it into the next available
        position under the current rule (the schedule's furthest occupied date) -- read
        fresh at the moment this button is clicked, after recalibrating against the real
        YouTube schedule. It never appends to the end blindly, never searches for the
        first empty date, and there is never more than one video active on this page at a
        time.

        AUTOMATIC RETRY: if the upload call fails (e.g. a transient network/SSL error --
        `[SSL: WRONG_VERSION_NUMBER]` is a real one seen in practice, and simply trying
        again by hand often succeeds), this automatically waits a short moment and tries
        the exact same upload once more, with no popup and no need to click Upload again --
        see the retry loop inside worker() below. After EVERY failed attempt (including the
        last), it checks whether that attempt actually went through despite raising
        (YouTubeClient.find_recent_upload_by_title()) so a lost response can never turn
        into a duplicate video on the channel -- if the video is there, it's adopted into
        this history entry and verified instead. Only if the attempts genuinely fail and
        YouTube doesn't have the video does this show the (still compact) error dialog,
        with a Retry button alongside Copy Error -- see _upload_failed().

        A returned upload is only the START of the story: see _complete_upload_request()
        and _start_verification() for how it becomes VERIFIED (or VERIFICATION FAILED)."""
        selected = self._selected
        if selected is None or self._uploading:
            return
        if not selected.title.strip() or not selected.description.strip():
            messagebox.showerror("Missing title/description", "Add a title and description for this video first.")
            return
        if len(selected.title) > MAX_TITLE_LEN:
            messagebox.showerror(
                "Title too long",
                f"This title is {len(selected.title)} characters -- YouTube allows at most {MAX_TITLE_LEN}. "
                f"Shorten it and try again.",
            )
            return
        if len(selected.description) > MAX_DESCRIPTION_LEN:
            messagebox.showerror(
                "Description too long",
                f"This description is {len(selected.description)} characters -- YouTube allows at most {MAX_DESCRIPTION_LEN}. "
                f"Shorten it and try again.",
            )
            return
        if not self.state.client or not self.state.repo:
            messagebox.showerror("Not connected", "Not connected to YouTube.")
            return
        if not os.path.isfile(selected.file_path):
            messagebox.showerror(
                "Video file not found",
                f"{os.path.basename(selected.file_path)} is no longer at:\n{selected.file_path}\n\n"
                "It may have been moved, renamed or deleted. Select it again to continue.",
            )
            return

        selected.status = "uploading"
        self._uploading = True
        self._lock_ui(True)
        playlists = list(self._playlists)
        filename = os.path.basename(selected.file_path)
        pre_upload_tech_info = self._tech_info  # snapshot now -- Upload Quality Protection's post-upload check compares against this, not whatever's on screen once the selection is later cleared
        self.state.set_last_action(f"Uploading {filename}")

        # HIST-001: this upload's history entry exists from the moment it starts (UPLOADING),
        # so it survives a crash/restart and can never be recorded twice -- every later
        # stage (request complete, verifying, verified/failed) updates THIS entry in place.
        entry = self._activity_log.record(UploadActivityEntry(
            filename=filename, uploaded_at=dt.datetime.now(dt.timezone.utc).isoformat(), status=STATUS_UPLOADING,
            title=selected.title, q_number=selected.q_number, attempts=0,
        ))
        self._active_upload_entry_id = entry.entry_id
        self._render_history()
        other_video_ids = self._activity_log.known_video_ids()  # videos already accounted for by OTHER entries -- never to be mistaken for this upload
        client = self.state.client

        def on_pct(p: float) -> None:
            self._safe_after(lambda: self.progress_bar.set(p) if self.winfo_exists() else None)

        def worker():
            self.state.current_upload_filename = filename
            video = None
            slot = None
            attempts = 0
            try:
                try:
                    self.state.repo.refresh()
                except Exception:
                    pass
                existing = list(self.state.repo.videos) if self.state.repo else []
                rate = self._current_daily_rate()
                slot = find_upload_slots_at_rate(existing, 1, rate)[0]
                self._safe_after(lambda: self._activity_update(entry, scheduled_at=slot.isoformat()))

                last_error_message = ""
                while attempts < UPLOAD_MAX_ATTEMPTS:
                    attempts += 1
                    self.state.current_upload_attempt = attempts
                    self._safe_after(lambda a=attempts: self._on_upload_attempt_started(entry, filename, a))
                    try:
                        video = client.upload_video(
                            selected.file_path, selected.title, selected.description, selected.made_for_kids, slot,
                            progress_callback=on_pct,
                        )
                        break
                    except Exception as e:
                        info = classify_exception(e)
                        last_error_message = info.message()  # friendly guidance where there is some, the real error always kept
                        if not info.retry_can_help:
                            # An expired sign-in, an exhausted quota or a missing file
                            # cannot be fixed by trying the same request again -- and for an
                            # expired sign-in the duplicate check below would fail too. Stop
                            # now with a message the user can act on.
                            break
                        # DUPLICATE PROTECTION: an upload call that raised is NOT proof
                        # nothing reached YouTube -- e.g. the connection dropped while
                        # reading the response to an insert the server had already
                        # accepted. So after a short pause (letting YouTube register it),
                        # look for the video before anything else happens: if it's there,
                        # attach that real video to this history entry and go verify it;
                        # never upload a second copy. Only when it genuinely isn't there
                        # does the next attempt (or the final failure) proceed. "Not
                        # found" is only inconclusive, but it's the best signal the
                        # YouTube Data API offers -- see find_recent_upload_by_title().
                        time.sleep(UPLOAD_RETRY_DELAY_SECONDS)
                        try:
                            video = client.find_recent_upload_by_title(
                                selected.title, exclude_ids=other_video_ids,
                                expected_publish_at=slot, require_publish_match=True,  # a video this attempt created MUST carry the slot we asked for -- a same-titled video at another time is a different upload
                            )
                        except Exception:
                            video = None
                        if video is not None:
                            break

                if video is None:
                    selected.status = "failed"
                    selected.error = last_error_message
                    self._safe_after(lambda: self._upload_failed(entry, selected, last_error_message, attempts))
                    return

                # Verify YouTube actually confirmed the slot we asked for -- video.publish_at
                # is parsed straight from the insert response, not an echo of our request, so
                # a mismatch here is real and must not be reported as a plain success.
                if video.publish_at is None or video.publish_at.astimezone() != slot.astimezone():
                    self._safe_after(lambda: self._upload_mismatch(entry, selected, video, slot, attempts))
                    return

                if selected.playlist_name and selected.playlist_name != NO_PLAYLIST:
                    playlist_id = next((p.playlist_id for p in playlists if p.title == selected.playlist_name), None)
                    if playlist_id:
                        try:
                            client.add_video_to_playlist(video.video_id, playlist_id)
                        except Exception:
                            pass
                selected.status = "done"
                self._safe_after(lambda: self._upload_done(entry, selected, video, slot, attempts, pre_upload_tech_info))
            except Exception as e:
                error_message = str(e)
                if video is not None:
                    # The video already exists on YouTube -- something later in this
                    # worker broke. It must NOT be reported as a failed upload (a retry
                    # would create a duplicate); hand it to verification like any other.
                    selected.status = "done"
                    self._safe_after(lambda: self._upload_done(entry, selected, video, slot, attempts, pre_upload_tech_info))
                    return
                selected.status = "failed"
                selected.error = error_message
                self._safe_after(lambda: self._upload_failed(entry, selected, error_message, max(attempts, 1)))
            finally:
                self.state.current_upload_filename = ""
                self.state.current_upload_attempt = 0

        self._start_worker(worker)

    def _safe_after(self, fn) -> None:
        """Marshals `fn` onto the Tk main thread from a worker. If the app is already
        shutting down there's nothing left to update, so that failure is deliberately ignored."""
        safe_after(self, fn)

    def _activity_update(self, entry: UploadActivityEntry, **fields) -> None:
        if not self.winfo_exists():
            return
        self._activity_log.update(entry, **fields)
        self._render_history()

    def _on_upload_attempt_started(self, entry: UploadActivityEntry, filename: str, attempt: int) -> None:
        if not self.winfo_exists():
            return
        self.state.set_last_action(f"Uploading {filename} (attempt {attempt}/{UPLOAD_MAX_ATTEMPTS})")
        self._activity_log.update(entry, attempts=attempt)

    def _finish_upload_common(self, selected: SelectedVideo, video_id: str) -> str:
        """Common cleanup once a video genuinely exists on the channel (success or
        verification mismatch -- either way it's real and must not be re-uploaded): mark
        its q-number uploaded so it can never resurface as new, clear the selection, and
        let other pages know. Returns the video's order code ('' if it has none)."""
        self._uploading = False
        self._active_upload_entry_id = ""
        self._lock_ui(False)
        code = self._record_queue_position(video_id, selected.title, selected.description)
        if selected.q_number:
            self._registry.mark_uploaded(selected.q_number, video_id)
        if self._selected is selected:
            self._selected = None
            self.detail_frame.pack_forget()
            self.empty_label.pack(pady=24, before=self.history_wrap)
            self._refresh_quick_buttons()
        if self.state.repo:
            try:
                self.state.repo.refresh_ids([video_id])
            except Exception:
                pass
        self.state.notify_videos_changed()
        self._scan_and_display_folder()  # picks up the freshly-uploaded status from the registry
        self._refresh_order_code_label()  # the next upload is now the one after this
        return code

    @staticmethod
    def _record_queue_position(video_id: str, title: str, description: str = "") -> str:
        """A new upload goes to the END of the permanent queue order (data/queue_order.py), so its place never
        depends on the date it was given, and it gets the next CODE (0232, 0233...), which is kept with its title and
        description in the ledger and in the upload History. The code stays inside the app: nothing about it is sent to
        YouTube. Returns the code ('' until an order has been recorded); never lets a problem with the ledger interfere
        with the upload itself."""
        try:
            rec = QueueOrderStore.shared().append(
                video_id, title=title, evidence="added to the end of the queue when it was uploaded", description=description,
            )
            return rec.code if rec is not None else ""
        except Exception as e:
            diagnostics.report_recovered("Recording the new upload's place in the queue order", e, page="Upload")
            return ""

    def _complete_upload_request(
        self, entry: UploadActivityEntry, selected: SelectedVideo, video, scheduled_at: Optional[dt.datetime], attempts: int,
    ) -> None:
        """UPLOAD REQUEST COMPLETE: YouTube's API returned a video (or, after an
        apparently-failed attempt, the video was found already on the channel). This is
        deliberately NOT "success" -- the entry moves to the request-complete stage with
        its video ID recorded, and _start_verification() then confirms it with YouTube."""
        order_code = self._finish_upload_common(selected, video.video_id)
        # "Scheduled" vs "Published" is decided once, right now, by comparing the confirmed
        # publish time against the current moment -- never recomputed later, so an entry
        # doesn't flip labels retroactively once its scheduled time has since passed.
        label = "Scheduled"
        if scheduled_at is not None and scheduled_at.astimezone() <= dt.datetime.now(dt.timezone.utc):
            label = "Published"
        self._activity_log.update(
            entry,
            status=STATUS_UPLOAD_COMPLETE,
            video_id=video.video_id,
            uploaded_at=dt.datetime.now(dt.timezone.utc).isoformat(),  # "Uploaded to YouTube": when the API upload actually occurred
            scheduled_at=scheduled_at.isoformat() if scheduled_at else entry.scheduled_at,  # "Scheduled for": when YouTube will publish it
            schedule_label=label,
            attempts=attempts,
            error="",
            **({"order_code": order_code} if order_code else {}),
        )
        self._render_history()

    def _upload_done(
        self, entry: UploadActivityEntry, selected: SelectedVideo, video, slot: Optional[dt.datetime], attempts: int = 1,
        pre_upload_tech_info: Optional[VideoTechInfo] = None,
    ) -> None:
        self._last_upload_error = ""
        self._last_upload_attempts = attempts
        self._last_quality_check = ""
        self._complete_upload_request(entry, selected, video, slot or video.publish_at, attempts)
        self._update_diagnostics()
        self._verify_upload_quality(video.video_id, pre_upload_tech_info)
        self._start_verification(entry)

    # ---- HIST-001: verify the upload actually reached YouTube ----

    def _start_verification(
        self, entry: UploadActivityEntry, *, initial_delay: Optional[float] = None, show_dialog: bool = True,
        on_finished: Optional[Callable[[], None]] = None,
    ) -> None:
        """VERIFYING ON YOUTUBE: checks with YouTube itself (see youtube/upload_verifier.py)
        that this entry's video really exists on the channel, retrying a limited number of
        times while YouTube registers it. Runs entirely in the background -- the page stays
        usable, and every attempt is persisted as it happens so an app restart can resume.
        This NEVER uploads anything: a verification problem can't turn into a duplicate."""
        client = self.state.client
        if client is None or entry.entry_id in self._verifying_entry_ids:
            if on_finished is not None:
                on_finished()
            return
        self._verifying_entry_ids.add(entry.entry_id)
        self._activity_log.update(entry, status=STATUS_VERIFYING)
        self._render_history()
        self._update_diagnostics()

        title = entry.title
        video_id = entry.video_id
        offset = entry.verification_attempts
        exclude_ids = self._activity_log.known_video_ids() - {video_id}
        try:
            expected_publish_at = dt.datetime.fromisoformat(entry.scheduled_at) if entry.scheduled_at else None
        except ValueError:
            expected_publish_at = None

        def on_attempt(number: int, detail: str, found_video_id: str) -> None:
            self._safe_after(lambda: self._on_verification_attempt(entry, number, detail, found_video_id))

        def worker():
            try:
                outcome = upload_verifier.run_verification(
                    client, video_id=video_id, title=title, exclude_ids=exclude_ids, expected_publish_at=expected_publish_at,
                    attempt_offset=offset, initial_delay=initial_delay, on_attempt=on_attempt,
                )
            except Exception as e:  # a bug in verification itself must still end in a visible, honest state
                outcome = upload_verifier.VerificationOutcome(
                    False, f"Verification itself failed unexpectedly: {type(e).__name__}: {e}", video_id, 0, offset,
                )
            self._safe_after(lambda: self._on_verification_done(entry, outcome, show_dialog, on_finished))

        self._start_worker(worker)

    def _on_verification_attempt(self, entry: UploadActivityEntry, number: int, detail: str, found_video_id: str) -> None:
        if not self.winfo_exists():
            return
        fields = {
            "verification_attempts": number,
            "last_verification_at": dt.datetime.now(dt.timezone.utc).isoformat(),
            "verification_detail": detail,
        }
        if found_video_id and not entry.video_id:
            fields["video_id"] = found_video_id  # found on YouTube by title -- attach the existing video
            code = self._record_queue_position(found_video_id, entry.title)
            if code:
                fields["order_code"] = code
        self._activity_log.update(entry, **fields)
        self._update_diagnostics()

    def _on_verification_done(
        self, entry: UploadActivityEntry, outcome, show_dialog: bool, on_finished: Optional[Callable[[], None]] = None,
    ) -> None:
        self._verifying_entry_ids.discard(entry.entry_id)
        if not self.winfo_exists():
            return
        had_video_id = bool(entry.video_id)
        fields = {
            "verification_detail": outcome.detail,
            "verification_attempts": outcome.last_attempt_number,
        }
        if outcome.video_id:
            fields["video_id"] = outcome.video_id
            if not had_video_id and not entry.order_code:
                code = self._record_queue_position(outcome.video_id, entry.title)
                if code:
                    fields["order_code"] = code
        if outcome.verified:
            fields["status"] = STATUS_VERIFIED
            fields["error"] = ""
            self._activity_log.update(entry, **fields)
            if not had_video_id and entry.q_number and entry.video_id and not self._registry.is_uploaded(entry.q_number):
                # Found on YouTube after an interrupted/apparently-failed upload: the file
                # DID go up, so make sure it can't resurface as "New" and get uploaded again.
                # (Never overwrites a q-number the registry already has as uploaded.)
                self._registry.mark_uploaded(entry.q_number, entry.video_id)
                self._scan_and_display_folder()
            self._render_history()
            self._update_diagnostics()
            if on_finished is not None:
                on_finished()
            return

        fields["status"] = STATUS_VERIFICATION_FAILED
        self._activity_log.update(entry, **fields)
        self._last_upload_error = f"Upload could not be verified on YouTube. {outcome.detail}"
        self._render_history()
        self._update_diagnostics()
        report_error(
            self, self.state, "0.9.6.21", f'Verifying "{entry.title}" on YouTube',
            "Upload could not be verified on YouTube.",
            details=entry.format_report(),
            operation=f"One Upload Page -- verifying {entry.filename} (attempt {entry.verification_attempts})",
            show_dialog=show_dialog,
            on_retry=lambda: self._retry_verification(entry, show_dialog=True),
            retry_label="Retry Verification",
        )
        if on_finished is not None:
            on_finished()

    def _retry_verification(self, entry: UploadActivityEntry, *, show_dialog: bool = True) -> None:
        """RETRY VERIFICATION: only ever re-checks YouTube -- it never re-uploads, so it
        can't create a duplicate Short. (If the video genuinely isn't there, the file is
        still un-marked as uploaded, so it can be uploaded again deliberately.)"""
        if entry.status != STATUS_VERIFICATION_FAILED or entry.entry_id in self._verifying_entry_ids:
            return
        self._start_verification(entry, initial_delay=0, show_dialog=show_dialog)

    def _resume_pending_verifications(self) -> None:
        """VERIFICATION SURVIVES A RESTART: an upload whose verification never finished
        (the app was closed first) is still UPLOADING/VERIFYING on disk -- pick those up
        again now. An entry left in UPLOADING has no video ID (the upload was cut off, so
        its response never arrived) but the file may still have reached YouTube, so it's
        looked up by title rather than assumed lost -- and only ever verified, never
        re-uploaded automatically.

        Today's LEGACY entries (recorded as plain "done" before verification existed, so
        never actually confirmed) are queued too, so the STATUS column becomes truthful for
        them instead of leaving them UNVERIFIED. They have no video ID, so they're matched by
        title -- which is why everything here runs ONE AT A TIME: each match is attached to its
        entry before the next lookup, so two same-titled uploads can never both claim one video.

        Failures found here are shown in the history row without a dialog, so several at
        start-up can't stack modal windows."""
        if not self.winfo_exists() or self.state.client is None:
            return
        queued = {e.entry_id for e in self._resume_queue}
        candidates = list(self._activity_log.pending_verification())
        candidates += [e for e in self._activity_log.today_entries() if e.status == STATUS_DONE][:MAX_LEGACY_VERIFICATIONS]
        for entry in candidates:
            if (
                entry.entry_id in self._verifying_entry_ids or entry.entry_id == self._active_upload_entry_id
                or entry.entry_id in queued
            ):
                continue
            self._resume_queue.append(entry)
            queued.add(entry.entry_id)
        self._resume_next()

    def _resume_next(self) -> None:
        if not self.winfo_exists():
            return
        while self._resume_queue:
            entry = self._resume_queue.pop(0)
            if entry.entry_id in self._verifying_entry_ids or entry.entry_id == self._active_upload_entry_id:
                continue
            if entry.status == STATUS_UPLOADING:
                self._activity_log.update(
                    entry, error="Interrupted: the app was closed while this upload was in progress.",
                    verification_detail="Checking whether YouTube received it before the app closed.",
                )
            elif entry.status == STATUS_DONE:
                self._activity_log.update(
                    entry, verification_detail="Recorded before upload verification existed -- checking YouTube for it now.",
                )
            self._start_verification(entry, initial_delay=0, show_dialog=False, on_finished=self._resume_next)
            return

    def _verify_upload_quality(self, video_id: str, pre_upload_tech_info: Optional[VideoTechInfo]) -> None:
        """UPLOAD QUALITY PROTECTION: an independent, best-effort confirmation that what
        YouTube recorded receiving (the Data API's fileDetails part) matches this page's
        own pre-upload reading of the local file -- i.e. that nothing was altered in
        transit. Purely informational (surfaced in Developer Diagnostics only, never a
        popup); never blocks, retries the upload itself, or affects whether the upload
        counts as successful. fileDetails can take a short while to become available
        right after a brand-new upload, so this polls a few times before giving up quietly."""
        if pre_upload_tech_info is None or not self.state.client:
            self._last_quality_check = "Not checked (technical info wasn't available before this upload)."
            self._update_diagnostics()
            return

        client = self.state.client

        def worker():
            remote = None
            for _ in range(UPLOAD_VERIFY_MAX_ATTEMPTS):
                try:
                    raw = client.get_file_details(video_id)
                except Exception:
                    raw = None
                remote = from_youtube_file_details(pre_upload_tech_info.filename, raw)
                if remote is not None:
                    break
                time.sleep(UPLOAD_VERIFY_DELAY_SECONDS)
            self._safe_after(lambda: self._on_quality_check_done(remote, pre_upload_tech_info))

        self._start_worker(worker)

    def _on_quality_check_done(self, remote: Optional[VideoTechInfo], pre_upload_tech_info: VideoTechInfo) -> None:
        if not self.winfo_exists():
            return
        if remote is None:
            self._last_quality_check = "YouTube hadn't finished registering the file's details yet -- not verified this time."
        else:
            matches, diffs = compare_tech_info(pre_upload_tech_info, remote)
            if matches:
                self._last_quality_check = (
                    f"Matches source file -- YouTube received {remote.width}x{remote.height} "
                    f"{remote.video_codec or 'video'}, {remote.audio_codec or 'audio'} audio, unchanged."
                )
            else:
                self._last_quality_check = "MISMATCH -- " + "; ".join(diffs)
        self._update_diagnostics()

    def _upload_mismatch(
        self, entry: UploadActivityEntry, selected: SelectedVideo, video, slot: dt.datetime, attempts: int = 1,
    ) -> None:
        self._complete_upload_request(entry, selected, video, video.publish_at, attempts)
        expected = f"{slot.astimezone().strftime('%d %b %Y')}, {slot.astimezone().strftime('%I:%M %p').lstrip('0')}"
        actual = (
            f"{video.publish_at.astimezone().strftime('%d %b %Y')}, {video.publish_at.astimezone().strftime('%I:%M %p').lstrip('0')}"
            if video.publish_at else "no scheduled time at all"
        )
        self._last_upload_error = (
            f"Verification mismatch: YouTube confirmed {actual} instead of the requested {expected}."
        )
        self._last_upload_attempts = attempts
        self._update_diagnostics()
        self._start_verification(entry)  # the video exists (it has an ID) -- whether YouTube really registered it is still verified like any other upload
        report_error(
            self, self.state, "0.9.6.3", f'Verifying "{selected.title}" after upload',
            f"The video uploaded, but YouTube confirmed {actual} instead of the requested {expected}. "
            f"Check it in Push or Calendar -- it was not scheduled where One Upload Page expected.",
            operation=f"One Upload Page -- verifying the upload for {os.path.basename(selected.file_path)}",
        )

    def _upload_failed(self, entry: UploadActivityEntry, selected: SelectedVideo, error: str, attempts: int = 1) -> None:
        """Reached only once every automatic attempt (see UPLOAD_MAX_ATTEMPTS) has
        genuinely failed AND YouTube doesn't already have the video (see the duplicate
        protection in _quick_upload's worker) -- the video and its typed title/description
        are deliberately left exactly as they were (never cleared/discarded) so Upload can
        simply be pressed again, and the error dialog's Retry button does exactly that."""
        self._uploading = False
        self._active_upload_entry_id = ""
        self._lock_ui(False)
        self._last_upload_error = error
        self._last_upload_attempts = attempts
        self._activity_log.update(
            entry, status=STATUS_FAILED, error=error, attempts=attempts,
            uploaded_at=dt.datetime.now(dt.timezone.utc).isoformat(),
        )
        self._update_diagnostics()
        self._render_history()
        attempt_note = f" (failed after {attempts} attempt{'s' if attempts != 1 else ''})" if attempts > 1 else ""
        report_error(
            self, self.state, "0.9.6.3", f'Uploading "{selected.title}"', error,
            details=entry.format_report(),
            operation=f"One Upload Page -- uploading {os.path.basename(selected.file_path)}{attempt_note}",
            on_retry=self._quick_upload,
        )

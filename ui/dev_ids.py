"""Canonical, hand-assigned hierarchical IDs for every page/section/component in the
app -- lets the user give precise feedback ("0.1.3.2 isn't working") instead of vague
descriptions. IDs are assigned once here and never renumbered or reused; adding a new
page/section/component only ever appends a new ID at the end of its level.

Purely a lookup table read by Developer Mode UI -- has no effect on app behaviour.
"""
from __future__ import annotations

from dataclasses import dataclass

KIND_PAGE = "page"
KIND_SECTION = "section"
KIND_COMPONENT = "component"


@dataclass(frozen=True)
class DevNode:
    id: str
    name: str
    kind: str
    short_code: str = ""  # human-friendly ID (e.g. "AS-001") for Developer Tools' copy formats; most leaf nodes don't need one
    description: str = ""  # the "Function:" line in a copied reference; falls back to the node's page if empty


REGISTRY: dict[str, DevNode] = {}


def _add(id: str, name: str, kind: str, short_code: str = "", description: str = "") -> None:
    REGISTRY[id] = DevNode(id=id, name=name, kind=kind, short_code=short_code, description=description)


def label(dev_id: str) -> str:
    node = REGISTRY.get(dev_id)
    if not node:
        return dev_id
    shown_id = node.short_code or node.id
    return f"{shown_id} — {node.name}"


def page_id_of(dev_id: str) -> str:
    parts = dev_id.split(".")
    return ".".join(parts[:2]) if len(parts) >= 2 else dev_id


# ---- 0.1 Schedule (retired again -- the 3-tab navigation (Upload / Auto Schedule /
# Settings) dropped this tab; HIT and Reset Schedule had no replacement home) ----
_add("0.1", "Schedule (retired, page removed)", KIND_PAGE)
_add("0.1.1", "Video List", KIND_SECTION)
_add("0.1.1.1", "Video Row", KIND_COMPONENT)
_add("0.1.1.2", "Date Group Indicator", KIND_COMPONENT)
_add("0.1.1.3", "Date Divider", KIND_COMPONENT)
_add("0.1.2", "Selection Controls", KIND_SECTION)
_add("0.1.2.1", "Select All", KIND_COMPONENT)
_add("0.1.2.2", "Ctrl Selection", KIND_COMPONENT)
_add("0.1.2.3", "Shift Range Selection", KIND_COMPONENT)
_add("0.1.2.4", "Drag Selection", KIND_COMPONENT)
_add("0.1.2.5", "Fill Range", KIND_COMPONENT)
_add("0.1.2.6", "Clear Selection", KIND_COMPONENT)
_add("0.1.3", "Zoom Controls", KIND_SECTION)
_add("0.1.3.1", "Zoom Out Button", KIND_COMPONENT)
_add("0.1.3.2", "Zoom In Button", KIND_COMPONENT)
_add("0.1.3.3", "Zoom Slider", KIND_COMPONENT)
_add("0.1.3.4", "Zoom Level Label", KIND_COMPONENT)
_add("0.1.4", "Date Filters", KIND_SECTION)
_add("0.1.4.1", "Search Box", KIND_COMPONENT)
_add("0.1.4.2", "Sort Toggle", KIND_COMPONENT)
_add("0.1.4.3", "From Date Picker", KIND_COMPONENT)
_add("0.1.4.4", "To Date Picker", KIND_COMPONENT)
_add("0.1.4.5", "Select Range Button", KIND_COMPONENT)
_add("0.1.5", "Bookmark Controls", KIND_SECTION)
_add("0.1.5.1", "Bookmark Marker", KIND_COMPONENT)
_add("0.1.5.2", "Bookmarked Only Filter", KIND_COMPONENT)
_add("0.1.6", "Schedule Actions", KIND_SECTION)
_add("0.1.6.1", "Schedule Selected Videos Button", KIND_COMPONENT)
_add("0.1.6.2", "Find Schedule Gaps Button", KIND_COMPONENT)
_add("0.1.6.3", "Fill Schedule Gaps Button", KIND_COMPONENT)
_add("0.1.6.4", "Find & Fill Gaps Button", KIND_COMPONENT)

# ---- 0.2 Calendar (retired -- page removed) ----
_add("0.2", "Calendar (retired, page removed)", KIND_PAGE)
_add("0.2.1", "Calendar Navigation", KIND_SECTION)
_add("0.2.1.1", "Prev Button", KIND_COMPONENT)
_add("0.2.1.2", "Today Button", KIND_COMPONENT)
_add("0.2.1.3", "Next Button", KIND_COMPONENT)
_add("0.2.1.4", "Jump To Date Picker", KIND_COMPONENT)
_add("0.2.1.5", "Month/Year View Toggle", KIND_COMPONENT)
_add("0.2.2", "Month View", KIND_SECTION)
_add("0.2.2.1", "Day Cell", KIND_COMPONENT)
_add("0.2.3", "Date Selection", KIND_SECTION)
_add("0.2.3.1", "Day Click", KIND_COMPONENT)
_add("0.2.3.2", "Shift-Click Range", KIND_COMPONENT)
_add("0.2.3.3", "Select Videos From These Dates Button", KIND_COMPONENT)
_add("0.2.4", "Video Count", KIND_SECTION)
_add("0.2.4.1", "Day Video Count Label", KIND_COMPONENT)
_add("0.2.5", "Gap Detection", KIND_SECTION)
_add("0.2.5.1", "Find Gaps Button", KIND_COMPONENT)
_add("0.2.5.2", "Fill Gaps Button", KIND_COMPONENT)

# ---- 0.3 Groups (retired again -- dropped by the 3-tab navigation) ----
_add("0.3", "Groups (retired, page removed)", KIND_PAGE)
_add("0.3.1", "Saved Groups", KIND_SECTION)

# ---- 0.4 Scheduler (retired again -- dropped by the 3-tab navigation) ----
_add("0.4", "Scheduler (retired, page removed)", KIND_PAGE)
_add("0.4.1", "Single Rule Controls", KIND_SECTION)
_add("0.4.1.1", "Start Date Picker", KIND_COMPONENT)
_add("0.4.1.2", "Videos Per Day", KIND_COMPONENT)
_add("0.4.1.3", "Publishing Window", KIND_COMPONENT)
_add("0.4.1.4", "Automatically Optimise Toggle", KIND_COMPONENT)
_add("0.4.2", "Scheduling Sections", KIND_SECTION)
_add("0.4.2.1", "Use Sections Toggle", KIND_COMPONENT)
_add("0.4.2.2", "Add Section Button", KIND_COMPONENT)
_add("0.4.2.3", "Section Row", KIND_COMPONENT)
_add("0.4.2.4", "Continue Past Last Section Toggle", KIND_COMPONENT)
_add("0.4.3", "Generate Preview", KIND_SECTION)
_add("0.4.3.1", "Generate Preview Button", KIND_COMPONENT)

# ---- 0.5 History (retired -- dropped by the 3-tab navigation) ----
_add("0.5", "History (retired, page removed)", KIND_PAGE)
_add("0.5.1", "History List", KIND_SECTION)
_add("0.5.1.1", "History Entry", KIND_COMPONENT)
_add("0.5.1.2", "Undo Button", KIND_COMPONENT)

# ---- 0.6 Timeline (retired again -- dropped by the 3-tab navigation, chapter pins
# (data/chapter_pins.py) removed along with it) ----
_add("0.6", "Timeline (retired, page removed)", KIND_PAGE)
_add("0.6.1", "Timeline", KIND_SECTION)
_add("0.6.1.1", "Day Group", KIND_COMPONENT)
_add("0.6.2", "Range Selection", KIND_SECTION)
_add("0.6.2.1", "From Date", KIND_COMPONENT)
_add("0.6.2.2", "To Date", KIND_COMPONENT)
_add("0.6.2.3", "Select Videos In Range Button", KIND_COMPONENT)
_add("0.6.3", "Chapter Pin System", KIND_SECTION)
_add("0.6.3.1", "Chapter Pin Column", KIND_COMPONENT)  # was "Chapter Column"; same slot, redesigned as pins
_add("0.6.3.2", "Chapter Filter (retired)", KIND_COMPONENT)  # superseded by the pin system; no longer tagged
_add("0.6.3.3", "Book Chapter Button (retired)", KIND_COMPONENT)  # superseded by "+ Chapter Pin"; no longer tagged
_add("0.6.3.4", "Chapter Pin Marker", KIND_COMPONENT)  # the small numbered pin on a chapter's start row
_add("0.6.3.5", "Add Chapter Pin Button", KIND_COMPONENT)
_add("0.6.3.6", "Chapter Pin Action Menu", KIND_COMPONENT)
_add("0.6.4", "Quick-Access Scheduling Rule Bar", KIND_SECTION)

# ---- 0.7 Preview (retired again -- dropped by the 3-tab navigation; its Apply button
# had already been retired separately before the whole page went with it) ----
_add("0.7", "Preview (retired, page removed)", KIND_PAGE)
_add("0.7.1", "Schedule Table", KIND_SECTION)
_add("0.7.1.1", "Video Row", KIND_COMPONENT)
_add("0.7.2", "Apply Controls (retired, Apply removed)", KIND_SECTION)  # Preview is read-only now; only Discard remains, untagged
_add("0.7.2.1", "Apply Schedule Button (retired)", KIND_COMPONENT)

# ---- 0.8 Settings ----
_add("0.8", "Settings", KIND_PAGE, short_code="SET-001", description="Container for the app's YouTube connection, Application, and Developer Tools settings.")
_add("0.8.1", "YouTube Connection", KIND_SECTION, short_code="YT-001", description="YouTube account connection status, sign-out, and API quota usage.")
_add("0.8.1.1", "Sign Out Button", KIND_COMPONENT)
_add("0.8.2", "API Quota", KIND_SECTION)
_add("0.8.3", "Developer Mode (retired, moved to 0.15 Developer Tools)", KIND_SECTION)
_add("0.8.3.1", "Developer Mode Toggle (retired, moved to 0.15.1.1)", KIND_COMPONENT)

# ---- 0.9 Upload (consolidated into One Upload Page -- Basic Upload and Upload On
# Specific Day, formerly two switchable modes under this tab, are now the same single
# page: 0.9.7's whole block is retired, and its date-picker capability that briefly lived
# on inside 0.9.6 as a manual scheduling override was later removed too -- see 0.9.6.16) ----
_add("0.9", "Upload", KIND_PAGE, description="Adding new videos to the channel via One Upload Page -- always placed automatically, continuing from the schedule's furthest occupied date.")
_add("0.9.1", "Drop Video Box (retired, merged into One Upload Page)", KIND_SECTION)
_add("0.9.1.1", "Browse/Drop Target (retired, see 0.9.6.1)", KIND_COMPONENT)
_add("0.9.1.2", "Remove/Replace Video", KIND_COMPONENT)
_add("0.9.2", "Title & Description (retired, merged into One Upload Page)", KIND_SECTION)
_add("0.9.2.1", "Combined Title+Description Field (retired)", KIND_COMPONENT)
_add("0.9.2.2", "Paste Button (retired, see 0.9.6.18)", KIND_COMPONENT)
_add("0.9.2.3", "Description Field (retired)", KIND_COMPONENT)  # merged into 0.9.2.1; no longer tagged
_add("0.9.2.4", "Description Paste Button (retired)", KIND_COMPONENT)  # merged into 0.9.2.2; no longer tagged
_add("0.9.2.5", "Parsed Output Preview", KIND_COMPONENT)
_add("0.9.3", "Upload Settings", KIND_SECTION)
_add("0.9.3.1", "Made For Kids Setting", KIND_COMPONENT)
_add("0.9.3.2", "Playlist Selector", KIND_COMPONENT)
_add("0.9.4", "Automatic Scheduling Preview", KIND_SECTION)
_add("0.9.5", "Upload & Schedule Button", KIND_SECTION)
_add("0.9.5.1", "Upload Progress", KIND_COMPONENT)
_add("0.9.5.2", "Upload Result Panel", KIND_COMPONENT)
_add("0.9.6", "One Upload Page", KIND_SECTION, short_code="UPLOAD-001", description="The single upload page: detect or drag one video, paste its title/description, and schedule it automatically at Auto Schedule's current daily rate, continuing from the furthest occupied date.")
_add("0.9.6.1", "Drag Video Zone", KIND_COMPONENT)  # was "Card Drop Zone"; now the large right-hand drop target for a one-off video
_add("0.9.6.2", "Selected Video Panel", KIND_COMPONENT)  # was "Video Card"; same slot, now the single active-video detail panel rather than one of several simultaneous cards
_add("0.9.6.3", "Upload Button", KIND_COMPONENT, description="Uploads/schedules the currently selected video using whatever Title/Description are already in the fields -- never pastes the clipboard or modifies them itself. Was briefly merged into a combined 'Quick Paste + Upload' button; separated back out into its own independent action so Upload always means just Upload.")
_add("0.9.6.4", "Videos Per Day Selector (retired, see 0.9.6.27)", KIND_COMPONENT)  # "Separate Daily Upload Setting from Auto Schedule" spec -- this used to read/write AutoScheduleConfig.normal_videos_per_day directly (a shared binding), which is exactly what that spec removed. Relocated out of the per-selection detail panel into the always-visible fixed UPLOAD ACTIONS panel, and rewired to its own independent AppConfig.upload_videos_per_day.
_add("0.9.6.5", "Copy Title Button", KIND_COMPONENT)
_add("0.9.6.6", "Copy Description Button", KIND_COMPONENT)
_add("0.9.6.7", "Title Field", KIND_COMPONENT)
_add("0.9.6.8", "Description Field", KIND_COMPONENT)
_add("0.9.6.9", "Scheduled Position Indicator", KIND_COMPONENT, description="A large, prominent date/time display -- the single most visible piece of information on the page, showing exactly when the selected video will publish.")
_add("0.9.6.10", "Clear Selection Button", KIND_COMPONENT)  # was "Remove Video Button"; same slot, now clears the single active selection rather than removing one card from a list
_add("0.9.6.11", "Shorts Folder Status", KIND_COMPONENT, description="Confirms which folder is selected and how many videos were found there, or shows the real error if selection/scanning failed -- never silently pretends success.")
_add("0.9.6.12", "Select Folder Button", KIND_COMPONENT)  # was "Folder Settings Button"; same control, renamed to match the spec
_add("0.9.6.13", "New Videos Detected List (retired)", KIND_COMPONENT, description="Replaced by 0.9.6.24 Folder Video List -- shown immediately after a folder is selected (and refreshed by Most Recent File), not continuously monitored.")
_add("0.9.6.14", "New Video Row (retired, see 0.9.6.25)", KIND_COMPONENT)
_add("0.9.6.15", "Next New Video Button (retired)", KIND_COMPONENT)  # superseded by Most Recent File itself always picking the single newest recent file
_add("0.9.6.16", "Schedule Mode Toggle (retired)", KIND_COMPONENT)  # removed -- there is no manual scheduling option anymore; One Upload Page is always-automatic, continuing from the schedule's furthest occupied date
_add("0.9.6.17", "Select Date Button (retired)", KIND_COMPONENT)  # removed along with the manual mode it belonged to
_add("0.9.6.18", "Quick Paste (standalone button, also embedded in 0.9.6.19)", KIND_COMPONENT, description="Reads and parses the clipboard into Title/Description ONLY -- never finds/selects a video, never uploads or schedules. Has its own standalone button (leftmost of the three in the fixed action panel) so it can be triggered manually on whatever's already selected, independent of Most Recent File. Also runs as the second step inside the combined Most Recent File + Quick Paste button (0.9.6.19), and automatically (silently, on any failure) right after a video is dragged/browsed in -- see _quick_paste()'s silent=True mode.")
_add("0.9.6.19", "Most Recent File + Quick Paste (combined button)", KIND_COMPONENT, description="Was 'Check New Button'. Re-scans the selected folder and selects the newest not-yet-uploaded video within the configured detection window (Settings -> Application) -- never falls back to an older file -- then immediately Quick Pastes (0.9.6.18) onto it. Never uploads. Sits between the standalone Quick Paste button (0.9.6.18) and the independent Upload button (0.9.6.3) in the fixed action panel: [ Quick Paste ] [ Most Recent File + Quick Paste ] [ Upload ].")
_add("0.9.6.20", "Most Recent File Result Display", KIND_COMPONENT)  # was "Check New Result Display"; same slot, renamed to match
_add("0.9.6.21", "History Today Section", KIND_SECTION, short_code="HIST-001", description="Today's uploads made via One Upload Page, as a compact single-row-per-upload table (FILE | TITLE | DATE | TIME | HOW LONG AGO | STATUS, HOW LONG AGO live-updating every 30s; Today / All toggle and a Refresh button that re-checks the whole record against YouTube) -- the source of truth for whether each upload was actually CONFIRMED on YouTube (see 0.9.6.32). Persisted in data/upload_activity.py, separate from the Push/Calendar reschedule History.")
_add("0.9.6.22", "History Entry Row", KIND_COMPONENT, description="One row per upload -- filename, clean title (no hashtags/description), and scheduled date/time in aligned columns. Truncated with '...' rather than wrapping to a second line.")
_add("0.9.6.23", "History Entry Copy Error (retired as a button)", KIND_COMPONENT)  # a failed row's error message is now copied by clicking the row itself, not a separate visible button -- keeps every row to exactly one line
_add("0.9.6.24", "Folder Video List", KIND_SECTION, description="Every q-numbered video in the selected folder, sorted numerically (q9, q10, q11 -- never alphabetically), tagged New/Uploaded and timestamped. Populated on folder selection and refreshed by Most Recent File; never continuously monitored.")
_add("0.9.6.25", "Folder Video Row", KIND_COMPONENT, description="Click to select that video -- highlighted differently when it's the current selection vs. when it's the file Most Recent File just found.")
_add("0.9.6.26", "Developer Diagnostics Panel", KIND_SECTION, short_code="DIAG-001", description="Developer Mode only: selected folder path/state, scan results, detection window, currently selected video, calculated next schedule position, and the last upload error with Copy Error. Never fakes success -- every field reflects the last real scan/selection/calculation.")
_add("0.9.6.27", "How Many Videos Per Day Selector", KIND_COMPONENT, description="'Separate Daily Upload Setting from Auto Schedule' spec -- ONE UPLOAD PAGE's own daily capacity (1-6), always visible in the fixed UPLOAD ACTIONS panel regardless of whether a video is selected. Reads/writes AppConfig.upload_videos_per_day, deliberately never AutoScheduleConfig.normal_videos_per_day (0.11.22.2) -- the two must never be bound together, though they share the same slot-time presets via the same ui/widgets/slot_selector.py SlotCountControl component.")
_add("0.9.6.28", "Configure Slot Times Gear Icon", KIND_COMPONENT, description="Same shared gear-icon control as 0.11.22.4/0.13.5.1 -- toggles the collapsible preset editor (0.9.6.29) for whichever count is currently selected here.")
_add("0.9.6.29", "Upload Time Preset Fields (collapsible)", KIND_COMPONENT, description="Same shared collapsible panel as 0.11.22.3/0.13.5.2 -- editing and saving a preset here updates the same global SET-0012.1 preset used everywhere, even though the selected daily count itself stays local to this page (AppConfig.upload_videos_per_day).")
_add("0.9.6.30", "Quality Information Panel (collapsible)", KIND_SECTION, short_code="QUALITY-001", description="Collapsed by default -- just a compact 'QUALITY INFORMATION' header with an arrow on its right (▼ collapsed / ▲ expanded); clicking the header or the arrow toggles it, and the last choice is remembered (AppConfig.quality_info_expanded). Upload Quality Protection spec -- shown for the currently selected video, before it's uploaded: filename, file size, resolution, fps, video codec/bitrate, audio codec/bitrate, read directly off the local file via a bundled read-only ffprobe (youtube/video_tech_info.py's probe_local_file()). Also carries the standing note that this app never re-encodes/compresses/resizes a video before upload, and that YouTube accepting the upload isn't the same as YouTube finishing its own background processing. After a successful upload, a best-effort independent check (YouTubeClient.get_file_details(), the Data API's fileDetails part) compares what YouTube recorded receiving against this panel's own pre-upload reading -- surfaced in Developer Diagnostics (0.9.6.26) as 'Last upload quality check', never a popup.")
_add("0.9.6.31", "Auto Paste & Enter Button", KIND_COMPONENT, description="Pastes the current clipboard contents into whichever text field currently has keyboard focus, then sends Enter -- see ui/widgets/auto_paste_enter_button.py. Unlike Quick Paste (0.9.6.18), it never parses the clipboard into Title/Description and never triggers Quick Paste or Upload; it only acts on whatever field the user last clicked into, anywhere in the app. Does nothing if no text field currently has focus.")
_add("0.9.6.32", "History Upload Status (STATUS column)", KIND_COMPONENT, description="HIST-001's STATUS column -- whether YouTube actually confirmed this upload: UPLOADING -> VERIFYING -> VERIFIED, or VERIFICATION FAILED / UPLOAD FAILED (UNVERIFIED only for legacy entries recorded before verification existed). VERIFIED means YouTube itself confirmed it received and registered the video (exists, this channel, expected title, in the uploads playlist, healthy uploadStatus) -- it says nothing about the scheduled publish time, which is the DATE/TIME columns. See data/upload_activity.py and youtube/upload_verifier.py.")
_add("0.9.6.33", "Retry Verification Button", KIND_COMPONENT, description="Shown on a VERIFICATION FAILED history row (and in the error dialog). Only re-checks YouTube for the already-uploaded video -- it NEVER uploads again, so it cannot create a duplicate Short.")
_add("0.9.6.34", "Copy Upload Report Button (Developer Mode)", KIND_COMPONENT, description="Developer Mode only: copies the full technical report for THAT history entry alone -- local filename, YouTube video ID, upload timestamp, verification attempt count, last verification attempt, API response/error, verification result and current upload state. Never any other entry, and never credentials.")
_add("0.9.6.35", "History View Toggle (Today / All)", KIND_COMPONENT, description="Switches HIST-001 between today's uploads and the PERMANENT record of every upload (newest first, 40 at a time with Show more). The record is never deleted for being old.")
_add("0.9.6.36", "History Refresh Button", KIND_COMPONENT, description="Re-checks the WHOLE upload record against YouTube (a few batched read-only calls, 50 videos each): confirms each video is still on the channel, brings its recorded scheduled time in step with the real schedule, and flags anything missing (NOT ON YOUTUBE) or failed. An API/network error changes nothing. See youtube/record_checker.py.")
_add("0.9.6.37", "Re-check Button (NOT ON YOUTUBE rows)", KIND_COMPONENT, description="Re-checks that one entry against YouTube -- e.g. after restoring a deleted video.")
_add("0.9.7", "Upload On Specific Day (retired, merged into 0.9.6 One Upload Page)", KIND_SECTION, short_code="UPLOAD-002")  # the whole separate mode/page this used to be no longer exists, and its manual-date capability (0.9.6.16/0.9.6.17) was later removed too -- One Upload Page is purely automatic
_add("0.9.7.1", "Upload Button (retired)", KIND_COMPONENT)
_add("0.9.7.2", "Drop Zone (retired)", KIND_COMPONENT)
_add("0.9.7.3", "Paste Button (retired)", KIND_COMPONENT)
_add("0.9.7.4", "Date Picker (retired, see 0.9.6.17)", KIND_COMPONENT)
_add("0.9.7.5", "Copy Title Button (retired)", KIND_COMPONENT)
_add("0.9.7.6", "Copy Description Button (retired)", KIND_COMPONENT)

# ---- 0.10 Home (retired -- the whole page was discarded when the app reverted to the
# 7-tab Schedule/Timeline/Groups/Scheduler/Preview/History/Auto Schedule navigation; its
# HIT and Reset Schedule buttons live on 0.1 Schedule now, and Basic Upload/Upload On
# Specific Day moved to 0.9 Upload) ----
_add("0.10", "Home (retired, page removed)", KIND_PAGE)
_add("0.10.1", "HIT (retired)", KIND_SECTION)
_add("0.10.1.1", "HIT Button (retired)", KIND_COMPONENT)
_add("0.10.2", "Reset Schedule (retired)", KIND_SECTION)
_add("0.10.2.1", "Reset Schedule Button (retired)", KIND_COMPONENT)
_add("0.10.3", "Upload On Specific Day (retired, see 0.9.7)", KIND_SECTION)
_add("0.10.3.1", "Upload Button (retired)", KIND_COMPONENT)
_add("0.10.3.2", "Drop Zone (retired)", KIND_COMPONENT)
_add("0.10.3.3", "Paste Button (retired)", KIND_COMPONENT)
_add("0.10.3.4", "Date Picker (retired)", KIND_COMPONENT)
_add("0.10.4", "Basic Upload (retired, see 0.9)", KIND_SECTION)
_add("0.10.4.1", "Drop Zone (retired)", KIND_COMPONENT)
_add("0.10.4.2", "Item List (retired)", KIND_COMPONENT)
_add("0.10.4.2.1", "Item Row (retired)", KIND_COMPONENT)
_add("0.10.4.3", "Paste Button (retired)", KIND_COMPONENT)
_add("0.10.4.4", "Upload All Button (retired)", KIND_COMPONENT)
_add("0.10.5", "Auto Schedule Status (retired)", KIND_SECTION)
_add("0.10.5.1", "Auto Schedule Status Display (retired)", KIND_COMPONENT)
_add("0.10.5.2", "Auto Schedule On/Off Toggle (Home) (retired)", KIND_COMPONENT)
_add("0.10.5.3", "Edit Settings Button (retired)", KIND_COMPONENT)
_add("0.10.6", "Set Videos Per Day (retired, page removed)", KIND_SECTION)
_add("0.10.6.1", "Set Videos Per Day Button (retired)", KIND_COMPONENT)
_add("0.10.7", "Upload Mode Switch (retired, see 0.9)", KIND_SECTION)
_add("0.10.7.1", "Mode Switch Control (retired)", KIND_COMPONENT)

# ---- 0.11 Auto Schedule Settings (content now shown directly on the top-level "Auto
# Schedule" tab -- the main automation section -- rather than nested under Settings.
# Revised logic (spec "AS-001 -- Auto Schedule Settings -- Revised Logic"): Auto Schedule
# no longer just recommends a count -- it PULLS Shorts forward out of the existing future
# schedule to actually close a shortfall, and can apply that change itself (see
# scheduling/auto_schedule.py's compute_auto_schedule_pull_forward(), which reuses
# scheduling/increase_slots.py rather than reimplementing the same pull-forward-and-cascade
# logic a second time). SET-0012.1 (publishing window / daily slot count / slot times) was
# moved to the top of this page and is the one source of truth Auto Schedule reads from --
# see 0.11.22 below; the old Automatic/Manual expected-views estimate system is gone too,
# replaced by a single flat "Estimated Views Per Short" figure (0.11.23).) ----
_add("0.11", "Auto Boost Settings", KIND_PAGE, short_code="AS-001", description="Evaluates recent Short performance against a daily or weekly view target, and when behind, pulls existing future Shorts forward to close the shortfall -- read-only preview until Apply Recommended Changes is clicked (or, unattended, via auto_schedule_runner.py once Automation is ON).")
_add("0.11.25", "Extra Videos When Below Target (Boost Size)", KIND_COMPONENT, description="Auto Boost: how many extra videos tomorrow gets when the last completed day missed its target -- Calculated (worked out from the shortfall and Estimated Views Per Short) or a fixed +1 / +2 / +3. Never more than Maximum Shorts / Day. Saved with Save Settings.")
_add("0.11.1", "Automation On/Off Switch", KIND_COMPONENT, description="Gates ONLY the unattended headless runner (auto_schedule_runner.py) -- the GUI's own Apply Recommended Changes button (0.11.24.3) works regardless of this switch.")
_add("0.11.2", "Daily Target Views Field", KIND_COMPONENT)  # was "Target Views Field"; same slot, renamed alongside the new Weekly Target field
_add("0.11.3", "Number of Daily Upload Slots Selector (retired, moved to 0.11.22.2)", KIND_COMPONENT)  # AS-001 point 2 -- SET-0012.1's controls moved to the top of the page as their own clearly-labeled section; this ID's slot is retired, not deleted, per this file's own numbering convention
_add("0.11.4", "Increase After Failure Field (retired)", KIND_COMPONENT)  # replaced by the automatic shortfall ÷ estimated-views-per-Short calculation -- see 0.11.23
_add("0.11.5", "Maximum Shorts Per Day Field", KIND_COMPONENT, description="The absolute safety limit for one day's pull-forward, capped at MAX_SLOT_COUNT (6) -- as many Upload Time Presets (SET-0012.1) as exist.")  # was "Maximum Videos Per Day Field"; same slot, default raised 4 -> 6 per the v2 spec
_add("0.11.6", "Evaluation Period Selector (retired)", KIND_COMPONENT)  # the evaluation period is now determined automatically -- spec section 8
_add("0.11.7", "View Calculation Selector (retired)", KIND_COMPONENT)  # always combines every Short on a day now -- spec section 9
_add("0.11.8", "Publishing Window Fields (retired, moved to 0.11.22.1)", KIND_COMPONENT)  # AS-001 point 3 -- moved to the top of the page as part of SET-0012.1's own section; this ID's slot is retired, not deleted
_add("0.11.9", "Minimum Gap Field (retired)", KIND_COMPONENT)  # superseded by the count-dependent even-spacing within the publishing window
_add("0.11.10", "Save Settings Button", KIND_COMPONENT, description="Saves the Target/Estimated-Views/Maximum/Minimum fields. SET-0012.1's own controls (window, slot count, slot times) all save themselves immediately and are not part of this button.")
_add("0.11.11", "Advanced Settings Section (retired)", KIND_SECTION, short_code="AS-002")  # multi-tier response table dropped; see 0.11.23 for the new (simpler) shortfall calculation
_add("0.11.12", "Manual Daily Override Controls (retired)", KIND_COMPONENT)
_add("0.11.13", "Target Mode Selector", KIND_COMPONENT, description="Daily Target vs Weekly Target -- which one is authoritative for the automatic evaluation (spec section 8).")
_add("0.11.14", "Weekly Target Views Field", KIND_COMPONENT)
_add("0.11.15", "Recommended Daily Views Display", KIND_COMPONENT, description="Live read-out of Weekly Target ÷ 7, kept in sync automatically as the weekly field changes.")
_add("0.11.16", "Minimum Daily Views Field", KIND_COMPONENT)
_add("0.11.17", "Expected Views Per Short Section (retired, see 0.11.23)", KIND_SECTION, short_code="AS-003")  # AS-001 point 7/23 -- the whole Automatic/Manual estimate system is gone, replaced by a single flat field
_add("0.11.17.1", "Automatic/Manual Toggle (retired)", KIND_COMPONENT)
_add("0.11.17.2", "Manual Value Field (retired, see 0.11.23)", KIND_COMPONENT)
_add("0.11.17.3", "Current Estimate Display (retired)", KIND_COMPONENT)
_add("0.11.18", "Recommended Changes Card (was Current Estimate Card)", KIND_SECTION, description="Live preview of what Auto Schedule would do right now: target/performance/shortfall/estimated-views-per-Short/recommended count, a Video|Current|New preview table (0.11.24.2), and Apply Recommended Changes (0.11.24.3) to actually pull the Shorts forward.")
_add("0.11.18.1", "Recommended Tomorrow Display (retired, see 0.11.24.1)", KIND_COMPONENT)
_add("0.11.18.2", "Recommendation Explanation Text", KIND_COMPONENT)
_add("0.11.19", "Weekly Performance Card", KIND_SECTION, short_code="AS-004", description="Mon-Sun visual breakdown of Shorts uploaded/views/target-met for the current week, with a click-through daily explanation panel. Weekly mode's summary line also shows the estimated number of Shorts likely required (spec point 18).")
_add("0.11.19.1", "Weekly Summary Display", KIND_COMPONENT)  # Target/Collected/Remaining/Required Average/Estimated Shorts Required -- shown only in Weekly Target mode
_add("0.11.19.2", "Day Column", KIND_COMPONENT)
_add("0.11.19.3", "Day Detail Panel", KIND_COMPONENT)
_add("0.11.20", "Refresh Preview Button", KIND_COMPONENT, description="Re-fetches recently-published Shorts AND the real future schedule from YouTube before recomputing the whole preview (estimate, pull-forward plan, weekly table) -- also runs on page load and right after Save.")
_add("0.11.21", "Upload Time Preset Fields (retired, see 0.11.22.3)", KIND_SECTION, short_code="SET-0012.1")  # AS-001 point 1 -- SET-0012.1's whole section moved to the top of the page; this ID's slot is retired, not deleted
_add("0.11.22", "Upload Time Presets Panel (SET-0012.1, moved to top)", KIND_SECTION, short_code="SET-0012.1", description="AS-001 point 1 -- the publishing window, daily slot count, and each count's exact times, all in one panel at the TOP of this page (Auto Schedule depends on these). One source of truth for daily upload times, used by Auto Schedule, One Upload Page, and Increase Daily Slots alike -- there is no second copy of the window or the daily rate anywhere else on this page.")
_add("0.11.22.1", "Publishing Window Fields", KIND_COMPONENT, description="Auto-saves on edit (Enter or clicking away) -- the only Publishing Window in the app (see SET-0012.1 point 3; the old duplicate copy at 0.12.1, and the whole 0.12 page it lived on, have since been removed).")
_add("0.11.22.2", "Number of Daily Upload Slots Selector", KIND_COMPONENT, description="Compact 1-6 selector (ui/widgets/slot_selector.py's shared SlotCountControl) -- the app's normal_videos_per_day setting, defaulting to 2 unless already saved. Changing it immediately persists on its own, without needing Save Settings, and swaps the collapsed gear panel (0.11.22.3) to that count's own preset.")
_add("0.11.22.3", "Upload Time Preset Fields (collapsible, via the gear icon 0.11.22.4)", KIND_COMPONENT, description="Collapsed by default -- one editable SLOT N time field per currently-selected daily slot count only (never all six at once), shown when the gear icon (0.11.22.4) is clicked. Auto-distributed across the Publishing Window by default and auto-saved per exact count (1-6, each independent) the moment it's edited and validated.")
_add("0.11.22.4", "Configure Slot Times Gear Icon", KIND_COMPONENT, description="Small ⚙ button beside the slot-count selector -- toggles the collapsible slot-time panel (0.11.22.3) open/closed. Keeps the compact '1 | 2 | 3 | 4 | 5 | 6 | ⚙' view clean until the user actually wants to see/edit the times.")
_add("0.11.23", "Estimated Views Per Short Field", KIND_COMPONENT, description="AS-001 point 7 -- a single flat number the user sets directly (e.g. 800), replacing the old Automatic/Manual estimate system. Used only to plan how many Shorts a shortfall requires (shortfall ÷ this number) -- never changes a video's real view count or claims a Short will get this many views.")
_add("0.11.24", "Recommended Changes Preview", KIND_SECTION, description="AS-001 points 20-21 -- what Auto Schedule would do right now, computed the same way the headless runner does, read-only until Apply Recommended Changes is clicked.")
_add("0.11.24.1", "Preview Summary Stats", KIND_COMPONENT, description="Target, Current Performance, Shortfall, Estimated Views/Short, Recommended Next-Day Slots, and Videos To Pull Forward -- exactly the numbers spec point 20 asks to show.")
_add("0.11.24.2", "Recommended Changes Preview Table", KIND_SECTION, description="Video | Current | New, one row per Short in the pull-forward's affected queue -- unchanged rows shown muted rather than omitted, so it's obvious which Shorts actually move.")
_add("0.11.24.3", "Apply Recommended Changes Button", KIND_COMPONENT, description="The one place Auto Schedule actually writes to YouTube from the GUI -- pulls the previewed Short(s) forward for real via the same shared apply_schedule_result() (order-preservation check, History audit trail, only-changed-Shorts-sent) every other scheduling feature uses. Independent of the Automation ON/OFF switch (0.11.1).")

# ---- 0.12 Scheduling Settings (retired -- the Settings > Scheduling sub-tab and its
# backing data/central_config.py were removed. Its Slot Interval / Max HIT Uploads /
# Default Frequency fields were shared defaults for HIT, Reset All, and Upload On Specific
# Day, all retired with the 3-tab navigation (see 0.10.1/0.10.2/0.10.3), so nothing read
# them any more) ----
_add("0.12", "Scheduling Settings (retired, page removed)", KIND_PAGE, short_code="SCHED-001")
_add("0.12.1", "Publishing Window Fields (retired, see 0.11.22.1)", KIND_COMPONENT)  # SET-0012.1: duplicated the one on Auto Schedule Settings, the window real scheduling actually reads, and was never itself wired to anything -- removed from the UI; the page itself is now gone too
_add("0.12.2", "Interval Field (retired, page removed)", KIND_COMPONENT)
_add("0.12.3", "Max HIT Uploads Field (retired, page removed)", KIND_COMPONENT)
_add("0.12.4", "Default Frequency Field (retired, page removed)", KIND_COMPONENT)
_add("0.12.5", "Save Settings Button (retired, page removed)", KIND_COMPONENT)

# ---- 0.13 Daily Upload Frequency (was Increase Daily Slots, was Push -- see scheduling/daily_frequency.py) ----
_add("0.13", "Daily Upload Frequency (was Increase Daily Slots / Push)", KIND_PAGE, short_code="PUSH-001", description="Choose how many Shorts publish per day (1-6). Changes ONLY dates and times -- never the order: the order comes from permanent sequence numbers (data/queue_order.py) and every new date is worked out by walking them (scheduling/daily_frequency.py). The queue order is recovered from evidence and recorded first; the preview shows current vs requested frequency, current vs proposed dates, order before/after, how many change, and any problem; applying re-validates against YouTube, updates only changed Shorts, records each change in the history, and verifies YouTube afterwards.")
_add("0.13.1", "Status Card", KIND_SECTION)
_add("0.13.1.1", "Current Time Display", KIND_COMPONENT)
_add("0.13.1.2", "Queue Size (was Affected Shorts Count)", KIND_COMPONENT, description="How many scheduled Shorts are in the queue this page changes.")
_add("0.13.1.3", "Summary Banner", KIND_COMPONENT, description="Plain-language read-out: how many Shorts get a new date or time at the requested frequency, and where the last Short moves to.")
_add("0.13.2", "Preview Table", KIND_SECTION, description="Order (the Short's place in the queue sequence) | Was # (its place when ordered by its CURRENT date -- red when the schedule has it out of sequence) | Video | Current | New, one row per queued Short in sequence order; unchanged rows are shown muted. Shows 60 at a time.")
_add("0.13.2.1", "Table Row", KIND_COMPONENT)
_add("0.13.3", "Action Bar", KIND_SECTION)
_add("0.13.3.1", "Confirm & Apply Button (was Confirm & Increase Daily Slots)", KIND_COMPONENT)
_add("0.13.3.2", "Refresh Calculation Button", KIND_COMPONENT, description="Re-fetches the real schedule from YouTube before recomputing the preview -- also runs automatically on tab focus and initial load. Changing the date range or slot count alone never hits the network (see point 11's API-efficiency rule).")
_add("0.13.4", "Date Range Controls (retired -- the frequency now applies to the whole future queue)", KIND_SECTION)
_add("0.13.4.1", "Start Date Picker (retired)", KIND_COMPONENT)
_add("0.13.4.2", "End Date Picker (retired)", KIND_COMPONENT)
_add("0.13.4.3", "Start Date Mode (retired)", KIND_COMPONENT)
_add("0.13.4.4", "End Date Mode (retired)", KIND_COMPONENT)
_add("0.13.1.4", "Gap Summary (retired)", KIND_COMPONENT)
_add("0.13.5", "Slots Per Day Selector", KIND_COMPONENT, description="1-6, one at a time (ui/widgets/slot_selector.py's shared SlotCountControl, same component as 0.11.22.2) -- the temporarily-increased rate applied only within the selected date range; this selection is never persisted as the normal daily rate. Uses the same SET-0012.1 saved presets as everywhere else in the app.")
_add("0.13.5.1", "Configure Slot Times Gear Icon", KIND_COMPONENT, description="Same shared gear-icon control as 0.11.22.4 -- toggles the collapsible preset editor (0.13.5.2) for whichever count is currently selected here.")
_add("0.13.5.2", "Upload Time Preset Fields (collapsible)", KIND_COMPONENT, description="Same shared collapsible panel as 0.11.22.3 -- editing and saving a preset here updates the same global SET-0012.1 preset used everywhere, even though the selected count itself stays local to this page.")
_add("0.13.1.5", "Current vs Requested Frequency", KIND_COMPONENT, description="The videos-per-day the schedule has now (the most common daily count, and how many days hold each count) next to the frequency being requested.")
_add("0.13.1.6", "Issues List", KIND_COMPONENT, description="Everything that blocks applying (shown with a cross) or is worth knowing: missing/duplicate sequence numbers, invalid or conflicting slots, videos left alone.")
_add("0.13.1.7", "API Cost Note", KIND_COMPONENT, description="How many YouTube API units applying would use (50 per changed Short) against the app's tracked daily budget.")
_add("0.13.1.8", "Verification Result", KIND_COMPONENT, description="After applying: YouTube is read back and compared with the validated plan; this says whether every Short is scheduled exactly as planned, in queue order.")
_add("0.13.6", "Queue Order Panel", KIND_SECTION, description="Whether the permanent queue order has been recorded, and the button to recover/review it (or to place Shorts that have no sequence number).")
_add("0.13.6.1", "Queue Order Status", KIND_COMPONENT)
_add("0.13.6.2", "Recover & Review / Place Button", KIND_COMPONENT, description="Opens the order review window. Reviewing and recording the order never changes anything on YouTube.")
_add("0.13.7", "Order Review Window", KIND_SECTION, description="Shows the queue order recovered from the evidence (upload times and the schedule-change history), every group of videos the evidence cannot settle (decided by you), and each video's evidence. Nothing is recorded until every group is decided and you save.")
_add("0.13.7.1", "Decisions Needed", KIND_COMPONENT, description="Groups of videos whose order the evidence cannot settle, each with both candidate orders. The recommended answer is the upload order, but nothing is applied unless you choose it.")
_add("0.13.7.2", "Recovered Order Table", KIND_COMPONENT, description="Position | Video | Uploaded | Now scheduled | Status | Evidence for every Short in the recovered order.")
_add("0.13.7.3", "Save Order Button", KIND_COMPONENT, description="Permanently records the reviewed order as sequence numbers. Enabled only when nothing blocks and every group is decided.")

# ---- 0.14 Calendar (monthly publishing-density overview -- see data/calendar_aggregator.py) ----
_add("0.14", "Calendar", KIND_PAGE, short_code="CAL-001", description="Monthly overview of publishing density, with a day-by-day breakdown of what's scheduled.")
_add("0.14.1", "Header Controls", KIND_SECTION)
_add("0.14.1.1", "Previous Month Button", KIND_COMPONENT)
_add("0.14.1.2", "Month Year Label", KIND_COMPONENT)
_add("0.14.1.3", "Next Month Button", KIND_COMPONENT)
_add("0.14.1.4", "Today Button", KIND_COMPONENT)
_add("0.14.1.5", "Summary Stats Badge", KIND_COMPONENT)
_add("0.14.2", "Calendar Grid", KIND_SECTION)
_add("0.14.2.1", "Day Cell", KIND_COMPONENT)
_add("0.14.2.2", "Event Chip", KIND_COMPONENT)  # one video's chip stacked inside a day cell (up to MAX_CHIPS_PER_CELL)
_add("0.14.2.3", "More Events Indicator", KIND_COMPONENT)  # "+N more" chip shown when a day exceeds MAX_CHIPS_PER_CELL
_add("0.14.3", "Day Detail Panel", KIND_SECTION)
_add("0.14.3.1", "Selected Date Heading", KIND_COMPONENT)
_add("0.14.3.2", "Scheduled Video Row", KIND_COMPONENT)

# ---- 0.15 Developer Tools (Settings sub-tab -- Item IDs, copyable references, and
# error reporting; see ui/dev_reporting.py and data/error_log.py) ----
_add("0.15", "Developer Tools", KIND_PAGE, short_code="DEV-001", description="Developer diagnostics: Item IDs, copyable references, and error reporting.")
_add("0.15.1", "Developer Mode Control", KIND_SECTION)
_add("0.15.1.1", "Developer Mode Toggle", KIND_COMPONENT)
_add("0.15.2", "Reference Copying", KIND_SECTION)
_add("0.15.2.1", "Current Item ID Display", KIND_COMPONENT)
_add("0.15.2.2", "Copy ID Button", KIND_COMPONENT)
_add("0.15.2.3", "Copy Page Reference Button", KIND_COMPONENT)
_add("0.15.2.4", "Copy Current Page Info Button", KIND_COMPONENT)
_add("0.15.3", "Error Reporting", KIND_SECTION)
_add("0.15.3.1", "Copy Last Error Button", KIND_COMPONENT)
_add("0.15.3.2", "View Error Log Button", KIND_COMPONENT)
_add("0.15.4", "Application Version Display", KIND_SECTION)
_add("0.15.5", "Error / Crash Log", KIND_SECTION, short_code="CRASH-001", description="Fatal/unhandled errors caught by the app's global exception handling (main.py) -- separate from 0.15.3's Error Reporting, which is for errors the app already handles gracefully (e.g. a failed upload). Survives restarts, so a crash from the previous run is still here as LAST APPLICATION ERROR.")
_add("0.15.5.1", "Last Application Error Display", KIND_COMPONENT)
_add("0.15.5.2", "Copy Crash Error Button", KIND_COMPONENT)
_add("0.15.5.3", "Copy Crash Diagnostics Button", KIND_COMPONENT)
_add("0.15.5.4", "Clear Crash Log Button", KIND_COMPONENT)

# ---- 0.16 Application (Settings sub-tab -- app-level installation options, currently
# just the desktop shortcut; see data/desktop_shortcut.py) ----
_add("0.16", "Application", KIND_PAGE, short_code="APP-001", description="App-level installation settings, separate from YouTube connection settings.")
_add("0.16.1", "Desktop Shortcut", KIND_SECTION, short_code="DSC-001", description="Creates or repairs a Windows desktop shortcut that launches the app directly (no console window), using a custom icon combining the YouTube and Battlefield V visual identity.")
_add("0.16.1.1", "Shortcut Status Display", KIND_COMPONENT)
_add("0.16.1.2", "Create/Recreate Shortcut Button", KIND_COMPONENT)
_add("0.16.2", "Upload Detection", KIND_SECTION, short_code="APP-002", description="How long ago a file must have been created/modified for One Upload Page's Most Recent File to consider it -- see data/shorts_folder.py's find_newest_recent_video().")
_add("0.16.2.1", "Detection Window Field", KIND_COMPONENT)

# ---- 0.17 Crash History (Settings sub-tab -- APP-OPT-001; see ui/pages/crash_history_page.py) ----
_add("0.17", "Crash History", KIND_PAGE, short_code="CRASH-002", description="Persistent record of every crash, shown error, automatic recovery, repaired data file and unexpected exit, newest first. Each entry expands to its full technical report and can be copied on its own (never the whole history, never credentials).")
_add("0.17.1", "Crash History List", KIND_SECTION, description="One line per entry: date/time | page | Item ID | operation | short error. Click a line to expand it.")
_add("0.17.2", "Copy Crash Report Button", KIND_COMPONENT, description="Copies the complete technical report for THAT entry only: error message, exception type, full traceback, date/time, page, Item ID, operation, application state and retry/attempt info. Credentials are always masked.")
_add("0.17.3", "Copy Error Button (Crash History)", KIND_COMPONENT, description="Copies just the error text of that one entry.")
_add("0.17.4", "Clear Crash History Controls", KIND_COMPONENT, description="Clear Older Than 7 Days / Clear All (both ask for confirmation).")

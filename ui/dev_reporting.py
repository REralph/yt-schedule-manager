"""Turns a dev_ids.py registry entry into the developer-friendly text blocks Developer
Tools copies to the clipboard, and the one entry point (report_error) every real failure
(an API call, an upload, a write to YouTube) should go through instead of a plain
messagebox. Purely diagnostic -- see the module docstrings on data/error_log.py and
ui/dialogs/error_dialog.py for what that means in practice; nothing here changes what
already happened to the schedule or the video data.
"""
from __future__ import annotations

from typing import Callable, Optional

from data.error_log import ErrorReport
from ui.dev_ids import REGISTRY, page_id_of
from ui.dialogs.error_dialog import ErrorDialog

# Maps MainWindow's top-level tab display names to that tab's page-level dev_id -- lets
# "Copy Current Page Info" work out what's on screen without every page having to say so
# itself. Kept here (not in main_window.py) since it's part of the same "resolve a dev_id
# into something copyable" job as the rest of this module.
TAB_DEV_IDS: dict[str, str] = {
    "Upload": "0.9",
    "Auto Boost": "0.11",
    "Daily Upload Frequency": "0.13",
    "Calendar": "0.14",
    "Settings": "0.8",
}


def current_page_dev_id(state) -> str:
    return TAB_DEV_IDS.get(state.current_tab, "")


def display_id(dev_id: str) -> str:
    """The short code (e.g. "AS-001") for nodes that have one, else the numeric ID."""
    node = REGISTRY.get(dev_id)
    return node.short_code if node and node.short_code else dev_id


def _page_name(dev_id: str) -> str:
    page_node = REGISTRY.get(page_id_of(dev_id))
    return page_node.name if page_node else "?"


def _function_text(dev_id: str) -> str:
    """The node's own description if it has one, else its page's, else just its name --
    always resolves to *something* usable rather than an empty Function: line."""
    node = REGISTRY.get(dev_id)
    if node and node.description:
        return node.description
    page_node = REGISTRY.get(page_id_of(dev_id))
    if page_node and page_node.description:
        return page_node.description
    return node.name if node else dev_id


def build_full_reference(dev_id: str) -> str:
    """Item ID / Page / Section / Function -- what Copy Page Reference and Copy Current
    Page Info put on the clipboard. Section is omitted when the ID already IS the page
    (nothing more specific to name)."""
    page_id = page_id_of(dev_id)
    parts = dev_id.split(".")
    section_id = ".".join(parts[:3]) if len(parts) >= 3 else None
    section_node = REGISTRY.get(section_id) if section_id and section_id != page_id else None

    lines = [f"Item ID: {display_id(dev_id)}", f"Page: {_page_name(dev_id)}"]
    if section_node:
        lines.append(f"Section: {section_node.name}")
    lines.append(f"Function: {_function_text(dev_id)}")
    return "\n".join(lines)


def report_error(
    master, state, dev_id: str, function: str, error_message: str,
    error_code: str = "", details: str = "", operation: str = "", show_dialog: bool = True,
    on_retry: Optional[Callable[[], None]] = None, retry_label: str = "Retry",
) -> ErrorReport:
    """The one entry point every real failure should go through: records the report
    (data/error_log.py) and remembers it as state.last_error for "Copy Last Error". Shows
    the one-click-copy ErrorDialog by default; pass show_dialog=False for a batch context
    (e.g. several uploads failing in one queue) where popping a dialog per failure would
    stack modal windows -- the failures are still all recorded and reachable via View
    Error Log / Copy Last Error either way. `function` describes the specific operation
    that failed (e.g. "Applying the schedule"), separate from the node's general
    Function: description used elsewhere. `on_retry`, when given, adds a Retry button to
    the dialog (e.g. after both of an upload's automatic attempts have failed) -- see
    ui/widgets/basic_upload_panel.py's _upload_failed(). `retry_label` renames that button
    when "Retry" would be ambiguous (e.g. "Retry Verification", which re-checks YouTube and
    never re-uploads)."""
    report = state.error_log.record(
        item_id=display_id(dev_id), page=_page_name(dev_id), function=function,
        error_message=error_message, error_code=error_code, details=details, operation=operation,
    )
    state.last_error = report
    if show_dialog:
        ErrorDialog(master, report, on_retry=on_retry, retry_label=retry_label)
    return report

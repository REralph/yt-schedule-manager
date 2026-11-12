"""Creates/verifies the Windows desktop shortcut ("YouTube Short Reschedule Manager.lnk")
that launches the app directly via the venv's pythonw.exe -- no console window, no
double-click-and-wait through run_app.bat.

Uses the OS's own cscript.exe + WScript.Shell COM object to write/read .lnk files rather
than adding pywin32 as a dependency for this one feature -- WScript.Shell ships with every
Windows install and can both create shortcuts and read back what an existing one points to.

The shortcut always targets THIS install's current pythonw.exe/main.py/icon paths
(resolved from __file__ at call time), so recreating it after the app folder is moved or
the venv is rebuilt re-points it correctly. There is no way to make an existing .lnk on the
Desktop auto-repair itself without the app running -- that's what the Recreate action in
Settings is for.
"""
from __future__ import annotations

import ctypes
import os
import pathlib
import subprocess
import tempfile

SHORTCUT_NAME = "YouTube Short Reschedule Manager.lnk"
APP_TITLE = "YouTube Short Reschedule Manager"

_PROJECT_ROOT = pathlib.Path(__file__).resolve().parent.parent
_PYTHONW = _PROJECT_ROOT / ".venv" / "Scripts" / "pythonw.exe"
_MAIN_PY = _PROJECT_ROOT / "main.py"
_ICON = _PROJECT_ROOT / "assets" / "app_icon.ico"

_CSIDL_DESKTOPDIRECTORY = 0x0010  # the real per-user Desktop, honours OneDrive redirection

DESKTOP_PATH_OVERRIDE: pathlib.Path | None = None  # tests point this at a tempdir so runs never touch the real Desktop


def get_desktop_path() -> pathlib.Path:
    if DESKTOP_PATH_OVERRIDE is not None:
        return DESKTOP_PATH_OVERRIDE
    buf = ctypes.create_unicode_buffer(260)
    ctypes.windll.shell32.SHGetFolderPathW(None, _CSIDL_DESKTOPDIRECTORY, None, 0, buf)
    return pathlib.Path(buf.value)


def get_shortcut_path() -> pathlib.Path:
    return get_desktop_path() / SHORTCUT_NAME


def _vbs_escape(value: str) -> str:
    return value.replace('"', '""')


def _run_vbs(script: str) -> str:
    fd, path = tempfile.mkstemp(suffix=".vbs")
    os.close(fd)
    try:
        pathlib.Path(path).write_text(script, encoding="utf-8")
        result = subprocess.run(
            ["cscript.exe", "//nologo", path],
            capture_output=True,
            text=True,
            timeout=10,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        return result.stdout
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


def create_shortcut() -> None:
    """Creates (or overwrites in place) the desktop shortcut. The target filename is
    always the same, so this can never produce a duplicate -- calling it again just
    re-points the one shortcut at this install's current paths."""
    shortcut_path = get_shortcut_path()
    args_value = f'"{_MAIN_PY}"'
    script = f'''
Set oWS = CreateObject("WScript.Shell")
Set oLink = oWS.CreateShortcut("{_vbs_escape(str(shortcut_path))}")
oLink.TargetPath = "{_vbs_escape(str(_PYTHONW))}"
oLink.Arguments = "{_vbs_escape(args_value)}"
oLink.WorkingDirectory = "{_vbs_escape(str(_PROJECT_ROOT))}"
oLink.IconLocation = "{_vbs_escape(str(_ICON))},0"
oLink.Description = "{_vbs_escape(APP_TITLE)}"
oLink.Save
'''
    _run_vbs(script)


def _read_shortcut_target() -> tuple[str, str] | None:
    shortcut_path = get_shortcut_path()
    if not shortcut_path.exists():
        return None
    script = f'''
Set oWS = CreateObject("WScript.Shell")
Set oLink = oWS.CreateShortcut("{_vbs_escape(str(shortcut_path))}")
WScript.Echo oLink.TargetPath
WScript.Echo oLink.Arguments
'''
    output = _run_vbs(script)
    lines = output.splitlines()
    if len(lines) < 2:
        return None
    return lines[0].strip(), lines[1].strip()


def shortcut_status() -> str:
    """Returns "not_installed", "installed", or "broken" -- broken means the shortcut file
    exists but the target it actually points to (e.g. a pythonw.exe from a deleted venv,
    or a main.py from a folder that no longer exists after the app was moved) is gone.
    Recreating always fixes "broken" by re-pointing at this install's real current paths."""
    target = _read_shortcut_target()
    if target is None:
        return "not_installed"
    target_path, arguments = target
    if not target_path or not pathlib.Path(target_path).exists():
        return "broken"
    main_py_arg = arguments.strip('"')
    if not main_py_arg or not pathlib.Path(main_py_arg).exists():
        return "broken"
    return "installed"

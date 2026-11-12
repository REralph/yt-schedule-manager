"""UPLOAD QUALITY PROTECTION: reads the real technical properties of a video -- either
the local file about to be uploaded (via a bundled, read-only ffprobe -- see
tools/ffprobe/) or what YouTube itself recorded receiving (via YouTubeClient.
get_file_details(), the Data API's fileDetails part) -- so the Upload page can show the
user exactly what's about to be sent, and independently confirm afterward that YouTube's
copy matches it. Nothing in this module ever transcodes, re-encodes, or writes a video
file; ffprobe (unlike ffmpeg) is fundamentally a read-only inspection tool, so bundling it
cannot introduce a re-encoding path into the app.

tools/ffprobe/ffprobe.exe is a standalone static build (see tools/ffprobe/README.txt for
its exact version/source/checksum) -- it is never added to PATH or installed system-wide,
just invoked by its full path from here.
"""
from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

FFPROBE_PATH = Path(__file__).resolve().parent.parent / "tools" / "ffprobe" / "ffprobe.exe"
PROBE_TIMEOUT_SECONDS = 30

_CODEC_DISPLAY_NAMES = {
    "h264": "H.264 (AVC)",
    "hevc": "H.265 (HEVC)",
    "vp9": "VP9",
    "vp8": "VP8",
    "av1": "AV1",
    "mpeg4": "MPEG-4",
    "mjpeg": "Motion JPEG",
    "aac": "AAC",
    "mp3": "MP3",
    "opus": "Opus",
    "vorbis": "Vorbis",
    "alac": "ALAC",
    "pcm_s16le": "PCM (uncompressed)",
    "pcm_s24le": "PCM (uncompressed)",
}


@dataclass
class VideoTechInfo:
    filename: str = ""
    file_size_bytes: int = 0
    width: int = 0
    height: int = 0
    fps: float = 0.0
    duration_seconds: float = 0.0
    video_codec: str = ""
    video_bitrate_bps: int = 0
    audio_codec: str = ""
    audio_bitrate_bps: int = 0


def format_size(num_bytes: int) -> str:
    if num_bytes <= 0:
        return "Unknown"
    mb = num_bytes / (1024 * 1024)
    return f"{mb / 1024:.2f} GB" if mb >= 1024 else f"{mb:.1f} MB"


def format_bitrate(bps: int) -> str:
    if bps <= 0:
        return "Unknown"
    mbps = bps / 1_000_000
    return f"{mbps:.1f} Mbps" if mbps >= 1 else f"{bps / 1000:.0f} kbps"


def _friendly_codec(name: str) -> str:
    if not name:
        return ""
    return _CODEC_DISPLAY_NAMES.get(name.lower(), name.upper())


def _int_or(value, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _parse_frame_rate(value: Optional[str]) -> float:
    if not value:
        return 0.0
    try:
        if "/" in value:
            num, den = value.split("/")
            num, den = float(num), float(den)
            return round(num / den, 2) if den else 0.0
        return round(float(value), 2)
    except (TypeError, ValueError):
        return 0.0


def probe_local_file(file_path: str) -> Optional[VideoTechInfo]:
    """Reads the SELECTED file's own real properties, straight from its bytes on disk --
    never a guess based on filename/extension. Returns None (never raises) if ffprobe
    isn't bundled, the file can't be read, or the output can't be parsed; callers should
    treat that as "technical info unavailable," not an error, since this is a diagnostic
    display, not something upload correctness depends on."""
    if not FFPROBE_PATH.exists() or not os.path.exists(file_path):
        return None
    try:
        creationflags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        result = subprocess.run(
            [str(FFPROBE_PATH), "-v", "quiet", "-print_format", "json", "-show_format", "-show_streams", file_path],
            capture_output=True, text=True, timeout=PROBE_TIMEOUT_SECONDS, creationflags=creationflags,
        )
        data = json.loads(result.stdout)
    except Exception:
        return None
    return _from_ffprobe_json(file_path, data)


def _from_ffprobe_json(file_path: str, data: dict) -> Optional[VideoTechInfo]:
    streams = data.get("streams", [])
    fmt = data.get("format", {})
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    if video is None:
        return None
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)

    info = VideoTechInfo(filename=os.path.basename(file_path))
    info.file_size_bytes = _int_or(fmt.get("size"), 0) or _safe_file_size(file_path)
    info.width = _int_or(video.get("width"), 0)
    info.height = _int_or(video.get("height"), 0)
    info.fps = _parse_frame_rate(video.get("avg_frame_rate") or video.get("r_frame_rate"))
    try:
        info.duration_seconds = float(fmt.get("duration") or video.get("duration") or 0)
    except (TypeError, ValueError):
        pass
    info.video_codec = _friendly_codec(video.get("codec_name", ""))
    info.video_bitrate_bps = _int_or(video.get("bit_rate"), 0)
    if audio is not None:
        info.audio_codec = _friendly_codec(audio.get("codec_name", ""))
        info.audio_bitrate_bps = _int_or(audio.get("bit_rate"), 0)
    return info


def _safe_file_size(file_path: str) -> int:
    try:
        return os.path.getsize(file_path)
    except OSError:
        return 0


def from_youtube_file_details(filename: str, raw: Optional[dict]) -> Optional[VideoTechInfo]:
    """Parses YouTubeClient.get_file_details()'s raw fileDetails dict into the same shape
    probe_local_file() returns, so the two can be compared field-for-field. Returns None
    if `raw` is empty/has no video stream -- e.g. YouTube hasn't finished registering the
    file's details yet, which is normal for a just-completed upload, not an error."""
    if not raw:
        return None
    video_streams = raw.get("videoStreams") or []
    if not video_streams:
        return None
    v = video_streams[0]
    audio_streams = raw.get("audioStreams") or []

    info = VideoTechInfo(filename=filename)
    info.file_size_bytes = _int_or(raw.get("fileSize"), 0)
    info.width = _int_or(v.get("widthPixels"), 0)
    info.height = _int_or(v.get("heightPixels"), 0)
    info.fps = round(float(v.get("frameRateFps") or 0), 2)
    info.video_codec = _friendly_codec(v.get("codec", ""))
    info.video_bitrate_bps = _int_or(v.get("bitrateBps"), 0)
    if audio_streams:
        a = audio_streams[0]
        info.audio_codec = _friendly_codec(a.get("codec", ""))
        info.audio_bitrate_bps = _int_or(a.get("bitrateBps"), 0)
    info.duration_seconds = _int_or(raw.get("durationMs"), 0) / 1000
    return info


def compare(local: VideoTechInfo, remote: VideoTechInfo) -> tuple[bool, list[str]]:
    """True + [] if `remote` (what YouTube recorded) looks like the same source material
    as `local` (what this app read before upload) -- resolution/frame-rate/codec, the
    properties that would actually change if something re-encoded the file. Bitrate is
    deliberately NOT compared here: YouTube and ffprobe can legitimately compute a
    container's overall bitrate slightly differently even for byte-identical files, so a
    small bitrate delta alone isn't evidence of quality loss -- it's still shown
    side-by-side in the UI for the user's own judgement, just not treated as a mismatch."""
    diffs = []
    if local.width and remote.width and (local.width, local.height) != (remote.width, remote.height):
        diffs.append(f"Resolution changed: {local.width}x{local.height} -> {remote.width}x{remote.height}")
    if local.fps and remote.fps and abs(local.fps - remote.fps) > 0.5:
        diffs.append(f"Frame rate changed: {local.fps:g}fps -> {remote.fps:g}fps")
    if local.video_codec and remote.video_codec and local.video_codec != remote.video_codec:
        diffs.append(f"Video codec changed: {local.video_codec} -> {remote.video_codec}")
    if local.audio_codec and remote.audio_codec and local.audio_codec != remote.audio_codec:
        diffs.append(f"Audio codec changed: {local.audio_codec} -> {remote.audio_codec}")
    return (not diffs, diffs)

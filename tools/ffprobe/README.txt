ffprobe.exe -- bundled for Upload Quality Protection (see youtube/video_tech_info.py)

Source:  https://www.gyan.dev/ffmpeg/builds/  (ffmpeg-release-essentials.zip)
Version: ffmpeg-9.0.1-essentials_build
Fetched: 2026-09-10
SHA256 of the downloaded zip (ffmpeg-9.0.1-essentials_build.zip):
  fec81ae03971d9dd4be3ebe02e263bd2ec1d789483f931bdba5f5715e65da2e9

Only ffprobe.exe was extracted from that zip -- ffmpeg.exe (the encoder/converter) was
NOT included, deliberately, since this app only ever needs to read a video's existing
properties, never write/re-encode one. ffprobe.exe is only ever invoked with
-show_format/-show_streams (read-only inspection) and is never added to PATH or installed
system-wide -- it's only referenced by its full path from youtube/video_tech_info.py.

See LICENSE in this folder for ffmpeg/ffprobe's own license (GPL/LGPL depending on build
options -- this build was compiled with --enable-gpl).

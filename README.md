# YouTube Shorts Schedule Manager

![Python](https://img.shields.io/badge/Python-3.13-3776AB?logo=python&logoColor=white)
![YouTube Data API](https://img.shields.io/badge/YouTube%20Data%20API-v3%20%2B%20OAuth%202.0-FF0000?logo=youtube&logoColor=white)
![CustomTkinter](https://img.shields.io/badge/UI-CustomTkinter-1f6feb)
![Windows](https://img.shields.io/badge/platform-Windows-0078D6?logo=windows&logoColor=white)
![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)

A Windows desktop app for running a YouTube Shorts channel's upload queue: upload new Shorts into the
next free slot, keep the publishing schedule in order, change how many Shorts go out per day, and
optionally let a daily background job pull Shorts forward when the channel is behind its view target.
It talks to YouTube only through the official **YouTube Data API v3** with OAuth: no browser
automation, scraping or simulated clicks.

## Features

| Tab | What it does |
|---|---|
| **Upload** | Pick a Short from your Shorts folder or drag one in, and paste its title and description in one go (*Quick Paste* splits one pasted block into the two). The app schedules it automatically after the last booked date, checks the file's quality with `ffprobe`, uploads it with a resumable upload, then **verifies the video really reached YouTube** with a limited retry loop. |
| **Auto Boost** | A headless daily job ([`auto_schedule_runner.py`](auto_schedule_runner.py), run by Windows Task Scheduler) groups recent Shorts' views by publish day, checks the channel against its daily or weekly target, and pulls queued Shorts forward to tomorrow when needed. Order is kept, double runs are guarded against, and every decision is logged. |
| **Daily Upload Frequency** | Choose 1–6 Shorts per day. The queue order comes from **permanent sequence numbers**, not the current dates, so an already-scrambled schedule can't spread its disorder. There is a full before/after preview, and a "recover & review the order" step rebuilds the order from upload history when it was never recorded. |
| **Calendar** | Month view of what publishes when. |
| **Settings** | Connection status, today's API quota use and remaining headroom, app and developer settings, crash history. |

**Safety rules the code follows**

- Every schedule change is **previewed before anything is sent** to YouTube.
- Rescheduling re-reads a video's full status first and changes only `status.publishAt`, so privacy,
  made-for-kids and other settings are never altered as a side effect.
- Saves are atomic, and crashes and API errors are logged locally (`data/`) for troubleshooting.

## Tech stack

- **Python 3.13**, **CustomTkinter** UI with drag-and-drop (`tkinterdnd2`)
- **google-api-python-client** + **google-auth-oauthlib** (OAuth 2.0 desktop flow, token refresh)
- **OpenCV** (headless) and **Pillow** for thumbnails, **ffprobe** for read-only video inspection
- Standalone regression-test scripts in [`tests/`](tests/)

## One-time setup (you do this once, in your browser)

1. Go to https://console.cloud.google.com and create (or select) a project.
2. **APIs & Services -> Library** -> enable **YouTube Data API v3**.
3. **APIs & Services -> OAuth consent screen** -> User type **External** -> keep it in
   **Testing** status -> add your own Google account under "Test users".
4. **APIs & Services -> Credentials** -> **Create Credentials -> OAuth client ID** ->
   Application type **Desktop app** -> Create -> **Download JSON**.
5. Save that downloaded file as `credentials\client_secret.json` inside this project folder.
   It is git-ignored, as is the login token the app saves in `data\token.json`.

The first time you click "Connect to YouTube", Google will show an **"unverified app"**
warning screen. This is expected for a personal-use app you created yourself that hasn't been
through Google's public-app review. Click **Advanced**, then **"Go to … (unsafe)"** to continue.
Only the Google accounts you added as test users can sign in.

**Optional: `ffprobe`** for the Upload tab's quality check. Download an ffmpeg *essentials* build
(for example from https://www.gyan.dev/ffmpeg/builds/) and copy only `ffprobe.exe` to
`tools\ffprobe\ffprobe.exe`. See [`tools/ffprobe/README.txt`](tools/ffprobe/README.txt).

## Running it

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python main.py
```

`run_app.bat` starts it without a console window once the virtual environment exists.

## Tests

Each file in `tests/` is a standalone regression script that prints `PASS`/`FAIL` for each check,
for example:

```bash
.venv\Scripts\python tests\test_next_slot_order.py
```

## API quota

YouTube Data API projects get 10,000 quota units/day by default. Listing videos is cheap
(~1 unit/page), but each reschedule costs 50 units, so roughly 200 reschedules a day before the
daily cap. The Settings tab shows how much quota you've used today and how much headroom is left.
A very large batch may need to be applied across more than one day.

## Project structure

```
main.py                    app entry point
auto_schedule_runner.py    Auto Boost daily job (Task Scheduler)
auth/                      OAuth flow and token storage
youtube/                   API client, models, quota tracking, upload verification, ffprobe info
scheduling/                slot rules, next-slot finder, ordering, daily frequency, auto boost
data/                      config, history, queue order, crash and error logs (JSON files are git-ignored)
ui/                        CustomTkinter window, pages, widgets and dialogs
tests/                     regression-test scripts
```

## Licence

[MIT](LICENSE) © 2026 Ralph Estrocio. YouTube is a trademark of Google LLC; this is an independent
tool that uses the public YouTube Data API.

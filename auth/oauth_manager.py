"""Handles the OAuth 2.0 desktop-app flow and local token caching for the YouTube Data API."""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow

from data import diagnostics, safe_io

SCOPES = ["https://www.googleapis.com/auth/youtube"]

ROOT_DIR = Path(__file__).resolve().parent.parent
CLIENT_SECRET_PATH = ROOT_DIR / "credentials" / "client_secret.json"
TOKEN_PATH = ROOT_DIR / "data" / "token.json"


class MissingClientSecretError(RuntimeError):
    """Raised when credentials/client_secret.json is missing or unreadable."""


def client_secret_present() -> bool:
    return CLIENT_SECRET_PATH.exists() and CLIENT_SECRET_PATH.stat().st_size > 0


def load_cached_credentials() -> Optional[Credentials]:
    """Returns valid cached credentials, refreshing if needed, or None if the user must (re)authenticate."""
    if not TOKEN_PATH.exists():
        return None
    try:
        creds = Credentials.from_authorized_user_file(str(TOKEN_PATH), SCOPES)
    except Exception as e:
        # A corrupt/unexpected token.json (not just the ValueError/OSError cases this
        # used to name explicitly) should fall back to "must sign in again", never crash
        # the whole app on startup -- but it's recorded, so Crash History says why.
        diagnostics.report_data_problem("token.json", f"the saved YouTube sign-in could not be read ({type(e).__name__}); sign-in required", e)
        return None

    if creds and creds.valid:
        return creds

    if creds and creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
        except Exception as e:
            # e.g. invalid_grant: the saved sign-in expired or was revoked. The user is
            # taken back to the sign-in screen; the reason is recorded rather than lost.
            diagnostics.report_recovered(
                "Refreshing the saved YouTube sign-in", e, page="Startup / sign-in", operation="Refreshing the saved YouTube sign-in",
            )
            return None
        _save_credentials(creds)
        return creds

    return None


def run_auth_flow() -> Credentials:
    """Launches the system browser for the OAuth consent flow. Raises MissingClientSecretError if not set up."""
    if not client_secret_present():
        raise MissingClientSecretError(
            f"No client_secret.json found at {CLIENT_SECRET_PATH}. "
            "Follow SETUP.md to create a Google Cloud OAuth Desktop client and download it there."
        )
    flow = InstalledAppFlow.from_client_secrets_file(str(CLIENT_SECRET_PATH), SCOPES)
    creds = flow.run_local_server(port=0)
    _save_credentials(creds)
    return creds


def _save_credentials(creds: Credentials) -> None:
    TOKEN_PATH.parent.mkdir(parents=True, exist_ok=True)
    safe_io.atomic_write_text(TOKEN_PATH, creds.to_json())  # atomic: a kill mid-write must never leave a half-written token that forces a fresh sign-in


def get_credentials(interactive: bool = True) -> Optional[Credentials]:
    """Main entry point: returns usable credentials, refreshing or (if interactive) prompting login as needed."""
    creds = load_cached_credentials()
    if creds:
        return creds
    if not interactive:
        return None
    return run_auth_flow()


def sign_out() -> None:
    """Deletes the cached token so the next get_credentials() call requires a fresh login."""
    if TOKEN_PATH.exists():
        TOKEN_PATH.unlink()

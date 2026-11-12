"""Tells the different kinds of YouTube/network failure apart, so the app can react sensibly
(APP-OPT-001): an expired sign-in is not something an automatic retry can fix, a dropped
connection is, and each deserves a message the user can act on -- with the real technical
error always kept alongside it, never replaced by it.
"""
from __future__ import annotations

import socket
import ssl
from dataclasses import dataclass

KIND_AUTH = "auth"
KIND_NETWORK = "network"
KIND_QUOTA = "quota"
KIND_FILE = "file"
KIND_OTHER = "other"

_AUTH_MARKERS = ("invalid_grant", "token has been expired or revoked", "invalid_client", "unauthorized_client", "refresherror")
_NETWORK_MARKERS = (
    "wrong_version_number", "ssl", "timed out", "timeout", "connection reset", "connection aborted", "connection refused",
    "name or service not known", "getaddrinfo", "temporary failure in name resolution", "network is unreachable",
    "broken pipe", "remote end closed", "servernotfound", "unable to find the server", "eof occurred",
)


@dataclass
class ErrorInfo:
    kind: str
    friendly: str  # what the user should know/do -- "" when there is nothing more useful than the raw error
    raw: str

    @property
    def retry_can_help(self) -> bool:
        """Whether repeating the same request could plausibly succeed."""
        return self.kind in (KIND_NETWORK, KIND_OTHER)

    def message(self) -> str:
        """The friendly text followed by the real error in brackets (or just the raw error
        if there is nothing friendlier to say). The technical detail is never dropped."""
        return f"{self.friendly} ({self.raw})" if self.friendly else self.raw


def classify_exception(exc: BaseException) -> ErrorInfo:
    raw = str(exc) or type(exc).__name__
    text = f"{type(exc).__name__} {raw}".lower()

    try:
        from google.auth.exceptions import RefreshError, TransportError
        is_refresh_error = isinstance(exc, RefreshError)
        is_transport_error = isinstance(exc, TransportError)
    except Exception:  # pragma: no cover -- google-auth is a hard dependency
        is_refresh_error = is_transport_error = False

    if is_refresh_error or any(m in text for m in _AUTH_MARKERS):
        return ErrorInfo(
            KIND_AUTH,
            "Your YouTube sign-in has expired or was revoked, so the app can't reach YouTube. Open Settings -> YouTube, "
            "choose Sign Out, then sign in again -- then retry.",
            raw,
        )

    status = getattr(getattr(exc, "resp", None), "status", None)
    if status == 401:
        return ErrorInfo(KIND_AUTH, "YouTube rejected the app's sign-in (401). Open Settings -> YouTube, Sign Out, then sign in again.", raw)
    if status == 403 and "quotaexceeded" in text:
        return ErrorInfo(KIND_QUOTA, "The YouTube API daily quota has been used up. It resets at midnight Pacific Time; try again later.", raw)

    if isinstance(exc, (FileNotFoundError, PermissionError)):
        return ErrorInfo(KIND_FILE, "The video file couldn't be opened -- it may have been moved, deleted or is locked by another program.", raw)

    if (
        is_transport_error or isinstance(exc, (ssl.SSLError, ConnectionError, TimeoutError, socket.timeout, socket.gaierror))
        or (isinstance(status, int) and status >= 500) or any(m in text for m in _NETWORK_MARKERS)
    ):
        return ErrorInfo(KIND_NETWORK, "", raw)  # transient: the app retries these itself; nothing better to say than the real error

    return ErrorInfo(KIND_OTHER, "", raw)

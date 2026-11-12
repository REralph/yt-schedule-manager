"""Brings the upload history and the order ledger up to date with each other: every recorded video gets its description kept beside its
code, and every upload in the history gets its code. Local only -- nothing here talks to YouTube. Safe to run as often as you like:
it only fills in what is missing."""
from __future__ import annotations

from data.queue_order import QueueOrderStore
from data.upload_activity import UploadActivityLog


def backfill_order_codes(videos=(), registry: QueueOrderStore = None, log: UploadActivityLog = None) -> tuple[int, int]:
    """Returns (ledger records given a description, history entries given a code)."""
    registry = registry or QueueOrderStore.shared()
    log = log or UploadActivityLog.shared()
    if not registry.is_adopted:
        return 0, 0
    described = registry.backfill_details(videos) if videos else 0
    coded = log.backfill_order_codes(registry)
    return described, coded

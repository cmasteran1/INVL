"""Shared business logic: applying counts, logging events, and queuing the
config changes that physical devices need to pick up on their next sync."""

from __future__ import annotations

import json
import sqlite3
from typing import Optional

from .db import ONLINE_WINDOW_SECONDS, clamp_count, utcnow_iso


def is_online(last_seen: Optional[str]) -> bool:
    """Online if last_seen is within the online window. Compared as ISO-8601
    UTC strings via timestamp math."""
    if not last_seen:
        return False
    from datetime import datetime, timezone

    try:
        seen = datetime.strptime(last_seen, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        return False
    delta = (datetime.now(timezone.utc) - seen).total_seconds()
    return delta <= ONLINE_WINDOW_SECONDS


def log_event(
    conn: sqlite3.Connection,
    *,
    item_id: Optional[int],
    device_id: Optional[str],
    source: str,
    delta: Optional[int],
    count_before: Optional[int],
    count_after: Optional[int],
    reason: Optional[str],
    raw_payload: Optional[dict] = None,
) -> None:
    conn.execute(
        """INSERT INTO events
           (timestamp, item_id, device_id, source, delta, count_before, count_after, reason, raw_payload)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            utcnow_iso(),
            item_id,
            device_id,
            source,
            delta,
            count_before,
            count_after,
            reason,
            json.dumps(raw_payload) if raw_payload is not None else None,
        ),
    )


def apply_count(
    conn: sqlite3.Connection,
    *,
    item: sqlite3.Row,
    new_count: int,
    source: str,
    reason: str,
    device_id: Optional[str] = None,
    raw_payload: Optional[dict] = None,
) -> int:
    """Set an item's count to new_count (floored at 0), log the event, and bump
    the assigned device's config so it re-syncs. Returns the official count."""
    before = item["current_count"]
    official = clamp_count(new_count)
    conn.execute(
        "UPDATE items SET current_count = ?, last_updated = ? WHERE item_id = ?",
        (official, utcnow_iso(), item["item_id"]),
    )
    log_event(
        conn,
        item_id=item["item_id"],
        device_id=device_id or item["device_id"],
        source=source,
        delta=official - before,
        count_before=before,
        count_after=official,
        reason=reason,
        raw_payload=raw_payload,
    )
    return official


def queue_device_sync(
    conn: sqlite3.Connection,
    *,
    device_id: str,
    item_code: Optional[str],
    new_count: Optional[int],
    new_low_threshold: Optional[int],
) -> None:
    """Bump a device's config_version and record a pending update so the device
    knows its assigned item's state changed on the next sync poll."""
    row = conn.execute(
        "SELECT config_version FROM devices WHERE device_id = ?", (device_id,)
    ).fetchone()
    if row is None:
        return
    new_version = (row["config_version"] or 0) + 1
    conn.execute(
        "UPDATE devices SET config_version = ? WHERE device_id = ?",
        (new_version, device_id),
    )
    conn.execute(
        """INSERT INTO pending_device_updates
           (device_id, new_item_code, new_count, new_low_threshold, config_version, created_at, status)
           VALUES (?, ?, ?, ?, ?, ?, 'pending')""",
        (device_id, item_code, new_count, new_low_threshold, new_version, utcnow_iso()),
    )


def device_for_item(conn: sqlite3.Connection, item_id: int) -> Optional[str]:
    """Return the device_id currently assigned to display this item, if any."""
    row = conn.execute(
        "SELECT device_id FROM devices WHERE assigned_item_id = ?", (item_id,)
    ).fetchone()
    return row["device_id"] if row else None

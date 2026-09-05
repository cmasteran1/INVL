"""SQLite access layer for Inventory Hub.

The database file is the single source of truth. We keep one connection per
process (SQLite handles concurrency fine for a single local app) guarded by a
lock so the FastAPI worker threads don't trip over each other.
"""

from __future__ import annotations

import os
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path

from .paths import default_db_path, resource_path

# Location of the data file. In a packaged build this resolves to a per-user
# data directory; in development, the project root. Override with INVENTORY_DB.
DB_PATH = default_db_path()
SCHEMA_PATH = resource_path("app", "schema.sql")

# How long (seconds) since last_seen before a device is considered offline.
ONLINE_WINDOW_SECONDS = int(os.environ.get("INVENTORY_ONLINE_WINDOW", "90"))

# Device display limits (firmware shows an unsigned 3-digit count).
COUNT_MIN = 0
COUNT_MAX = 999

_conn: sqlite3.Connection | None = None
_lock = threading.RLock()


def utcnow_iso() -> str:
    """Current UTC time as an ISO-8601 string with a trailing Z."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def get_conn() -> sqlite3.Connection:
    """Return the shared connection, initializing the schema on first use."""
    global _conn
    with _lock:
        if _conn is None:
            _conn = sqlite3.connect(DB_PATH, check_same_thread=False)
            _conn.row_factory = sqlite3.Row
            _conn.execute("PRAGMA foreign_keys = ON")
            _conn.execute("PRAGMA journal_mode = WAL")
            _init_schema(_conn)
        return _conn


# Columns added after the first release. schema.sql uses CREATE TABLE IF NOT
# EXISTS, which silently does nothing to a database that already exists, so new
# columns have to be ALTERed in explicitly.
_MIGRATIONS = [
    ("devices", "last_boot_id", "TEXT"),
    ("devices", "last_event_seq", "INTEGER NOT NULL DEFAULT -1"),
    ("items", "override_epoch", "INTEGER NOT NULL DEFAULT 0"),
]


def _migrate(conn: sqlite3.Connection) -> None:
    for table, column, decl in _MIGRATIONS:
        cols = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}
        if column not in cols:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")
            print(f"[db] migrated: added {table}.{column}")


def _init_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA_PATH.read_text())
    _migrate(conn)
    conn.commit()


class transaction:
    """Context manager yielding the connection and committing/rolling back.

    Serializes writers via the module lock so concurrent requests can't
    interleave a read-modify-write on a count.
    """

    def __enter__(self) -> sqlite3.Connection:
        _lock.acquire()
        self.conn = get_conn()
        return self.conn

    def __exit__(self, exc_type, exc, tb):
        try:
            if exc_type is None:
                self.conn.commit()
            else:
                self.conn.rollback()
        finally:
            _lock.release()
        return False


def clamp_count(value: int) -> int:
    """Floor the count at 0. Software is the source of truth, so we do not cap
    the upper bound here — the device firmware caps its own display at 999."""
    return max(COUNT_MIN, value)

"""File operations the desktop window performs through native dialogs.

The dashboard runs inside a pywebview window, and a web view is not a browser:
an ``<a href="/api/export.csv">`` link navigates the whole window to the CSV
text (the dashboard vanishes, which looks like the app closed), and a
``download`` link is silently cancelled. So saving and restoring go through
these functions instead, called from desktop.py after the user picks a path in
a native Save/Open dialog.

They are deliberately NOT exposed over HTTP with a caller-chosen path: the
server listens on the LAN for devices, and nothing on the network should be
able to write files on this computer or replace its database.
"""

from __future__ import annotations

import csv
import io
import sqlite3
from pathlib import Path

from . import db, paths
from .db import get_conn, transaction, utcnow_iso

# Tables a file must have to be accepted as an INVL Hub backup.
_REQUIRED_TABLES = {"items", "devices", "events"}


def _stamp() -> str:
    return utcnow_iso().replace(":", "").replace("-", "")  # 20260620T162654Z


def export_filename() -> str:
    return f"inventory_export_{_stamp()[:8]}.csv"


def backup_filename() -> str:
    return f"inventory_backup_{_stamp()}.sqlite"


def build_csv() -> str:
    """The inventory as CSV text (the same rows the dashboard table shows)."""
    from .routes_items import _item_row  # local import: routes_items imports us

    with transaction() as conn:
        rows = conn.execute(
            "SELECT * FROM items WHERE is_active = 1 ORDER BY item_name COLLATE NOCASE"
        ).fetchall()
        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow(
            ["item_code", "item_name", "location", "unit_name", "current_count",
             "low_threshold", "unit_cost", "status", "last_updated", "device_id", "device_online"]
        )
        for r in rows:
            rec = _item_row(conn, r)
            dev = rec["device"]
            writer.writerow([
                rec["item_code"], rec["item_name"], rec["location"], rec["unit_name"],
                rec["current_count"], rec["low_threshold"],
                "" if rec["unit_cost"] is None else rec["unit_cost"], rec["status"], rec["last_updated"],
                dev["device_id"] if dev else "", (dev["online"] if dev else ""),
            ])
        return buf.getvalue()


def write_csv(dest: str | Path) -> dict:
    text = build_csv()
    # utf-8-sig so Excel on Windows reads non-ASCII item names correctly.
    Path(dest).write_text(text, encoding="utf-8-sig", newline="")
    return {"path": str(dest), "rows": max(text.count("\n") - 1, 0)}


def write_backup(dest: str | Path) -> dict:
    """Write a transactionally consistent copy of the live database using
    SQLite's online backup API (safe while devices are reporting)."""
    with db._lock:
        src = get_conn()
        out = sqlite3.connect(str(dest))
        try:
            with out:
                src.backup(out)
        finally:
            out.close()
    return {"path": str(dest)}


def snapshot() -> dict:
    """Timestamped backup into the per-user data folder."""
    backups = paths.user_data_dir() / "backups"
    backups.mkdir(parents=True, exist_ok=True)
    return write_backup(backups / backup_filename())


class RestoreError(Exception):
    """The chosen file can't be restored; the message is shown to the user."""


def validate_backup(src_path: str | Path) -> dict:
    """Check a file really is a readable INVL Hub database before touching the
    live one. Returns a short summary for the confirmation prompt."""
    p = Path(src_path)
    if not p.is_file():
        raise RestoreError("That file doesn't exist.")
    try:
        conn = sqlite3.connect(f"file:{p.as_posix()}?mode=ro", uri=True)
    except sqlite3.Error as exc:
        raise RestoreError(f"Couldn't open that file: {exc}") from exc
    try:
        conn.row_factory = sqlite3.Row
        try:
            ok = conn.execute("PRAGMA integrity_check").fetchone()[0]
            tables = {r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'")}
        except sqlite3.DatabaseError as exc:
            raise RestoreError("That file isn't an INVL Hub backup.") from exc
        if ok != "ok":
            raise RestoreError("That backup is damaged and can't be restored.")
        if not _REQUIRED_TABLES <= tables:
            raise RestoreError("That file isn't an INVL Hub backup.")
        items = conn.execute("SELECT COUNT(*) FROM items WHERE is_active = 1").fetchone()[0]
        devices = conn.execute("SELECT COUNT(*) FROM devices").fetchone()[0]
        last = conn.execute("SELECT MAX(timestamp) FROM events").fetchone()[0]
        return {"items": items, "devices": devices, "last_event": last}
    finally:
        conn.close()


def restore_backup(src_path: str | Path) -> dict:
    """Replace the live database with a backup.

    The current database is snapshotted first, so a restore is itself
    undoable. Restoring is treated like a manager "set exact" on every item:
    device batches counted against the pre-restore numbers are superseded, and
    every device is told to pick up the restored count on its next check-in.
    """
    summary = validate_backup(src_path)
    safety = snapshot()["path"]

    with db._lock:
        live = get_conn()
        # Remember sync state the devices already hold, so restoring an older
        # file can't move version counters backwards (a device would then
        # ignore the restored values, or replay a batch it already sent).
        old_items = {r["item_id"]: r["override_epoch"]
                     for r in live.execute("SELECT item_id, override_epoch FROM items")}
        old_devices = {r["device_id"]: dict(r) for r in live.execute(
            "SELECT device_id, config_version, last_boot_id, last_event_seq FROM devices")}

        src = sqlite3.connect(f"file:{Path(src_path).as_posix()}?mode=ro", uri=True)
        try:
            # A backup can't be copied into a WAL database whose page size
            # differs, so drop out of WAL for the copy. This is the only
            # connection, so the mode change is allowed.
            live.execute("PRAGMA journal_mode = DELETE")
            src.backup(live)
        finally:
            src.close()
            live.execute("PRAGMA journal_mode = WAL")
            live.execute("PRAGMA foreign_keys = ON")

        # Backups from older versions may lack newer columns.
        db._init_schema(live)

        try:
            from .service import queue_device_sync

            for it in live.execute("SELECT item_id, override_epoch FROM items").fetchall():
                epoch = max(it["override_epoch"], old_items.get(it["item_id"], 0)) + 1
                live.execute("UPDATE items SET override_epoch = ? WHERE item_id = ?",
                             (epoch, it["item_id"]))
            for dev in live.execute("SELECT * FROM devices").fetchall():
                prev = old_devices.get(dev["device_id"])
                if prev:
                    live.execute(
                        """UPDATE devices SET config_version = ?, last_boot_id = ?,
                           last_event_seq = ? WHERE device_id = ?""",
                        (max(dev["config_version"], prev["config_version"]),
                         prev["last_boot_id"], prev["last_event_seq"], dev["device_id"]))
                item = live.execute(
                    "SELECT item_code, current_count, low_threshold FROM items WHERE item_id = ?",
                    (dev["assigned_item_id"],)).fetchone() if dev["assigned_item_id"] else None
                queue_device_sync(
                    live, device_id=dev["device_id"],
                    item_code=item["item_code"] if item else None,
                    new_count=item["current_count"] if item else None,
                    new_low_threshold=item["low_threshold"] if item else None,
                )
            live.execute(
                """INSERT INTO events (timestamp, source, reason)
                   VALUES (?, 'software', ?)""",
                (utcnow_iso(), f"database restored from {Path(src_path).name}"),
            )
            live.commit()
        except Exception:
            live.rollback()
            raise

    return {**summary, "safety_backup": safety, "path": str(src_path)}

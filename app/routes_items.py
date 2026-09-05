"""Manager-facing endpoints used by the dashboard UI."""

from __future__ import annotations

import csv
import io
import os
import sqlite3
import tempfile

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from starlette.background import BackgroundTask

from . import mdns, paths
from .db import DB_PATH, get_conn, transaction, utcnow_iso
from .models import (
    AdjustRequest,
    AssignRequest,
    DeviceCreate,
    DeviceUpdate,
    ItemCreate,
    ItemUpdate,
)
from .service import apply_count, is_online, log_event, queue_device_sync

router = APIRouter(prefix="/api", tags=["ui"])


def _item_status(count: int, threshold: int | None) -> str:
    if threshold is not None and count <= threshold:
        return "LOW"
    return "OK"


def _item_row(conn, row) -> dict:
    """Shape an items row joined with its device into a dashboard record."""
    device = None
    dev = conn.execute(
        "SELECT * FROM devices WHERE assigned_item_id = ?", (row["item_id"],)
    ).fetchone()
    if dev:
        device = {
            "device_id": dev["device_id"],
            "nickname": dev["nickname"],
            "online": is_online(dev["last_seen"]),
            "last_seen": dev["last_seen"],
            "battery_level": dev["battery_level"],
            "config_version": dev["config_version"],
            "last_ack_config_version": dev["last_ack_config_version"],
        }
    return {
        "item_id": row["item_id"],
        "item_code": row["item_code"],
        "item_name": row["item_name"],
        "location": row["location"],
        "unit_name": row["unit_name"],
        "current_count": row["current_count"],
        "low_threshold": row["low_threshold"],
        "is_active": bool(row["is_active"]),
        "last_updated": row["last_updated"],
        "status": _item_status(row["current_count"], row["low_threshold"]),
        "device": device,
    }


# --- Items ------------------------------------------------------------------

@router.get("/items")
def list_items(include_inactive: bool = False):
    with transaction() as conn:
        sql = "SELECT * FROM items"
        if not include_inactive:
            sql += " WHERE is_active = 1"
        sql += " ORDER BY item_name COLLATE NOCASE"
        rows = conn.execute(sql).fetchall()
        return [_item_row(conn, r) for r in rows]


@router.get("/items/{item_id}")
def get_item(item_id: int):
    with transaction() as conn:
        row = conn.execute("SELECT * FROM items WHERE item_id = ?", (item_id,)).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="item not found")
        return _item_row(conn, row)


@router.post("/items", status_code=201)
def create_item(payload: ItemCreate):
    with transaction() as conn:
        cur = conn.execute(
            """INSERT INTO items
               (item_code, item_name, location, unit_name, current_count, low_threshold, last_updated)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                payload.item_code.upper(),
                payload.item_name,
                payload.location,
                payload.unit_name,
                max(0, payload.current_count),
                payload.low_threshold,
                utcnow_iso(),
            ),
        )
        item_id = cur.lastrowid
        item = conn.execute("SELECT * FROM items WHERE item_id = ?", (item_id,)).fetchone()
        log_event(
            conn, item_id=item_id, device_id=None, source="software", delta=item["current_count"],
            count_before=0, count_after=item["current_count"], reason="item_created",
        )
        if payload.device_id:
            _assign(conn, payload.device_id, item_id)
        return _item_row(conn, conn.execute(
            "SELECT * FROM items WHERE item_id = ?", (item_id,)).fetchone())


@router.patch("/items/{item_id}")
def update_item(item_id: int, payload: ItemUpdate):
    with transaction() as conn:
        item = conn.execute("SELECT * FROM items WHERE item_id = ?", (item_id,)).fetchone()
        if item is None:
            raise HTTPException(status_code=404, detail="item not found")

        fields = payload.model_dump(exclude_unset=True)
        if "item_code" in fields and fields["item_code"]:
            fields["item_code"] = fields["item_code"].upper()
        if "is_active" in fields:
            fields["is_active"] = 1 if fields["is_active"] else 0

        if fields:
            sets = ", ".join(f"{k} = ?" for k in fields)
            conn.execute(
                f"UPDATE items SET {sets} WHERE item_id = ?",
                (*fields.values(), item_id),
            )
            log_event(
                conn, item_id=item_id, device_id=item["device_id"], source="software",
                delta=None, count_before=item["current_count"], count_after=item["current_count"],
                reason="metadata_update", raw_payload=fields,
            )
            # If a device shows this item, push the new code/threshold.
            dev = conn.execute(
                "SELECT device_id FROM devices WHERE assigned_item_id = ?", (item_id,)
            ).fetchone()
            if dev and ("item_code" in fields or "low_threshold" in fields):
                updated = conn.execute(
                    "SELECT * FROM items WHERE item_id = ?", (item_id,)).fetchone()
                queue_device_sync(
                    conn, device_id=dev["device_id"], item_code=updated["item_code"],
                    new_count=updated["current_count"], new_low_threshold=updated["low_threshold"],
                )
        return _item_row(conn, conn.execute(
            "SELECT * FROM items WHERE item_id = ?", (item_id,)).fetchone())


@router.post("/items/{item_id}/adjust")
def adjust_item(item_id: int, payload: AdjustRequest):
    with transaction() as conn:
        item = conn.execute("SELECT * FROM items WHERE item_id = ?", (item_id,)).fetchone()
        if item is None:
            raise HTTPException(status_code=404, detail="item not found")

        if payload.mode == "delta":
            if payload.amount is None:
                raise HTTPException(status_code=400, detail="amount required for delta mode")
            new_count = item["current_count"] + payload.amount
            reason = payload.reason or "manual_adjustment"
        else:  # set
            if payload.count is None:
                raise HTTPException(status_code=400, detail="count required for set mode")
            new_count = payload.count
            reason = payload.reason or "manual_set"
            # A "set" is a physical recount, so it supersedes anything a device
            # counted beforehand. Bumping the epoch tells the hub to drop device
            # batches that were still accumulating when this happened, instead of
            # applying them on top and undoing the recount. A "delta" adjustment
            # deliberately does NOT bump it — an addition and a device's
            # subtractions are both real and must combine.
            conn.execute(
                "UPDATE items SET override_epoch = override_epoch + 1 WHERE item_id = ?",
                (item_id,),
            )

        official = apply_count(
            conn, item=item, new_count=new_count, source="software", reason=reason,
            raw_payload=payload.model_dump(exclude_none=True),
        )
        dev = conn.execute(
            "SELECT device_id FROM devices WHERE assigned_item_id = ?", (item_id,)
        ).fetchone()
        if dev:
            queue_device_sync(
                conn, device_id=dev["device_id"], item_code=item["item_code"],
                new_count=official, new_low_threshold=item["low_threshold"],
            )
        return _item_row(conn, conn.execute(
            "SELECT * FROM items WHERE item_id = ?", (item_id,)).fetchone())


@router.delete("/items/{item_id}")
def delete_item(item_id: int):
    """Remove an item from the list entirely.

    Any device assigned to it is detached (left registered but unassigned).
    History rows are kept for the audit trail (their item_id is set NULL by the
    foreign key).
    """
    with transaction() as conn:
        item = conn.execute("SELECT item_id FROM items WHERE item_id = ?", (item_id,)).fetchone()
        if item is None:
            raise HTTPException(status_code=404, detail="item not found")
        # Detach any device pointing at this item so it doesn't dangle.
        conn.execute(
            "UPDATE devices SET assigned_item_id = NULL WHERE assigned_item_id = ?", (item_id,)
        )
        conn.execute("DELETE FROM items WHERE item_id = ?", (item_id,))
        return {"status": "ok", "item_id": item_id}


@router.get("/items/{item_id}/history")
def item_history(item_id: int, limit: int = 200):
    with transaction() as conn:
        rows = conn.execute(
            "SELECT * FROM events WHERE item_id = ? ORDER BY timestamp DESC, event_id DESC LIMIT ?",
            (item_id, limit),
        ).fetchall()
        return [dict(r) for r in rows]


# --- Devices ----------------------------------------------------------------

def _assign(conn, device_id: str, item_id: int | None) -> None:
    """Maintain the 1:1 device<->item link on both sides consistently."""
    device = conn.execute(
        "SELECT * FROM devices WHERE device_id = ?", (device_id,)
    ).fetchone()
    if device is None:
        raise HTTPException(status_code=404, detail="device not found")

    # Clear this device's previous item.
    if device["assigned_item_id"] is not None:
        conn.execute(
            "UPDATE items SET device_id = NULL WHERE item_id = ?", (device["assigned_item_id"],)
        )

    if item_id is not None:
        item = conn.execute("SELECT * FROM items WHERE item_id = ?", (item_id,)).fetchone()
        if item is None:
            raise HTTPException(status_code=404, detail="item not found")
        # Detach any other device currently on this item.
        other = conn.execute(
            "SELECT device_id FROM devices WHERE assigned_item_id = ? AND device_id != ?",
            (item_id, device_id),
        ).fetchone()
        if other:
            conn.execute(
                "UPDATE devices SET assigned_item_id = NULL WHERE device_id = ?",
                (other["device_id"],),
            )
        conn.execute(
            "UPDATE devices SET assigned_item_id = ? WHERE device_id = ?", (item_id, device_id)
        )
        conn.execute(
            "UPDATE items SET device_id = ? WHERE item_id = ?", (device_id, item_id)
        )
        log_event(
            conn, item_id=item_id, device_id=device_id, source="software", delta=None,
            count_before=item["current_count"], count_after=item["current_count"],
            reason="item_reassignment",
        )
        queue_device_sync(
            conn, device_id=device_id, item_code=item["item_code"],
            new_count=item["current_count"], new_low_threshold=item["low_threshold"],
        )
    else:
        conn.execute(
            "UPDATE devices SET assigned_item_id = NULL WHERE device_id = ?", (device_id,)
        )


@router.get("/devices")
def list_devices():
    with transaction() as conn:
        rows = conn.execute("SELECT * FROM devices ORDER BY device_id").fetchall()
        out = []
        for d in rows:
            rec = dict(d)
            rec["is_active"] = bool(d["is_active"])
            rec["online"] = is_online(d["last_seen"])
            if d["assigned_item_id"]:
                item = conn.execute(
                    "SELECT item_code, item_name FROM items WHERE item_id = ?",
                    (d["assigned_item_id"],),
                ).fetchone()
                rec["assigned_item"] = dict(item) if item else None
            else:
                rec["assigned_item"] = None
            out.append(rec)
        return out


@router.post("/devices", status_code=201)
def register_device(payload: DeviceCreate):
    with transaction() as conn:
        existing = conn.execute(
            "SELECT device_id FROM devices WHERE device_id = ?", (payload.device_id,)
        ).fetchone()
        if existing:
            raise HTTPException(status_code=409, detail="device already registered")
        conn.execute(
            """INSERT INTO devices (device_id, nickname, device_key, last_seen)
               VALUES (?, ?, ?, ?)""",
            (payload.device_id, payload.nickname, payload.device_key, None),
        )
        # Clear any pending sighting now that it's registered.
        conn.execute(
            "DELETE FROM unregistered_sightings WHERE device_id = ?", (payload.device_id,)
        )
        if payload.assigned_item_id is not None:
            _assign(conn, payload.device_id, payload.assigned_item_id)
        return {"status": "ok", "device_id": payload.device_id}


@router.patch("/devices/{device_id}")
def update_device(device_id: str, payload: DeviceUpdate):
    with transaction() as conn:
        device = conn.execute(
            "SELECT device_id FROM devices WHERE device_id = ?", (device_id,)
        ).fetchone()
        if device is None:
            raise HTTPException(status_code=404, detail="device not found")
        fields = payload.model_dump(exclude_unset=True)
        if "is_active" in fields:
            fields["is_active"] = 1 if fields["is_active"] else 0
        if fields:
            sets = ", ".join(f"{k} = ?" for k in fields)
            conn.execute(
                f"UPDATE devices SET {sets} WHERE device_id = ?", (*fields.values(), device_id)
            )
        return {"status": "ok", "device_id": device_id}


@router.post("/devices/{device_id}/assign")
def assign_device(device_id: str, payload: AssignRequest):
    with transaction() as conn:
        _assign(conn, device_id, payload.item_id)
        return {"status": "ok", "device_id": device_id, "item_id": payload.item_id}


@router.delete("/devices/{device_id}")
def forget_device(device_id: str):
    """Forget (deregister) a device entirely.

    Detaches it from any item, drops its pending sync queue, and removes the
    device row. Historical event rows are kept for the audit trail (their
    device_id is set NULL by the foreign key). Any future contact from this
    device would simply re-appear as a newly discovered, unregistered device.
    """
    with transaction() as conn:
        device = conn.execute(
            "SELECT assigned_item_id FROM devices WHERE device_id = ?", (device_id,)
        ).fetchone()
        if device is None:
            raise HTTPException(status_code=404, detail="device not found")

        # Detach from its item first so items.device_id doesn't dangle.
        if device["assigned_item_id"] is not None:
            conn.execute(
                "UPDATE items SET device_id = NULL WHERE device_id = ?", (device_id,)
            )
        # pending_device_updates has ON DELETE CASCADE; events/items use SET NULL.
        conn.execute("DELETE FROM devices WHERE device_id = ?", (device_id,))
        # Also clear any stale "unregistered" sighting so it doesn't linger.
        conn.execute(
            "DELETE FROM unregistered_sightings WHERE device_id = ?", (device_id,)
        )
        return {"status": "ok", "device_id": device_id}


@router.get("/devices/unknown")
def list_unknown_devices():
    """Unregistered devices that have tried to contact the server."""
    with transaction() as conn:
        rows = conn.execute(
            "SELECT * FROM unregistered_sightings ORDER BY last_seen DESC"
        ).fetchall()
        return [dict(r) for r in rows]


@router.delete("/devices/unknown/{device_id}")
def dismiss_unknown_device(device_id: str):
    with transaction() as conn:
        conn.execute("DELETE FROM unregistered_sightings WHERE device_id = ?", (device_id,))
        return {"status": "ok"}


# --- Export -----------------------------------------------------------------

@router.get("/export.csv")
def export_csv():
    with transaction() as conn:
        rows = conn.execute(
            "SELECT * FROM items WHERE is_active = 1 ORDER BY item_name COLLATE NOCASE"
        ).fetchall()
        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow(
            ["item_code", "item_name", "location", "unit_name", "current_count",
             "low_threshold", "status", "last_updated", "device_id", "device_online"]
        )
        for r in rows:
            rec = _item_row(conn, r)
            dev = rec["device"]
            writer.writerow([
                rec["item_code"], rec["item_name"], rec["location"], rec["unit_name"],
                rec["current_count"], rec["low_threshold"], rec["status"], rec["last_updated"],
                dev["device_id"] if dev else "", (dev["online"] if dev else ""),
            ])
        buf.seek(0)
        return StreamingResponse(
            iter([buf.getvalue()]),
            media_type="text/csv",
            headers={"Content-Disposition": "attachment; filename=inventory_export.csv"},
        )


# --- Backup & info ----------------------------------------------------------

def _consistent_backup(dest_path: str) -> None:
    """Write a transactionally-consistent copy of the live DB using SQLite's
    online backup API (safe even while the app is running)."""
    src = get_conn()
    dest = sqlite3.connect(dest_path)
    try:
        with dest:
            src.backup(dest)
    finally:
        dest.close()


@router.get("/backup")
def download_backup():
    """Download a consistent snapshot of the SQLite database file."""
    stamp = utcnow_iso().replace(":", "").replace("-", "")  # 20260620T162654Z
    fd, tmp = tempfile.mkstemp(suffix=".sqlite", prefix="inventory_backup_")
    os.close(fd)
    _consistent_backup(tmp)
    return FileResponse(
        tmp,
        media_type="application/x-sqlite3",
        filename=f"inventory_backup_{stamp}.sqlite",
        background=BackgroundTask(lambda: os.path.exists(tmp) and os.remove(tmp)),
    )


@router.post("/backup/snapshot")
def snapshot_backup():
    """Write a timestamped backup into the local data dir (stays on device)."""
    backups = paths.user_data_dir() / "backups"
    backups.mkdir(parents=True, exist_ok=True)
    stamp = utcnow_iso().replace(":", "").replace("-", "")
    dest = backups / f"inventory_backup_{stamp}.sqlite"
    _consistent_backup(str(dest))
    return {"status": "ok", "path": str(dest)}


@router.get("/about")
def about():
    """Where the data lives and a quick summary — shown in the UI footer."""
    with transaction() as conn:
        items_count = conn.execute("SELECT COUNT(*) AS c FROM items WHERE is_active = 1").fetchone()["c"]
        devices_count = conn.execute("SELECT COUNT(*) AS c FROM devices").fetchone()["c"]
    # Anything that would stop a device from reaching this hub. The packaged app
    # has no console, so these have to reach the operator through the UI or they
    # reach nobody — and a customer who just clicked past a security warning to
    # install this will blame the install, not the network.
    port = int(os.environ.get("PORT", "8000"))
    mdns_state = mdns.status()
    warnings: list[str] = []
    if not mdns_state["ok"]:
        warnings.append(
            f"Devices can't discover this hub automatically: {mdns_state['reason']}. "
            f"Set each device's Hub host to this computer's IP address instead."
        )
    if port != 8000:
        warnings.append(
            f"Running on port {port}, but devices always connect on port 8000. "
            f"Quit whatever is using port 8000, then restart Inventory Hub."
        )
    return {
        "version": "0.1.0",
        "db_path": str(DB_PATH),
        "data_dir": str(paths.user_data_dir()),
        "frozen": paths.is_frozen(),
        "items": items_count,
        "devices": devices_count,
        "port": port,
        "mdns": mdns_state,
        "warnings": warnings,
    }

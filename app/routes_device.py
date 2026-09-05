"""Endpoints the ESP32 counters talk to: event, sync, ack.

Source-of-truth rules (plan §10): the database count wins. A button press sends
a delta; the server applies it to the DB count and returns the official count so
the device can correct its display.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from .db import transaction, utcnow_iso
from .models import DeviceAck, DeviceEvent
from .service import apply_count, log_event

router = APIRouter(prefix="/api/device", tags=["device"])


def _record_sighting(conn, device_id: str, firmware: str | None, local_count: int | None) -> None:
    """Log an unknown device so the manager can register it from the UI."""
    now = utcnow_iso()
    existing = conn.execute(
        "SELECT device_id FROM unregistered_sightings WHERE device_id = ?", (device_id,)
    ).fetchone()
    if existing:
        conn.execute(
            """UPDATE unregistered_sightings
               SET last_seen = ?, attempts = attempts + 1,
                   last_firmware = ?, last_local_count = ?
               WHERE device_id = ?""",
            (now, firmware, local_count, device_id),
        )
    else:
        conn.execute(
            """INSERT INTO unregistered_sightings
               (device_id, first_seen, last_seen, attempts, last_firmware, last_local_count)
               VALUES (?, ?, ?, 1, ?, ?)""",
            (device_id, now, now, firmware, local_count),
        )


@router.post("/event")
def device_event(evt: DeviceEvent):
    # We compute either a result or an error inside the transaction, then act on
    # it after the block closes. Raising *inside* transaction() rolls back, which
    # would discard the unknown-device sighting and the last_seen update — both of
    # which we want to persist even when the request is ultimately rejected.
    result: dict | None = None
    error: tuple[int, dict] | None = None

    with transaction() as conn:
        device = conn.execute(
            "SELECT * FROM devices WHERE device_id = ?", (evt.device_id,)
        ).fetchone()

        if device is None:
            # Strict rejection of unknown devices — but capture the sighting so
            # the manager can register it from the dashboard.
            _record_sighting(conn, evt.device_id, evt.firmware_version, evt.local_count)
            error = (404, {"status": "unregistered_device", "device_id": evt.device_id})
        elif device["device_key"] and device["device_key"] != evt.device_key:
            error = (401, {"status": "invalid_device_key"})
        else:
            # Update device liveness/metadata on every authenticated contact.
            conn.execute(
                "UPDATE devices SET last_seen = ?, firmware_version = COALESCE(?, firmware_version), "
                "battery_level = COALESCE(?, battery_level) WHERE device_id = ?",
                (utcnow_iso(), evt.firmware_version, evt.battery_level, evt.device_id),
            )
            item_id = device["assigned_item_id"]
            item = (
                conn.execute("SELECT * FROM items WHERE item_id = ?", (item_id,)).fetchone()
                if item_id is not None else None
            )
            if not device["is_active"]:
                error = (403, {"status": "device_inactive"})
            elif item is None:
                error = (409, {"status": "unassigned_device"})
            else:
                # Duplicate suppression. The device retries a report verbatim
                # when the reply is lost, so the same batch can arrive more than
                # once — including out of order, after a newer one. Applying it
                # twice is how a single press became +2. (boot_id, seq)
                # identifies the batch; anything at or below the last applied
                # seq for the same boot has already been counted.
                #
                # Firmware older than 0.5.0 sends neither field and is handled
                # exactly as before — unguarded, but no worse than it was.
                duplicate = (
                    evt.boot_id is not None
                    and evt.seq is not None
                    and device["last_boot_id"] == evt.boot_id
                    and evt.seq <= device["last_event_seq"]
                )

                # Batched reporting: delta is the NET change since the device's
                # last successful report. delta == 0 is a heartbeat/sync — it
                # logs nothing but returns the authoritative state so the device
                # can pick up manager-side edits and refresh last_seen.
                # Superseded by a manager recount. The device batched these
                # presses before it learned of a "set exact", so applying them
                # would undo the physical count the manager just took. Drop the
                # batch and hand back the authoritative number; the device
                # re-baselines to it and its NEXT batch carries the new epoch.
                superseded = (
                    evt.epoch is not None
                    and evt.epoch < item["override_epoch"]
                    and evt.delta != 0
                )

                if duplicate:
                    # Already counted. Return the authoritative state so the
                    # device can still clear its pending delta and move on.
                    print(f"[device] duplicate batch ignored: {evt.device_id} "
                          f"seq={evt.seq} delta={evt.delta}")
                    official = item["current_count"]
                elif superseded:
                    print(f"[device] batch superseded by a manager set: "
                          f"{evt.device_id} delta={evt.delta} "
                          f"device_epoch={evt.epoch} item_epoch={item['override_epoch']}")
                    log_event(
                        conn, item_id=item["item_id"], device_id=evt.device_id,
                        source="device_button", delta=None,
                        count_before=item["current_count"],
                        count_after=item["current_count"],
                        reason="superseded_by_manual_set",
                        raw_payload=evt.model_dump(),
                    )
                    official = item["current_count"]
                elif evt.delta != 0:
                    official = apply_count(
                        conn,
                        item=item,
                        new_count=item["current_count"] + evt.delta,
                        source="device_button",
                        reason="device_batch",
                        device_id=evt.device_id,
                        raw_payload=evt.model_dump(),
                    )
                else:
                    official = item["current_count"]

                if not duplicate and evt.boot_id is not None and evt.seq is not None:
                    # Superseded batches count as processed: a retry of the same
                    # batch must not be re-examined.
                    conn.execute(
                        "UPDATE devices SET last_boot_id = ?, last_event_seq = ? "
                        "WHERE device_id = ?",
                        (evt.boot_id, evt.seq, evt.device_id),
                    )
                result = {
                    "status": "ok",
                    "device_id": evt.device_id,
                    "item_code": item["item_code"],
                    "official_count": official,
                    "low_threshold": item["low_threshold"],
                    "config_version": device["config_version"],
                    # The device must receive this or it can never advance past a
                    # set, and every later batch would be dropped as stale.
                    "override_epoch": item["override_epoch"],
                }

    if error:
        raise HTTPException(status_code=error[0], detail=error[1])
    return result


@router.get("/{device_id}/sync")
def device_sync(device_id: str):
    """Devices poll this for the authoritative count/code. Returns live DB state."""
    with transaction() as conn:
        device = conn.execute(
            "SELECT * FROM devices WHERE device_id = ?", (device_id,)
        ).fetchone()
        if device is None:
            raise HTTPException(
                status_code=404,
                detail={"status": "unregistered_device", "device_id": device_id},
            )

        conn.execute(
            "UPDATE devices SET last_seen = ? WHERE device_id = ?",
            (utcnow_iso(), device_id),
        )

        item_id = device["assigned_item_id"]
        if item_id is None:
            return {"status": "unassigned", "device_id": device_id,
                    "config_version": device["config_version"]}

        item = conn.execute("SELECT * FROM items WHERE item_id = ?", (item_id,)).fetchone()
        if item is None:
            return {"status": "unassigned", "device_id": device_id,
                    "config_version": device["config_version"]}

        return {
            "status": "ok",
            "device_id": device_id,
            "item_code": item["item_code"],
            "official_count": item["current_count"],
            "low_threshold": item["low_threshold"],
            "config_version": device["config_version"],
            "override_epoch": item["override_epoch"],
        }


@router.post("/ack")
def device_ack(ack: DeviceAck):
    with transaction() as conn:
        device = conn.execute(
            "SELECT device_id FROM devices WHERE device_id = ?", (ack.device_id,)
        ).fetchone()
        if device is None:
            raise HTTPException(status_code=404, detail={"status": "unregistered_device"})

        conn.execute(
            "UPDATE devices SET last_ack_config_version = ?, last_seen = ? WHERE device_id = ?",
            (ack.ack_config_version, utcnow_iso(), ack.device_id),
        )
        conn.execute(
            """UPDATE pending_device_updates
               SET status = 'acknowledged', acknowledged_at = ?
               WHERE device_id = ? AND config_version <= ? AND status = 'pending'""",
            (utcnow_iso(), ack.device_id, ack.ack_config_version),
        )
        return {"status": "ok", "device_id": ack.device_id}

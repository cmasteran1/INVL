"""Pydantic request/response models for the Inventory Hub API."""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field


# --- Device-facing models ---------------------------------------------------

class DeviceEvent(BaseModel):
    device_id: str
    device_key: Optional[str] = None
    # Net accumulated change since the device's last successful report. Batched
    # reporting means this can be any magnitude, and 0 is a valid heartbeat that
    # just syncs the device (updates last_seen, returns the official state).
    delta: int = 0
    local_count: Optional[int] = None
    firmware_version: Optional[str] = None
    battery_level: Optional[int] = None
    # Idempotency key. A report is retried verbatim when its reply is lost, so
    # (boot_id, seq) identifies the *batch* rather than the request: a retry
    # carries the same pair and must not be applied twice. boot_id changes on
    # every cold boot, which is what lets seq restart from zero safely.
    # Optional so firmware predating 0.5.0 keeps working (unguarded, as before).
    boot_id: Optional[str] = None
    seq: Optional[int] = None
    # The override epoch the device believes is current. If the hub has moved
    # past it, a manager has set an exact count since this batch started
    # accumulating, and the batch is superseded. Optional for older firmware.
    epoch: Optional[int] = None


class DeviceAck(BaseModel):
    device_id: str
    ack_config_version: int


# --- UI-facing models -------------------------------------------------------

class AdjustRequest(BaseModel):
    mode: Literal["delta", "set"]
    amount: Optional[int] = None   # used when mode == "delta"
    count: Optional[int] = None    # used when mode == "set"
    reason: Optional[str] = None


class ItemCreate(BaseModel):
    item_code: str = Field(min_length=1, max_length=5)
    item_name: str = Field(min_length=1)
    location: Optional[str] = None
    unit_name: Optional[str] = None
    current_count: int = 0
    low_threshold: Optional[int] = None
    device_id: Optional[str] = None  # optionally assign a device on creation


class ItemUpdate(BaseModel):
    item_code: Optional[str] = Field(default=None, min_length=1, max_length=5)
    item_name: Optional[str] = Field(default=None, min_length=1)
    location: Optional[str] = None
    unit_name: Optional[str] = None
    low_threshold: Optional[int] = None
    is_active: Optional[bool] = None


class DeviceCreate(BaseModel):
    device_id: str = Field(min_length=1)
    nickname: Optional[str] = None
    device_key: Optional[str] = None
    assigned_item_id: Optional[int] = None


class DeviceUpdate(BaseModel):
    nickname: Optional[str] = None
    device_key: Optional[str] = None
    is_active: Optional[bool] = None


class AssignRequest(BaseModel):
    item_id: Optional[int] = None  # null/omitted unassigns the device

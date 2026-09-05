-- Inventory Hub local schema. SQLite is the source of truth.
-- All timestamps are stored as ISO-8601 UTC strings (e.g. 2026-06-20T17:04:00Z).

PRAGMA foreign_keys = ON;

-- Physical ESP32 counter devices.
CREATE TABLE IF NOT EXISTS devices (
    device_id               TEXT PRIMARY KEY,
    nickname                TEXT,
    assigned_item_id        INTEGER,
    firmware_version        TEXT,
    battery_level           INTEGER,
    last_seen               TEXT,
    config_version          INTEGER NOT NULL DEFAULT 1,
    last_ack_config_version INTEGER NOT NULL DEFAULT 0,
    is_active               INTEGER NOT NULL DEFAULT 1,
    device_key              TEXT,
    last_boot_id            TEXT,
    last_event_seq          INTEGER NOT NULL DEFAULT -1,
    FOREIGN KEY (assigned_item_id) REFERENCES items(item_id) ON DELETE SET NULL
);

-- Current inventory state. One row per tracked item.
CREATE TABLE IF NOT EXISTS items (
    item_id        INTEGER PRIMARY KEY AUTOINCREMENT,
    device_id      TEXT,
    item_code      TEXT NOT NULL,
    item_name      TEXT NOT NULL,
    location       TEXT,
    unit_name      TEXT,
    current_count  INTEGER NOT NULL DEFAULT 0,
    -- Bumped on every "set exact". A device batch stamped with an older epoch
    -- was counted before the manager physically recounted the shelf, so it is
    -- superseded rather than applied on top.
    override_epoch INTEGER NOT NULL DEFAULT 0,
    low_threshold  INTEGER,
    is_active      INTEGER NOT NULL DEFAULT 1,
    last_updated   TEXT,
    FOREIGN KEY (device_id) REFERENCES devices(device_id) ON DELETE SET NULL
);

-- Append-only log of every inventory change.
CREATE TABLE IF NOT EXISTS events (
    event_id      INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp     TEXT NOT NULL,
    item_id       INTEGER,
    device_id     TEXT,
    source        TEXT NOT NULL,   -- device_button | software | system_sync
    delta         INTEGER,
    count_before  INTEGER,
    count_after   INTEGER,
    reason        TEXT,
    raw_payload   TEXT,
    FOREIGN KEY (item_id)   REFERENCES items(item_id)   ON DELETE SET NULL,
    FOREIGN KEY (device_id) REFERENCES devices(device_id) ON DELETE SET NULL
);

-- Changes a physical device still needs to pick up (audit of sync intent).
CREATE TABLE IF NOT EXISTS pending_device_updates (
    update_id        INTEGER PRIMARY KEY AUTOINCREMENT,
    device_id        TEXT NOT NULL,
    new_item_code    TEXT,
    new_count        INTEGER,
    new_low_threshold INTEGER,
    config_version   INTEGER,
    created_at       TEXT NOT NULL,
    acknowledged_at  TEXT,
    status           TEXT NOT NULL DEFAULT 'pending',  -- pending | acknowledged
    FOREIGN KEY (device_id) REFERENCES devices(device_id) ON DELETE CASCADE
);

-- Unknown devices that tried to talk to the server but are not registered.
-- Surfaced in the UI so a manager can register them with one click instead of
-- reading MAC addresses off the serial monitor. Rejected events land here only.
CREATE TABLE IF NOT EXISTS unregistered_sightings (
    device_id        TEXT PRIMARY KEY,
    first_seen       TEXT NOT NULL,
    last_seen        TEXT NOT NULL,
    attempts         INTEGER NOT NULL DEFAULT 1,
    last_firmware    TEXT,
    last_local_count INTEGER
);

CREATE INDEX IF NOT EXISTS idx_events_item_time ON events(item_id, timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_pending_device   ON pending_device_updates(device_id, status);

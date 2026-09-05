# Inventory Hub

Local-first inventory counting server. Physical ESP32 counters with `+`/`−`
buttons report to this app over the LAN; it stores everything in a local SQLite
file and is the source of truth for every count.

No cloud, no accounts, no external database. Everything runs on one computer on
the local Wi-Fi network.

> This repository contains the **hub software only**. The device firmware,
> the PCB/enclosure design and the product's commercial material are kept
> outside it.

## Architecture

```
ESP32 counter ──HTTP──► Inventory Hub (FastAPI) ──► inventory_data.sqlite
   (buttons + OLED)        dashboard at  /            (source of truth)
```

- **Backend:** FastAPI + stdlib `sqlite3` (`app/`)
- **Frontend:** single-page vanilla HTML/CSS/JS dashboard (`app/static/`)

## Running

```bash
./run.sh
```

Creates a virtualenv, installs dependencies, and serves on `http://0.0.0.0:8000`.
Open the dashboard at <http://localhost:8000>.

The hub advertises itself over mDNS as `inventoryhub.local` and as an
`_invhub._tcp` service, so devices find it without a hardcoded IP even when the
host machine's DHCP address changes.

## Configuration

| Env var | Default | Meaning |
|---|---|---|
| `INVENTORY_DB` | `./inventory_data.sqlite` | Database file path |
| `INVENTORY_ONLINE_WINDOW` | `90` | Seconds since `last_seen` before a device is "offline" |
| `PORT` | `8000` | Server port |

## Source-of-truth rules

Counts reconcile in one direction each way: **deltas up, authority down.**

- A device reports a **delta**, never an absolute count, so a manager adding
  stock in the dashboard and staff subtracting it on the buttons both land — the
  hub applies the delta to whatever it currently holds.
- The response carries the authoritative count back down, and the device adopts
  it.
- A manager **"set exact"** is a physical recount and wins outright. The hub
  bumps a per-item `override_epoch`; a device batch stamped with an older epoch
  was counted before the recount, so it is dropped rather than applied on top.
  Dropped batches are recorded in the event log as `superseded_by_manual_set`
  rather than disappearing silently.
- Every report carries a `(boot_id, seq)` idempotency key. A report whose reply
  is lost is retried verbatim, and without this the hub would apply the same
  delta twice — which is exactly how a single button press became `+2`.

## Packaging

Builds a standalone, double-clickable desktop app with no Python install needed
on the target machine — a native window (pywebview) that still serves devices on
the LAN in the background.

```bash
./build.sh        # macOS / Linux   -> dist/InventoryHub.app
build.bat         # Windows         -> dist\InventoryHub\InventoryHub.exe
```

`release.sh` / `release.bat` go further and produce something you can put on a
download page: they ad-hoc sign the macOS bundle, package it with
[INSTALL.md](INSTALL.md), and emit SHA-256 checksums.

PyInstaller cannot cross-compile, so build the Windows executable on Windows.

Set `INVENTORY_NO_WINDOW=1` to run as a headless LAN server with no window.

See [INSTALL.md](INSTALL.md) for the end-user install path, including the
security warnings unsigned builds produce on both platforms and the firewall
prompt — denying that one leaves the dashboard working while every device
silently fails to connect.

## Where data lives (packaged app)

The database stays on the machine, in a per-user folder that survives
reinstalling the app:

| OS | Path |
|---|---|
| macOS | `~/Library/Application Support/InventoryHub/inventory_data.sqlite` |
| Windows | `%APPDATA%\InventoryHub\inventory_data.sqlite` |
| Linux | `~/.local/share/InventoryHub/inventory_data.sqlite` |

## API summary

Device endpoints (`app/routes_device.py`):

- `POST /api/device/event` — batched delta; returns the official count
- `GET  /api/device/{device_id}/sync` — poll for authoritative count/code
- `POST /api/device/ack` — acknowledge a config version

Manager endpoints (`app/routes_items.py`):

- `GET/POST /api/items`, `GET/PATCH /api/items/{id}`, `POST /api/items/{id}/adjust`
- `GET /api/items/{id}/history`
- `GET/POST /api/devices`, `PATCH /api/devices/{id}`, `POST /api/devices/{id}/assign`
- `DELETE /api/devices/{id}` — forget (deregister) a device
- `GET /api/devices/unknown`, `DELETE /api/devices/unknown/{id}`
- `GET /api/export.csv`

Interactive API docs at `/docs` while the server runs.

"""Filesystem path resolution that works both in development and inside a
PyInstaller bundle.

Two distinct kinds of paths:

* **Resources** (schema.sql, the static dashboard) are read-only and ship inside
  the bundle. When frozen, PyInstaller extracts them under ``sys._MEIPASS``.
* **User data** (the SQLite database, backups) must live in a stable, writable
  per-user location — never next to the executable, which may be read-only or a
  temporary extraction dir.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

APP_NAME = "InventoryHub"


def is_frozen() -> bool:
    return getattr(sys, "frozen", False)


def resource_path(*parts: str) -> Path:
    """Locate a bundled read-only resource by its path relative to the project
    root (e.g. resource_path("app", "schema.sql"))."""
    if is_frozen():
        base = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    else:
        # project root is the parent of the app/ package
        base = Path(__file__).resolve().parent.parent
    return base.joinpath(*parts)


def user_data_dir() -> Path:
    """Per-user writable directory for the database and backups.

    macOS:   ~/Library/Application Support/InventoryHub
    Windows: %APPDATA%\\InventoryHub
    Linux:   $XDG_DATA_HOME/InventoryHub (or ~/.local/share/InventoryHub)
    """
    home = Path.home()
    if sys.platform == "darwin":
        base = home / "Library" / "Application Support"
    elif os.name == "nt":
        base = Path(os.environ.get("APPDATA", home / "AppData" / "Roaming"))
    else:
        base = Path(os.environ.get("XDG_DATA_HOME", home / ".local" / "share"))
    d = base / APP_NAME
    d.mkdir(parents=True, exist_ok=True)
    return d


def default_db_path() -> Path:
    """Where the SQLite database lives by default.

    Honors the INVENTORY_DB override (used by tests and power users). When frozen,
    defaults to the per-user data dir. In plain development, defaults to the
    project root so the file is easy to find while hacking.
    """
    override = os.environ.get("INVENTORY_DB")
    if override:
        return Path(override)
    if is_frozen():
        return user_data_dir() / "inventory_data.sqlite"
    return Path(__file__).resolve().parent.parent / "inventory_data.sqlite"

"""Desktop entrypoint for INVL Hub.

Starts the FastAPI/uvicorn server (bound to the LAN so ESP32 devices can still
reach it) and opens the dashboard in a native window via pywebview. If the
window backend isn't available (e.g. a headless environment), it falls back to
opening the default web browser.

Run in development:   python desktop.py
Packaged:             this is the PyInstaller entry script.
"""

from __future__ import annotations

import os
import sys

# A windowed PyInstaller build on Windows has no console, so sys.stdout and
# sys.stderr are None. Anything that probes them crashes the app before the
# window opens — uvicorn's log formatter calls sys.stdout.isatty() while
# uvicorn.Config is being constructed. Route them to devnull instead. This must
# run before uvicorn is imported or configured. (macOS windowed builds get real
# streams, which is why only Windows crashed.)
if sys.stdout is None or sys.stderr is None:
    _devnull = open(os.devnull, "w", encoding="utf-8")
    sys.stdout = sys.stdout or _devnull
    sys.stderr = sys.stderr or _devnull

import json
import socket
import subprocess
import threading
import time
from pathlib import Path

import uvicorn

try:
    import webview  # pywebview; absent in a plain server install
except Exception:  # noqa: BLE001
    webview = None

from app import db, fileops, paths
from app.main import app

# Bind on all interfaces so physical devices on the LAN can post events, but
# point the local window at the loopback address.
HOST = "0.0.0.0"
LOOPBACK = "127.0.0.1"
PREFERRED_PORT = int(os.environ.get("PORT", "8000"))


def _port_is_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind((HOST, port))
            return True
        except OSError:
            return False


def _choose_port() -> int:
    """Prefer 8000 (what devices are configured for); fall back if it's taken."""
    if _port_is_free(PREFERRED_PORT):
        return PREFERRED_PORT
    for port in range(PREFERRED_PORT + 1, PREFERRED_PORT + 50):
        if _port_is_free(port):
            print(f"Port {PREFERRED_PORT} busy; using {port}. "
                  f"Devices configured for {PREFERRED_PORT} won't reach this instance.")
            return port
    raise RuntimeError("No free port found")


def _start_server(port: int) -> uvicorn.Server:
    # app.main's startup hook reads PORT to decide what to advertise over mDNS.
    # If we picked a fallback port and didn't tell it, it would advertise a port
    # nothing is listening on — worse than not advertising at all.
    os.environ["PORT"] = str(port)
    config = uvicorn.Config(app, host=HOST, port=port, log_level="warning")
    server = uvicorn.Server(config)
    global _server, _server_thread
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    _server, _server_thread = server, thread
    # Wait until the server is accepting connections.
    deadline = time.time() + 15
    while not server.started and time.time() < deadline:
        time.sleep(0.05)
    return server


# --- Native file operations, menu, uninstall --------------------------------
#
# A web view is not a browser: it can't take downloads. The dashboard's Export
# CSV and Back up buttons therefore call into Python (Bridge, exposed to the
# page as window.pywebview.api), which shows a native Save dialog and writes the
# file itself. The File menu reaches the same code, plus restore and uninstall.

_window = None          # the pywebview window, once created
_server = None          # the uvicorn server, so uninstall can stop it
_server_thread = None


def _notify(message: str, error: bool = False, reload: bool = False) -> None:
    """Show a toast in the dashboard (and optionally reload its data)."""
    if _window is None:
        return
    args = ", ".join(json.dumps(v) for v in (message, error, reload))
    try:
        _window.evaluate_js(f"window.invlNotify && window.invlNotify({args})")
    except Exception:  # noqa: BLE001 - a missing toast must never crash the app
        pass


def _ask_save_path(filename: str, file_type: str) -> str | None:
    result = _window.create_file_dialog(
        webview.SAVE_DIALOG,
        directory=str(Path.home() / "Documents"),
        save_filename=filename,
        file_types=(file_type,),
    )
    if not result:
        return None
    return result if isinstance(result, str) else result[0]


def _ask_open_path(file_type: str) -> str | None:
    result = _window.create_file_dialog(
        webview.OPEN_DIALOG,
        directory=str(paths.user_data_dir() / "backups"),
        file_types=(file_type, "All files (*.*)"),
    )
    if not result:
        return None
    return result if isinstance(result, str) else result[0]


CSV_TYPE = "CSV file (*.csv)"
DB_TYPE = "INVL Hub backup (*.sqlite;*.db)"


def export_csv() -> dict:
    path = _ask_save_path(fileops.export_filename(), CSV_TYPE)
    if not path:
        return {"status": "cancelled"}
    try:
        r = fileops.write_csv(path)
    except Exception as exc:  # noqa: BLE001
        _notify(f"Export failed: {exc}", error=True)
        return {"status": "error", "message": str(exc)}
    _notify(f"Exported {r['rows']} items to {path}")
    return {"status": "ok", **r}


def backup_db() -> dict:
    path = _ask_save_path(fileops.backup_filename(), DB_TYPE)
    if not path:
        return {"status": "cancelled"}
    try:
        r = fileops.write_backup(path)
    except Exception as exc:  # noqa: BLE001
        _notify(f"Backup failed: {exc}", error=True)
        return {"status": "error", "message": str(exc)}
    _notify(f"Backup saved to {path}")
    return {"status": "ok", **r}


def restore_db() -> None:
    path = _ask_open_path(DB_TYPE)
    if not path:
        return
    try:
        info = fileops.validate_backup(path)
    except fileops.RestoreError as exc:
        _notify(str(exc), error=True)
        return
    last = (info["last_event"] or "no history").replace("T", " ").rstrip("Z")
    if not _window.create_confirmation_dialog(
        "Restore this backup?",
        f"{Path(path).name}\n{info['items']} items, {info['devices']} devices, "
        f"last change {last}.\n\n"
        "Every count is replaced with the backup's. Your current data is saved "
        "to the backups folder first, so this can be undone by restoring that.",
    ):
        return
    try:
        r = fileops.restore_backup(path)
    except Exception as exc:  # noqa: BLE001
        _notify(f"Restore failed: {exc}", error=True)
        return
    _notify(f"Restored {r['items']} items from {Path(path).name}. "
            f"Previous data saved as {Path(r['safety_backup']).name}.", reload=True)


def open_data_folder() -> None:
    folder = str(paths.user_data_dir())
    if sys.platform == "darwin":
        subprocess.Popen(["open", folder])
    elif os.name == "nt":
        os.startfile(folder)  # noqa: S606 - opens Explorer on our own folder
    else:
        subprocess.Popen(["xdg-open", folder])


class Bridge:
    """Exposed to the page as window.pywebview.api. Keep this to plain methods:
    pywebview walks public attributes, so it must not hold the window."""

    def export_csv(self):
        return export_csv()

    def backup_db(self):
        return backup_db()


def _stop_server_and_close_db() -> None:
    """Release the database file so it can be moved (Windows locks open files)."""
    if _server is not None:
        _server.should_exit = True
    if _server_thread is not None:
        _server_thread.join(timeout=5)
    with db._lock:
        if db._conn is not None:
            db._conn.close()
            db._conn = None


def _mac_trash(path: Path) -> None:
    import Foundation  # pyobjc, always present with the cocoa backend

    url = Foundation.NSURL.fileURLWithPath_(str(path))
    ok, _, err = Foundation.NSFileManager.defaultManager() \
        .trashItemAtURL_resultingItemURL_error_(url, None, None)
    if not ok:
        raise OSError(str(err.localizedDescription()) if err else f"couldn't move {path} to the Trash")


def _windows_recycle(path: Path) -> None:
    ps = ("Add-Type -AssemblyName Microsoft.VisualBasic; "
          "[Microsoft.VisualBasic.FileIO.FileSystem]::DeleteDirectory("
          f"'{str(path).replace(chr(39), chr(39) * 2)}', 'OnlyErrorDialogs', 'SendToRecycleBin')")
    subprocess.run(["powershell", "-NoProfile", "-Command", ps], check=True,
                   creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))


def uninstall() -> None:
    if not paths.is_frozen():
        _notify("Uninstall only works in the installed app (this is a development run).", error=True)
        return
    if sys.platform == "darwin":
        bundle = next((p for p in Path(sys.executable).resolve().parents
                       if p.suffix == ".app"), None)
        if bundle is None:
            _notify("Couldn't find the INVL Hub app to remove.", error=True)
            return
        if str(bundle).startswith("/Volumes/"):
            _notify("INVL Hub is running from the installer disk image. Eject it; there's nothing to uninstall.",
                    error=True)
            return
        uninstaller = None
    elif os.name == "nt":
        uninstaller = next(iter(sorted(Path(sys.executable).parent.glob("unins*.exe"))), None)
        if uninstaller is None:
            _notify("Uninstaller not found. Use Settings > Apps > Installed apps instead.", error=True)
            return
        bundle = None
    else:
        _notify("Uninstall isn't supported on this system. Delete the app folder to remove it.", error=True)
        return

    data_dir = paths.user_data_dir()
    if not _window.create_confirmation_dialog(
        "Uninstall INVL Hub?",
        "This removes the INVL Hub app from this computer and quits it. "
        "Counter devices will stop reporting until it is reinstalled.",
    ):
        return
    bin_name = "Trash" if sys.platform == "darwin" else "Recycle Bin"
    remove_data = _window.create_confirmation_dialog(
        "Also remove your inventory data?",
        f"OK: also move your counts, history and backups to the {bin_name}.\n"
        f"Cancel: keep them in {data_dir}, so reinstalling picks up where you left off.",
    )

    try:
        if bundle is not None:
            _mac_trash(bundle)            # a running app can be moved; nothing else changes if this fails
        if remove_data:
            _stop_server_and_close_db()
            if sys.platform == "darwin":
                _mac_trash(data_dir)
            else:
                _windows_recycle(data_dir)
        if uninstaller is not None:
            # /SILENT: we've already confirmed, so skip Inno's own prompt. The
            # uninstaller waits on our files, so quit right after launching it.
            subprocess.Popen([str(uninstaller), "/SILENT"], close_fds=True)
    except Exception as exc:  # noqa: BLE001
        _notify(f"Uninstall didn't finish: {exc}", error=True)
        return
    _window.destroy()


def _app_menu():
    from webview.menu import Menu, MenuAction, MenuSeparator

    return [
        Menu("File", [
            MenuAction("Export CSV…", export_csv),
            MenuAction("Back Up Database…", backup_db),
            MenuAction("Restore From Backup…", restore_db),
            MenuSeparator(),
            MenuAction("Open Data Folder", open_data_folder),
            MenuSeparator(),
            MenuAction("Uninstall INVL Hub…", uninstall),
        ]),
    ]


def _run_headless(server, url: str) -> None:
    """Keep the server running with no window (pure LAN-server mode)."""
    print(f"Headless mode — dashboard at {url}. Ctrl-C to stop.")
    try:
        while not server.should_exit:
            time.sleep(1)
    except KeyboardInterrupt:
        pass


def main() -> None:
    port = _choose_port()
    server = _start_server(port)
    url = f"http://{LOOPBACK}:{port}/"
    print(f"INVL Hub running at {url} (LAN: http://0.0.0.0:{port})")

    # Headless mode: run as a background LAN server with no native window.
    if os.environ.get("INVENTORY_NO_WINDOW"):
        try:
            _run_headless(server, url)
        finally:
            server.should_exit = True
        return

    global _window
    try:
        if webview is None:
            raise RuntimeError("pywebview is not installed")
        _window = webview.create_window(
            "INVL Hub", url, width=1180, height=800, min_size=(900, 600),
            js_api=Bridge(),
        )
        webview.start(menu=_app_menu())  # blocks until the window is closed
    except Exception as exc:  # noqa: BLE001 - any failure means no GUI backend
        print(f"Native window unavailable ({exc}); opening in your browser instead.")
        import webbrowser

        webbrowser.open(url)
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            pass
    finally:
        # Window closed (or Ctrl-C) -> shut the server down and exit.
        server.should_exit = True
        time.sleep(0.3)


if __name__ == "__main__":
    main()

"""Desktop entrypoint for Inventory Hub.

Starts the FastAPI/uvicorn server (bound to the LAN so ESP32 devices can still
reach it) and opens the dashboard in a native window via pywebview. If the
window backend isn't available (e.g. a headless environment), it falls back to
opening the default web browser.

Run in development:   python desktop.py
Packaged:             this is the PyInstaller entry script.
"""

from __future__ import annotations

import os
import socket
import threading
import time

import uvicorn

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
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    # Wait until the server is accepting connections.
    deadline = time.time() + 15
    while not server.started and time.time() < deadline:
        time.sleep(0.05)
    return server


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
    print(f"Inventory Hub running at {url} (LAN: http://0.0.0.0:{port})")

    # Headless mode: run as a background LAN server with no native window.
    if os.environ.get("INVENTORY_NO_WINDOW"):
        try:
            _run_headless(server, url)
        finally:
            server.should_exit = True
        return

    try:
        import webview  # pywebview

        window = webview.create_window(
            "Inventory Hub", url, width=1180, height=800, min_size=(900, 600)
        )
        webview.start()  # blocks until the window is closed
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

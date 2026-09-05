"""Inventory Hub — FastAPI entrypoint.

Run locally with:  uvicorn app.main:app --host 0.0.0.0 --port 8000
The dashboard is served at /, the API under /api.
"""

from __future__ import annotations

import os
import threading

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from . import mdns
from .db import get_conn
from .paths import resource_path
from .routes_device import router as device_router
from .routes_items import router as items_router

STATIC_DIR = resource_path("app", "static")

app = FastAPI(title="Inventory Hub", version="0.2.0")
app.include_router(device_router)
app.include_router(items_router)


@app.on_event("startup")
def _startup() -> None:
    # Initialize the database/schema on boot so the first request is fast.
    get_conn()
    # Advertise the hub as inventoryhub.local so devices find it without a
    # hardcoded IP. Zeroconf's sync API must NOT be constructed on uvicorn's
    # event-loop thread (it deadlocks), so register it on a background thread.
    # Port must match how the server was launched.
    port = int(os.environ.get("PORT", "8000"))
    threading.Thread(target=mdns.start, args=(port,), daemon=True).start()


@app.on_event("shutdown")
def _shutdown() -> None:
    mdns.stop()


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


# Static assets (app.js, style.css). Mounted last so /api and / win.
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for INVL Hub.

Builds a standalone desktop app:
  * macOS   -> "dist/INVL Hub.app"  (double-clickable bundle)
  * Windows -> dist/INVLHub/INVLHub.exe
  * Linux   -> dist/INVLHub/INVLHub

Build with:  pyinstaller --noconfirm --clean invl_hub.spec

Icons come from packaging/ (regenerate with packaging/make_icons.py).
"""

import os
import sys
from PyInstaller.utils.hooks import collect_all, collect_submodules

# Version comes from one place. CI exports INVL_VERSION from the git tag;
# a local build falls back to the VERSION file. Previously this spec carried a
# hardcoded string that had already drifted from what release.sh was stamping
# on the filename.
try:
    _spec_dir = SPECPATH  # injected by PyInstaller
except NameError:
    _spec_dir = os.path.dirname(os.path.abspath(__file__))

VERSION = os.environ.get("INVL_VERSION", "").strip()
if not VERSION:
    try:
        with open(os.path.join(_spec_dir, "VERSION")) as fh:
            VERSION = fh.read().strip()
    except OSError:
        VERSION = "0.0.0"

# Bundle the read-only resources the app reads at runtime.
datas = [
    ("app/schema.sql", "app"),
    ("app/static", "app/static"),
    ("VERSION", "."),          # read at runtime by paths.app_version()
]
binaries = []
hiddenimports = []

# uvicorn imports its loop/protocol implementations dynamically.
hiddenimports += collect_submodules("uvicorn")

# pywebview loads its platform backend (cocoa / winforms / gtk) dynamically.
web_datas, web_binaries, web_hidden = collect_all("webview")
datas += web_datas
binaries += web_binaries
hiddenimports += web_hidden

# zeroconf ships Cython extension modules and imports much of itself lazily, so
# static analysis misses pieces of it. It must be collected explicitly: without
# mDNS the hub still serves the dashboard but advertises nothing, and every
# device on the LAN silently fails to discover it. ifaddr is its interface-
# enumeration dependency and gets missed the same way.
zc_datas, zc_binaries, zc_hidden = collect_all("zeroconf")
datas += zc_datas
binaries += zc_binaries
hiddenimports += zc_hidden
hiddenimports += collect_submodules("ifaddr")

block_cipher = None

a = Analysis(
    ["desktop.py"],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="INVLHub",
    # The .ico only applies to the Windows executable; macOS gets its icon
    # from the BUNDLE below.
    icon="packaging/icon.ico" if sys.platform == "win32" else None,
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,  # no terminal window; the native window is the UI
    disable_windowed_traceback=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="INVLHub",
)

# macOS: wrap the collected output in a proper .app bundle.
if sys.platform == "darwin":
    app = BUNDLE(
        coll,
        name="INVL Hub.app",
        icon="packaging/icon.icns",
        # Changed from com.inventoryhub.app at the INVL Hub rebrand: macOS
        # treats this as a new app, so first launch re-asks the firewall
        # question. Data survives via the migration in app/paths.py.
        bundle_identifier="com.getinvl.hub",
        info_plist={
            "CFBundleName": "INVL Hub",
            "CFBundleDisplayName": "INVL Hub",
            "CFBundleShortVersionString": VERSION,
            "CFBundleVersion": VERSION,
            "NSHighResolutionCapable": True,
            # The dashboard is served over plain HTTP on localhost/LAN; allow
            # WKWebView to load it without HTTPS.
            "NSAppTransportSecurity": {"NSAllowsLocalNetworking": True},
        },
    )

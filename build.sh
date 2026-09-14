#!/usr/bin/env bash
# Build the standalone INVL Hub desktop app with PyInstaller.
#
#   macOS   -> "dist/INVL Hub.app"
#   Windows -> dist\INVLHub\INVLHub.exe   (run build.bat there instead)
#   Linux   -> dist/INVLHub/INVLHub
#
# PyInstaller builds for the OS it runs on — build the Windows version on Windows.
set -euo pipefail
cd "$(dirname "$0")"

if [ ! -d ".venv" ]; then
  python3 -m venv .venv
fi
./.venv/bin/pip install --quiet --upgrade pip
./.venv/bin/pip install --quiet -r requirements-desktop.txt

echo "Building… (this can take a minute)"
./.venv/bin/pyinstaller --noconfirm --clean invl_hub.spec

case "$(uname -s)" in
  Darwin) echo "Done -> 'dist/INVL Hub.app'  (double-click to launch)";;
  *)      echo "Done -> dist/INVLHub/      (run the INVLHub binary)";;
esac

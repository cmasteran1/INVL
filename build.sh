#!/usr/bin/env bash
# Build the standalone Inventory Hub desktop app with PyInstaller.
#
#   macOS   -> dist/InventoryHub.app
#   Windows -> dist\InventoryHub\InventoryHub.exe   (run build.bat there instead)
#   Linux   -> dist/InventoryHub/InventoryHub
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
./.venv/bin/pyinstaller --noconfirm --clean inventory_hub.spec

case "$(uname -s)" in
  Darwin) echo "Done -> dist/InventoryHub.app  (double-click to launch)";;
  *)      echo "Done -> dist/InventoryHub/      (run the InventoryHub binary)";;
esac

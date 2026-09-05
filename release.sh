#!/usr/bin/env bash
# Build a distributable Inventory Hub release for the CURRENT platform.
#
# Produces, in release/:
#   macOS  -> InventoryHub-<version>-macos-<arch>.dmg
#   Linux  -> InventoryHub-<version>-linux-<arch>.tar.gz
#   plus SHA256SUMS.txt
#
# There is no paid certificate involved here. On macOS the bundle is ad-hoc
# signed, which is NOT the same as Developer ID signing: it does not satisfy
# Gatekeeper, but it does stop Apple Silicon from killing an unsigned binary
# outright. Customers still have to explicitly allow the app on first launch —
# see INSTALL.md, which you should ship alongside the download.
#
# Windows builds must run on Windows: use release.bat.
set -euo pipefail
cd "$(dirname "$0")"

VERSION="${VERSION:-0.2.0}"
ARCH="$(uname -m)"
OUT="release"

./build.sh

mkdir -p "$OUT"
rm -f "$OUT"/*.dmg "$OUT"/*.tar.gz "$OUT"/SHA256SUMS.txt 2>/dev/null || true

case "$(uname -s)" in
  Darwin)
    APP="dist/InventoryHub.app"
    [ -d "$APP" ] || { echo "error: $APP not found — did build.sh succeed?" >&2; exit 1; }

    # Ad-hoc signature ("-" identity). Free, no Apple account. Replaces the
    # per-file signatures PyInstaller leaves behind with one consistent one so
    # the bundle isn't seen as tampered with.
    echo "Ad-hoc signing…"
    codesign --force --deep --sign - "$APP"
    codesign --verify --deep --strict "$APP" && echo "  signature OK (ad-hoc)"

    DMG="$OUT/InventoryHub-${VERSION}-macos-${ARCH}.dmg"
    echo "Building $DMG…"
    STAGE="$(mktemp -d)"
    cp -R "$APP" "$STAGE/"
    cp INSTALL.md "$STAGE/READ ME FIRST.md"
    ln -s /Applications "$STAGE/Applications"      # drag-to-install target
    hdiutil create -volname "Inventory Hub" -srcfolder "$STAGE" \
                   -ov -format UDZO "$DMG" >/dev/null
    rm -rf "$STAGE"
    ;;
  *)
    TARBALL="$OUT/InventoryHub-${VERSION}-linux-${ARCH}.tar.gz"
    echo "Building $TARBALL…"
    tar -czf "$TARBALL" -C dist InventoryHub
    ;;
esac

# Checksums let a customer confirm the download wasn't corrupted or swapped.
# With no code signature this is the only integrity signal you can offer —
# publish the hash on the download page, not just in the archive.
( cd "$OUT" && shasum -a 256 * > SHA256SUMS.txt 2>/dev/null || true )

echo
echo "Release artifacts in $OUT/:"
ls -lh "$OUT"
echo
echo "Publish the .dmg AND the SHA256 hash. Ship INSTALL.md with the download link."

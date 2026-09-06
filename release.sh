#!/usr/bin/env bash
# Build a distributable Inventory Hub release for the CURRENT platform.
#
# Produces, in release/:
#   macOS  -> InventoryHub-<version>-macos-<arm64|x64>.dmg
#   Linux  -> InventoryHub-<version>-linux-<arm64|x64>.tar.gz
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

VERSION="${VERSION:-$(cat VERSION)}"
export INVL_VERSION="$VERSION"        # the .spec stamps this into Info.plist
OUT="release"

# Normalise the machine name so artifact names match across platforms:
# uname reports x86_64 on Intel, but the Windows build calls the same thing x64.
case "$(uname -m)" in
  arm64|aarch64) ARCH="arm64" ;;
  x86_64|amd64)  ARCH="x64" ;;
  *)             ARCH="$(uname -m)" ;;
esac

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
    echo "Ad-hoc signing..."
    codesign --force --deep --sign - "$APP"
    codesign --verify --deep --strict "$APP" && echo "  signature OK (ad-hoc)"

    DMG="$OUT/InventoryHub-${VERSION}-macos-${ARCH}.dmg"
    echo "Building ${DMG}..."
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
    echo "Building ${TARBALL}..."
    tar -czf "$TARBALL" -C dist InventoryHub
    ;;
esac

# Checksums let a customer confirm the download wasn't corrupted or swapped.
# With no code signature this is the only integrity signal you can offer —
# publish the hash on the download page, not just in the archive.
( cd "$OUT" && shasum -a 256 InventoryHub-* > SHA256SUMS.txt )

echo
echo "Release artifacts in $OUT/:"
ls -lh "$OUT"
echo
echo "Publish the .dmg AND the SHA256 hash. Ship INSTALL.md with the download link."

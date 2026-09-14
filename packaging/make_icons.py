#!/usr/bin/env python3
"""Generate the INVL Hub app icons (icon.icns, icon.ico, icon_1024.png).

The design is a white rounded box with the INVL geometric wordmark in the
brand green (#24583c). The wordmark polygons are copied exactly from the
website's wordmark.svg (viewBox 0 0 135 44): uniform 9-unit stems, squared
terminals, mitred diagonals.

The generated icon.icns / icon.ico are committed so CI builders don't need
Pillow or macOS iconutil. Re-run this script (macOS, with Pillow) after any
design change:

    .venv/bin/python packaging/make_icons.py
"""

from __future__ import annotations

import pathlib
import shutil
import subprocess
import sys
import tempfile

from PIL import Image, ImageDraw

HERE = pathlib.Path(__file__).resolve().parent

GREEN = (0x24, 0x58, 0x3C, 255)   # brand forest green
BORDER = (0xC7, 0xDC, 0xD0, 255)  # brand soft border green
WHITE = (255, 255, 255, 255)

# INVL wordmark outlines, one polygon per letter, in a 135 x 44 unit box.
WORDMARK = [
    # I
    [(0, 0), (9, 0), (9, 44), (0, 44)],
    # N
    [(21, 44), (21, 0), (30, 0), (42, 23), (42, 0), (51, 0),
     (51, 44), (42, 44), (30, 21), (30, 44)],
    # V
    [(63, 0), (72, 0), (78, 22), (84, 0), (93, 0), (81, 44), (75, 44)],
    # L
    [(105, 0), (114, 0), (114, 35), (135, 35), (135, 44), (105, 44)],
]
WM_W, WM_H = 135, 44


def render_master(size: int = 1024, supersample: int = 4) -> Image.Image:
    """Draw the icon at `size`, supersampled for clean edges."""
    s = size * supersample
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    # The box: white rounded square on the macOS icon grid (content ~80% of
    # the canvas), with a soft green border so it reads on white backgrounds.
    margin = round(s * 0.098)
    radius = round(s * 0.18)
    stroke = max(supersample, round(s * 0.008))
    d.rounded_rectangle(
        (margin, margin, s - margin, s - margin),
        radius=radius, fill=WHITE, outline=BORDER, width=stroke,
    )

    # The wordmark, centred, at ~62% of the box width.
    box_w = s - 2 * margin
    scale = (box_w * 0.62) / WM_W
    wm_w, wm_h = WM_W * scale, WM_H * scale
    ox, oy = (s - wm_w) / 2, (s - wm_h) / 2
    for poly in WORDMARK:
        d.polygon([(ox + x * scale, oy + y * scale) for x, y in poly], fill=GREEN)

    return img.resize((size, size), Image.LANCZOS)


def main() -> None:
    master = render_master(1024)
    master.save(HERE / "icon_1024.png")

    # Windows .ico: Pillow embeds each size from the master.
    master.save(HERE / "icon.ico",
                sizes=[(256, 256), (128, 128), (64, 64), (48, 48), (32, 32), (16, 16)])

    # macOS .icns via iconutil (macOS only).
    if sys.platform != "darwin" or shutil.which("iconutil") is None:
        print("iconutil not available: wrote icon_1024.png and icon.ico only")
        return
    with tempfile.TemporaryDirectory() as td:
        iconset = pathlib.Path(td) / "icon.iconset"
        iconset.mkdir()
        for pt in (16, 32, 128, 256, 512):
            master.resize((pt, pt), Image.LANCZOS).save(iconset / f"icon_{pt}x{pt}.png")
            master.resize((pt * 2, pt * 2), Image.LANCZOS).save(
                iconset / f"icon_{pt}x{pt}@2x.png")
        subprocess.run(["iconutil", "-c", "icns", str(iconset),
                        "-o", str(HERE / "icon.icns")], check=True)

    print("wrote", ", ".join(p.name for p in sorted(HERE.glob("icon*"))))


if __name__ == "__main__":
    main()

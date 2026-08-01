#!/usr/bin/env python3
"""
Generate the RF Scanner app icon as a set of PNGs, then convert to ICNS
using macOS's iconutil.

Run once:  python3 make_icon.py
Output:    assets/AppIcon.icns   (and the .iconset folder used to build it)
"""

import math
import os
import shutil
import subprocess
import sys

from PIL import Image, ImageDraw, ImageFilter, ImageFont

# ---------------------------------------------------------------------------
# Colour palette
# ---------------------------------------------------------------------------
BG_DARK   = (15, 20, 35)          # deep navy
BG_GRAD   = (22, 32, 55)          # lighter navy for gradient
GRID      = (40, 55, 80, 80)      # semi-transparent blue-grey grid lines
SWEEP     = (0, 220, 180)         # teal/cyan peak line
FILL_TOP  = (0, 220, 180, 200)    # spectrum fill – top colour
FILL_BOT  = (0, 80,  60,  0)      # spectrum fill – bottom (transparent)
GLOW      = (0, 255, 200, 60)     # outer glow around the waveform
LABEL_COL = (0, 200, 160)         # "RF" text colour


def spectrum_y(x: float, width: float, height: float) -> float:
    """
    A plausible-looking RF spectrum envelope – a mix of sine waves plus
    a couple of sharp 'channel' peaks, all mapped to canvas coordinates.

    x      : 0..width
    returns: y pixel (0 = top of canvas)
    """
    t = x / width  # 0..1

    # Base noise floor
    base = 0.82

    # Broad rolloff near the edges
    rolloff = math.sin(math.pi * t) ** 0.4

    # A few bumpy background carriers
    bumps = (
        0.08 * math.sin(2 * math.pi * t * 3.1 + 0.5)
        + 0.05 * math.sin(2 * math.pi * t * 7.3 + 1.2)
        + 0.04 * math.sin(2 * math.pi * t * 13.7 + 0.3)
    )

    # Two dominant channel peaks
    def peak(center, width_frac, height_frac):
        d = (t - center) / width_frac
        return height_frac * math.exp(-d * d * 6)

    peaks = peak(0.28, 0.07, 0.55) + peak(0.65, 0.09, 0.45)

    signal = base + bumps + peaks
    signal = max(0.05, min(0.98, signal * rolloff))

    # Flip: high signal → low y (towards top)
    return height * (1.0 - signal * 0.88)


def draw_icon(size: int) -> Image.Image:
    W = H = size
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    # ---- background with subtle radial gradient ----
    for row in range(H):
        t = row / H
        r = int(BG_DARK[0] + (BG_GRAD[0] - BG_DARK[0]) * t)
        g = int(BG_DARK[1] + (BG_GRAD[1] - BG_DARK[1]) * t)
        b = int(BG_DARK[2] + (BG_GRAD[2] - BG_DARK[2]) * t)
        draw.line([(0, row), (W, row)], fill=(r, g, b, 255))

    # Rounded-rect mask for the app icon shape
    radius = int(W * 0.22)
    mask = Image.new("L", (W, H), 0)
    md = ImageDraw.Draw(mask)
    md.rounded_rectangle([0, 0, W - 1, H - 1], radius=radius, fill=255)
    img.putalpha(mask)

    # ---- grid lines ----
    n_horiz = 5
    n_vert  = 7
    pad = int(W * 0.10)
    grid_area = (pad, pad, W - pad, H - pad)

    for i in range(1, n_horiz):
        y = pad + (H - 2 * pad) * i // n_horiz
        draw.line([(pad, y), (W - pad, y)], fill=GRID, width=max(1, size // 256))

    for i in range(1, n_vert):
        x = pad + (W - 2 * pad) * i // n_vert
        draw.line([(x, pad), (x, H - pad)], fill=GRID, width=max(1, size // 256))

    # ---- spectrum waveform ----
    sweep_pad_x = int(W * 0.07)
    sweep_pad_y = int(H * 0.12)
    sw = W - 2 * sweep_pad_x
    sh = H - 2 * sweep_pad_y

    # Sample the curve
    steps = max(sw, 128)
    pts = []
    for i in range(steps + 1):
        px = sweep_pad_x + sw * i / steps
        sy_rel = spectrum_y(i / steps * sw, sw, sh)
        py = sweep_pad_y + sy_rel
        pts.append((px, py))

    # Filled area under the curve (gradient-like via layered polygons)
    bottom_y = H - sweep_pad_y
    for layer in range(12):
        alpha = int(160 * (1 - layer / 12))
        shift = int(sh * 0.02 * layer)
        fill_pts = [(x, y + shift) for x, y in pts]
        fill_pts += [(W - sweep_pad_x, bottom_y), (sweep_pad_x, bottom_y)]
        layer_img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        layer_draw = ImageDraw.Draw(layer_img)
        layer_draw.polygon(
            fill_pts,
            fill=(FILL_TOP[0], FILL_TOP[1], FILL_TOP[2], alpha),
        )
        img = Image.alpha_composite(img, layer_img)

    draw = ImageDraw.Draw(img)

    # Glow line (slightly thicker, semi-transparent)
    lw_glow = max(3, size // 48)
    for di in range(-lw_glow, lw_glow + 1):
        alpha = int(100 * (1 - abs(di) / lw_glow))
        glow_pts = [(x, y + di) for x, y in pts]
        if len(glow_pts) >= 2:
            draw.line(glow_pts, fill=(*SWEEP, alpha), width=1)

    # Main sweep line
    lw = max(2, size // 96)
    if len(pts) >= 2:
        draw.line(pts, fill=(*SWEEP, 255), width=lw)

    # ---- "RF" label – bottom-right corner ----
    label_size = max(8, int(W * 0.20))
    try:
        # Try to load a bold system font
        font = ImageFont.truetype(
            "/System/Library/Fonts/Helvetica.ttc", label_size
        )
    except Exception:
        font = ImageFont.load_default()

    label = "RF"
    # Measure with getbbox for accuracy
    bbox = font.getbbox(label)
    tw = bbox[2] - bbox[0]
    th = bbox[3] - bbox[1]
    tx = W - sweep_pad_x - tw - int(W * 0.01)
    ty = H - sweep_pad_y - th - int(H * 0.01)

    # Subtle drop shadow
    draw.text((tx + max(1, size // 256), ty + max(1, size // 256)),
              label, font=font, fill=(0, 0, 0, 160))
    draw.text((tx, ty), label, font=font, fill=(*LABEL_COL, 220))

    # Re-apply rounded mask to keep edges clean after compositing
    mask2 = Image.new("L", (W, H), 0)
    md2 = ImageDraw.Draw(mask2)
    md2.rounded_rectangle([0, 0, W - 1, H - 1], radius=radius, fill=255)
    img.putalpha(mask2)

    return img


def build_icns(out_path: str):
    iconset_dir = out_path.replace(".icns", ".iconset")
    os.makedirs(iconset_dir, exist_ok=True)

    # macOS iconset required sizes  (name → pixel dimension)
    sizes = {
        "icon_16x16.png":       16,
        "icon_16x16@2x.png":    32,
        "icon_32x32.png":       32,
        "icon_32x32@2x.png":    64,
        "icon_128x128.png":     128,
        "icon_128x128@2x.png":  256,
        "icon_256x256.png":     256,
        "icon_256x256@2x.png":  512,
        "icon_512x512.png":     512,
        "icon_512x512@2x.png":  1024,
    }

    for fname, px in sizes.items():
        img = draw_icon(px)
        img.save(os.path.join(iconset_dir, fname))
        print(f"  drew {fname}  ({px}px)")

    # Convert to ICNS
    result = subprocess.run(
        ["iconutil", "-c", "icns", iconset_dir, "-o", out_path],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        print("iconutil error:", result.stderr, file=sys.stderr)
        sys.exit(1)

    print(f"\n✓  Icon written → {out_path}")
    shutil.rmtree(iconset_dir)


if __name__ == "__main__":
    assets = os.path.join(os.path.dirname(__file__), "assets")
    os.makedirs(assets, exist_ok=True)
    build_icns(os.path.join(assets, "AppIcon.icns"))

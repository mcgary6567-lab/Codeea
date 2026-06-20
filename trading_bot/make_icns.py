"""Generate macOS icon.icns from logo.png (Pillow).

Run before the macOS build:  python make_icns.py
The mac spec bundles icon.icns as the .app icon. Safe to skip if Pillow or the
logo is missing — the build still succeeds (the app just uses a default icon).
"""

from __future__ import annotations

import os

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, "logo.png")
OUT = os.path.join(HERE, "icon.icns")


def main() -> int:
    try:
        from PIL import Image
    except Exception as exc:  # noqa: BLE001
        print(f"[make_icns] Pillow not available ({exc}); skipping")
        return 0
    if not os.path.exists(SRC):
        print(f"[make_icns] no {SRC}; run fetch_logo.py first — skipping")
        return 0
    img = Image.open(SRC).convert("RGBA")
    # Pad to a square on a transparent canvas, then upscale to 1024 so the .icns
    # carries every standard size cleanly.
    side = max(img.width, img.height)
    canvas = Image.new("RGBA", (side, side), (0, 0, 0, 0))
    canvas.paste(img, ((side - img.width) // 2, (side - img.height) // 2), img)
    canvas = canvas.resize((1024, 1024), Image.LANCZOS)
    try:
        canvas.save(OUT, format="ICNS")
        print(f"[make_icns] wrote {OUT}")
    except Exception as exc:  # noqa: BLE001
        print(f"[make_icns] could not write ICNS ({exc}); skipping")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

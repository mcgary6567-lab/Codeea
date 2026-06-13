"""Generate Windows icon.ico from logo.png (Pillow).

Run before the Windows build:  python make_ico.py
The Windows spec embeds icon.ico as the .exe icon. Safe to skip if Pillow or the
logo is missing — the build still succeeds (the exe just uses a default icon).
"""

from __future__ import annotations

import os

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, "logo.png")
OUT = os.path.join(HERE, "icon.ico")


def main() -> int:
    try:
        from PIL import Image
    except Exception as exc:  # noqa: BLE001
        print(f"[make_ico] Pillow not available ({exc}); skipping")
        return 0
    if not os.path.exists(SRC):
        print(f"[make_ico] no {SRC}; skipping")
        return 0
    img = Image.open(SRC).convert("RGBA")
    # Pad to a centred square on transparent canvas so every .ico size is crisp.
    side = max(img.size)
    canvas = Image.new("RGBA", (side, side), (0, 0, 0, 0))
    canvas.paste(img, ((side - img.width) // 2, (side - img.height) // 2), img)
    canvas = canvas.resize((256, 256), Image.LANCZOS)
    canvas.save(OUT, sizes=[(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    print(f"[make_ico] wrote {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

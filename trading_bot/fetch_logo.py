"""Download the brand logo from the official URL and produce logo.png + icon.ico.

Runs at build time (CI runner / your PC — both have internet). This sandbox
blocks the host, so it can't fetch here; the real build does.

The ONLY logo source is the URL below (override with LOGO_URL env or an arg).
No placeholder is bundled — the build downloads the real logo every time.

Usage:  python fetch_logo.py [url]
"""

import io
import os
import sys
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
LOGO_PATH = os.path.join(HERE, "logo.png")
ICO_PATH = os.path.join(HERE, "icon.ico")

DEFAULT_URL = "https://prometheusai.tech/assets/favicon.png"
ICO_SIZES = [(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]


def _download(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    data = urllib.request.urlopen(req, timeout=30).read()
    if len(data) < 64:
        raise ValueError(f"response too small ({len(data)} bytes)")
    return data


def main() -> int:
    url = sys.argv[1] if len(sys.argv) > 1 else os.environ.get("LOGO_URL", DEFAULT_URL)
    try:
        data = _download(url)
    except Exception as exc:  # noqa: BLE001
        print(f"[fetch_logo] could not download {url}: {exc}")
        return 0  # build continues; app falls back to no-logo gracefully

    try:
        from PIL import Image

        img = Image.open(io.BytesIO(data)).convert("RGBA")
        img.save(LOGO_PATH)
        img.save(ICO_PATH, sizes=ICO_SIZES)
        print(f"[fetch_logo] logo set from {url} ({img.size[0]}x{img.size[1]})")
    except Exception as exc:  # noqa: BLE001
        # No Pillow: at least write the raw bytes as the PNG logo.
        try:
            with open(LOGO_PATH, "wb") as fh:
                fh.write(data)
            print(f"[fetch_logo] saved logo.png raw (no Pillow for .ico): {exc}")
        except Exception as exc2:  # noqa: BLE001
            print(f"[fetch_logo] failed to write logo: {exc2}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

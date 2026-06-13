"""Prepare the brand logo: use a committed logo file if present, else download.

Runs at build time (CI runner / your PC). Priority:
  1. A committed ``logo.png`` / ``logo.jpg`` in this folder  ->  used directly
     (converted to logo.png + icon.ico). This is the reliable path.
  2. Otherwise download from the URL below (override with LOGO_URL env / arg).

So once a real logo file is committed, the app always shows it — no dependence
on a URL that may be unreachable from the build server.

Usage:  python fetch_logo.py [url]
"""

import io
import os
import sys
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
LOGO_PATH = os.path.join(HERE, "logo.png")
JPG_PATH = os.path.join(HERE, "logo.jpg")
ICO_PATH = os.path.join(HERE, "icon.ico")

DEFAULT_URL = "https://trevolto.com/assets/favicon.png"
ICO_SIZES = [(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]


def _download(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    data = urllib.request.urlopen(req, timeout=30).read()
    if len(data) < 64:
        raise ValueError(f"response too small ({len(data)} bytes)")
    return data


def _write_from_image(data: bytes, source: str) -> bool:
    try:
        from PIL import Image

        img = Image.open(io.BytesIO(data)).convert("RGBA")
        img.save(LOGO_PATH)
        img.save(ICO_PATH, sizes=ICO_SIZES)
        print(f"[fetch_logo] logo set from {source} ({img.size[0]}x{img.size[1]})")
        return True
    except Exception as exc:  # noqa: BLE001 - no Pillow: at least keep a PNG
        try:
            with open(LOGO_PATH, "wb") as fh:
                fh.write(data)
            print(f"[fetch_logo] saved logo.png raw (no Pillow for .ico): {exc}")
            return True
        except Exception as exc2:  # noqa: BLE001
            print(f"[fetch_logo] failed to write logo: {exc2}")
            return False


def main() -> int:
    explicit = sys.argv[1] if len(sys.argv) > 1 else os.environ.get("LOGO_URL")

    # 1) Prefer a committed logo file (the reliable path).
    if not explicit:
        for src in (LOGO_PATH, JPG_PATH):
            if os.path.exists(src):
                try:
                    with open(src, "rb") as fh:
                        _write_from_image(fh.read(), os.path.basename(src))
                    return 0
                except Exception as exc:  # noqa: BLE001
                    print(f"[fetch_logo] couldn't use committed {src}: {exc}")

    # 2) Otherwise download from the URL.
    url = explicit or DEFAULT_URL
    try:
        data = _download(url)
    except Exception as exc:  # noqa: BLE001
        print(f"[fetch_logo] could not download {url}: {exc}")
        return 0  # build continues; app falls back to no-logo gracefully
    _write_from_image(data, url)
    return 0


if __name__ == "__main__":
    sys.exit(main())


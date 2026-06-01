"""Download the official brand logo and produce logo.png + icon.ico.

Run at build time (CI runner / your own PC — both have internet). This sandbox
blocks outbound hosts, so the committed placeholder is used here; the real logo
is baked in whenever the exe is actually built.

Best-effort: on any failure it leaves the existing logo.png / icon.ico in place.

Usage:  python fetch_logo.py [url]
"""

import io
import os
import sys
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
# Candidate URLs tried in order; first that returns a valid image wins.
# Override with:  python fetch_logo.py <url>   (or set LOGO_URL env var)
CANDIDATE_URLS = [
    os.environ.get("LOGO_URL", ""),
    "https://hooks.prometheusai.tech/logo.png",
    "https://prometheusai.tech/logo.png",
    "https://prometheusai.tech/assets/favicon.png",
]
ICO_SIZES = [(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]


def _download(url: str):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    data = urllib.request.urlopen(req, timeout=30).read()
    if len(data) < 100:
        raise ValueError(f"response too small ({len(data)} bytes)")
    return data


def main() -> int:
    urls = [sys.argv[1]] if len(sys.argv) > 1 else [u for u in CANDIDATE_URLS if u]
    data = None
    used = ""
    for url in urls:
        try:
            data = _download(url)
            used = url
            break
        except Exception as exc:  # noqa: BLE001
            print(f"[fetch_logo] {url} -> failed ({exc})")
    if data is None:
        print("[fetch_logo] no logo fetched; keeping placeholder logo")
        return 0

    try:
        from PIL import Image

        img = Image.open(io.BytesIO(data)).convert("RGBA")
        img.save(os.path.join(HERE, "logo.png"))
        img.save(os.path.join(HERE, "icon.ico"), sizes=ICO_SIZES)
        print(f"[fetch_logo] installed real logo from {used} ({img.size[0]}x{img.size[1]})")
    except Exception as exc:  # noqa: BLE001
        try:
            with open(os.path.join(HERE, "logo.png"), "wb") as fh:
                fh.write(data)
            print(f"[fetch_logo] saved logo.png (no Pillow, icon.ico unchanged): {exc}")
        except Exception as exc2:  # noqa: BLE001
            print(f"[fetch_logo] could not write logo: {exc2}; keeping placeholder")
    return 0


if __name__ == "__main__":
    sys.exit(main())

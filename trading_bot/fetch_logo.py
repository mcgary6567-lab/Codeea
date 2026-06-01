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
    "https://prometheusai.tech/assets/favicon.png",
    "https://hooks.prometheusai.tech/logo.png",
    "https://prometheusai.tech/logo.png",
]
ICO_SIZES = [(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]


def _download(url: str):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    data = urllib.request.urlopen(req, timeout=30).read()
    if len(data) < 100:
        raise ValueError(f"response too small ({len(data)} bytes)")
    return data


def main() -> int:
    logo_path = os.path.join(HERE, "logo.png")
    ico_path = os.path.join(HERE, "icon.ico")

    # Download ONLY if a URL is explicitly given (arg or LOGO_URL env). By
    # default we use whatever logo.png is committed in the repo — deterministic,
    # no flaky favicon fetch overriding a good logo.
    urls = []
    if len(sys.argv) > 1:
        urls = [sys.argv[1]]
    elif os.environ.get("LOGO_URL"):
        urls = [os.environ["LOGO_URL"]]

    for url in urls:
        try:
            data = _download(url)
            with open(logo_path, "wb") as fh:
                fh.write(data)
            print(f"[fetch_logo] downloaded logo from {url}")
            break
        except Exception as exc:  # noqa: BLE001
            print(f"[fetch_logo] {url} -> failed ({exc})")
    if not urls:
        print("[fetch_logo] using committed logo.png (no download requested)")

    # Always (re)generate icon.ico from the current logo.png.
    try:
        from PIL import Image

        img = Image.open(logo_path).convert("RGBA")
        img.save(ico_path, sizes=ICO_SIZES)
        print(f"[fetch_logo] icon.ico regenerated from logo.png ({img.size[0]}x{img.size[1]})")
    except Exception as exc:  # noqa: BLE001
        print(f"[fetch_logo] icon.ico not regenerated ({exc}); keeping existing icon")
    return 0


if __name__ == "__main__":
    sys.exit(main())

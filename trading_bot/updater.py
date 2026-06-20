"""Self-update: learn the latest version from GitHub Releases, download the new
signed exe, verify its SHA-256, then hand off to a tiny helper that swaps the
running exe and relaunches.

Design notes
------------
* Version source is GitHub's ``releases/latest`` API (nothing to hand-edit per
  release). A legacy ``version.json`` URL is still honoured as a fallback so the
  app keeps working if the API is unreachable or rate-limited.
* A Windows exe can't overwrite itself while running, so :func:`apply_update`
  writes a small batch script that waits for *this* process to exit, moves the
  freshly-downloaded exe over the old one (with retries while the file is still
  locked), relaunches, and deletes itself.
* Everything that doesn't touch the network or the OS is pure and unit-tested
  (version compare, release-JSON parsing, checksum extraction/verification,
  script generation), so the risky bits are covered without a Windows runner.

The module is import-safe on every platform; the self-replace path is only taken
on Windows from a frozen (PyInstaller) build.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import tempfile
import urllib.request
from typing import Callable, Dict, List, Optional

_HEX64 = re.compile(r"\b([0-9a-fA-F]{64})\b")


# ---------------------------------------------------------------------------
# Version comparison
# ---------------------------------------------------------------------------
def parse_version(v: str) -> tuple:
    """``"v1.2.10"`` / ``"1.2.10"`` -> ``(1, 2, 10)``. Non-numeric parts drop."""
    nums = re.findall(r"\d+", str(v or ""))
    return tuple(int(n) for n in nums)


def is_newer(latest: str, current: str) -> bool:
    """True when ``latest`` is a strictly higher version than ``current``."""
    lp, cp = parse_version(latest), parse_version(current)
    if not lp:
        return False
    # Pad to equal length so (1,2) vs (1,2,0) compares equal, not less.
    n = max(len(lp), len(cp))
    return lp + (0,) * (n - len(lp)) > cp + (0,) * (n - len(cp))


# ---------------------------------------------------------------------------
# Discovering the latest release
# ---------------------------------------------------------------------------
def _pick_asset(assets: List[dict], pred: Callable[[str], bool]) -> Optional[str]:
    for a in assets or []:
        name = str(a.get("name", ""))
        if pred(name.lower()):
            url = a.get("browser_download_url")
            if url:
                return str(url)
    return None


def parse_github_release(data: dict, asset_hint: str = ".exe") -> Optional[Dict]:
    """Turn a GitHub ``releases/latest`` payload into our update dict, or None.

    Returns ``{"version", "url", "sha256_url", "notes", "required", "page"}``.
    ``required`` is True when the release notes contain a ``[required]`` marker
    (so a critical update can be forced)."""
    if not isinstance(data, dict):
        return None
    tag = data.get("tag_name") or data.get("name") or ""
    version = ".".join(str(x) for x in parse_version(tag))
    if not version:
        return None
    assets = data.get("assets") or []
    hint = asset_hint.lower()
    exe_url = _pick_asset(assets, lambda n: n.endswith(hint))
    sha_url = _pick_asset(assets, lambda n: n.endswith(".sha256") or n.endswith("sha256sums.txt"))
    notes = str(data.get("body") or "").strip()
    return {
        "version": version,
        "url": exe_url or data.get("html_url"),
        "sha256_url": sha_url,
        "notes": notes,
        "required": "[required]" in notes.lower(),
        "page": data.get("html_url"),
    }


def _get_json(url: str, timeout: float) -> dict:
    req = urllib.request.Request(
        url, headers={"User-Agent": "PrometheusBot",
                      "Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def check_latest(repo: str = "", fallback_url: str = "", timeout: float = 8.0,
                 asset_hint: str = ".exe") -> Optional[Dict]:
    """Best-effort: query GitHub Releases for ``repo`` (``owner/name``); if that
    fails, fall back to a legacy ``version.json`` at ``fallback_url``. Returns the
    update dict (see :func:`parse_github_release`) or None on any failure."""
    if repo:
        try:
            data = _get_json(f"https://api.github.com/repos/{repo}/releases/latest", timeout)
            info = parse_github_release(data, asset_hint)
            if info:
                return info
        except Exception:  # noqa: BLE001 - offline / rate-limited / private repo
            pass
    if fallback_url:
        try:
            data = _get_json(fallback_url, timeout)
            return {
                "version": ".".join(str(x) for x in parse_version(data.get("version", ""))),
                "url": data.get("url"),
                "sha256_url": data.get("sha256_url"),
                "notes": str(data.get("notes") or "").strip(),
                "required": bool(data.get("required")),
                "page": data.get("url"),
            }
        except Exception:  # noqa: BLE001
            pass
    return None


# ---------------------------------------------------------------------------
# Download + integrity
# ---------------------------------------------------------------------------
def extract_sha256(text: str) -> Optional[str]:
    """Pull the first 64-hex digest out of a ``.sha256`` / ``SHA256SUMS`` file
    (tolerates ``<hex>  filename`` lines and bare PowerShell ``Get-FileHash``)."""
    m = _HEX64.search(text or "")
    return m.group(1).lower() if m else None


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def verify_sha256(path: str, expected: str) -> bool:
    return bool(expected) and sha256_file(path) == str(expected).strip().lower()


def download(url: str, dest: str, timeout: float = 60.0,
             progress: Optional[Callable[[int, int], None]] = None) -> str:
    """Stream ``url`` to ``dest`` (atomically via a ``.part`` temp). Calls
    ``progress(downloaded, total)`` as it goes (total is 0 when unknown)."""
    req = urllib.request.Request(url, headers={"User-Agent": "PrometheusBot"})
    tmp = dest + ".part"
    with urllib.request.urlopen(req, timeout=timeout) as r:
        total = int(r.headers.get("Content-Length") or 0)
        done = 0
        with open(tmp, "wb") as f:
            while True:
                chunk = r.read(1 << 16)
                if not chunk:
                    break
                f.write(chunk)
                done += len(chunk)
                if progress:
                    progress(done, total)
    os.replace(tmp, dest)
    return dest


# ---------------------------------------------------------------------------
# Applying the update (Windows self-replace)
# ---------------------------------------------------------------------------
def frozen_exe_path() -> Optional[str]:
    """Path to the running exe when packaged by PyInstaller, else None (we're
    running from source, where self-replace doesn't apply)."""
    if getattr(sys, "frozen", False):
        return sys.executable
    return None


def build_update_script(pid: int, new_exe: str, cur_exe: str,
                        max_tries: int = 60) -> str:
    """Batch that waits for ``pid`` to exit, swaps ``new_exe`` over ``cur_exe``
    (retrying while the file is locked), relaunches, then deletes itself."""
    return (
        "@echo off\r\n"
        "setlocal\r\n"
        ":waitproc\r\n"
        f'tasklist /FI "PID eq {pid}" 2>nul | findstr /I " {pid} " >nul\r\n'
        "if not errorlevel 1 (\r\n"
        "  timeout /t 1 /nobreak >nul\r\n"
        "  goto waitproc\r\n"
        ")\r\n"
        "set /a tries=0\r\n"
        ":trymove\r\n"
        f'move /y "{new_exe}" "{cur_exe}" >nul 2>&1\r\n'
        "if errorlevel 1 (\r\n"
        "  set /a tries+=1\r\n"
        f"  if %tries% lss {max_tries} (\r\n"
        "    timeout /t 1 /nobreak >nul\r\n"
        "    goto trymove\r\n"
        "  )\r\n"
        ")\r\n"
        f'start "" "{cur_exe}"\r\n'
        'del "%~f0"\r\n'
    )


def apply_update(new_exe: str, cur_exe: Optional[str] = None) -> bool:
    """Spawn the detached swap-and-relaunch helper. The caller must then exit the
    app promptly so the helper can replace the (now-unlocked) exe. Returns True
    if the helper was launched; False if self-replace isn't possible here."""
    cur_exe = cur_exe or frozen_exe_path()
    if not cur_exe or os.name != "nt":
        return False
    import subprocess
    script = build_update_script(os.getpid(), os.path.abspath(new_exe),
                                 os.path.abspath(cur_exe))
    bat = os.path.join(tempfile.gettempdir(), "prometheus_update.bat")
    with open(bat, "w", encoding="ascii") as f:
        f.write(script)
    DETACHED = 0x00000008 | 0x00000200  # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
    subprocess.Popen(["cmd", "/c", bat], close_fds=True, creationflags=DETACHED)
    return True

"""Run-at-login helper for hands-off 24/7 running.

* Windows: registers under the per-user *Run* key
  (``HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Run``).
* macOS: installs a per-user **LaunchAgent** plist in
  ``~/Library/LaunchAgents`` and (un)loads it with ``launchctl``.

Both are per-user, so no admin rights are needed. All functions are no-ops
(returning ``False``) on unsupported platforms, so the GUI can call them
unconditionally.
"""

from __future__ import annotations

import os
import sys

APP_NAME = "PrometheusAICryptoBot"
BUNDLE_ID = "tech.prometheusai.bot"           # macOS LaunchAgent label

_IS_WINDOWS = sys.platform == "win32"
_IS_MAC = sys.platform == "darwin"


def _program_args() -> list:
    """Argv the OS should run at login.

    For a PyInstaller bundle ``sys.frozen`` is set and ``sys.executable`` is the
    app binary itself; from source we re-run this package's ``main.py``.
    """
    exe = sys.executable
    if getattr(sys, "frozen", False):
        return [exe]
    main = os.path.join(os.path.dirname(os.path.abspath(__file__)), "main.py")
    return [exe, main]


# ---------------------------------------------------------------------------
# Windows (registry Run key)
# ---------------------------------------------------------------------------
def _winreg():
    if not _IS_WINDOWS:
        return None
    try:
        import winreg  # noqa: WPS433 (stdlib, Windows-only)
        return winreg
    except ImportError:  # pragma: no cover - non-Windows
        return None


def _run_command() -> str:
    return " ".join(f'"{a}"' for a in _program_args())


def _win_is_enabled() -> bool:
    winreg = _winreg()
    if winreg is None:
        return False
    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Run",
        ) as key:
            winreg.QueryValueEx(key, APP_NAME)
            return True
    except OSError:
        return False


def _win_set_enabled(enable: bool) -> bool:
    winreg = _winreg()
    if winreg is None:
        return False
    path = r"Software\Microsoft\Windows\CurrentVersion\Run"
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, path, 0, winreg.KEY_SET_VALUE) as key:
            if enable:
                winreg.SetValueEx(key, APP_NAME, 0, winreg.REG_SZ, _run_command())
            else:
                try:
                    winreg.DeleteValue(key, APP_NAME)
                except FileNotFoundError:
                    pass
    except OSError:
        return _win_is_enabled()
    return enable


# ---------------------------------------------------------------------------
# macOS (LaunchAgent)
# ---------------------------------------------------------------------------
def _plist_path() -> str:
    return os.path.join(os.path.expanduser("~"), "Library", "LaunchAgents",
                        f"{BUNDLE_ID}.plist")


def _mac_is_enabled() -> bool:
    return os.path.exists(_plist_path())


def _launchctl(*args) -> None:
    """Best-effort launchctl call — registers the agent for *this* login session.
    The plist's RunAtLoad makes it start at the next login regardless, so a
    failure here (or a missing launchctl) is non-fatal."""
    import subprocess
    try:
        subprocess.run(["launchctl", *args], timeout=10,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:  # noqa: BLE001
        pass


def _mac_set_enabled(enable: bool) -> bool:
    from xml.sax.saxutils import escape
    plist = _plist_path()
    try:
        if enable:
            os.makedirs(os.path.dirname(plist), exist_ok=True)
            args = "".join(f"        <string>{escape(a)}</string>\n" for a in _program_args())
            doc = (
                '<?xml version="1.0" encoding="UTF-8"?>\n'
                '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" '
                '"http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n'
                '<plist version="1.0">\n<dict>\n'
                f'    <key>Label</key>\n    <string>{BUNDLE_ID}</string>\n'
                f'    <key>ProgramArguments</key>\n    <array>\n{args}    </array>\n'
                '    <key>RunAtLoad</key>\n    <true/>\n'
                '</dict>\n</plist>\n'
            )
            with open(plist, "w", encoding="utf-8") as fh:
                fh.write(doc)
            _launchctl("load", "-w", plist)
        else:
            # Always remove the plist (so it won't run next login) even if the
            # live unload can't run — the file's existence is our source of truth.
            _launchctl("unload", "-w", plist)
            if os.path.exists(plist):
                os.remove(plist)
    except Exception:  # noqa: BLE001 - never let a startup toggle crash the app
        return _mac_is_enabled()
    return enable


# ---------------------------------------------------------------------------
# Public, platform-dispatching API
# ---------------------------------------------------------------------------
def is_enabled() -> bool:
    if _IS_WINDOWS:
        return _win_is_enabled()
    if _IS_MAC:
        return _mac_is_enabled()
    return False


def set_enabled(enable: bool) -> bool:
    """Add or remove the run-at-login entry. Returns the resulting on/off state."""
    if _IS_WINDOWS:
        return _win_set_enabled(enable)
    if _IS_MAC:
        return _mac_set_enabled(enable)
    return False

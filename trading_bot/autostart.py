"""Run-on-Windows-startup helper.

Registers (or removes) the app under the per-user *Run* key
(``HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Run``) so the bot
launches automatically when the user logs in — the basis for hands-off
24/7 operation. Per-user, so it needs no admin rights.

All functions are no-ops (and report ``False``) off Windows, so the GUI can
call them unconditionally on any platform.
"""

from __future__ import annotations

import sys

APP_NAME = "PrometheusAICryptoBot"


def _winreg():
    """Return the winreg module on Windows, else None."""
    if sys.platform != "win32":
        return None
    try:
        import winreg  # noqa: WPS433 (stdlib, Windows-only)
        return winreg
    except ImportError:  # pragma: no cover - non-Windows
        return None


def _run_command() -> str:
    """The command Windows should run at login.

    For a PyInstaller one-file exe ``sys.frozen`` is set and ``sys.executable``
    is the exe itself. Running from source we launch ``python main.py``.
    """
    exe = sys.executable
    if getattr(sys, "frozen", False):
        return f'"{exe}"'
    # Source checkout: re-run this package's entry point.
    import os
    main = os.path.join(os.path.dirname(os.path.abspath(__file__)), "main.py")
    return f'"{exe}" "{main}"'


def is_enabled() -> bool:
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


def set_enabled(enable: bool) -> bool:
    """Add or remove the startup entry. Returns the resulting on/off state."""
    winreg = _winreg()
    if winreg is None:
        return False
    path = r"Software\Microsoft\Windows\CurrentVersion\Run"
    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, path, 0, winreg.KEY_SET_VALUE
        ) as key:
            if enable:
                winreg.SetValueEx(key, APP_NAME, 0, winreg.REG_SZ, _run_command())
            else:
                try:
                    winreg.DeleteValue(key, APP_NAME)
                except FileNotFoundError:
                    pass
    except OSError:
        return is_enabled()
    return enable

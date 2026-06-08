"""Single-instance guard — strictly one running copy of the bot per user.

The app may be downloaded/installed into several folders; a path-based check
would never notice a sibling launched from a different folder. So we contend
for one OS-level exclusive lock on a fixed file in the shared per-user data
dir (:data:`config.DATA_DIR`): *every* copy, wherever its executable lives,
fights over the same lock. The live process holds it for its whole lifetime
and the OS frees it automatically on exit **or crash**, so there is no stale
lock to clean up (unlike a PID file).

Why this matters here: two live instances on the same account both connect and
both fire orders from the same strategy, while their guardrails (max-open,
daily-loss, cooldown) can't see each other — double entries and double size.
One instance per user is a safety guarantee, not a nicety.

Cross-platform: ``msvcrt`` on Windows, ``fcntl`` elsewhere (so it runs and is
testable on Linux/macOS during development too).
"""

from __future__ import annotations

import os
import sys
from typing import Optional

from config import DATA_DIR

LOCK_FILE = os.path.join(DATA_DIR, "instance.lock")


class SingleInstance:
    """Holds an exclusive lock for as long as the object lives.

    Keep the returned instance referenced for the whole process lifetime — if
    it is garbage-collected the file handle closes and the lock is released.
    """

    def __init__(self, path: str = LOCK_FILE) -> None:
        self.path = path
        self._fh = None
        self.locked = False

    def acquire(self) -> bool:
        """Try to take the lock. ``True`` => we own it (or couldn't lock at all,
        in which case we fail *open* rather than block the user); ``False`` =>
        another instance already holds it."""
        try:
            # Open (creating if needed) and keep the handle for the lifetime of
            # the process. "a+" never truncates an existing file.
            self._fh = open(self.path, "a+")
        except OSError:
            # Can't even open the lock file (odd permissions, read-only dir) —
            # don't lock the user out of their own app over a bookkeeping file.
            return True
        try:
            if os.name == "nt":
                import msvcrt
                self._fh.seek(0)
                msvcrt.locking(self._fh.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self._fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            # Lock is held by another instance.
            try:
                self._fh.close()
            except OSError:
                pass
            self._fh = None
            return False
        # Record our PID for diagnostics (purely informational).
        try:
            self._fh.seek(0)
            self._fh.truncate()
            self._fh.write(str(os.getpid()))
            self._fh.flush()
        except OSError:
            pass
        self.locked = True
        return True

    def release(self) -> None:
        if not self._fh:
            return
        try:
            if os.name == "nt":
                import msvcrt
                self._fh.seek(0)
                try:
                    msvcrt.locking(self._fh.fileno(), msvcrt.LK_UNLCK, 1)
                except OSError:
                    pass
            else:
                import fcntl
                fcntl.flock(self._fh.fileno(), fcntl.LOCK_UN)
        finally:
            try:
                self._fh.close()
            except OSError:
                pass
            self._fh = None
            self.locked = False


def try_focus_existing(title: str) -> bool:
    """Best-effort: bring the already-running window to the front (Windows + macOS).

    Returns True if a window was found and raised. A no-op (returns False) on
    other platforms or if anything goes wrong — purely a UX nicety on top of the
    hard guarantee provided by the lock."""
    if sys.platform == "darwin":
        # Activate the running app by title (works for a .app bundle or the
        # python process running it). Best-effort — the lock is the guarantee.
        try:
            import subprocess
            script = f'tell application "{title}" to activate'
            return subprocess.run(["osascript", "-e", script], timeout=5,
                                  stdout=subprocess.DEVNULL,
                                  stderr=subprocess.DEVNULL).returncode == 0
        except Exception:  # noqa: BLE001
            return False
    if os.name != "nt":
        return False
    try:
        import ctypes

        user32 = ctypes.windll.user32
        hwnd = user32.FindWindowW(None, title)
        if not hwnd:
            return False
        SW_RESTORE = 9
        user32.ShowWindow(hwnd, SW_RESTORE)   # un-minimise if needed
        user32.SetForegroundWindow(hwnd)
        return True
    except Exception:  # noqa: BLE001
        return False

"""Entry point for the Windows Trading Bot.

Run with:  python main.py

Flow: hidden root -> PIN login dialog -> main GUI window.
"""

import sys
import atexit
import tkinter as tk
from tkinter import messagebox

import theme
from config import APP_TITLE
from gui import TradingBotGUI
from login import LoginDialog
from single_instance import SingleInstance, try_focus_existing

# Held for the whole process lifetime so the lock isn't released early.
_INSTANCE = SingleInstance()


def main() -> int:
    # Strictly one running copy per user — wherever it was installed from.
    # A second launch raises the running window, says so, and exits, so two
    # instances can never trade the same account at once.
    if not _INSTANCE.acquire():
        try_focus_existing(APP_TITLE)
        warn = tk.Tk()
        warn.withdraw()
        theme.apply(warn)
        messagebox.showinfo(
            APP_TITLE,
            f"{APP_TITLE} is already running.\n\n"
            "Only one copy can run at a time — switch to the open window.")
        warn.destroy()
        return 0
    atexit.register(_INSTANCE.release)

    root = tk.Tk()
    root.title(APP_TITLE)
    theme.apply(root)          # dark theme for the login dialog too
    root.withdraw()            # hide until the user unlocks

    login = LoginDialog(root)
    root.wait_window(login.win)

    if not login.pin:
        return 0  # cancelled / locked out

    root.deiconify()
    try:
        TradingBotGUI(root, pin=login.pin, saved=login.saved)
    except Exception as exc:  # noqa: BLE001
        messagebox.showerror("Startup error", str(exc))
        return 1

    root.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())

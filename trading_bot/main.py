"""Entry point for the Windows Trading Bot.

Run with:  python main.py

Flow: hidden root -> PIN login dialog -> main GUI window.
"""

import sys
import tkinter as tk
from tkinter import messagebox

import theme
from config import APP_TITLE
from gui import TradingBotGUI
from login import LoginDialog


def main() -> int:
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

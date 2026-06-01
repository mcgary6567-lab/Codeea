"""PIN/password gate shown before the main window.

First run: the user creates a PIN (entered twice). Subsequent runs: the user
must enter the correct PIN, which is verified by decrypting the credential
store. Three failed attempts and the app exits.
"""

from __future__ import annotations

import tkinter as tk
from tkinter import messagebox

import security
import theme
from config import APP_TITLE, resource_path

BG = theme.HEADER
CARD = theme.PANEL


class LoginDialog:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.pin: str | None = None
        self.saved: dict = {}
        self.attempts = 0
        self.first_run = not security.is_initialised()

        self.win = tk.Toplevel(root)
        self.win.title(APP_TITLE)
        self.win.geometry("380x300")
        self.win.resizable(False, False)
        self.win.configure(bg=BG)
        self.win.grab_set()
        self.win.protocol("WM_DELETE_WINDOW", self._cancel)

        # Logo.
        try:
            img = tk.PhotoImage(file=resource_path("logo.png"))
            n = max(1, img.width() // 64)
            self._logo = img.subsample(n, n)
            tk.Label(self.win, image=self._logo, bg=BG).pack(pady=(20, 6))
        except Exception:  # noqa: BLE001
            pass

        tk.Label(self.win, text=APP_TITLE, bg=BG, fg=theme.ACCENT,
                 font=("Segoe UI Semibold", 13)).pack()
        sub = "Create a PIN" if self.first_run else "Enter your PIN"
        tk.Label(self.win, text=sub, bg=BG, fg=theme.TXT_DIM, font=("Segoe UI", 10)).pack(pady=(2, 10))

        self.pin_var = tk.StringVar()
        e1 = tk.Entry(self.win, textvariable=self.pin_var, show="•", justify="center",
                      font=("Segoe UI", 12), bg=theme.ELEV, fg=theme.TXT,
                      insertbackground=theme.TXT, relief="flat")
        e1.pack(pady=4, ipady=4, ipadx=10)
        e1.focus_set()

        self.confirm_var = tk.StringVar()
        if self.first_run:
            tk.Label(self.win, text="Confirm PIN:", bg=BG, fg=theme.TXT_DIM).pack(pady=(6, 0))
            tk.Entry(self.win, textvariable=self.confirm_var, show="•", justify="center",
                     bg=theme.ELEV, fg=theme.TXT, insertbackground=theme.TXT,
                     relief="flat").pack(pady=4, ipady=4, ipadx=10)

        btn = tk.Button(self.win, text="Unlock", width=18, command=self._submit)
        theme.style_button(btn, "accent")
        btn.pack(pady=16)
        self.win.bind("<Return>", lambda e: self._submit())

    def _submit(self) -> None:
        pin = self.pin_var.get().strip()
        if len(pin) < 4:
            messagebox.showwarning("PIN too short", "Use at least 4 characters.", parent=self.win)
            return

        if self.first_run:
            if pin != self.confirm_var.get().strip():
                messagebox.showerror("Mismatch", "The two PINs do not match.", parent=self.win)
                return
            # Initialise an empty encrypted store under this PIN.
            security.save_credentials(pin, {})
            self.pin = pin
            self.saved = {}
            self.win.destroy()
            return

        if security.verify_pin(pin):
            self.pin = pin
            self.saved = security.load_credentials(pin)
            self.win.destroy()
        else:
            self.attempts += 1
            if self.attempts >= 3:
                messagebox.showerror("Locked", "Too many failed attempts.", parent=self.win)
                self._cancel()
            else:
                messagebox.showerror(
                    "Wrong PIN", f"Incorrect. {3 - self.attempts} attempt(s) left.", parent=self.win
                )

    def _cancel(self) -> None:
        self.pin = None
        self.win.destroy()
        self.root.destroy()

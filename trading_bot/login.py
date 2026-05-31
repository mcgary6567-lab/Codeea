"""PIN/password gate shown before the main window.

First run: the user creates a PIN (entered twice). Subsequent runs: the user
must enter the correct PIN, which is verified by decrypting the credential
store. Three failed attempts and the app exits.
"""

from __future__ import annotations

import tkinter as tk
from tkinter import messagebox

import security


class LoginDialog:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.pin: str | None = None
        self.saved: dict = {}
        self.attempts = 0
        self.first_run = not security.is_initialised()

        self.win = tk.Toplevel(root)
        self.win.title("Unlock Trading Bot")
        self.win.geometry("360x200")
        self.win.resizable(False, False)
        self.win.grab_set()
        self.win.protocol("WM_DELETE_WINDOW", self._cancel)

        title = "Create a PIN" if self.first_run else "Enter your PIN"
        tk.Label(self.win, text=title, font=("Segoe UI", 13, "bold")).pack(pady=(18, 8))

        self.pin_var = tk.StringVar()
        e1 = tk.Entry(self.win, textvariable=self.pin_var, show="•", justify="center", font=("Segoe UI", 12))
        e1.pack(pady=4)
        e1.focus_set()

        self.confirm_var = tk.StringVar()
        if self.first_run:
            tk.Label(self.win, text="Confirm PIN:").pack()
            tk.Entry(self.win, textvariable=self.confirm_var, show="•", justify="center").pack(pady=4)

        tk.Button(self.win, text="Unlock", width=16, command=self._submit).pack(pady=12)
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

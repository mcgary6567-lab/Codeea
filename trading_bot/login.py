"""PIN/password gate shown before the main window.

First run: the user starts their free trial by entering an **email** and
creating a PIN (entered twice). The email is sent to the relay's ``trial.php``
to issue a 10-day full-access licence; the PIN encrypts the local credential
store (the licence token is saved into it). Subsequent runs: the user simply
enters their PIN, which is verified by decrypting the credential store. Three
failed attempts and the app exits.

The PIN is still the vault's encryption key — first run just folds the trial
sign-up into the same screen so a new user is licensed before the app opens.
"""

from __future__ import annotations

import threading
import time
import tkinter as tk
import webbrowser
from tkinter import messagebox

import licence
import security
import theme
from config import (
    APP_TITLE,
    DEFAULT_RELAY_URL,
    SUPPORT_EMAIL,
    TRIAL_DAYS,
    WEBSITE_URL,
    resource_path,
)

BG = theme.HEADER
CARD = theme.PANEL


def _valid_email(email: str) -> bool:
    return "@" in email and "." in email.split("@")[-1]


class LoginDialog:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.pin: str | None = None
        self.saved: dict = {}
        self.attempts = 0
        self.first_run = not security.is_initialised()

        self.win = tk.Toplevel(root)
        self.win.title(APP_TITLE)
        self.win.geometry("380x460" if self.first_run else "380x340")
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
        sub = "Start your free trial" if self.first_run else "Enter your PIN"
        tk.Label(self.win, text=sub, bg=BG, fg=theme.TXT_DIM, font=("Segoe UI", 10)).pack(pady=(2, 10))

        # First run: email field (required) ties the 10-day trial to the user.
        self.email_var = tk.StringVar()
        if self.first_run:
            tk.Label(self.win, text="Email (starts your free trial):", bg=BG,
                     fg=theme.TXT_DIM).pack()
            em = tk.Entry(self.win, textvariable=self.email_var, justify="center",
                          font=("Segoe UI", 11), bg=theme.ELEV, fg=theme.TXT,
                          insertbackground=theme.TXT, relief="flat")
            em.pack(pady=(2, 8), ipady=4, ipadx=10)
            em.focus_set()

            tk.Label(self.win, text="Create PIN (encrypts your keys):", bg=BG,
                     fg=theme.TXT_DIM).pack()

        self.pin_var = tk.StringVar()
        e1 = tk.Entry(self.win, textvariable=self.pin_var, show="•", justify="center",
                      font=("Segoe UI", 12), bg=theme.ELEV, fg=theme.TXT,
                      insertbackground=theme.TXT, relief="flat")
        e1.pack(pady=4, ipady=4, ipadx=10)
        if not self.first_run:
            e1.focus_set()

        self.confirm_var = tk.StringVar()
        if self.first_run:
            tk.Label(self.win, text="Confirm PIN:", bg=BG, fg=theme.TXT_DIM).pack(pady=(6, 0))
            tk.Entry(self.win, textvariable=self.confirm_var, show="•", justify="center",
                     bg=theme.ELEV, fg=theme.TXT, insertbackground=theme.TXT,
                     relief="flat").pack(pady=4, ipady=4, ipadx=10)

        btn_text = f"Start {TRIAL_DAYS}-day free trial" if self.first_run else "Unlock"
        self.btn = tk.Button(self.win, text=btn_text, width=22, command=self._submit)
        theme.style_button(self.btn, "accent")
        self.btn.pack(pady=16)
        self.win.bind("<Return>", lambda e: self._submit())

        # Website + email links.
        links = tk.Frame(self.win, bg=BG)
        links.pack(side="bottom", pady=8)
        self._link(links, "prometheusai.tech", WEBSITE_URL)
        tk.Label(links, text="  ·  ", bg=BG, fg=theme.TXT_DIM).pack(side="left")
        self._link(links, SUPPORT_EMAIL, f"mailto:{SUPPORT_EMAIL}")

    def _link(self, parent, text, url):
        lbl = tk.Label(parent, text=text, bg=BG, fg=theme.ACCENT, cursor="hand2",
                       font=("Segoe UI", 9, "underline"))
        lbl.pack(side="left")
        lbl.bind("<Button-1>", lambda e: self._open(url))

    @staticmethod
    def _open(url):
        try:
            webbrowser.open(url)
        except Exception:  # noqa: BLE001
            pass

    def _submit(self) -> None:
        pin = self.pin_var.get().strip()
        if len(pin) < 4:
            messagebox.showwarning("PIN too short", "Use at least 4 characters.", parent=self.win)
            return

        if self.first_run:
            email = self.email_var.get().strip()
            if not _valid_email(email):
                messagebox.showwarning(
                    "Free trial", "Enter a valid email to start your free trial.", parent=self.win)
                return
            if pin != self.confirm_var.get().strip():
                messagebox.showerror("Mismatch", "The two PINs do not match.", parent=self.win)
                return
            self._start_trial(pin, email)
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

    # --- First-run trial sign-up -----------------------------------------
    def _start_trial(self, pin: str, email: str) -> None:
        """Request the 10-day trial off the UI thread, then finish on it."""
        self.btn.config(state="disabled", text="Starting…")
        trial_url = licence.trial_url_from_relay(DEFAULT_RELAY_URL)
        machine = licence.machine_fingerprint()

        def work():
            result = licence.start_trial(trial_url, email, machine)
            # Hop back to the Tk thread before touching widgets / the store.
            self.win.after(0, lambda: self._trial_done(pin, email, *result))

        threading.Thread(target=work, daemon=True).start()

    def _trial_done(self, pin: str, email: str, status: str, message: str,
                    token: str, expires_at: int) -> None:
        self.btn.config(state="normal", text=f"Start {TRIAL_DAYS}-day free trial")

        if status == "ok" and token:
            # Licensed: create the PIN vault with the trial token already in it,
            # so the app auto-connects and unlocks the strategy on open.
            self._finish(pin, {"relay_token": token, "trial_email": email})
            days = max(0, (expires_at - int(time.time())) // 86400) if expires_at else TRIAL_DAYS
            messagebox.showinfo(
                "Free trial started",
                f"Your {days}-day free trial is active — full access unlocked.\n\n"
                "When it ends, open the License tab and click “Get License”.",
                parent=self.root,
            )
            return

        # Trial unavailable (already used, or the server was unreachable). Still
        # create the PIN vault and let the user in — unlicensed — so they can
        # paste a purchased key or retry the trial from the License tab.
        if status == "used":
            note = (message or "Your free trial has already been used.") + \
                "\n\nOpen the License tab to enter a licence key or Get License."
            title = "Free trial"
        else:
            note = "Couldn't reach the licence server.\n\nYour PIN is set — you can " \
                "start your free trial later from the License tab."
            title = "Free trial"
        self._finish(pin, {"trial_email": email})
        messagebox.showinfo(title, note, parent=self.root)

    def _finish(self, pin: str, payload: dict) -> None:
        """Persist the initial vault under ``pin`` and hand control to the app."""
        try:
            security.save_credentials(pin, payload)
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror("Setup error", f"Could not save your settings:\n{exc}",
                                 parent=self.win)
            return
        self.pin = pin
        self.saved = payload
        self.win.destroy()

    def _cancel(self) -> None:
        self.pin = None
        self.win.destroy()
        self.root.destroy()

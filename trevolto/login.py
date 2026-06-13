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
        self.win.geometry("380x540" if self.first_run else "380x340")
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
                 font=(theme.UI_SB, 13)).pack()
        sub = "Start your free trial" if self.first_run else "Enter your PIN"
        tk.Label(self.win, text=sub, bg=BG, fg=theme.TXT_DIM, font=(theme.UI, 10)).pack(pady=(2, 10))

        # First run: email field (required) ties the 10-day trial to the user.
        self.email_var = tk.StringVar()
        if self.first_run:
            tk.Label(self.win, text="Email (starts your free trial):", bg=BG,
                     fg=theme.TXT_DIM).pack()
            em = tk.Entry(self.win, textvariable=self.email_var, justify="center",
                          font=(theme.UI, 11), bg=theme.ELEV, fg=theme.TXT,
                          insertbackground=theme.TXT, relief="flat")
            em.pack(pady=(2, 8), ipady=4, ipadx=10)
            em.focus_set()

            tk.Label(self.win, text="Create your 4-digit PIN (encrypts your keys):", bg=BG,
                     fg=theme.TXT_DIM).pack()

        self.pin_var = tk.StringVar()
        e1 = tk.Entry(self.win, textvariable=self.pin_var, show="•", justify="center",
                      font=(theme.UI, 12), bg=theme.ELEV, fg=theme.TXT,
                      insertbackground=theme.TXT, relief="flat")
        e1.pack(pady=4, ipady=4, ipadx=10)
        if not self.first_run:
            e1.focus_set()

        self.confirm_var = tk.StringVar()
        if self.first_run:
            tk.Label(self.win, text="Confirm your 4-digit PIN:", bg=BG, fg=theme.TXT_DIM).pack(pady=(6, 0))
            tk.Entry(self.win, textvariable=self.confirm_var, show="•", justify="center",
                     bg=theme.ELEV, fg=theme.TXT, insertbackground=theme.TXT,
                     relief="flat").pack(pady=4, ipady=4, ipadx=10)

        btn_text = f"🎁 Start {TRIAL_DAYS}-day free trial" if self.first_run else "🔓 Unlock"
        self.btn = tk.Button(self.win, text=btn_text, width=22, command=self._submit)
        theme.style_button(self.btn, "accent")
        self.btn.pack(pady=16)
        self.win.bind("<Return>", lambda e: self._submit())

        # Returning users who forgot their PIN: reset it (admin must authorise
        # first in the licence panel). First-run users have no PIN to forget.
        if not self.first_run:
            forgot = tk.Label(self.win, text="Forgot PIN?", bg=BG, fg=theme.TXT_DIM,
                              cursor="hand2", font=(theme.UI, 9, "underline"))
            forgot.pack()
            forgot.bind("<Button-1>", lambda e: self._forgot_pin())

        # Website + email links.
        links = tk.Frame(self.win, bg=BG)
        links.pack(side="bottom", pady=8)
        self._link(links, "trevolto.com", WEBSITE_URL)
        tk.Label(links, text="  ·  ", bg=BG, fg=theme.TXT_DIM).pack(side="left")
        self._link(links, SUPPORT_EMAIL, f"mailto:{SUPPORT_EMAIL}")

    def _link(self, parent, text, url):
        lbl = tk.Label(parent, text=text, bg=BG, fg=theme.ACCENT, cursor="hand2",
                       font=(theme.UI, 9, "underline"))
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
        """Request the 10-day trial off the UI thread, then finish on it.

        A watchdog guarantees the UI never gets stuck on "Starting…": if the
        licence server hangs (e.g. a DNS stall the socket timeout doesn't cover),
        we proceed offline after a few seconds and let the user in so they can
        retry the trial later from the License tab."""
        self.btn.config(state="disabled", text="Starting…")
        self._trial_settled = False
        trial_url = licence.trial_url_from_relay(DEFAULT_RELAY_URL)
        machine = licence.machine_fingerprint()

        def work():
            try:
                result = licence.start_trial(trial_url, email, machine, timeout=12.0)
            except Exception as exc:  # noqa: BLE001 - never let the thread die silently
                result = ("error", f"trial error ({exc})", "", 0)
            try:
                self.win.after(0, lambda: self._trial_done(pin, email, *result))
            except Exception:  # noqa: BLE001 - window already closed by the watchdog
                pass

        threading.Thread(target=work, daemon=True).start()
        # Hard cap: if no answer in time, stop waiting and continue offline.
        self._trial_watchdog = self.win.after(
            16000,
            lambda: self._trial_done(pin, email, "error",
                                     "the licence server didn't respond", "", 0))

    def _trial_done(self, pin: str, email: str, status: str, message: str,
                    token: str, expires_at: int) -> None:
        # Only the first of (real result, watchdog) is allowed to finish.
        if getattr(self, "_trial_settled", False):
            return
        self._trial_settled = True
        if getattr(self, "_trial_watchdog", None):
            try:
                self.win.after_cancel(self._trial_watchdog)
            except Exception:  # noqa: BLE001
                pass
            self._trial_watchdog = None
        self.btn.config(state="normal", text=f"🎁 Start {TRIAL_DAYS}-day free trial")

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

    # --- Forgot PIN: admin-authorised reset ------------------------------
    def _forgot_pin(self) -> None:
        """Two-step reset dialog: (1) verify an admin-authorised reset by email,
        (2) create a new PIN. The local vault is wiped (saved API keys can't be
        recovered) and the licence token is restored from the server."""
        rw = tk.Toplevel(self.win)
        self._reset_win = rw
        rw.title("Reset PIN")
        rw.configure(bg=BG)
        rw.resizable(False, False)
        rw.geometry("360x300")
        rw.grab_set()
        rw.protocol("WM_DELETE_WINDOW", rw.destroy)
        self._reset_body = tk.Frame(rw, bg=BG)
        self._reset_body.pack(fill="both", expand=True, padx=18, pady=16)
        self._forgot_build_email_step()

    def _forgot_clear(self) -> None:
        for ch in self._reset_body.winfo_children():
            ch.destroy()

    def _forgot_build_email_step(self) -> None:
        self._forgot_clear()
        b = self._reset_body
        tk.Label(b, text="Reset your PIN", bg=BG, fg=theme.ACCENT,
                 font=(theme.UI_SB, 13)).pack(anchor="w")
        tk.Label(b, text="Ask your admin to authorise a reset, then enter your\n"
                         "registered email below.", bg=BG, fg=theme.TXT_DIM,
                 justify="left", font=(theme.UI, 9)).pack(anchor="w", pady=(4, 10))
        self._reset_email_var = tk.StringVar(value=self.email_var.get())
        e = tk.Entry(b, textvariable=self._reset_email_var, font=(theme.UI, 11),
                     bg=theme.ELEV, fg=theme.TXT, insertbackground=theme.TXT, relief="flat")
        e.pack(fill="x", ipady=4)
        e.focus_set()
        self._reset_status = tk.Label(b, text="", bg=BG, fg=theme.TXT_DIM, font=(theme.UI, 9))
        self._reset_status.pack(anchor="w", pady=(8, 0))
        self._reset_btn = tk.Button(b, text="Continue", command=self._forgot_submit_email)
        theme.style_button(self._reset_btn, "accent")
        self._reset_btn.pack(pady=14)
        self._reset_win.bind("<Return>", lambda e: self._forgot_submit_email())

    def _forgot_submit_email(self) -> None:
        email = self._reset_email_var.get().strip()
        if not _valid_email(email):
            self._reset_status.config(text="Enter a valid email.", fg=theme.RED)
            return
        self._reset_btn.config(state="disabled", text="Checking…")
        self._reset_status.config(text="Contacting the licence server…", fg=theme.TXT_DIM)
        reset_url = licence.reset_url_from_relay(DEFAULT_RELAY_URL)
        machine = licence.machine_fingerprint()

        def work():
            res = licence.request_pin_reset(reset_url, email, machine, timeout=15.0)
            try:
                self._reset_win.after(0, lambda: self._forgot_email_done(email, *res))
            except Exception:  # noqa: BLE001 - dialog already closed
                pass

        threading.Thread(target=work, daemon=True).start()

    def _forgot_email_done(self, email: str, status: str, message: str,
                           token: str, expires_at: int) -> None:
        if not getattr(self, "_reset_win", None) or not self._reset_win.winfo_exists():
            return
        if status == "ok" and token:
            self._forgot_build_pin_step(email, token)
            return
        self._reset_btn.config(state="normal", text="Continue")
        self._reset_status.config(
            text=message or ("No reset authorised yet." if status == "denied"
                             else "Couldn't reach the server."),
            fg=theme.RED)

    def _forgot_build_pin_step(self, email: str, token: str) -> None:
        self._forgot_clear()
        b = self._reset_body
        tk.Label(b, text="Create a new PIN", bg=BG, fg=theme.ACCENT,
                 font=(theme.UI_SB, 13)).pack(anchor="w")
        tk.Label(b, text="Reset authorised. Your licence is kept; your saved\n"
                         "exchange API keys are cleared — re-enter them after.",
                 bg=BG, fg=theme.TXT_DIM, justify="left", font=(theme.UI, 9)).pack(
            anchor="w", pady=(4, 10))
        self._reset_pin1 = tk.StringVar()
        self._reset_pin2 = tk.StringVar()
        tk.Label(b, text="New 4-digit PIN:", bg=BG, fg=theme.TXT_DIM).pack(anchor="w")
        p1 = tk.Entry(b, textvariable=self._reset_pin1, show="•", font=(theme.UI, 12),
                      bg=theme.ELEV, fg=theme.TXT, insertbackground=theme.TXT, relief="flat")
        p1.pack(fill="x", ipady=4, pady=(0, 6))
        p1.focus_set()
        tk.Label(b, text="Confirm new PIN:", bg=BG, fg=theme.TXT_DIM).pack(anchor="w")
        tk.Entry(b, textvariable=self._reset_pin2, show="•", font=(theme.UI, 12),
                 bg=theme.ELEV, fg=theme.TXT, insertbackground=theme.TXT, relief="flat").pack(
            fill="x", ipady=4)
        self._reset_status = tk.Label(b, text="", bg=BG, fg=theme.TXT_DIM, font=(theme.UI, 9))
        self._reset_status.pack(anchor="w", pady=(8, 0))
        sb = tk.Button(b, text="Set new PIN", command=lambda: self._forgot_set_pin(email, token))
        theme.style_button(sb, "accent")
        sb.pack(pady=12)
        self._reset_win.bind("<Return>", lambda e: self._forgot_set_pin(email, token))

    def _forgot_set_pin(self, email: str, token: str) -> None:
        pin = self._reset_pin1.get().strip()
        if len(pin) < 4:
            self._reset_status.config(text="Use at least 4 characters.", fg=theme.RED)
            return
        if pin != self._reset_pin2.get().strip():
            self._reset_status.config(text="The two PINs do not match.", fg=theme.RED)
            return
        try:
            security.reset_local_vault()
            security.save_credentials(pin, {"relay_token": token, "trial_email": email})
        except Exception as exc:  # noqa: BLE001
            self._reset_status.config(text=f"Couldn't save: {exc}", fg=theme.RED)
            return
        # Treat exactly like a successful unlock so the app opens with the
        # restored licence (the user re-enters API keys in the app).
        self.pin = pin
        self.saved = {"relay_token": token, "trial_email": email}
        try:
            self._reset_win.destroy()
        except Exception:  # noqa: BLE001
            pass
        self.win.destroy()

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

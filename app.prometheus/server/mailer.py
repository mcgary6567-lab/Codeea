"""Minimal SMTP email sender (stdlib only).

Configure via env: PROMETHEUS_SMTP_HOST, PROMETHEUS_SMTP_PORT (587),
PROMETHEUS_SMTP_USER, PROMETHEUS_SMTP_PASS, PROMETHEUS_SMTP_FROM. If unset,
send_email() returns (False, reason) so callers can degrade gracefully.
"""
from __future__ import annotations

import os
import smtplib
import ssl
from email.mime.text import MIMEText


def configured() -> bool:
    return bool(os.environ.get("PROMETHEUS_SMTP_HOST") and os.environ.get("PROMETHEUS_SMTP_USER"))


def send_email(to: str, subject: str, body: str) -> tuple:
    host = os.environ.get("PROMETHEUS_SMTP_HOST", "").strip()
    if not host:
        return False, "email is not configured (set PROMETHEUS_SMTP_* env vars)"
    port = int(os.environ.get("PROMETHEUS_SMTP_PORT", "587"))
    user = os.environ.get("PROMETHEUS_SMTP_USER", "").strip()
    pw = os.environ.get("PROMETHEUS_SMTP_PASS", "").strip()
    frm = os.environ.get("PROMETHEUS_SMTP_FROM", user).strip()
    try:
        msg = MIMEText(body, "plain", "utf-8")
        msg["Subject"] = subject
        msg["From"] = frm
        msg["To"] = to
        ctx = ssl.create_default_context()
        if port == 465:
            with smtplib.SMTP_SSL(host, port, context=ctx, timeout=15) as s:
                if user:
                    s.login(user, pw)
                s.sendmail(frm, [to], msg.as_string())
        else:
            with smtplib.SMTP(host, port, timeout=15) as s:
                s.starttls(context=ctx)
                if user:
                    s.login(user, pw)
                s.sendmail(frm, [to], msg.as_string())
        return True, "sent"
    except Exception as e:  # noqa: BLE001
        return False, f"email send failed: {e}"

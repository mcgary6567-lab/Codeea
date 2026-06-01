"""Lightweight notifications: sound + optional Telegram.

No third-party dependencies. Sound uses Windows' ``winsound`` (stdlib, present
on every Windows version) and is a no-op elsewhere. Telegram uses the Bot API
over ``urllib`` and only fires when a token + chat id are configured. All sends
are best-effort and non-blocking — a failed notification never affects trading.
"""

from __future__ import annotations

import json
import threading
import urllib.parse
import urllib.request
import subprocess
import sys

try:
    import winsound

    _HAS_SOUND = True
except ImportError:  # non-Windows / headless
    winsound = None
    _HAS_SOUND = False

_IS_WINDOWS = sys.platform.startswith("win")


class Notifier:
    def __init__(self) -> None:
        self.sound_enabled = True
        self.desktop_enabled = True
        self.telegram_token = ""
        self.telegram_chat_id = ""

    def configure(self, sound: bool, token: str, chat_id: str, desktop: bool = True) -> None:
        self.sound_enabled = sound
        self.desktop_enabled = desktop
        self.telegram_token = (token or "").strip()
        self.telegram_chat_id = (chat_id or "").strip()

    def telegram_ready(self) -> bool:
        return bool(self.telegram_token and self.telegram_chat_id)

    def test_telegram(self, token: str, chat_id: str):
        """Send a one-off test message. Returns ``(ok: bool, message: str)``."""
        token, chat_id = (token or "").strip(), (chat_id or "").strip()
        if not token or not chat_id:
            return False, "Enter both the bot token and chat id first."
        try:
            text = ("✅ *Prometheus AI Crypto Bot*\nTelegram test successful — "
                    "your notifications are connected and working.")
            data = urllib.parse.urlencode(
                {"chat_id": chat_id, "text": text, "parse_mode": "Markdown"}).encode()
            req = urllib.request.Request(
                f"https://api.telegram.org/bot{token}/sendMessage", data=data)
            info = json.loads(urllib.request.urlopen(req, timeout=10).read().decode("utf-8"))
            if info.get("ok"):
                return True, "Test message sent — check your Telegram chat."
            return False, f"Telegram rejected it: {info.get('description', 'unknown error')}"
        except Exception as exc:  # noqa: BLE001
            return False, (f"Could not reach Telegram: {exc}\n\n"
                           "Check the bot token, that you've messaged the bot once, "
                           "and that the chat id is correct.")

    # -- public -------------------------------------------------------------
    def notify(self, title: str, message: str, level: str = "info") -> None:
        if self.sound_enabled:
            self._beep(level)
        if self.desktop_enabled and _IS_WINDOWS:
            threading.Thread(target=self._toast, args=(title, message), daemon=True).start()
        if self.telegram_ready():
            threading.Thread(
                target=self._send_telegram, args=(f"*{title}*\n{message}",), daemon=True
            ).start()

    # -- native Windows toast (PowerShell + WinRT, no extra dependency) ------
    def _toast(self, title: str, message: str) -> None:
        try:
            title = title.replace('"', "'")[:80]
            message = message.replace('"', "'")[:160]
            ps = (
                "[Windows.UI.Notifications.ToastNotificationManager,Windows.UI.Notifications,"
                "ContentType=WindowsRuntime]>$null;"
                "$t=[Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent("
                "[Windows.UI.Notifications.ToastTemplateType]::ToastText02);"
                "$x=$t.GetElementsByTagName('text');"
                f"$x.Item(0).AppendChild($t.CreateTextNode(\"{title}\"))>$null;"
                f"$x.Item(1).AppendChild($t.CreateTextNode(\"{message}\"))>$null;"
                "$n=[Windows.UI.Notifications.ToastNotification]::new($t);"
                "[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier("
                "'Prometheus AI Crypto Bot').Show($n);"
            )
            flags = 0x08000000  # CREATE_NO_WINDOW
            subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                           creationflags=flags, timeout=8,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception:  # noqa: BLE001 - toast is best-effort
            pass

    # -- internals ----------------------------------------------------------
    def _beep(self, level: str) -> None:
        if not _HAS_SOUND:
            return
        try:
            mapping = {
                "error": winsound.MB_ICONHAND,
                "ok": winsound.MB_ICONASTERISK,
                "info": winsound.MB_OK,
            }
            winsound.MessageBeep(mapping.get(level, winsound.MB_OK))
        except Exception:  # noqa: BLE001
            pass

    def _telegram_url(self) -> str:
        return f"https://api.telegram.org/bot{self.telegram_token}/sendMessage"

    def _send_telegram(self, text: str) -> None:
        try:
            data = urllib.parse.urlencode(
                {"chat_id": self.telegram_chat_id, "text": text, "parse_mode": "Markdown"}
            ).encode()
            req = urllib.request.Request(self._telegram_url(), data=data)
            urllib.request.urlopen(req, timeout=10).read()
        except Exception:  # noqa: BLE001 - notifications must never raise
            pass

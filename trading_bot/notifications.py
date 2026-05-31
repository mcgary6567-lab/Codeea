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

try:
    import winsound

    _HAS_SOUND = True
except ImportError:  # non-Windows / headless
    winsound = None
    _HAS_SOUND = False


class Notifier:
    def __init__(self) -> None:
        self.sound_enabled = True
        self.telegram_token = ""
        self.telegram_chat_id = ""

    def configure(self, sound: bool, token: str, chat_id: str) -> None:
        self.sound_enabled = sound
        self.telegram_token = (token or "").strip()
        self.telegram_chat_id = (chat_id or "").strip()

    def telegram_ready(self) -> bool:
        return bool(self.telegram_token and self.telegram_chat_id)

    # -- public -------------------------------------------------------------
    def notify(self, title: str, message: str, level: str = "info") -> None:
        if self.sound_enabled:
            self._beep(level)
        if self.telegram_ready():
            threading.Thread(
                target=self._send_telegram, args=(f"*{title}*\n{message}",), daemon=True
            ).start()

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

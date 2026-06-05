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
        # When True, only events flagged important reach Telegram (entries,
        # closes+PnL, halts, security). Routine/noisy events still beep and
        # toast locally. Off = send every event to Telegram (legacy behaviour).
        self.telegram_important_only = True

    def configure(self, sound: bool, token: str, chat_id: str, desktop: bool = True,
                  telegram_important_only: bool = True) -> None:
        self.sound_enabled = sound
        self.desktop_enabled = desktop
        self.telegram_token = (token or "").strip()
        self.telegram_chat_id = (chat_id or "").strip()
        self.telegram_important_only = telegram_important_only
        self._tg_fail_logged = False     # settings changed → allow re-logging a failure

    def telegram_ready(self) -> bool:
        return bool(self.telegram_token and self.telegram_chat_id)

    def send_message(self, text: str) -> None:
        """Best-effort async Telegram message (no sound/toast). Used for the
        end-of-day P&L summary so it never blocks the caller."""
        if self.telegram_ready():
            threading.Thread(target=self._send_telegram, args=(text,), daemon=True).start()

    def test_telegram(self, token: str, chat_id: str):
        """Send a one-off test message. Returns ``(ok: bool, message: str)``."""
        token, chat_id = (token or "").strip(), (chat_id or "").strip()
        if not token or not chat_id:
            return False, "Enter both the bot token and chat id first."
        try:
            text = ("✅ Prometheus AI Crypto Bot\nTelegram test successful — "
                    "your notifications are connected and working.")
            data = urllib.parse.urlencode({"chat_id": chat_id, "text": text}).encode()
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
    def notify(self, title: str, message: str, level: str = "info",
               important: bool = False) -> None:
        """Fire a notification. Sound + desktop toast always run (locally);
        Telegram is sent only for ``important`` events when important-only mode
        is on (default), so routine/noisy events don't spam your chat."""
        if self.sound_enabled:
            self._beep(level)
        if self.desktop_enabled and _IS_WINDOWS:
            threading.Thread(target=self._toast, args=(title, message), daemon=True).start()
        telegram_ok = important or not self.telegram_important_only
        if telegram_ok and self.telegram_ready():
            threading.Thread(
                target=self._send_telegram, args=(f"{title}\n{message}",), daemon=True
            ).start()

    # -- native Windows toast (PowerShell + WinRT, no extra dependency) ------
    def _show_toast(self, title: str, message: str) -> int:
        """Show a Windows toast; returns the PowerShell exit code (0 = ok)."""
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
        return subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                              creationflags=flags, timeout=10,
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode

    def _toast(self, title: str, message: str) -> None:
        try:
            self._show_toast(title, message)
        except Exception:  # noqa: BLE001 - toast is best-effort
            pass

    def test_desktop(self):
        """Fire a test toast. Returns ``(ok: bool, message: str)``."""
        if not _IS_WINDOWS:
            return False, "Desktop toast notifications are only available on Windows."
        try:
            rc = self._show_toast("Prometheus AI Crypto Bot",
                                  "Test notification — desktop alerts are working.")
            if rc == 0:
                return True, ("Test toast sent — check the bottom-right of your screen.\n\n"
                              "If nothing appeared, allow notifications for the app in "
                              "Windows Settings → System → Notifications.")
            return False, (f"PowerShell exited with code {rc}. Toasts may be blocked in "
                           "Windows notification settings (Focus Assist / Do Not Disturb).")
        except Exception as exc:  # noqa: BLE001
            return False, f"Could not show a toast: {exc}"

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
        # Plain text (no parse_mode): exchange/error strings frequently contain
        # underscores, asterisks and brackets that break Telegram's Markdown
        # parser, which would reject the message (HTTP 400) and the alert would be
        # silently lost — exactly the important alerts we most need to deliver.
        try:
            data = urllib.parse.urlencode(
                {"chat_id": self.telegram_chat_id, "text": text}).encode()
            req = urllib.request.Request(self._telegram_url(), data=data)
            resp = urllib.request.urlopen(req, timeout=10).read()
            info = json.loads(resp.decode("utf-8"))
            if info.get("ok"):
                self._tg_fail_logged = False     # healthy again → re-arm logging
            else:
                self._log_tg_failure(info.get("description", "rejected"))
        except Exception as exc:  # noqa: BLE001 - notifications must never raise
            self._log_tg_failure(str(exc))

    def _log_tg_failure(self, detail: str) -> None:
        """Record the first Telegram failure to app.log so a silently-broken
        notifier (expired token, bot blocked) is diagnosable from the support log."""
        if getattr(self, "_tg_fail_logged", False):
            return
        self._tg_fail_logged = True
        try:
            import applog
            applog.get_logger().warning("Telegram send failed: %s", detail)
        except Exception:  # noqa: BLE001
            pass

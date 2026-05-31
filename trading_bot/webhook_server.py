"""TradingView webhook receiver.

Runs a tiny stdlib HTTP server on a background thread. When TradingView fires an
alert whose message is JSON, it POSTs here; we validate an optional passphrase
and hand the parsed signal to a callback (which enqueues a trade command).

Expected JSON (flexible — extra keys are ignored)::

    {
      "passphrase": "your-secret",   # optional, must match if configured
      "action": "buy" | "sell",      # or "side"
      "ticker": "BTCUSDT",           # or "symbol"
      "size": 0.001                  # optional; falls back to GUI lot size
    }

Health check: GET / returns 200 "ok".
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Callable, Optional

# Lifecycle events some indicators emit after entry (e.g. Gold Scalpers).
KNOWN_EVENTS = {"tp1_hit", "tp2_hit", "sl_hit", "sl_after_partial"}


class WebhookServer:
    def __init__(
        self,
        host: str,
        port: int,
        on_signal: Callable[[dict], None],
        get_passphrase: Callable[[], str],
        log: Callable[[str], None],
    ) -> None:
        self.host = host
        self.port = port
        self.on_signal = on_signal
        self.get_passphrase = get_passphrase
        self.log = log
        self._httpd: Optional[ThreadingHTTPServer] = None
        self._thread: Optional[threading.Thread] = None
        self.running = False

    def start(self) -> None:
        if self.running:
            return
        handler = self._make_handler()
        self._httpd = ThreadingHTTPServer((self.host, self.port), handler)
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)
        self._thread.start()
        self.running = True
        self.log(f"Webhook listening on {self.host}:{self.port}")

    def stop(self) -> None:
        if self._httpd:
            self._httpd.shutdown()
            self._httpd.server_close()
        self.running = False
        self.log("Webhook stopped")

    def _make_handler(self):
        server = self

        class Handler(BaseHTTPRequestHandler):
            # Silence the default stderr access log; we log via the app.
            def log_message(self, *args):  # noqa: D401
                return

            def _send(self, code: int, body: str) -> None:
                self.send_response(code)
                self.send_header("Content-Type", "text/plain")
                self.end_headers()
                self.wfile.write(body.encode("utf-8"))

            def do_GET(self):  # noqa: N802 - required name
                self._send(200, "ok")

            def do_POST(self):  # noqa: N802 - required name
                length = int(self.headers.get("Content-Length", 0) or 0)
                raw = self.rfile.read(length) if length else b""
                try:
                    payload = json.loads(raw.decode("utf-8"))
                except (ValueError, UnicodeDecodeError):
                    server.log("Webhook: bad JSON payload rejected")
                    self._send(400, "bad json")
                    return

                expected = server.get_passphrase()
                if expected and str(payload.get("passphrase", "")) != expected:
                    server.log("Webhook: passphrase mismatch — rejected")
                    self._send(401, "unauthorized")
                    return

                action = str(payload.get("action") or payload.get("side") or "").lower()
                event = str(payload.get("event") or "").lower()
                ticker = str(payload.get("ticker") or payload.get("symbol") or "")
                valid_entry = action in ("buy", "sell")
                valid_event = event in KNOWN_EVENTS
                if not ticker or not (valid_entry or valid_event):
                    server.log(f"Webhook: invalid signal {payload!r}")
                    self._send(422, "invalid signal")
                    return

                def _num(key):
                    try:
                        return float(payload[key])
                    except (KeyError, TypeError, ValueError):
                        return None

                signal = {
                    "action": action,
                    "event": event,
                    "ticker": ticker,
                    "size": payload.get("size"),
                    "entry": _num("entry"),
                    "sl": _num("sl"),
                    "tp1": _num("tp1"),
                    "tp2": _num("tp2"),
                    "price": _num("price"),
                    "source": "webhook",
                }
                server.log(f"Webhook signal: {(event or action).upper()} {ticker}")
                try:
                    server.on_signal(signal)
                except Exception as exc:  # noqa: BLE001
                    server.log(f"Webhook handler error: {exc}")
                    self._send(500, "handler error")
                    return
                self._send(200, "accepted")

        return Handler

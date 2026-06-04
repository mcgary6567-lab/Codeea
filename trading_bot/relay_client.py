"""Cloud-relay client (optional alternative to the local webhook).

Polls your relay (e.g. https://hooks.prometheusai.tech/poll.php?token=...) for
new signals broadcast from your TradingView account, and feeds each one into the
same handler the local webhook uses. Lets customers receive signals with just a
licence token — no ngrok, no port-forwarding (the poll is outbound).
"""

from __future__ import annotations

import json
import threading
import time
import urllib.parse
import urllib.request
from typing import Callable, Optional

from config import RELAY_POLL_INTERVAL
from webhook_server import parse_payload


class RelayClient:
    def __init__(
        self,
        get_url: Callable[[], str],
        get_token: Callable[[], str],
        on_signal: Callable[[dict], None],
        log: Callable[[str], None],
    ) -> None:
        self.get_url = get_url
        self.get_token = get_token
        self.on_signal = on_signal
        self.log = log
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self.running = False
        self._fail_count = 0
        self.last_poll_ts = 0.0     # last successful poll (feed alive)
        self.last_signal_ts = 0.0   # last signal received

    def start(self) -> None:
        if self.running:
            return
        token = self.get_token().strip()
        if not token:
            self.log("Cloud signals: enter a licence token first")
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        self.running = True
        self.log("Cloud signals: connected, polling for signals")

    def stop(self) -> None:
        self._stop.set()
        self.running = False

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self._poll_once()
                self._fail_count = 0
            except Exception as exc:  # noqa: BLE001 - keep polling through errors
                self._fail_count += 1
                if self._fail_count in (1, 5, 20):  # log sparingly
                    self.log(f"Cloud signals: poll error ({exc})")
            # Back off on repeated errors (e.g. a host/Cloudflare 403 from
            # rate-limiting) so we stop hammering — up to 60s, reset on success.
            if self._fail_count == 0:
                wait = RELAY_POLL_INTERVAL
            else:
                wait = min(60.0, RELAY_POLL_INTERVAL * (2 ** min(self._fail_count, 5)))
            self._stop.wait(wait)

    def _poll_once(self) -> None:
        token = self.get_token().strip()
        base = self.get_url().strip()
        if not token or not base:
            return
        url = base + ("&" if "?" in base else "?") + "token=" + urllib.parse.quote(token)
        req = urllib.request.Request(url, headers={"User-Agent": "PrometheusBot"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))

        if not data.get("ok", False):
            err = data.get("error", "rejected")
            if self._fail_count == 0:  # only log first time
                self.log(f"Cloud signals: {err}")
            self._fail_count = 1
            return

        self.last_poll_ts = time.time()   # feed is alive
        for payload in data.get("signals", []):
            signal = parse_payload(payload, source="relay")
            if signal:
                self.last_signal_ts = time.time()
                self.on_signal(signal)

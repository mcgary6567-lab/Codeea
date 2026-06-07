"""Tkinter frontend — mirrors the supplied mockup.

Layout:
    +-------------------------------------------------------------+
    | Status bar: connection | exchange | balance | PnL           |
    +----------------------------+--------------------------------+
    | API Settings               | Trade Settings                 |
    | BUY / SELL                  | Trade Log (+ Export/Clear)     |
    | Open Positions             |                                |
    +----------------------------+--------------------------------+

The GUI never touches the exchange directly; it submits commands to the Backend
and drains UI updates from ``backend.ui_queue`` on the Tk event loop.
"""

from __future__ import annotations

import csv
import os
import queue
import threading
import time
import webbrowser
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

import applog
import autostart
import connectivity
import history
import licence
import tray as tray_helper
import security
import theme
from analytics_window import AnalyticsWindow
from backend import Backend
from chart_window import ChartWindow
from relay_client import RelayClient
from strategy import StrategyParams
from strategy_runner import StrategyRunner
from config import (
    APP_TITLE,
    APP_VERSION,
    CHECKOUT_URL,
    EXCHANGE_LABELS,
    exchange_id,
    exchange_label,
    resource_path,
    DEFAULT_AUTO_BRACKET,
    DEFAULT_LEVERAGE,
    DEFAULT_MARGIN_MODE,
    DEFAULT_MAX_OPEN_POSITIONS,
    DEFAULT_ORDER_TYPE,
    DEFAULT_RISK_PERCENT,
    DEFAULT_SIZING_MODE,
    DEFAULT_SLIPPAGE_PCT,
    DEFAULT_SAFE_MODE,
    DEFAULT_TRADE_SIZE,
    DEFAULT_RELAY_URL,
    DEFAULT_STRATEGY_SYMBOLS,
    DEFAULT_STRATEGY_TIMEFRAME,
    DEFAULT_WEBHOOK_PASSPHRASE,
    ORDER_TYPES,
    QUOTE_CURRENCY,
    SIZING_MODE_LABELS,
    SIZING_MODES,
    SPOT_ONLY_EXCHANGES,
    STRATEGY_TIMEFRAMES,
    SUPPORTED_EXCHANGES,
    SUPPORT_EMAIL,
    TOP_PAIRS,
    WEBHOOK_HOST,
    WEBHOOK_PORT,
    WEBSITE_URL,
)
from webhook_server import WebhookServer

# Exchanges that need an API passphrase in addition to key/secret.
PASSPHRASE_EXCHANGES = {"okx", "kucoin", "bitget"}

# Colours come from the dark theme palette.
GREEN = theme.GREEN_HL
RED = theme.RED_HL
GREY = theme.GREY
ACCENT = theme.ACCENT
PANEL = theme.PANEL
ELEV = theme.ELEV
TXT = theme.TXT
TXT_DIM = theme.TXT_DIM
HEADER = theme.HEADER


class TradingBotGUI:
    def __init__(self, root: tk.Tk, pin: str, saved: dict) -> None:
        self.root = root
        self.pin = pin
        self.saved = saved or {}

        self.backend = Backend()
        self.backend.start()

        self.webhook = WebhookServer(
            host=WEBHOOK_HOST,
            port=WEBHOOK_PORT,
            on_signal=self._on_webhook_signal,
            get_passphrase=lambda: self.webhook_pass_var.get().strip(),
            get_strategy_filter=lambda: self.strategy_filter_var.get().strip(),
            log=lambda m: self.backend.ui_queue.put(
                {"kind": "log", "time": "", "message": m, "signal": "", "pair": "", "status": ""}
            ),
        )

        # Cloud relay client (optional) — polls your relay for broadcast signals.
        self.relay = RelayClient(
            get_url=lambda: self.relay_url_var.get().strip(),
            get_token=lambda: self.relay_token_var.get().strip(),
            on_signal=self._on_webhook_signal,
            log=lambda m: self.backend.ui_queue.put(
                {"kind": "log", "time": "", "message": m, "signal": "", "pair": "", "status": ""}
            ),
        )

        # Built-in strategy engine (the bot's own port of the indicator) — runs
        # off the same webhook handler, tagged source="strategy". Gated by the
        # same licence token as the cloud feed (verified against the relay).
        self.strategy_runner = StrategyRunner(
            on_signal=self._on_webhook_signal,
            log=lambda m: self.backend.ui_queue.put(
                {"kind": "log", "time": "", "message": m, "signal": "", "pair": "", "status": ""}
            ),
            get_token=lambda: self.relay_token_var.get().strip(),
            get_verify_url=lambda: licence.verify_url_from_relay(self.relay_url_var.get().strip()),
            get_positions=self.backend.open_position_sides,
        )

        self.connected = False
        self._live_ack = bool(self.saved.get("live_ack", False))
        self._required_update = False     # a [required] update is pending → block connect
        self._latest_update = None        # last update info dict seen (for the prompt)
        self._skipped_version = str(self.saved.get("skipped_version", ""))
        self._build_ui()
        self._load_saved_into_ui()
        self._update_manual_state()      # disabled until connected (+ manual toggle)
        self._push_settings()
        self._push_strategy()            # apply built-in strategy config
        self._autostart_webhook()       # ready to receive signals out of the box
        self._autostart_relay()         # auto-connect cloud signals if licensed
        self.root.after(2000, self._refresh_trial_status)  # trial/licence countdown strip
        self._tray = None                # lazy-created system-tray controller
        self._online = None              # tri-state: None=unknown, True/False
        self._net_check_running = False  # guards against overlapping probes
        self._notify_ip_next = False     # alert public IP on the next connect
        self.root.after(150, self._drain_ui_queue)
        self.root.after(3000, self._check_update)
        self.root.after(3000, self._schedule_update_recheck)
        self.root.after(800, self._check_internet)
        self.root.after(1200, self._auto_show_ip)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    def _check_update(self, manual: bool = False) -> None:
        """Background check (GitHub Releases, version.json fallback). On startup
        it's silent unless an update exists; ``manual=True`` (the footer link)
        also reports 'up to date' / 'offline' so the click always has feedback."""
        import updater
        from config import UPDATE_REPO, UPDATE_URL, UPDATE_ASSET

        if manual and getattr(self, "_update_link", None):
            self._update_link.config(text="Checking…")

        def worker():
            try:
                info = updater.check_latest(repo=UPDATE_REPO, fallback_url=UPDATE_URL,
                                            asset_hint=UPDATE_ASSET)
                ok = True
            except Exception:  # noqa: BLE001 - offline / rate-limited
                info, ok = None, False
            self.root.after(0, lambda: self._update_check_done(info, ok, manual))

        threading.Thread(target=worker, daemon=True).start()

    def _update_check_done(self, info, ok: bool, manual: bool) -> None:
        import updater
        if getattr(self, "_update_link", None):
            self._update_link.config(text="Check for updates")
        newer = bool(info) and updater.is_newer(info.get("version", ""), APP_VERSION)
        if newer:
            self._latest_update = info
            version = info.get("version", "")
            if info.get("required"):
                self._required_update = True
                self._show_required_banner(version)
            # Auto checks respect a skipped (optional) version; required updates and
            # the manual link always prompt.
            elif not manual and version == self._skipped_version:
                return
            self._notify_update(info, manual=manual)
        elif manual:
            if not ok:
                messagebox.showwarning(
                    "Check for updates",
                    "Couldn't reach the update server.\n\nCheck your internet "
                    "connection and try again.")
            else:
                messagebox.showinfo("Check for updates",
                                    f"You're up to date — v{APP_VERSION} is the latest.")

    def _show_required_banner(self, latest: str) -> None:
        """Make the required-update state visible and disable Connect."""
        if getattr(self, "alert_label", None):
            self.alert_label.config(
                text=f"⚠ Required update v{latest} — connecting is disabled until you update")
        if getattr(self, "connect_btn", None) and not self.connected:
            self.connect_btn.config(state="disabled")

    def _schedule_update_recheck(self) -> None:
        """Re-check for updates periodically so a long-running (unattended) bot
        eventually sees a new release without needing a restart."""
        from config import UPDATE_RECHECK_HOURS
        interval = max(1, int(UPDATE_RECHECK_HOURS)) * 3600 * 1000
        self.root.after(interval, self._periodic_update_check)

    def _periodic_update_check(self) -> None:
        self._check_update(manual=False)
        self._schedule_update_recheck()

    def _notify_update(self, info: dict, manual: bool = False) -> None:
        import updater
        latest = info.get("version", "")
        notes = (info.get("notes") or "").strip()
        required = bool(info.get("required"))
        # Can we self-update? Only from a frozen Windows build with a verifiable exe.
        can_auto = (updater.frozen_exe_path() is not None and os.name == "nt"
                    and bool(info.get("url")) and bool(info.get("sha256_url")))
        # Offer "Skip this version" for optional updates only (never for required).
        action = self._update_dialog(latest, notes, required, can_auto,
                                     allow_skip=not required)
        if action == "install":
            if can_auto:
                self._do_self_update(info)
            else:
                self._open_url(info.get("page") or info.get("url") or WEBSITE_URL)
        elif action == "skip":
            self._skipped_version = latest

    def _update_dialog(self, latest, notes, required, can_auto, allow_skip) -> str:
        """Modal update prompt. Returns 'install', 'skip', or 'later'."""
        dlg = tk.Toplevel(self.root)
        dlg.title("Required update" if required else "Update available")
        dlg.transient(self.root)
        dlg.resizable(False, False)
        dlg.configure(bg=theme.BG)
        result = {"v": "later"}

        head = f"Version {latest} is available — you have {APP_VERSION}."
        tk.Label(dlg, text=head, bg=theme.BG, fg=theme.TXT, font=("Segoe UI Semibold", 11),
                 wraplength=420, justify="left").pack(anchor="w", padx=16, pady=(14, 4))
        if required:
            tk.Label(dlg, text="This is a required update — connecting is disabled until "
                               "you install it.", bg=theme.BG, fg=RED, wraplength=420,
                     justify="left").pack(anchor="w", padx=16, pady=(0, 4))
        if notes:
            box = tk.Text(dlg, height=min(10, max(3, notes.count(chr(10)) + 2)), width=54,
                          bg=theme.ELEV, fg=theme.TXT, relief="flat", wrap="word",
                          font=("Segoe UI", 9))
            box.insert("1.0", notes)
            box.configure(state="disabled")
            box.pack(fill="both", padx=16, pady=6)

        btns = tk.Frame(dlg, bg=theme.BG)
        btns.pack(fill="x", padx=16, pady=(6, 14))

        def choose(v):
            result["v"] = v
            dlg.destroy()

        primary = "⬇ Install now" if can_auto else "⬇ Open download page"
        b = tk.Button(btns, text=primary, command=lambda: choose("install"))
        theme.style_button(b, "accent")
        b.pack(side="right")
        tk.Button(btns, text="Later", command=lambda: choose("later")).pack(side="right", padx=8)
        if allow_skip:
            tk.Button(btns, text="Skip this version",
                      command=lambda: choose("skip")).pack(side="right")

        dlg.protocol("WM_DELETE_WINDOW", lambda: choose("later"))
        dlg.grab_set()
        self.root.wait_window(dlg)
        return result["v"]

    def _do_self_update(self, info: dict) -> None:
        """Download the new exe, verify its SHA-256, then hand off to the swap-
        and-relaunch helper and quit. Runs the download off the UI thread."""
        import tempfile
        import updater

        prog = tk.Toplevel(self.root)
        prog.title("Updating")
        prog.transient(self.root)
        prog.resizable(False, False)
        lbl = tk.Label(prog, text="Downloading update…", padx=24, pady=12)
        lbl.pack()
        bar = ttk.Progressbar(prog, length=320, mode="determinate", maximum=100)
        bar.pack(padx=24, pady=(0, 16))

        def set_progress(done, total):
            pct = (done * 100 // total) if total else 0
            self.root.after(0, lambda: (bar.configure(value=pct),
                                        lbl.configure(text=f"Downloading update… {pct}%")))

        def fail(msg):
            prog.destroy()
            messagebox.showerror("Update failed",
                                 f"{msg}\n\nYou can download it manually instead.")
            self._open_url(info.get("page") or WEBSITE_URL)

        def worker():
            try:
                dest = os.path.join(tempfile.gettempdir(), "PrometheusAICryptoBot_new.exe")
                updater.download(info["url"], dest, progress=set_progress)
                # Fetch + verify checksum.
                import urllib.request
                req = urllib.request.Request(info["sha256_url"],
                                             headers={"User-Agent": "PrometheusBot"})
                sha_body = urllib.request.urlopen(req, timeout=15).read().decode("utf-8", "replace")
                expected = updater.extract_sha256(sha_body)
                if not expected or not updater.verify_sha256(dest, expected):
                    self.root.after(0, lambda: fail("Checksum verification failed — "
                                                    "the download may be corrupt or tampered."))
                    return
                if not updater.apply_update(dest):
                    self.root.after(0, lambda: fail("Couldn't start the installer."))
                    return
                self.root.after(0, self._quit_for_update)
            except Exception as exc:  # noqa: BLE001
                self.root.after(0, lambda: fail(str(exc)))

        threading.Thread(target=worker, daemon=True).start()

    def _quit_for_update(self) -> None:
        """Stop everything cleanly and exit so the helper can replace the exe and
        relaunch it. Reuses the normal shutdown path (never hides to tray)."""
        self._real_quit()

    # ====================================================================
    # Internet connectivity
    # ====================================================================
    def _check_internet(self) -> None:
        """Probe connectivity off-thread, then reschedule. Never blocks the GUI."""
        # Skip if a probe is still in flight (slow/captive networks) so worker
        # threads can't pile up; just reschedule a check.
        if not self._net_check_running:
            self._net_check_running = True

            def worker():
                try:
                    online = connectivity.has_internet(timeout=2.0)
                finally:
                    self._net_check_running = False
                self.root.after(0, lambda: self._apply_internet_state(online))

            threading.Thread(target=worker, daemon=True).start()
        # Poll faster while offline so recovery shows quickly; relax when online.
        delay = 5000 if self._online else 3000
        self.root.after(delay, self._check_internet)

    def _apply_internet_state(self, online: bool) -> None:
        if not getattr(self, "net_label", None):
            return
        prev = self._online                 # None on the very first probe
        self._online = online
        if online:
            self.net_dot.config(fg=GREEN)
            self.net_label.config(text="Online", fg=TXT_DIM)
        else:
            self.net_dot.config(fg=RED)
            self.net_label.config(text="No internet", fg=RED)
        # Log on real transitions only (skip the first probe's None->state).
        if prev is True and not online:
            self.backend.ui_queue.put({
                "kind": "log", "time": "", "signal": "", "pair": "", "status": "Offline",
                "message": "⚠ Internet connection lost — orders, price feed and "
                           "cloud signals are paused until it returns.",
            })
        elif prev is False and online:
            self.backend.ui_queue.put({
                "kind": "log", "time": "", "signal": "", "pair": "", "status": "Online",
                "message": "✓ Internet connection restored.",
            })

    # ====================================================================
    # UI construction
    # ====================================================================
    def _build_ui(self) -> None:
        theme.apply(self.root)
        self.root.title(APP_TITLE)
        self.root.geometry("1160x780")
        self.root.minsize(1000, 720)
        self._set_window_icon()
        # Flat, modern tk.Buttons everywhere (set before widgets are built).
        # Default is a dark "ghost" look; coloured buttons override their bg.
        self.root.option_add("*Button.relief", "flat")
        self.root.option_add("*Button.borderWidth", "0")
        self.root.option_add("*Button.highlightThickness", "0")
        self.root.option_add("*Button.cursor", "hand2")
        self.root.option_add("*Button.background", ELEV)
        self.root.option_add("*Button.foreground", TXT)
        self.root.option_add("*Button.activeBackground", theme.BORDER)
        self.root.option_add("*Button.activeForeground", TXT)
        self.root.option_add("*Button.font", "{Segoe UI Semibold} 9")
        self.root.option_add("*Button.padX", "10")
        self.root.option_add("*Button.padY", "4")

        self._build_header()
        self._build_footer()

        body = ttk.Frame(self.root, padding=12)
        body.pack(fill="both", expand=True)
        body.columnconfigure(0, weight=1, uniform="col")
        body.columnconfigure(1, weight=1, uniform="col")
        body.rowconfigure(0, weight=1)

        # Independent left/right columns so the tall Trade Settings panel on the
        # right doesn't stretch the left column's rows.
        left = ttk.Frame(body)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 6))
        left.columnconfigure(0, weight=1)
        left.rowconfigure(1, weight=1)   # Open Positions fills the middle

        right = ttk.Frame(body)
        right.grid(row=0, column=1, sticky="nsew", padx=(6, 0))
        right.columnconfigure(0, weight=1)
        right.rowconfigure(1, weight=1)  # Trade Log fills

        self._build_api_panel(left)        # row 0 (top)
        self._build_positions(left)        # row 1 (expands, ~10+ trades)
        self._build_trade_buttons(left)    # row 2 (bottom)
        self._build_trade_settings(right)  # row 0
        self._build_trade_log(right)       # row 1 (expands)

    def _set_window_icon(self) -> None:
        try:
            self.root.iconbitmap(resource_path("icon.ico"))
        except Exception:  # noqa: BLE001 - non-Windows / missing icon
            try:
                self._win_icon = tk.PhotoImage(file=resource_path("logo.png"))
                self.root.iconphoto(True, self._win_icon)
            except Exception:  # noqa: BLE001
                pass

    def _build_header(self) -> None:
        bar = tk.Frame(self.root, bg=HEADER, height=58)
        bar.pack(fill="x", side="top")
        bar.pack_propagate(False)

        # Logo (kept as an attribute so it isn't garbage-collected).
        try:
            img = tk.PhotoImage(file=resource_path("logo.png"))
            # Scale to fit the header bar height (works for portrait logos too).
            n = max(1, img.height() // 40)
            self._logo_img = img.subsample(n, n)
            tk.Label(bar, image=self._logo_img, bg=HEADER).pack(side="left", padx=(14, 8))
        except Exception:  # noqa: BLE001
            tk.Label(bar, text="🔥", bg=HEADER, fg=ACCENT, font=("Segoe UI", 18)).pack(side="left", padx=14)

        title_box = tk.Frame(bar, bg=HEADER)
        title_box.pack(side="left")
        tk.Label(title_box, text=APP_TITLE, bg=HEADER, fg=TXT,
                 font=("Segoe UI Semibold", 15)).pack(anchor="w")
        tk.Label(title_box, text=f"v{APP_VERSION}", bg=HEADER, fg=TXT_DIM,
                 font=("Segoe UI", 8)).pack(anchor="w")

        # Live status on the right.
        self.alert_label = tk.Label(bar, text="", bg=HEADER, fg=RED, font=("Segoe UI", 9, "bold"))
        self.alert_label.pack(side="right", padx=14)

        stat = tk.Frame(bar, bg=HEADER)
        stat.pack(side="right", padx=10)
        self.status_dot = tk.Label(stat, text="●", fg=RED, bg=HEADER, font=("Segoe UI", 12))
        self.status_dot.pack(side="left", padx=(0, 4))
        self.conn_label = tk.Label(stat, text="Disconnected", bg=HEADER, fg=TXT,
                                   font=("Segoe UI Semibold", 10))
        self.conn_label.pack(side="left")
        self.exch_label = tk.Label(stat, text="  ·  —", bg=HEADER, fg=TXT_DIM, font=("Segoe UI", 10))
        self.exch_label.pack(side="left")
        self.bal_label = tk.Label(stat, text="  ·  $0.00", bg=HEADER, fg=TXT, font=("Segoe UI", 10))
        self.bal_label.pack(side="left")
        self.pnl_label = tk.Label(stat, text="  ·  PnL $0.00", bg=HEADER, fg=TXT,
                                  font=("Segoe UI Semibold", 10))
        self.pnl_label.pack(side="left")

    def _build_footer(self) -> None:
        bar = tk.Frame(self.root, bg=HEADER, height=26)
        bar.pack(fill="x", side="bottom")
        bar.pack_propagate(False)

        tk.Label(bar, text="© Prometheus AI", bg=HEADER, fg=TXT_DIM,
                 font=("Segoe UI", 8)).pack(side="left", padx=12)

        # Internet connectivity indicator. Starts "checking" until the first
        # probe completes (see _check_internet).
        net = tk.Frame(bar, bg=HEADER)
        net.pack(side="left", padx=4)
        self.net_dot = tk.Label(net, text="●", bg=HEADER, fg=GREY, font=("Segoe UI", 9))
        self.net_dot.pack(side="left", padx=(0, 3))
        self.net_label = tk.Label(net, text="Checking internet…", bg=HEADER, fg=TXT_DIM,
                                  font=("Segoe UI", 8))
        self.net_label.pack(side="left")

        def link(parent, text, action):
            lbl = tk.Label(parent, text=text, bg=HEADER, fg=ACCENT, cursor="hand2",
                           font=("Segoe UI", 9, "underline"))
            lbl.pack(side="right", padx=10)
            lbl.bind("<Button-1>", lambda e: action())
            return lbl

        link(bar, SUPPORT_EMAIL, lambda: self._open_url(f"mailto:{SUPPORT_EMAIL}"))
        link(bar, "Website: prometheusai.tech", lambda: self._open_url(WEBSITE_URL))
        self._update_link = link(bar, "Check for updates",
                                 lambda: self._check_update(manual=True))

    def _open_url(self, url: str) -> None:
        try:
            webbrowser.open(url)
        except Exception:  # noqa: BLE001
            messagebox.showinfo("Link", url)

    def _build_api_panel(self, parent) -> None:
        f = ttk.LabelFrame(parent, text="API Settings", padding=10)
        f.grid(row=0, column=0, sticky="new", padx=5, pady=5)   # hug top, natural height
        f.columnconfigure(1, weight=1)

        ttk.Label(f, text="Select Exchange:").grid(row=0, column=0, sticky="w", pady=4)
        self._exchange_labels = [EXCHANGE_LABELS[e] for e in SUPPORTED_EXCHANGES]
        self.exchange_var = tk.StringVar(value=self._exchange_labels[0])
        self.exchange_combo = ttk.Combobox(
            f, textvariable=self.exchange_var, values=self._exchange_labels, state="readonly"
        )
        self.exchange_combo.grid(row=0, column=1, sticky="ew", pady=4)
        self.exchange_combo.bind("<<ComboboxSelected>>",
                                 lambda e: (self._toggle_passphrase(), self._sync_chart()))

        ttk.Label(f, text="API Key:").grid(row=1, column=0, sticky="w", pady=4)
        self.api_key_var = tk.StringVar()
        ttk.Entry(f, textvariable=self.api_key_var, show="•").grid(row=1, column=1, sticky="ew", pady=4)

        ttk.Label(f, text="API Secret:").grid(row=2, column=0, sticky="w", pady=4)
        self.api_secret_var = tk.StringVar()
        ttk.Entry(f, textvariable=self.api_secret_var, show="•").grid(row=2, column=1, sticky="ew", pady=4)

        self.pass_label = ttk.Label(f, text="API Passphrase:")
        self.passphrase_var = tk.StringVar()
        self.pass_entry = ttk.Entry(f, textvariable=self.passphrase_var, show="•")

        ttk.Label(f, text="Market:").grid(row=4, column=0, sticky="w", pady=4)
        self.market_var = tk.StringVar(value="Spot")
        self.market_combo = ttk.Combobox(f, textvariable=self.market_var,
                                         values=["Spot", "Futures"], state="readonly")
        self.market_combo.grid(row=4, column=1, sticky="ew", pady=4)
        self.market_combo.bind("<<ComboboxSelected>>", lambda e: self._sync_chart())

        btns = ttk.Frame(f)
        btns.grid(row=5, column=0, columnspan=2, pady=(10, 0))
        self.connect_btn = tk.Button(btns, text="🔌 Connect", width=14, command=self._on_connect)
        theme.style_button(self.connect_btn, "buy")
        self.connect_btn.pack(side="left", padx=5)
        self.disconnect_btn = tk.Button(btns, text="⛔ Disconnect", width=14,
                                        command=self._on_disconnect, state="disabled")
        theme.style_button(self.disconnect_btn, "sell")
        self.disconnect_btn.pack(side="left", padx=5)

        # Public-IP helper — paste this into the exchange API key's IP whitelist
        # (Binance/others require a whitelist once any trading permission is on).
        ipf = ttk.Frame(f)
        ipf.grid(row=6, column=0, columnspan=2, sticky="ew", pady=(10, 0))
        self.ip_btn = tk.Button(ipf, text="🌐 Show my IP", command=self._show_my_ip)
        theme.style_button(self.ip_btn, "ghost")
        self.ip_btn.pack(side="left")
        self.ip_value = tk.StringVar(value="—")
        tk.Label(ipf, textvariable=self.ip_value, bg=PANEL, fg=ACCENT,
                 font=("Segoe UI Semibold", 10)).pack(side="left", padx=8)
        self.ip_copy_btn = tk.Button(ipf, text="📋 Copy", command=self._copy_my_ip, state="disabled")
        theme.style_button(self.ip_copy_btn, "ghost")
        self.ip_copy_btn.pack(side="left")
        ttk.Label(f, text="Your public IP — paste it into your exchange API key's IP whitelist "
                          "(required once you enable trading).",
                  style="Dim.TLabel", wraplength=380).grid(
            row=7, column=0, columnspan=2, sticky="w", pady=(2, 0))

        # Strategy chart — lives here so it's reachable from any Trade Settings
        # tab. Visualises the built-in strategy's signals on exchange candles.
        chart_btn = tk.Button(f, text="📈 Chart", command=self._open_strategy_chart)
        theme.style_button(chart_btn, "accent")
        chart_btn.grid(row=8, column=0, columnspan=2, sticky="w", pady=(10, 0))

        self._toggle_passphrase()

    def _toggle_passphrase(self) -> None:
        needs = exchange_id(self.exchange_var.get()) in PASSPHRASE_EXCHANGES
        if needs:
            self.pass_label.grid(row=3, column=0, sticky="w", pady=4)
            self.pass_entry.grid(row=3, column=1, sticky="ew", pady=4)
        else:
            self.pass_label.grid_remove()
            self.pass_entry.grid_remove()

    # -- public IP (for the exchange IP whitelist) --------------------------
    def _show_my_ip(self) -> None:
        """Fetch this machine's public IP off-thread and show it for copying."""
        self.ip_btn.config(text="…", state="disabled")

        def worker():
            ip = connectivity.public_ip()
            self.root.after(0, lambda: self._show_my_ip_done(ip, manual=True))

        threading.Thread(target=worker, daemon=True).start()

    def _auto_show_ip(self) -> None:
        """Quietly fetch the public IP on launch so it's visible without a click."""
        def worker():
            ip = connectivity.public_ip()
            self.root.after(0, lambda: self._show_my_ip_done(ip, manual=False))

        threading.Thread(target=worker, daemon=True).start()

    def _notify_ip_after_connect(self, exchange: str) -> None:
        """After a user connect, push the public IP as a desktop + Telegram alert."""
        def worker():
            ip = connectivity.public_ip()
            self.root.after(0, lambda: self._show_my_ip_done(ip, manual=False,
                                                             notify_exchange=exchange))

        threading.Thread(target=worker, daemon=True).start()

    def _show_my_ip_done(self, ip, manual: bool, notify_exchange: str | None = None) -> None:
        self.ip_btn.config(text="🌐 Show my IP", state="normal")
        if not ip:
            if manual:
                self.ip_value.set("unavailable (no internet?)")
            if notify_exchange:
                self.backend.notifier.notify(
                    f"Connected · {exchange_label(notify_exchange)}",
                    "Couldn't determine your public IP — verify your exchange API-key "
                    "IP whitelist manually.", level="info", important=True)
            return
        self.ip_value.set(ip)
        self.ip_copy_btn.config(state="normal")
        # Warn if the public IP changed since the last saved one — a whitelisted
        # key will reject trades from a new IP until the whitelist is updated.
        prev = self.saved.get("last_ip", "")
        changed = bool(prev and prev != ip)
        if changed:
            self.backend.ui_queue.put({
                "kind": "log", "time": "", "signal": "", "pair": "", "status": "IP changed",
                "message": f"⚠ Your public IP changed (was {prev}, now {ip}). "
                           "Update your exchange API-key IP whitelist or trades will be rejected.",
            })
        # Desktop + Telegram alert on a user-initiated connect.
        if notify_exchange:
            if changed:
                self.backend.notifier.notify(
                    "⚠ Public IP changed",
                    f"Your IP is now {ip} (was {prev}). Update your "
                    f"{exchange_label(notify_exchange)} API-key IP whitelist or trades "
                    "will be rejected.", level="error", important=True)
            else:
                self.backend.notifier.notify(
                    f"Connected · {exchange_label(notify_exchange)}",
                    f"Your public IP is {ip}. Make sure it's whitelisted on your "
                    "exchange API key.", level="ok", important=True)
        self.saved["last_ip"] = ip   # persisted on next Save

    def _copy_my_ip(self) -> None:
        ip = self.ip_value.get()
        if ip and ip[0].isdigit():
            self.root.clipboard_clear()
            self.root.clipboard_append(ip)
            self.ip_copy_btn.config(text="✓ Copied")
            self.root.after(1200, lambda: self.ip_copy_btn.config(text="📋 Copy"))

    def _build_trade_buttons(self, parent) -> None:
        f = ttk.Frame(parent, padding=(5, 0))
        f.grid(row=2, column=0, sticky="ew", padx=5, pady=5)   # bottom of left column
        f.columnconfigure(0, weight=1)
        f.columnconfigure(1, weight=1)

        self.buy_btn = theme.RoundedButton(
            f, text="▲ BUY", bg=GREEN, active=theme.GREEN, fg="white",
            radius=5, height=40, font=("Segoe UI", 15, "bold"),
            command=lambda: self._on_manual_trade("buy"),
        )
        self.buy_btn.grid(row=0, column=0, sticky="ew", padx=(0, 5))
        self.sell_btn = theme.RoundedButton(
            f, text="▼ SELL", bg=RED, active=theme.RED, fg="white",
            radius=5, height=40, font=("Segoe UI", 15, "bold"),
            command=lambda: self._on_manual_trade("sell"),
        )
        self.sell_btn.grid(row=0, column=1, sticky="ew", padx=(5, 0))

    def _build_positions(self, parent) -> None:
        f = ttk.LabelFrame(parent, text="Open Positions", padding=8)
        f.grid(row=1, column=0, sticky="nsew", padx=5, pady=5)   # fills the middle, expands
        f.rowconfigure(1, weight=1)
        f.columnconfigure(0, weight=1)

        top = ttk.Frame(f)
        top.grid(row=0, column=0, sticky="ew", pady=(0, 6))
        ttk.Label(top, text="Symbol:").pack(side="left")
        self.symbol_var = tk.StringVar(value=TOP_PAIRS[0])
        sym_box = ttk.Combobox(top, textvariable=self.symbol_var, values=TOP_PAIRS, width=13)
        sym_box.pack(side="left", padx=6)
        self.symbol_box = sym_box
        # Live current price for the selected symbol (streamed even with no position).
        ttk.Label(top, text="Current Price:").pack(side="left", padx=(6, 2))
        self.mark_label = tk.Label(top, text="—", fg=ACCENT, bg=PANEL,
                                   font=("Segoe UI Semibold", 11))
        self.mark_label.pack(side="left", padx=2)
        # Re-subscribe the feed whenever the symbol is picked/typed.
        sym_box.bind("<<ComboboxSelected>>", lambda e: self._watch_manual_symbol())
        sym_box.bind("<Return>", lambda e: self._watch_manual_symbol())
        sym_box.bind("<FocusOut>", lambda e: self._watch_manual_symbol())
        tk.Button(top, text="🔄 Refresh Now", command=lambda: self.backend.submit({"cmd": "refresh"})).pack(side="right")

        cols = ("pair", "side", "size", "entry", "current", "pnl", "status")
        self.pos_tree = ttk.Treeview(f, columns=cols, show="headings", height=12)
        headings = ["Pair", "Type", "Size", "Entry", "Current", "PnL", "Status"]
        for c, h in zip(cols, headings):
            self.pos_tree.heading(c, text=h)
            self.pos_tree.column(c, width=80, anchor="center")
        # Colour PnL rows: green for profit, red for loss.
        self.pos_tree.tag_configure("pos", foreground=GREEN)
        self.pos_tree.tag_configure("neg", foreground=RED)
        self.pos_tree.grid(row=1, column=0, sticky="nsew")
        sb = ttk.Scrollbar(f, orient="vertical", command=self.pos_tree.yview)
        self.pos_tree.configure(yscrollcommand=sb.set)
        sb.grid(row=1, column=1, sticky="ns")

        cbar = ttk.Frame(f)
        cbar.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(6, 0))
        tk.Button(cbar, text="✖ Close Selected", command=self._close_selected).pack(side="left", padx=4)
        tk.Button(cbar, text="✂ Close %", command=self._close_partial).pack(side="left", padx=4)
        self.close_pct_var = tk.StringVar(value="50")
        ttk.Combobox(cbar, textvariable=self.close_pct_var, width=4,
                     values=("25", "33", "50", "75"), state="normal").pack(side="left")
        ttk.Label(cbar, text="%").pack(side="left", padx=(1, 4))
        tk.Button(cbar, text="🛡 Set SL/TP", command=self._set_protection).pack(side="left", padx=4)
        tk.Button(
            cbar, text="🛑 PANIC: Close All", bg=RED, fg="white", activebackground=theme.RED,
            font=("Segoe UI", 9, "bold"), command=self._close_all,
        ).pack(side="left", padx=4)

    def _set_protection(self) -> None:
        sel = self.pos_tree.selection()
        if not sel:
            messagebox.showinfo("Set SL/TP", "Select a position row first.")
            return
        vals = self.pos_tree.item(sel[0])["values"]
        pair = vals[0]
        win = tk.Toplevel(self.root)
        win.title(f"Set SL/TP — {pair}")
        win.configure(bg=PANEL)
        win.resizable(False, False)
        tk.Label(win, text=f"{pair}", bg=PANEL, fg=ACCENT,
                 font=("Segoe UI Semibold", 12)).grid(row=0, column=0, columnspan=2, padx=14, pady=(12, 8))
        tk.Label(win, text="Stop-loss price:", bg=PANEL, fg=TXT).grid(row=1, column=0, sticky="w", padx=14, pady=4)
        sl_v = tk.StringVar()
        tk.Entry(win, textvariable=sl_v, bg=ELEV, fg=TXT, relief="flat",
                 insertbackground=TXT).grid(row=1, column=1, padx=14, pady=4)
        tk.Label(win, text="Take-profit price:", bg=PANEL, fg=TXT).grid(row=2, column=0, sticky="w", padx=14, pady=4)
        tp_v = tk.StringVar()
        tk.Entry(win, textvariable=tp_v, bg=ELEV, fg=TXT, relief="flat",
                 insertbackground=TXT).grid(row=2, column=1, padx=14, pady=4)

        def apply():
            self.backend.submit({"cmd": "set_protection", "symbol": pair,
                                 "sl": self._float(sl_v.get(), 0) or None,
                                 "tp": self._float(tp_v.get(), 0) or None})
            win.destroy()

        b = tk.Button(win, text="✅ Place orders", command=apply)
        theme.style_button(b, "accent")
        b.grid(row=3, column=0, columnspan=2, padx=14, pady=12, sticky="ew")

    def _close_selected(self) -> None:
        sel = self.pos_tree.selection()
        if not sel:
            messagebox.showinfo("Close", "Select a position row first.")
            return
        pair = self.pos_tree.item(sel[0])["values"][0]
        if messagebox.askyesno("Close position", f"Close {pair} at market?"):
            self.backend.submit({"cmd": "close", "pair": pair})

    def _close_partial(self) -> None:
        sel = self.pos_tree.selection()
        if not sel:
            messagebox.showinfo("Close %", "Select a position row first.")
            return
        pair = self.pos_tree.item(sel[0])["values"][0]
        pct = self._float(self.close_pct_var.get(), 0)
        if not 0 < pct < 100:
            messagebox.showwarning("Close %", "Enter a percentage between 1 and 99.")
            return
        if messagebox.askyesno("Partial close",
                               f"Close {pct:g}% of {pair} at market (reduce-only)?"):
            self.backend.submit({"cmd": "close", "pair": pair, "fraction": pct / 100.0})

    def _close_all(self) -> None:
        if messagebox.askyesno(
            "PANIC — Close All",
            "Flatten ALL open positions at market right now?\n\nThis cannot be undone.",
        ):
            self.backend.submit({"cmd": "close", "pair": None})

    def _open_analytics(self) -> None:
        AnalyticsWindow(self.root, history)

    def _build_trade_settings(self, parent) -> None:
        outer = ttk.LabelFrame(parent, text="Trade Settings", padding=8)
        outer.grid(row=0, column=0, sticky="nsew", padx=5, pady=5)
        outer.columnconfigure(0, weight=1)

        nb = ttk.Notebook(outer)
        nb.grid(row=0, column=0, sticky="nsew")
        exec_tab = ttk.Frame(nb, padding=8)
        risk_tab = ttk.Frame(nb, padding=8)
        webhook_tab = ttk.Frame(nb, padding=8)
        strategy_tab = ttk.Frame(nb, padding=8)
        alerts_tab = ttk.Frame(nb, padding=8)
        nb.add(exec_tab, text="Execution")
        nb.add(risk_tab, text="Modes & Risk")
        nb.add(webhook_tab, text="License")
        nb.add(strategy_tab, text="Strategy")
        nb.add(alerts_tab, text="Alerts")
        for t in (exec_tab, risk_tab, webhook_tab, strategy_tab, alerts_tab):
            t.columnconfigure(1, weight=1)

        self._build_exec_tab(exec_tab)
        self._build_risk_tab(risk_tab)
        self._build_webhook_tab(webhook_tab)
        self._build_strategy_tab(strategy_tab)
        self._build_alerts_tab(alerts_tab)

        # Size the notebook to the SELECTED tab (not the tallest), so short tabs
        # like Webhook/Alerts don't leave empty space — the buttons rise up and
        # the Trade Log below grows to fill it.
        def _fit_nb(_=None):
            try:
                cur = nb.nametowidget(nb.select())
                nb.configure(height=max(cur.winfo_reqheight(), 1))
            except Exception:  # noqa: BLE001
                pass
        nb.bind("<<NotebookTabChanged>>", lambda e: self.root.after_idle(_fit_nb))
        self.root.after_idle(_fit_nb)

        btnrow = ttk.Frame(outer)
        btnrow.grid(row=1, column=0, sticky="ew", pady=(8, 0))
        tk.Button(btnrow, text="💾 Save", command=self._save_all).pack(
            side="left", expand=True, fill="x", padx=2)
        tk.Button(btnrow, text="📦 Backup", command=self._backup_settings).pack(
            side="left", expand=True, fill="x", padx=2)
        tk.Button(btnrow, text="📂 Restore", command=self._restore_settings).pack(
            side="left", expand=True, fill="x", padx=2)
        reset_btn = tk.Button(btnrow, text="🔄 Reset", command=self._reset_all_defaults)
        theme.style_button(reset_btn, "danger")
        reset_btn.pack(side="left", expand=True, fill="x", padx=2)

    def _backup_settings(self) -> None:
        path = filedialog.asksaveasfilename(
            defaultextension=".prom", filetypes=[("Prometheus backup", "*.prom")],
            title="Backup encrypted settings")
        if not path:
            return
        try:
            security.save_credentials(self.pin, self._collect_settings())
            security.export_portable(self.pin, path)
            messagebox.showinfo("Backup", f"Encrypted backup saved:\n{path}")
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror("Backup failed", str(exc))

    def _restore_settings(self) -> None:
        path = filedialog.askopenfilename(
            filetypes=[("Prometheus backup", "*.prom"), ("All files", "*.*")],
            title="Restore settings backup")
        if not path:
            return
        try:
            data = security.import_portable(self.pin, path)
        except Exception:  # noqa: BLE001
            messagebox.showerror("Restore failed",
                                 "Couldn't read the backup — wrong PIN or file.")
            return
        self.saved = data
        self._load_saved_into_ui()
        self._push_settings()
        security.save_credentials(self.pin, self._collect_settings())
        messagebox.showinfo("Restore", "Settings restored from backup.")

    def _build_exec_tab(self, f) -> None:
        ttk.Label(f, text="Trade Size:").grid(row=0, column=0, sticky="w", pady=4)
        size_row = ttk.Frame(f)
        size_row.grid(row=0, column=1, sticky="w", pady=4)
        self.size_var = tk.StringVar(value=str(DEFAULT_TRADE_SIZE))
        size_entry = ttk.Entry(size_row, textvariable=self.size_var, width=12)
        size_entry.pack(side="left")
        size_entry.bind("<FocusOut>", lambda ev: self._push_settings())
        size_entry.bind("<Return>", lambda ev: self._push_settings())
        self.size_hint = ttk.Label(size_row, text="base (e.g. BTC)")
        self.size_hint.pack(side="left", padx=6)

        ttk.Label(f, text="Sizing mode:").grid(row=1, column=0, sticky="w", pady=4)
        self.sizing_mode_var = tk.StringVar(value=SIZING_MODE_LABELS[DEFAULT_SIZING_MODE])
        ttk.Combobox(
            f, textvariable=self.sizing_mode_var,
            values=[SIZING_MODE_LABELS[m] for m in SIZING_MODES], state="readonly",
        ).grid(row=1, column=1, sticky="ew", pady=4)
        self.sizing_mode_var.trace_add("write", lambda *a: self._on_sizing_mode_change())

        rr = ttk.Frame(f)
        rr.grid(row=2, column=0, columnspan=2, sticky="w")
        ttk.Label(rr, text="Risk %:").pack(side="left")
        self.risk_pct_var = tk.StringVar(value=str(DEFAULT_RISK_PERCENT))
        risk_entry = ttk.Entry(rr, textvariable=self.risk_pct_var, width=6)
        risk_entry.pack(side="left", padx=6)
        risk_entry.bind("<FocusOut>", lambda ev: self._push_settings())
        risk_entry.bind("<Return>", lambda ev: self._push_settings())
        ttk.Label(rr, text="(risk-based modes)").pack(side="left")

        self.auto_bracket_var = tk.BooleanVar(value=DEFAULT_AUTO_BRACKET)
        theme.make_check(
            f, text="Auto-place SL/TP from alerts (scale out TP1/TP2)",
            variable=self.auto_bracket_var, command=self._push_settings,
        ).grid(row=3, column=0, columnspan=2, sticky="w", pady=2)

        scr = ttk.Frame(f)
        scr.grid(row=4, column=0, columnspan=2, sticky="w")
        ttk.Label(scr, text="TP1 fill %:").pack(side="left")
        self.tp1_scale_var = tk.StringVar(value="50")
        e = ttk.Entry(scr, textvariable=self.tp1_scale_var, width=6)
        e.pack(side="left", padx=6)
        e.bind("<FocusOut>", lambda ev: self._push_settings())
        ttk.Label(scr, text="(% closed at TP1, rest at TP2)").pack(side="left")

        ttk.Separator(f, orient="horizontal").grid(row=5, column=0, columnspan=2, sticky="ew", pady=6)

        ttk.Label(f, text="Order type:").grid(row=6, column=0, sticky="w", pady=4)
        otr = ttk.Frame(f)
        otr.grid(row=6, column=1, sticky="w", pady=4)
        self.order_type_var = tk.StringVar(value=DEFAULT_ORDER_TYPE)
        ttk.Combobox(
            otr, textvariable=self.order_type_var, values=ORDER_TYPES, state="readonly", width=8,
        ).pack(side="left")
        ttk.Label(otr, text="Limit px:").pack(side="left", padx=(8, 2))
        self.limit_price_var = tk.StringVar()
        ttk.Entry(otr, textvariable=self.limit_price_var, width=12).pack(side="left")
        self.order_type_var.trace_add("write", lambda *a: self._push_settings())

        ttk.Label(f, text="Leverage (x):").grid(row=7, column=0, sticky="w", pady=4)
        lvr = ttk.Frame(f)
        lvr.grid(row=7, column=1, sticky="w", pady=4)
        self.leverage_var = tk.StringVar(value=str(DEFAULT_LEVERAGE))
        ttk.Entry(lvr, textvariable=self.leverage_var, width=6).pack(side="left")
        ttk.Label(lvr, text="0 = leave as-is   Margin:").pack(side="left", padx=(6, 2))
        self.margin_mode_var = tk.StringVar(value=DEFAULT_MARGIN_MODE)
        ttk.Combobox(
            lvr, textvariable=self.margin_mode_var,
            values=["(default)", "cross", "isolated"], state="readonly", width=9,
        ).pack(side="left")
        self.leverage_var.trace_add("write", lambda *a: self._on_leverage_change())

        sl = ttk.Frame(f)
        sl.grid(row=8, column=0, columnspan=2, sticky="w", pady=4)
        ttk.Label(sl, text="Slippage guard %:").pack(side="left")
        self.slippage_var = tk.StringVar(value=str(DEFAULT_SLIPPAGE_PCT))
        se = ttk.Entry(sl, textvariable=self.slippage_var, width=6)
        se.pack(side="left", padx=6)
        se.bind("<FocusOut>", lambda ev: self._push_settings())
        ttk.Label(sl, text="(0 = off; skip if price moved this far from the signal)").pack(side="left")

        self.round_min_var = tk.BooleanVar(value=False)
        theme.make_check(
            f, text="Round small orders up to the exchange minimum (else block)",
            variable=self.round_min_var, command=self._push_settings,
        ).grid(row=9, column=0, columnspan=2, sticky="w", pady=2)

    def _build_risk_tab(self, f) -> None:
        self.manual_var = tk.BooleanVar(value=True)
        theme.make_check(
            f, text="Enable Manual Trading", variable=self.manual_var,
            command=self._update_manual_state,
        ).grid(row=0, column=0, columnspan=2, sticky="w", pady=2)

        self.safe_var = tk.BooleanVar(value=DEFAULT_SAFE_MODE)
        theme.make_check(
            f, text="Safe Mode (simulate, no real orders)", variable=self.safe_var,
            command=self._push_settings,
        ).grid(row=1, column=0, columnspan=2, sticky="w", pady=2)

        self.readonly_var = tk.BooleanVar(value=False)
        theme.make_check(
            f, text="Read-only monitoring (block all orders)", variable=self.readonly_var,
            command=self._push_settings,
        ).grid(row=2, column=0, columnspan=2, sticky="w", pady=2)

        ttk.Separator(f, orient="horizontal").grid(row=3, column=0, columnspan=2, sticky="ew", pady=6)
        ttk.Label(f, text="Guardrails (0 = off):", font=("Segoe UI", 9, "bold")).grid(
            row=4, column=0, columnspan=2, sticky="w"
        )

        self.max_open_var = tk.StringVar(value=str(DEFAULT_MAX_OPEN_POSITIONS))
        self.daily_loss_var = tk.StringVar(value="0")
        self.daily_loss_pct_var = tk.StringVar(value="5")
        self.daily_profit_var = tk.StringVar(value="0")
        self.daily_profit_pct_var = tk.StringVar(value="0")
        self.cooldown_var = tk.StringVar(value="0")
        self.dedupe_var = tk.StringVar(value="0")
        self.loss_streak_var = tk.StringVar(value="0")
        self.streak_cd_var = tk.StringVar(value="0")
        self.max_dd_var = tk.StringVar(value="0")
        self.start_hour_var = tk.StringVar(value="0")
        self.end_hour_var = tk.StringVar(value="0")
        rows = [
            ("Max open positions:", self.max_open_var),
            (f"Daily loss limit ({QUOTE_CURRENCY}):", self.daily_loss_var),
            ("Daily loss limit (% of balance):", self.daily_loss_pct_var),
            (f"Daily profit limit ({QUOTE_CURRENCY}):", self.daily_profit_var),
            ("Daily profit limit (% of balance):", self.daily_profit_pct_var),
            ("Cooldown / symbol (s):", self.cooldown_var),
            ("Dedupe window (s):", self.dedupe_var),
            ("Loss-streak limit (losses):", self.loss_streak_var),
            ("Loss-streak pause (s):", self.streak_cd_var),
            ("Max drawdown halt (%):", self.max_dd_var),
            ("Trade start hour (0-23):", self.start_hour_var),
            ("Trade end hour (0-23):", self.end_hour_var),
        ]
        for i, (text, var) in enumerate(rows, start=5):
            ttk.Label(f, text=text).grid(row=i, column=0, sticky="w", pady=2)
            e = ttk.Entry(f, textvariable=var, width=10)
            e.grid(row=i, column=1, sticky="w", pady=2)
            e.bind("<FocusOut>", lambda ev: self._push_settings())

        gr = ttk.Frame(f)
        gr.grid(row=17, column=0, columnspan=2, sticky="ew", pady=(8, 0))
        tk.Button(gr, text="🔁 Reset daily limit", command=self._reset_daily).pack(side="left")
        self.guardrail_status = tk.Label(gr, text="", fg=RED, bg=PANEL, font=("Segoe UI", 9, "bold"))
        self.guardrail_status.pack(side="left", padx=8)
        # Shows the *resolved* daily limits in quote ccy (converts the % fields
        # using the current balance) so it's unambiguous which limit is active.
        self.daily_limit_lbl = tk.Label(gr, text="", fg=TXT_DIM, bg=PANEL, font=("Segoe UI", 9))
        self.daily_limit_lbl.pack(side="left", padx=8)
        for v in (self.daily_loss_var, self.daily_loss_pct_var,
                  self.daily_profit_var, self.daily_profit_pct_var):
            v.trace_add("write", lambda *a: self._on_daily_limit_edit())
        self._refresh_daily_limit_status()

        ttk.Separator(f, orient="horizontal").grid(row=18, column=0, columnspan=2, sticky="ew", pady=6)
        self.move_be_var = tk.BooleanVar(value=False)
        theme.make_check(
            f, text="Move stop to breakeven on TP1 event (from indicator)",
            variable=self.move_be_var, command=self._push_settings,
        ).grid(row=20, column=0, columnspan=2, sticky="w", pady=2)
        self.auto_reconnect_var = tk.BooleanVar(value=True)
        theme.make_check(
            f, text="Auto-reconnect if the exchange connection drops",
            variable=self.auto_reconnect_var, command=self._push_settings,
        ).grid(row=19, column=0, columnspan=2, sticky="w", pady=2)
        tr = ttk.Frame(f)
        tr.grid(row=21, column=0, columnspan=2, sticky="w", pady=2)
        ttk.Label(tr, text="Trailing stop %:").pack(side="left")
        self.trailing_var = tk.StringVar(value="0")
        e = ttk.Entry(tr, textvariable=self.trailing_var, width=6)
        e.pack(side="left", padx=6)
        e.bind("<FocusOut>", lambda ev: self._push_settings())
        ttk.Label(tr, text="(0 = off; close if price falls X% from its peak)").pack(side="left")

        ttk.Separator(f, orient="horizontal").grid(row=22, column=0, columnspan=2, sticky="ew", pady=6)
        self.autostart_var = tk.BooleanVar(value=autostart.is_enabled())
        theme.make_check(
            f, text="Run automatically when Windows starts (24/7)",
            variable=self.autostart_var, command=self._on_autostart_toggle,
        ).grid(row=23, column=0, columnspan=2, sticky="w", pady=2)
        ttk.Label(f, text="Launches the app at login. Combine with Safe Mode off + a saved "
                          "licence/webhook so it trades unattended.",
                  style="Dim.TLabel", wraplength=380).grid(
            row=24, column=0, columnspan=2, sticky="w")

        self.tray_var = tk.BooleanVar(value=False)
        tray_ok = tray_helper.available()
        theme.make_check(
            f, text="Minimize to system tray on close (keep trading in background)",
            variable=self.tray_var, command=self._push_settings,
        ).grid(row=25, column=0, columnspan=2, sticky="w", pady=(4, 0))
        if not tray_ok:
            ttk.Label(f, text="(system-tray support not installed — closing the window will exit)",
                      style="Dim.TLabel", wraplength=380).grid(
                row=26, column=0, columnspan=2, sticky="w")

    def _build_webhook_tab(self, f) -> None:
        ttk.Label(f, text=f"Webhook (TradingView) on port {WEBHOOK_PORT}:").grid(
            row=0, column=0, columnspan=2, sticky="w"
        )
        wr = ttk.Frame(f)
        wr.grid(row=1, column=0, columnspan=2, sticky="ew", pady=4)
        ttk.Label(wr, text="Passphrase:").pack(side="left")
        self.webhook_pass_var = tk.StringVar(value=DEFAULT_WEBHOOK_PASSPHRASE)
        ttk.Entry(wr, textvariable=self.webhook_pass_var, width=16).pack(side="left", padx=6)
        self.webhook_btn = tk.Button(wr, text="▶ Start Webhook", command=self._toggle_webhook)
        self.webhook_btn.pack(side="left", padx=6)
        self.webhook_status = tk.Label(wr, text="● off", fg=GREY, bg=PANEL)
        self.webhook_status.pack(side="left")
        self.test_signal_btn = tk.Button(wr, text="🧪 Test Signal", command=self._test_signal)
        theme.style_button(self.test_signal_btn, "accent")
        self.test_signal_btn.pack(side="right")

        # Strategy filter — locked to the Prometheus indicator (read-only) so
        # customers can't break signal matching.
        ttk.Label(f, text="Strategy filter:").grid(row=2, column=0, sticky="w", pady=2)
        self.strategy_filter_var = tk.StringVar(value="Prometheus")
        ttk.Entry(f, textvariable=self.strategy_filter_var,
                  state="readonly").grid(row=2, column=1, sticky="ew", pady=2)
        ttk.Label(f, text="Locked to the Prometheus indicator (matches its Order Comment).",
                  style="Dim.TLabel", wraplength=380).grid(row=3, column=0, columnspan=2, sticky="w")

        ttk.Separator(f, orient="horizontal").grid(row=4, column=0, columnspan=2, sticky="ew", pady=6)
        ttk.Label(f, text="License",
                  font=("Segoe UI", 9, "bold")).grid(row=5, column=0, columnspan=2, sticky="w")
        # Relay URL is fixed to our server — kept in a (hidden) var, not shown/edited.
        self.relay_url_var = tk.StringVar(value=DEFAULT_RELAY_URL)
        ttk.Label(f, text="License token:").grid(row=6, column=0, sticky="w", pady=2)
        tr = ttk.Frame(f)
        tr.grid(row=6, column=1, sticky="ew", pady=2)
        tr.columnconfigure(0, weight=1)
        self.relay_token_var = tk.StringVar()          # empty until trial / purchase
        ttk.Entry(tr, textvariable=self.relay_token_var).grid(row=0, column=0, sticky="ew")
        self.relay_btn = tk.Button(tr, text="🔗 Connect", command=self._toggle_relay)
        self.relay_btn.grid(row=0, column=1, padx=(6, 0))
        self.relay_status = tk.Label(f, text="● off", fg=GREY, bg=PANEL, font=("Segoe UI", 9))
        self.relay_status.grid(row=7, column=0, columnspan=2, sticky="w")

        # --- Free trial / licence (two rows so the buttons never clip) ------
        er = ttk.Frame(f)
        er.grid(row=8, column=0, columnspan=2, sticky="ew", pady=(8, 0))
        er.columnconfigure(1, weight=1)
        ttk.Label(er, text="Free trial — Email (required):").grid(row=0, column=0, sticky="w")
        self.trial_email_var = tk.StringVar()
        ttk.Entry(er, textvariable=self.trial_email_var).grid(row=0, column=1, sticky="ew", padx=(6, 0))

        bf = ttk.Frame(f)
        bf.grid(row=9, column=0, columnspan=2, sticky="ew", pady=(4, 0))
        self.trial_btn = tk.Button(bf, text="🎁 Start Free Trial", width=16,
                                   command=self._start_free_trial)
        theme.style_button(self.trial_btn, "accent")
        self.trial_btn.pack(side="left", padx=(0, 8))
        self.getlic_btn = tk.Button(bf, text="🔑 Get License", width=16,
                                    command=self._open_checkout)
        theme.style_button(self.getlic_btn, "accent")
        self.getlic_btn.pack(side="left")
        self.trial_status = tk.Label(f, text="", fg=GREY, bg=PANEL, font=("Segoe UI", 9))
        self.trial_status.grid(row=10, column=0, columnspan=2, sticky="w", pady=(2, 0))

    def _build_strategy_tab(self, f) -> None:
        """Built-in strategy: trade the bot's own port of the indicator with no
        TradingView account. Same dip→green-sequence logic, run on exchange
        candles. Confirmed BUYs flow through the normal sizing/bracket pipeline."""
        def num_entry(parent, var, width=7):
            e = ttk.Entry(parent, textvariable=var, width=width)
            e.bind("<FocusOut>", lambda ev: self._push_strategy())
            e.bind("<Return>", lambda ev: self._push_strategy())
            return e

        row = ttk.Frame(f)
        row.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 4))
        self.strat_enabled_var = tk.BooleanVar(value=False)
        theme.make_check(row, text="Enable built-in strategy",
                         variable=self.strat_enabled_var,
                         command=self._push_strategy).pack(side="left")
        self.strat_status = tk.Label(row, text="● off", fg=GREY, bg=PANEL,
                                     font=("Segoe UI", 9))
        self.strat_status.pack(side="right")

        ttk.Label(f, text="Symbols (comma-separated):").grid(row=1, column=0, sticky="w", pady=2)
        self.strat_symbols_var = tk.StringVar(value=DEFAULT_STRATEGY_SYMBOLS)
        num_entry(f, self.strat_symbols_var, width=24).grid(row=1, column=1, sticky="ew", pady=2)

        sr = ttk.Frame(f)
        sr.grid(row=2, column=0, columnspan=2, sticky="w", pady=2)
        ttk.Label(sr, text="Timeframe:").pack(side="left")
        self.strat_tf_var = tk.StringVar(value=DEFAULT_STRATEGY_TIMEFRAME)
        tf_box = ttk.Combobox(sr, textvariable=self.strat_tf_var, values=STRATEGY_TIMEFRAMES,
                              state="readonly", width=6)
        tf_box.pack(side="left", padx=6)
        tf_box.bind("<<ComboboxSelected>>", lambda ev: self._push_strategy())
        ttk.Label(sr, text="  EMA20 + RSI-50 crossover (long + short)",
                  style="Dim.TLabel").pack(side="left", padx=(10, 0))

        # EMA / RSI levels / swing-stop / scale-out.
        maf = ttk.Frame(f)
        maf.grid(row=3, column=0, columnspan=2, sticky="w", pady=2)
        ttk.Label(maf, text="EMA:").pack(side="left")
        self.strat_ma_len_var = tk.StringVar(value="20")
        num_entry(maf, self.strat_ma_len_var, 4).pack(side="left", padx=(2, 8))
        ttk.Label(maf, text="RSI OB:").pack(side="left")
        self.strat_ma_ob_var = tk.StringVar(value="70")
        num_entry(maf, self.strat_ma_ob_var, 4).pack(side="left", padx=(2, 8))
        ttk.Label(maf, text="OS:").pack(side="left")
        self.strat_ma_os_var = tk.StringVar(value="30")
        num_entry(maf, self.strat_ma_os_var, 4).pack(side="left", padx=(2, 8))
        ttk.Label(maf, text="Swing:").pack(side="left")
        self.strat_ma_swing_var = tk.StringVar(value="10")
        num_entry(maf, self.strat_ma_swing_var, 4).pack(side="left", padx=(2, 8))
        ttk.Label(maf, text="SL buf%:").pack(side="left")
        self.strat_ma_slbuf_var = tk.StringVar(value="0.10")
        num_entry(maf, self.strat_ma_slbuf_var, 5).pack(side="left", padx=(2, 8))
        ttk.Label(maf, text="Scale%:").pack(side="left")
        self.strat_ma_scale_var = tk.StringVar(value="50")
        num_entry(maf, self.strat_ma_scale_var, 4).pack(side="left", padx=2)

        # Entry confirmation: N strong green candles for a BUY / red for a SELL.
        maf2 = ttk.Frame(f)
        maf2.grid(row=4, column=0, columnspan=2, sticky="w", pady=2)
        ttk.Label(maf2, text="Confirm candles — Buy:green / Sell:red:").pack(side="left")
        self.strat_ma_confirm_var = tk.StringVar(value="2")
        num_entry(maf2, self.strat_ma_confirm_var, 4).pack(side="left", padx=(2, 8))
        ttk.Label(maf2, text="Body ≥ (of range):").pack(side="left")
        self.strat_ma_body_var = tk.StringVar(value="0.20")
        num_entry(maf2, self.strat_ma_body_var, 5).pack(side="left", padx=2)

        # Trend filter (HTF-style) + optional ATR stop.
        maf3 = ttk.Frame(f)
        maf3.grid(row=5, column=0, columnspan=2, sticky="w", pady=2)
        ttk.Label(maf3, text="Trend EMA (0=off):").pack(side="left")
        self.strat_ma_trend_var = tk.StringVar(value="100")
        num_entry(maf3, self.strat_ma_trend_var, 5).pack(side="left", padx=(2, 8))
        ttk.Label(maf3, text="ATR stop × (0=swing):").pack(side="left")
        self.strat_ma_atrmult_var = tk.StringVar(value="2.5")
        num_entry(maf3, self.strat_ma_atrmult_var, 4).pack(side="left", padx=(2, 8))
        ttk.Label(maf3, text="ATR len:").pack(side="left")
        self.strat_ma_atrlen_var = tk.StringVar(value="14")
        num_entry(maf3, self.strat_ma_atrlen_var, 4).pack(side="left", padx=2)

        # Analysis tools: backtest + trade analytics, side by side.
        bt = ttk.Frame(f)
        bt.grid(row=6, column=0, columnspan=2, sticky="w", pady=(8, 0))
        self.backtest_btn = tk.Button(bt, text="📊 Backtest", command=self._open_backtest)
        theme.style_button(self.backtest_btn, "accent")
        self.backtest_btn.pack(side="left")
        self.analytics_btn = tk.Button(bt, text="📈 Analytics", command=self._open_analytics)
        self.analytics_btn.pack(side="left", padx=(6, 0))

    def _strategy_params(self) -> StrategyParams:
        return StrategyParams(
            ma_len=max(2, int(self._float(self.strat_ma_len_var.get(), 20))),
            ma_ob=self._float(self.strat_ma_ob_var.get(), 70.0),
            ma_os=self._float(self.strat_ma_os_var.get(), 30.0),
            ma_swing=max(2, int(self._float(self.strat_ma_swing_var.get(), 10))),
            ma_sl_buf=self._float(self.strat_ma_slbuf_var.get(), 0.10),
            ma_scale=max(0.0, min(1.0, self._float(self.strat_ma_scale_var.get(), 50) / 100.0)),
            ma_confirm=max(0, int(self._float(self.strat_ma_confirm_var.get(), 2))),
            ma_min_body=max(0.0, self._float(self.strat_ma_body_var.get(), 0.20)),
            ma_trend_len=max(0, int(self._float(self.strat_ma_trend_var.get(), 100))),
            ma_atr_len=max(2, int(self._float(self.strat_ma_atrlen_var.get(), 14))),
            ma_atr_mult=max(0.0, self._float(self.strat_ma_atrmult_var.get(), 2.5)),
        )

    def _push_strategy(self) -> None:
        """Apply the built-in strategy config to the runner and start/stop it.

        The runner only trades when enabled *and* connected (so signals don't
        fire while the bot couldn't place an order anyway)."""
        if not hasattr(self, "strategy_runner"):
            return
        enabled = self.strat_enabled_var.get()
        symbols = [s.strip() for s in self.strat_symbols_var.get().split(",") if s.strip()]
        active = enabled and self.connected
        self.strategy_runner.configure(
            enabled=active,
            symbols=symbols,
            timeframe=self.strat_tf_var.get(),
            params=self._strategy_params(),
            exchange_id=exchange_id(self.exchange_var.get()),
            market_type="futures" if self.market_var.get() == "Futures" else "spot",
        )
        if active and not self.strategy_runner.running:
            self.strategy_runner.start()
        elif not active and self.strategy_runner.running:
            self.strategy_runner.stop()
        self._update_strategy_status()
        self._sync_chart()   # keep an open chart mirroring the live settings

    def _update_strategy_status(self) -> None:
        if not hasattr(self, "strat_status"):
            return
        if not self.strat_enabled_var.get():
            self.strat_status.config(text="● off", fg=GREY)
            return
        if not self.connected:
            self.strat_status.config(text="● waiting for connection", fg="#e67e22")
            return
        # Reflect the licence gate (verified asynchronously by the runner).
        licensed = self.strategy_runner.licensed() if hasattr(self, "strategy_runner") else None
        if licensed is False:
            self.strat_status.config(text="● blocked — licence invalid/expired", fg=RED)
            return
        if licensed is None:
            self.strat_status.config(text="● verifying licence…", fg="#e67e22")
            return
        n = len([s for s in self.strat_symbols_var.get().split(",") if s.strip()])
        self.strat_status.config(
            text=f"● running · {n} symbol{'s' if n != 1 else ''} · {self.strat_tf_var.get()}",
            fg=GREEN)

    def _open_strategy_chart(self) -> None:
        """Open (or focus) the candlestick chart with the strategy's overlays."""
        if getattr(self, "_chart_win", None) and self._chart_win.alive():
            self._chart_win.focus()
            return
        self._chart_win = ChartWindow(
            self.root,
            get_symbols=lambda: [s.strip() for s in self.strat_symbols_var.get().split(",")
                                 if s.strip()],
            get_timeframe=self.strat_tf_var.get,
            get_params=self._strategy_params,
            get_exchange=lambda: exchange_id(self.exchange_var.get()),
            get_market=lambda: "futures" if self.market_var.get() == "Futures" else "spot",
        )

    def _sync_chart(self) -> None:
        """Push live settings to an open chart so it keeps mirroring the bot."""
        if getattr(self, "_chart_win", None) and self._chart_win.alive():
            self._chart_win.sync()

    def _open_backtest(self) -> None:
        """Open (or focus) the historical backtester for the built-in strategy."""
        from backtest_window import BacktestWindow
        if getattr(self, "_bt_win", None) and self._bt_win.alive():
            self._bt_win.focus()
            return
        self._bt_win = BacktestWindow(
            self.root,
            get_symbols=lambda: [s.strip() for s in self.strat_symbols_var.get().split(",")
                                 if s.strip()],
            get_timeframe=self.strat_tf_var.get,
            get_params=self._strategy_params,
            get_exchange=lambda: exchange_id(self.exchange_var.get()),
            get_market=lambda: "futures" if self.market_var.get() == "Futures" else "spot",
        )

    def _build_alerts_tab(self, f) -> None:
        self.sound_var = tk.BooleanVar(value=True)
        theme.make_check(
            f, text="Sound on fills/signals", variable=self.sound_var,
            command=self._push_settings,
        ).grid(row=0, column=0, sticky="w", pady=2)
        self.desktop_var = tk.BooleanVar(value=True)
        dr = ttk.Frame(f)
        dr.grid(row=0, column=1, sticky="w", pady=2)
        theme.make_check(
            dr, text="Desktop notifications", variable=self.desktop_var,
            command=self._push_settings,
        ).pack(side="left")
        self.desk_test_btn = tk.Button(dr, text="🔔 Test", command=self._test_desktop)
        self.desk_test_btn.pack(side="left", padx=6)
        ttk.Label(f, text="Telegram bot token:").grid(row=1, column=0, sticky="w", pady=2)
        self.tg_token_var = tk.StringVar()
        ttk.Entry(f, textvariable=self.tg_token_var, show="•").grid(row=1, column=1, sticky="ew", pady=2)
        ttk.Label(f, text="Telegram chat id:").grid(row=2, column=0, sticky="w", pady=2)
        self.tg_chat_var = tk.StringVar()
        cr = ttk.Frame(f)
        cr.grid(row=2, column=1, sticky="ew", pady=2)
        cr.columnconfigure(0, weight=1)
        ttk.Entry(cr, textvariable=self.tg_chat_var).grid(row=0, column=0, sticky="ew")
        self.tg_test_btn = tk.Button(cr, text="✈ Test", command=self._test_telegram)
        theme.style_button(self.tg_test_btn, "accent")
        self.tg_test_btn.grid(row=0, column=1, padx=(6, 0))

        # Telegram noise filter — important events only (entries, closes, halts).
        self.tg_important_var = tk.BooleanVar(value=True)
        theme.make_check(
            f, text="Telegram: important events only (entries, closes & PnL, halts, security)",
            variable=self.tg_important_var, command=self._push_settings,
        ).grid(row=3, column=0, columnspan=2, sticky="w", pady=(6, 0))
        ttk.Label(f, text="Off = every event (trailing, blocked signals, indicator events…) "
                          "also goes to Telegram. Desktop + sound always show everything.",
                  style="Dim.TLabel", wraplength=380).grid(
            row=4, column=0, columnspan=2, sticky="w")

        # Daily Telegram P&L recap (end-of-day, pushed via the Telegram bot).
        sr = ttk.Frame(f)
        sr.grid(row=5, column=0, columnspan=2, sticky="w", pady=2)
        self.daily_summary_var = tk.BooleanVar(value=False)
        theme.make_check(sr, text="Daily Telegram P&L summary at", variable=self.daily_summary_var,
                        command=self._push_settings).pack(side="left")
        self.summary_hour_var = tk.StringVar(value="23")
        he = ttk.Entry(sr, textvariable=self.summary_hour_var, width=4)
        he.pack(side="left", padx=4)
        he.bind("<FocusOut>", lambda ev: self._push_settings())
        ttk.Label(sr, text=":00 (local hour, 0–23)").pack(side="left")

    def _toggle_relay(self) -> None:
        if self.relay.running:
            self.relay.stop()
            self.relay_btn.config(text="🔗 Connect")
            self.relay_status.config(text="● off", fg=GREY)
        else:
            self.relay.start()
            if self.relay.running:
                self.relay_btn.config(text="🔌 Disconnect")
                self.relay_status.config(text="● License connected", fg=GREEN)

    def _autostart_relay(self) -> None:
        """Auto-connect cloud signals if a licence token was saved."""
        if self.relay_token_var.get().strip():
            self._toggle_relay()

    # --- Free trial / licence --------------------------------------------
    def _open_checkout(self) -> None:
        """Open the checkout page so the customer can buy a full licence."""
        try:
            webbrowser.open(CHECKOUT_URL)
        except Exception:  # noqa: BLE001 - opening a browser must never crash the app
            messagebox.showinfo("Get License", CHECKOUT_URL)

    def _start_free_trial(self) -> None:
        """Request a self-service 10-day trial and, on success, license the app.

        Email is required: the relay validates it and ties the trial to it
        (anti-abuse = machine + email)."""
        email = self.trial_email_var.get().strip()
        if "@" not in email or "." not in email.split("@")[-1]:
            messagebox.showwarning("Free trial", "Enter a valid email to start your free trial.")
            return
        self.trial_btn.config(state="disabled", text="Starting…")
        trial_url = licence.trial_url_from_relay(self.relay_url_var.get().strip())
        machine = licence.machine_fingerprint()

        def work():
            result = licence.start_trial(trial_url, email, machine)
            self.root.after(0, lambda: self._trial_done(*result))

        threading.Thread(target=work, daemon=True).start()

    def _trial_done(self, status: str, message: str, token: str, expires_at: int) -> None:
        self.trial_btn.config(state="normal", text="🎁 Start Free Trial")
        if status == "ok" and token:
            self.relay_token_var.set(token)
            try:  # persist the new token silently
                security.save_credentials(self.pin, self._collect_settings())
            except Exception:  # noqa: BLE001
                pass
            if not self.relay.running:   # connect signals + unlock strategy
                self._toggle_relay()
            days = max(0, (expires_at - int(time.time())) // 86400) if expires_at else 0
            self.trial_status.config(text=f"✓ License active — {days} days left", fg=GREEN)
            messagebox.showinfo(
                "Free trial started",
                f"Your {days}-day free trial is active — full access unlocked.\n\n"
                "When it ends, click “Get License” to continue.",
            )
        elif status == "used":
            self.trial_status.config(text="Trial already used — click Get License", fg=RED)
            if messagebox.askyesno("Free trial", message + "\n\nOpen the checkout page now?"):
                self._open_checkout()
        else:
            self.trial_status.config(text="Couldn't start trial — try again", fg=RED)
            messagebox.showerror("Free trial", message)

    def _refresh_trial_status(self) -> None:
        """Update the licence/trial countdown strip, then reschedule (5 min)."""
        token = self.relay_token_var.get().strip()
        if not token:
            self.trial_status.config(text="No license — start a free trial or Get License", fg=GREY)
        else:
            vurl = licence.verify_url_from_relay(self.relay_url_var.get().strip())

            def work():
                st = licence.licence_status(vurl, token)
                self.root.after(0, lambda: self._apply_trial_status(st))

            threading.Thread(target=work, daemon=True).start()
        self.root.after(300_000, self._refresh_trial_status)

    def _apply_trial_status(self, st: dict) -> None:
        if not getattr(self, "trial_status", None):
            return
        if st["status"] == "ok":
            exp = st["expires_at"]
            if not exp:
                self.trial_status.config(text="✓ License active — no expiry", fg=GREEN)
            else:
                days = max(0, (exp - int(time.time())) // 86400)
                colour = "#e67e22" if days <= 3 else GREEN
                self.trial_status.config(text=f"✓ License active — {days} days left", fg=colour)
        elif st["status"] == "rejected":
            self.trial_status.config(text="License expired/invalid — click Get License", fg=RED)
        else:
            self.trial_status.config(text="License: server unreachable (will retry)", fg=GREY)

    def _test_telegram(self) -> None:
        """Send a test Telegram message so the user can confirm it works."""
        token = self.tg_token_var.get().strip()
        chat = self.tg_chat_var.get().strip()
        if not token or not chat:
            messagebox.showwarning("Telegram test",
                                   "Enter both the bot token and chat id first.")
            return
        self.tg_test_btn.config(text="Sending…", state="disabled")

        def worker():
            ok, msg = self.backend.notifier.test_telegram(token, chat)
            self.root.after(0, lambda: self._telegram_test_done(ok, msg))

        threading.Thread(target=worker, daemon=True).start()

    def _telegram_test_done(self, ok: bool, msg: str) -> None:
        self.tg_test_btn.config(text="✈ Test", state="normal")
        if ok:
            messagebox.showinfo("Telegram test", msg)
        else:
            messagebox.showerror("Telegram test failed", msg)

    def _test_desktop(self) -> None:
        """Fire a test desktop toast so the user can confirm it shows."""
        self.desk_test_btn.config(text="…", state="disabled")

        def worker():
            ok, msg = self.backend.notifier.test_desktop()
            self.root.after(0, lambda: self._desktop_test_done(ok, msg))

        threading.Thread(target=worker, daemon=True).start()

    def _desktop_test_done(self, ok: bool, msg: str) -> None:
        self.desk_test_btn.config(text="🔔 Test", state="normal")
        if ok:
            messagebox.showinfo("Desktop notification test", msg)
        else:
            messagebox.showerror("Desktop notification test", msg)

    def _build_trade_log(self, parent) -> None:
        f = ttk.LabelFrame(parent, text="Trade Log", padding=8)
        f.grid(row=1, column=0, sticky="nsew", padx=5, pady=5)
        f.rowconfigure(0, weight=1)
        f.columnconfigure(0, weight=1)

        cols = ("time", "signal", "pair", "status", "message")
        # height=14 asks for a tall log by default; it still expands with the
        # window since this row is weighted in the right column.
        self.log_tree = ttk.Treeview(f, columns=cols, show="headings", height=14)
        # stretch=False keeps the widths so long messages overflow and the
        # horizontal scrollbar can reach them; double-click shows the full text.
        for c, w in zip(cols, (70, 60, 90, 90, 460)):
            self.log_tree.heading(c, text=c.capitalize())
            self.log_tree.column(c, width=w, anchor="w", stretch=False)
        # Status-based row colours so failures stand out at a glance.
        self.log_tree.tag_configure("error", foreground=RED)
        self.log_tree.tag_configure("ok", foreground=GREEN)
        self.log_tree.tag_configure("warn", foreground="#e3a008")
        self.log_tree.tag_configure("event", foreground=ACCENT)
        self.log_tree.grid(row=0, column=0, sticky="nsew")
        self.log_tree.bind("<Double-1>", self._show_log_detail)

        vsb = ttk.Scrollbar(f, orient="vertical", command=self.log_tree.yview)
        hsb = ttk.Scrollbar(f, orient="horizontal", command=self.log_tree.xview)
        self.log_tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)
        vsb.grid(row=0, column=1, sticky="ns")
        hsb.grid(row=1, column=0, sticky="ew")

        bar = ttk.Frame(f)
        bar.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(6, 0))
        tk.Button(bar, text="📤 Export Log", command=self._export_log).pack(side="left", padx=4)
        tk.Button(bar, text="🗑 Clear Log", command=self._clear_log).pack(side="left", padx=4)
        tk.Button(bar, text="📁 Support Log", command=self._open_log_folder).pack(side="left", padx=4)

    # Map a log status to a colour tag (default = no tag / normal text).
    _LOG_TAGS = {
        "Rejected": "error", "Error": "error", "Blocked": "error",
        "Halted": "error", "Offline": "error",
        "Filled": "ok", "Simulated": "ok", "OK": "ok",
        "Connected": "ok", "Online": "ok",
        "WARNING": "warn", "IP changed": "warn", "Reconnect": "warn", "Trailing": "warn",
        "Event": "event", "Summary": "event",
    }

    def _show_log_detail(self, _event=None) -> None:
        """Double-click a row → show its full, copyable message in a popup."""
        sel = self.log_tree.selection()
        if not sel:
            return
        vals = self.log_tree.item(sel[0]).get("values", [])
        if not vals:
            return
        time_, signal, pair, status, message = (list(vals) + [""] * 5)[:5]
        win = tk.Toplevel(self.root)
        win.title("Log entry")
        win.configure(bg=PANEL)
        win.geometry("520x240")
        header = f"{time_}   {signal}   {pair}   [{status}]".strip()
        tk.Label(win, text=header, bg=PANEL, fg=ACCENT, anchor="w",
                 font=("Segoe UI Semibold", 10)).pack(fill="x", padx=12, pady=(12, 6))
        txt = tk.Text(win, wrap="word", bg=ELEV, fg=TXT, relief="flat", bd=0,
                      height=6, font=("Segoe UI", 10), padx=8, pady=8)
        txt.insert("1.0", str(message))
        txt.configure(state="disabled")
        txt.pack(fill="both", expand=True, padx=12, pady=(0, 8))

        def copy_msg():
            self.root.clipboard_clear()
            self.root.clipboard_append(str(message))
        br = tk.Frame(win, bg=PANEL)
        br.pack(fill="x", padx=12, pady=(0, 12))
        cb = tk.Button(br, text="📋 Copy message", command=copy_msg)
        theme.style_button(cb, "accent")
        cb.pack(side="right")
        cl = tk.Button(br, text="✖ Close", command=win.destroy)
        theme.style_button(cl, "ghost")
        cl.pack(side="right", padx=8)

    # ====================================================================
    # Settings persistence / prefill
    # ====================================================================
    def _load_saved_into_ui(self) -> None:
        # An empty dict is valid: every field falls back to its default — this is
        # exactly what the Reset button relies on to restore factory defaults.
        s = self.saved or {}
        saved_ex = s.get("exchange", SUPPORTED_EXCHANGES[0])
        self.exchange_var.set(EXCHANGE_LABELS.get(saved_ex, saved_ex))
        self.market_var.set("Futures" if s.get("market_type") == "futures" else "Spot")
        self.api_key_var.set(s.get("api_key", ""))
        self.api_secret_var.set(s.get("api_secret", ""))
        self.passphrase_var.set(s.get("passphrase", ""))
        self.manual_var.set(bool(s.get("manual_trading", True)))
        self.size_var.set(str(s.get("size", DEFAULT_TRADE_SIZE)))
        self.sizing_mode_var.set(SIZING_MODE_LABELS.get(s.get("sizing_mode", DEFAULT_SIZING_MODE), SIZING_MODE_LABELS[DEFAULT_SIZING_MODE]))
        self.risk_pct_var.set(str(s.get("risk_percent", DEFAULT_RISK_PERCENT)))
        self.auto_bracket_var.set(s.get("auto_bracket", DEFAULT_AUTO_BRACKET))
        self.tp1_scale_var.set(str(s.get("tp1_scale_pct", 50)))
        self.auto_reconnect_var.set(s.get("auto_reconnect", True))
        self.trailing_var.set(str(s.get("trailing_pct", 0)))
        self.desktop_var.set(s.get("desktop", True))
        self.order_type_var.set(s.get("order_type", DEFAULT_ORDER_TYPE))
        self.limit_price_var.set(str(s.get("limit_price", "")))
        self.leverage_var.set(str(s.get("leverage", DEFAULT_LEVERAGE)))
        mm = s.get("margin_mode", DEFAULT_MARGIN_MODE)
        self.margin_mode_var.set(mm if mm in ("cross", "isolated") else "(default)")
        self.move_be_var.set(s.get("move_be", False))
        self.max_open_var.set(str(s.get("max_open", DEFAULT_MAX_OPEN_POSITIONS)))
        self.daily_loss_var.set(str(s.get("daily_loss", 0)))
        self.daily_loss_pct_var.set(str(s.get("daily_loss_pct", 5)))
        self.daily_profit_var.set(str(s.get("daily_profit", 0)))
        self.daily_profit_pct_var.set(str(s.get("daily_profit_pct", 0)))
        self.cooldown_var.set(str(s.get("cooldown", 0)))
        self.dedupe_var.set(str(s.get("dedupe", 0)))
        self.loss_streak_var.set(str(s.get("loss_streak", 0)))
        self.streak_cd_var.set(str(s.get("streak_cooldown", 0)))
        self.max_dd_var.set(str(s.get("max_drawdown", 0)))
        self.start_hour_var.set(str(s.get("start_hour", 0)))
        self.end_hour_var.set(str(s.get("end_hour", 0)))
        self.slippage_var.set(str(s.get("slippage_pct", DEFAULT_SLIPPAGE_PCT)))
        self.round_min_var.set(s.get("round_to_min", False))
        self.safe_var.set(s.get("safe_mode", DEFAULT_SAFE_MODE))
        self.readonly_var.set(s.get("read_only", False))
        self.webhook_pass_var.set(s.get("webhook_passphrase", DEFAULT_WEBHOOK_PASSPHRASE))
        self.strategy_filter_var.set(s.get("strategy_filter", "Prometheus"))
        # Relay URL is fixed (hidden field) — always pin to our server so a stale
        # saved value can't strand a customer.
        self.relay_url_var.set(DEFAULT_RELAY_URL)
        self.relay_token_var.set(s.get("relay_token", ""))
        self.sound_var.set(s.get("sound", True))
        self.tg_token_var.set(s.get("telegram_token", ""))
        self.tg_chat_var.set(s.get("telegram_chat_id", ""))
        self.daily_summary_var.set(s.get("daily_summary", False))
        self.summary_hour_var.set(str(s.get("summary_hour", 23)))
        self.tg_important_var.set(s.get("telegram_important_only", True))
        self.tray_var.set(s.get("minimize_to_tray", False))
        # Built-in strategy settings.
        self.strat_enabled_var.set(s.get("strat_enabled", False))
        self.strat_symbols_var.set(s.get("strat_symbols", DEFAULT_STRATEGY_SYMBOLS))
        self.strat_tf_var.set(s.get("strat_timeframe", DEFAULT_STRATEGY_TIMEFRAME))
        self.strat_ma_len_var.set(str(s.get("strat_ma_len", 20)))
        self.strat_ma_ob_var.set(str(s.get("strat_ma_ob", 70)))
        self.strat_ma_os_var.set(str(s.get("strat_ma_os", 30)))
        self.strat_ma_swing_var.set(str(s.get("strat_ma_swing", 10)))
        self.strat_ma_slbuf_var.set(str(s.get("strat_ma_slbuf", 0.10)))
        self.strat_ma_scale_var.set(str(s.get("strat_ma_scale", 50)))
        self.strat_ma_confirm_var.set(str(s.get("strat_ma_confirm", 2)))
        self.strat_ma_body_var.set(str(s.get("strat_ma_body", 0.20)))
        self.strat_ma_trend_var.set(str(s.get("strat_ma_trend", 100)))
        self.strat_ma_atrmult_var.set(str(s.get("strat_ma_atrmult", 2.5)))
        self.strat_ma_atrlen_var.set(str(s.get("strat_ma_atrlen", 14)))
        self._toggle_passphrase()

    def _collect_settings(self) -> dict:
        return {
            "exchange": self.exchange_var.get(),
            "market_type": "futures" if self.market_var.get() == "Futures" else "spot",
            "api_key": self.api_key_var.get(),
            "api_secret": self.api_secret_var.get(),
            "passphrase": self.passphrase_var.get(),
            "manual_trading": self.manual_var.get(),
            "size": self._float(self.size_var.get(), DEFAULT_TRADE_SIZE),
            "sizing_mode": self._sizing_mode_value(),
            "risk_percent": self._float(self.risk_pct_var.get(), DEFAULT_RISK_PERCENT),
            "auto_bracket": self.auto_bracket_var.get(),
            "tp1_scale_pct": self._float(self.tp1_scale_var.get(), 50),
            "auto_reconnect": self.auto_reconnect_var.get(),
            "order_type": self.order_type_var.get(),
            "limit_price": self.limit_price_var.get(),
            "leverage": int(self._float(self.leverage_var.get(), 0)),
            "margin_mode": self._margin_mode_value(),
            "move_be": self.move_be_var.get(),
            "trailing_pct": self._float(self.trailing_var.get(), 0),
            "desktop": self.desktop_var.get(),
            "max_open": int(self._float(self.max_open_var.get(), 0)),
            "daily_loss": self._float(self.daily_loss_var.get(), 0),
            "daily_loss_pct": self._float(self.daily_loss_pct_var.get(), 0),
            "daily_profit": self._float(self.daily_profit_var.get(), 0),
            "daily_profit_pct": self._float(self.daily_profit_pct_var.get(), 0),
            "cooldown": int(self._float(self.cooldown_var.get(), 0)),
            "dedupe": int(self._float(self.dedupe_var.get(), 0)),
            "loss_streak": int(self._float(self.loss_streak_var.get(), 0)),
            "streak_cooldown": int(self._float(self.streak_cd_var.get(), 0)),
            "max_drawdown": self._float(self.max_dd_var.get(), 0),
            "start_hour": int(self._float(self.start_hour_var.get(), 0)),
            "end_hour": int(self._float(self.end_hour_var.get(), 0)),
            "slippage_pct": self._float(self.slippage_var.get(), 0),
            "round_to_min": self.round_min_var.get(),
            "safe_mode": self.safe_var.get(),
            "read_only": self.readonly_var.get(),
            "webhook_passphrase": self.webhook_pass_var.get(),
            "strategy_filter": self.strategy_filter_var.get(),
            "relay_url": self.relay_url_var.get(),
            "relay_token": self.relay_token_var.get(),
            "sound": self.sound_var.get(),
            "telegram_token": self.tg_token_var.get(),
            "telegram_chat_id": self.tg_chat_var.get(),
            "daily_summary": self.daily_summary_var.get(),
            "summary_hour": int(self._float(self.summary_hour_var.get(), 23)),
            "telegram_important_only": self.tg_important_var.get(),
            "minimize_to_tray": self.tray_var.get(),
            "live_ack": self._live_ack,
            "last_ip": self.saved.get("last_ip", ""),
            "skipped_version": self._skipped_version,
            # Built-in strategy.
            "strat_enabled": self.strat_enabled_var.get(),
            "strat_symbols": self.strat_symbols_var.get(),
            "strat_timeframe": self.strat_tf_var.get(),
            "strat_ma_len": self.strat_ma_len_var.get(),
            "strat_ma_ob": self.strat_ma_ob_var.get(),
            "strat_ma_os": self.strat_ma_os_var.get(),
            "strat_ma_swing": self.strat_ma_swing_var.get(),
            "strat_ma_slbuf": self.strat_ma_slbuf_var.get(),
            "strat_ma_scale": self.strat_ma_scale_var.get(),
            "strat_ma_confirm": self.strat_ma_confirm_var.get(),
            "strat_ma_body": self.strat_ma_body_var.get(),
            "strat_ma_trend": self.strat_ma_trend_var.get(),
            "strat_ma_atrmult": self.strat_ma_atrmult_var.get(),
            "strat_ma_atrlen": self.strat_ma_atrlen_var.get(),
        }

    def _save_all(self) -> None:
        security.save_credentials(self.pin, self._collect_settings())
        self._push_settings()
        messagebox.showinfo("Saved", "Settings encrypted and saved.")

    def _reset_all_defaults(self) -> None:
        """Factory-reset every field to its default — including API credentials and
        the licence token (per the user's choice). Guarded by a confirmation; the
        change is applied live but only persisted when the user clicks Save."""
        if self.connected:
            messagebox.showwarning(
                "Disconnect first", "Disconnect before resetting all settings.",
                parent=self.root)
            return
        if not messagebox.askyesno(
                "Reset everything to defaults",
                "This restores ALL settings to their defaults — including your API "
                "key, secret, passphrase and licence token.\n\nThey'll be cleared "
                "from the form (and saved only if you then click Save).\n\nContinue?",
                icon="warning", parent=self.root):
            return
        # An empty vault means every field in _load_saved_into_ui takes its default.
        self.saved = {}
        self._live_ack = False
        self._skipped_version = ""
        self._load_saved_into_ui()
        self._update_manual_state()
        self._push_settings()
        self._push_strategy()
        messagebox.showinfo(
            "Reset", "All settings reset to defaults. Click 💾 Save to keep them.",
            parent=self.root)

    def _sizing_mode_value(self) -> str:
        label = self.sizing_mode_var.get()
        for value, lab in SIZING_MODE_LABELS.items():
            if lab == label:
                return value
        return "fixed"

    def _update_size_hint(self) -> None:
        """Show the unit of the Trade Size field for the selected sizing mode."""
        hints = {
            "fixed": "base coin (e.g. 0.006 BTC)",
            "fixed_quote": "USDT $ (e.g. 100)",
            "risk_balance": "(unused — risk % below)",
            "risk_stop": "(unused — risk % below)",
        }
        self.size_hint.config(text=hints.get(self._sizing_mode_value(), "base coin"))

    def _on_sizing_mode_change(self) -> None:
        self._update_size_hint()
        self._push_settings()

    def _on_leverage_change(self) -> None:
        self._push_settings()
        lev = int(self._float(self.leverage_var.get(), 0))
        if lev > 10 and self.market_var.get() == "Futures" and not getattr(self, "_lev_warned", False):
            self._lev_warned = True
            messagebox.showwarning(
                "High leverage",
                f"{lev}x is high — on crypto futures that means liquidation at "
                f"roughly −{100.0/lev:.1f}% from entry, and crypto moves fast.\n\n"
                "Consider 3–5x with Isolated margin and a stop-loss.")

    def _on_autostart_toggle(self) -> None:
        want = self.autostart_var.get()
        result = autostart.set_enabled(want)
        if result != want:
            # Off Windows (or registry refused) — revert the checkbox and explain.
            self.autostart_var.set(result)
            if want:
                messagebox.showinfo(
                    "Run on startup",
                    "Run-on-startup is only available on Windows.")

    def _margin_mode_value(self) -> str:
        m = self.margin_mode_var.get()
        return m if m in ("cross", "isolated") else ""

    def _reset_daily(self) -> None:
        self.backend.submit({"cmd": "reset_daily"})
        self.guardrail_status.config(text="")

    def _push_settings(self) -> None:
        """Send current sizing/execution/guardrail/notification settings."""
        self.backend.submit({
            "cmd": "settings",
            "fixed_size": self._float(self.size_var.get(), DEFAULT_TRADE_SIZE),
            "sizing_mode": self._sizing_mode_value(),
            "risk_percent": self._float(self.risk_pct_var.get(), DEFAULT_RISK_PERCENT),
            "auto_bracket": self.auto_bracket_var.get(),
            "tp1_scale": self._float(self.tp1_scale_var.get(), 50) / 100.0,
            "auto_reconnect": self.auto_reconnect_var.get(),
            "order_type": self.order_type_var.get(),
            "leverage": int(self._float(self.leverage_var.get(), 0)),
            "margin_mode": self._margin_mode_value(),
            "move_be": self.move_be_var.get(),
            "trailing_pct": self._float(self.trailing_var.get(), 0),
            "desktop": self.desktop_var.get(),
            "max_open": int(self._float(self.max_open_var.get(), 0)),
            "daily_loss": self._float(self.daily_loss_var.get(), 0),
            "daily_loss_pct": self._float(self.daily_loss_pct_var.get(), 0),
            "daily_profit": self._float(self.daily_profit_var.get(), 0),
            "daily_profit_pct": self._float(self.daily_profit_pct_var.get(), 0),
            "cooldown": int(self._float(self.cooldown_var.get(), 0)),
            "dedupe": int(self._float(self.dedupe_var.get(), 0)),
            "loss_streak": int(self._float(self.loss_streak_var.get(), 0)),
            "streak_cooldown": int(self._float(self.streak_cd_var.get(), 0)),
            "max_drawdown": self._float(self.max_dd_var.get(), 0),
            "start_hour": int(self._float(self.start_hour_var.get(), 0)),
            "end_hour": int(self._float(self.end_hour_var.get(), 0)),
            "slippage_pct": self._float(self.slippage_var.get(), 0),
            "round_to_min": self.round_min_var.get(),
            "safe_mode": self.safe_var.get(),
            "read_only": self.readonly_var.get(),
            "sound": self.sound_var.get(),
            "telegram_token": self.tg_token_var.get(),
            "telegram_chat_id": self.tg_chat_var.get(),
            "daily_summary": self.daily_summary_var.get(),
            "summary_hour": int(self._float(self.summary_hour_var.get(), 23)),
            "telegram_important_only": self.tg_important_var.get(),
        })

    # ====================================================================
    # Actions
    # ====================================================================
    def _on_connect(self) -> None:
        # A [required] update blocks connecting (trading) until the user updates —
        # used for breaking server/strategy changes. Disconnecting is unaffected.
        if self._required_update and not self.connected:
            info = self._latest_update or {}
            messagebox.showwarning(
                "Update required",
                f"A required update (v{info.get('version', '')}) must be installed "
                "before you can connect and trade.\n\nInstall it now from the "
                "update prompt, or use 'Check for updates' at the bottom.")
            self._notify_update(info, manual=True)
            return
        ex = exchange_id(self.exchange_var.get())
        if not self.api_key_var.get() or not self.api_secret_var.get():
            messagebox.showwarning("Missing keys", "Enter API key and secret first.")
            return
        if ex in PASSPHRASE_EXCHANGES and not self.passphrase_var.get():
            messagebox.showwarning("Missing passphrase",
                                   f"{exchange_label(ex)} requires an API passphrase.")
            return

        # Spot-only platforms (e.g. Binance.US) have no futures product line.
        if ex in SPOT_ONLY_EXCHANGES and self.market_var.get() == "Futures":
            messagebox.showinfo(
                "Spot only",
                f"{exchange_label(ex)} offers Spot trading only (no futures). "
                "Switching to Spot.")
            self.market_var.set("Spot")

        # Coinbase futures only exist on Coinbase International (institutional /
        # non-US). Warn upfront so users aren't surprised; Spot works for everyone.
        if ex == "coinbase" and self.market_var.get() == "Futures":
            go = messagebox.askyesno(
                "Coinbase futures",
                "Coinbase futures run on Coinbase International Exchange — available "
                "to eligible (non-US / institutional) accounts only, using a Coinbase "
                "International API key.\n\nMost users should use Coinbase Spot, or "
                "Kraken for futures.\n\nTry connecting Coinbase futures anyway?",
                icon="warning",
            )
            if not go:
                return

        # One-time LIVE confirmation: Safe Mode is off by default, so warn once
        # that real orders will be placed automatically on signals.
        if not self.safe_var.get() and not self._live_ack:
            go = messagebox.askyesno(
                "Going LIVE",
                "⚠  You are connecting in LIVE mode.\n\n"
                "Real orders will be placed automatically on indicator signals "
                "and on manual BUY / SELL — using real funds.\n\n"
                "Tip: turn on Safe Mode (Modes & Risk) to simulate first.\n\n"
                "Continue in LIVE mode?",
                icon="warning",
            )
            if not go:
                return
            self._live_ack = True
            try:
                security.save_credentials(self.pin, self._collect_settings())
            except Exception:  # noqa: BLE001 - persistence is best-effort
                pass

        self._push_settings()
        self._notify_ip_next = True   # send an IP alert once this connect lands
        self.backend.submit({
            "cmd": "connect",
            "exchange_id": ex,
            "api_key": self.api_key_var.get(),
            "secret": self.api_secret_var.get(),
            "password": self.passphrase_var.get(),
            "testnet": False,
            "read_only": self.readonly_var.get(),
            "safe_mode": self.safe_var.get(),
            "market_type": "futures" if self.market_var.get() == "Futures" else "spot",
        })

    def _on_disconnect(self) -> None:
        self.backend.submit({"cmd": "disconnect"})

    def _on_manual_trade(self, side: str) -> None:
        if not self.manual_var.get():
            messagebox.showinfo("Disabled", "Enable Manual Trading to use BUY/SELL.")
            return
        if not self.connected:
            messagebox.showwarning("Not connected", "Connect to an exchange first.")
            return
        self._push_settings()   # ensure the latest typed size/mode is in effect
        symbol = self.symbol_var.get().strip()
        size = self.size_var.get().strip()
        sm = self._sizing_mode_value()
        size_desc = (f"${size} (USDT)" if sm == "fixed_quote"
                     else f"{size} (coin)" if sm == "fixed"
                     else "auto (risk %)")
        mode = "SIMULATED" if self.safe_var.get() else "LIVE"
        otype = self.order_type_var.get()
        limit_px = self.limit_price_var.get().strip()
        order_desc = f"{otype.upper()}" + (f" @ {limit_px}" if otype == "limit" and limit_px else "")
        if not messagebox.askyesno(
            "Confirm order",
            f"{mode} {order_desc} {side.upper()} {size_desc} of {symbol} on "
            f"{self.exchange_var.get().upper()}?\n\nProceed?",
        ):
            return
        self.backend.submit({
            "cmd": "trade",
            "symbol": symbol,
            "side": side,
            "source": "manual",
            "order_type": otype,
            "limit_price": self._float(limit_px, 0) or None,
            # size omitted -> backend computes from fixed/risk settings
        })

    def _watch_manual_symbol(self) -> None:
        """Ask the backend to stream the mark price for the manual symbol."""
        symbol = self.symbol_var.get().strip()
        if symbol:
            self.backend.submit({"cmd": "watch", "symbol": symbol})

    def _auto_fill_symbol(self, symbol: str) -> None:
        """A pair arrived from the indicator — show it in the Manual box and
        stream its live Mark price (so indicator pairs appear automatically)."""
        if symbol and symbol != self.symbol_var.get().strip():
            self.symbol_var.set(symbol)
            self._watch_manual_symbol()

    def _on_webhook_signal(self, signal: dict) -> None:
        """Called from the webhook server thread.

        Entry alerts (action buy/sell) open a trade + bracket. Lifecycle events
        (tp1_hit / tp2_hit / sl_hit / sl_after_partial) are routed to the event
        handler — e.g. move stop to breakeven after TP1.
        """
        event = signal.get("event")
        if event == "scale_out":
            # Partial de-risk (Strategy B RSI-extreme scale-out): close a fraction
            # and move the stop to breakeven so the runner can't turn into a loss.
            self.backend.submit({
                "cmd": "close",
                "pair": signal["ticker"],
                "fraction": float(signal.get("fraction") or 0.5),
                "breakeven": True,
            })
            return
        if event:
            self.backend.submit({
                "cmd": "signal_event",
                "event": signal["event"],
                "symbol": signal["ticker"],
                "entry": signal.get("entry"),
                "price": signal.get("price"),
            })
            return
        self.backend.submit({
            "cmd": "trade",
            "symbol": signal["ticker"],
            "side": signal["action"],
            "size": signal.get("size"),
            "entry": signal.get("entry"),
            "sl": signal.get("sl"),
            "tp1": signal.get("tp1"),
            "tp2": signal.get("tp2"),
            "source": signal.get("source", "webhook"),
        })

    def _test_signal(self) -> None:
        """Inject a synthetic BUY through the real signal pipeline so the user can
        verify sizing → order → SL/TP end-to-end without waiting for the indicator.

        It runs exactly like a webhook alert (guardrails, sizing, bracket). In
        Safe Mode it is simulated; live mode places a real order, so we confirm.
        """
        if not self.connected:
            messagebox.showwarning("Not connected", "Connect to an exchange first.")
            return
        symbol = self.symbol_var.get().strip()
        if not symbol:
            messagebox.showwarning("Test Signal", "Pick a symbol first.")
            return
        price = getattr(self, "_last_mark", None)
        live = not self.safe_var.get()
        if live and not messagebox.askyesno(
            "Test Signal — LIVE",
            f"Safe Mode is OFF. This will place a REAL test BUY on {symbol} "
            f"({self.exchange_var.get().upper()}) with a stop/target bracket.\n\n"
            "Turn on Safe Mode to simulate instead. Proceed with a real order?",
        ):
            return
        # Build a bracket around the live price (≈1% stop, 1%/2% targets) when we
        # have one; otherwise leave them blank and let the backend size/skip SL.
        sig = {"ticker": symbol, "action": "buy", "source": "test",
               "comment": self.strategy_filter_var.get().strip() or "Prometheus"}
        if price:
            sig["entry"] = price
            sig["sl"] = round(price * 0.99, 8)
            sig["tp1"] = round(price * 1.01, 8)
            sig["tp2"] = round(price * 1.02, 8)
        self._push_settings()
        self._on_webhook_signal(sig)
        self.backend.ui_queue.put({
            "kind": "log", "time": "", "signal": "TEST", "pair": symbol,
            "status": "simulated" if not live else "live",
            "message": f"Test BUY signal injected for {symbol} "
                       f"({'simulated' if not live else 'LIVE'}) — watch the log/positions.",
        })

    def _toggle_webhook(self) -> None:
        if self.webhook.running:
            self.webhook.stop()
            self.webhook_btn.config(text="▶ Start Webhook")
            self.webhook_status.config(text="● off", fg=GREY)
        else:
            try:
                self.webhook.start()
                self.webhook_btn.config(text="⏹ Stop Webhook")
                self.webhook_status.config(text="● listening", fg=GREEN)
            except OSError as exc:
                messagebox.showerror("Webhook", f"Could not start: {exc}")

    def _autostart_webhook(self) -> None:
        """Start the webhook receiver on launch so the app is ready to receive
        indicator signals as soon as the exchange is connected (plug-and-play)."""
        try:
            self.webhook.start()
            self.webhook_btn.config(text="⏹ Stop Webhook")
            self.webhook_status.config(text="● listening", fg=GREEN)
        except OSError as exc:  # port busy etc. — leave it stopped, user can retry
            self.webhook_status.config(text="● off (port busy)", fg=RED)
            self.backend.ui_queue.put({
                "kind": "log", "time": "", "signal": "", "pair": "", "status": "",
                "message": f"Webhook auto-start failed: {exc} — click Start Webhook to retry",
            })

    def _update_manual_state(self) -> None:
        # BUY/SELL require BOTH a live connection and the manual-trading toggle —
        # so the buttons never advertise an action that would be refused.
        enabled = self.manual_var.get() and getattr(self, "connected", False)
        state = "normal" if enabled else "disabled"
        self.buy_btn.config(state=state)
        self.sell_btn.config(state=state)

    # ====================================================================
    # Log buttons
    # ====================================================================
    def _export_log(self) -> None:
        path = filedialog.asksaveasfilename(
            defaultextension=".csv", filetypes=[("CSV", "*.csv")], title="Export trade log"
        )
        if not path:
            return
        with open(path, "w", newline="", encoding="utf-8") as fh:
            writer = csv.writer(fh)
            writer.writerow(["time", "signal", "pair", "status", "message"])
            for item in self.log_tree.get_children():
                writer.writerow(self.log_tree.item(item)["values"])
        messagebox.showinfo("Exported", f"Log exported to:\n{path}")

    def _clear_log(self) -> None:
        if messagebox.askyesno("Clear log", "Clear the on-screen trade log?"):
            for item in self.log_tree.get_children():
                self.log_tree.delete(item)

    def _open_log_folder(self) -> None:
        """Open the data folder containing the rotating support log (app.log)."""
        import os
        import subprocess
        import sys
        from config import DATA_DIR, DEBUG_LOG
        try:
            if sys.platform.startswith("win"):
                os.startfile(DATA_DIR)  # noqa: S606 - opens Explorer
            elif sys.platform == "darwin":
                subprocess.Popen(["open", DATA_DIR])
            else:
                subprocess.Popen(["xdg-open", DATA_DIR])
        except Exception:  # noqa: BLE001
            messagebox.showinfo(
                "Support log",
                f"Attach this file to your support ticket:\n\n{DEBUG_LOG}")

    # ====================================================================
    # UI queue draining (runs on Tk main loop)
    # ====================================================================
    def _drain_ui_queue(self) -> None:
        # Drain everything queued since the last tick. Empty is the normal exit;
        # a handler error is surfaced (not silently swallowed) but never stops
        # the loop, so one bad event can't freeze the UI.
        while True:
            try:
                msg = self.backend.ui_queue.get_nowait()
            except queue.Empty:
                break
            try:
                self._apply_ui_event(msg)
            except Exception as exc:  # noqa: BLE001
                applog.get_logger().exception("UI event failed: %s", msg.get("kind"))
                self._add_log_row({"time": "", "signal": "", "pair": "",
                                   "status": "Error", "message": f"UI error: {exc}"})
        self._update_relay_status()
        self._update_strategy_status()
        self.root.after(150, self._drain_ui_queue)

    @staticmethod
    def _fmt_age(seconds: float) -> str:
        s = int(seconds)
        if s < 60:
            return f"{s}s"
        if s < 3600:
            return f"{s // 60}m"
        if s < 86400:
            return f"{s // 3600}h"
        return f"{s // 86400}d"

    def _update_relay_status(self) -> None:
        """Live freshness for the Cloud signals feed (last poll / last signal)."""
        if not getattr(self, "relay", None) or not getattr(self, "relay_status", None):
            return
        if not self.relay.running:
            return  # _toggle_relay owns the off state
        now = time.time()
        if not self.relay.last_poll_ts:
            self.relay_status.config(text="● connecting…", fg=GREY)
            return
        poll_age = now - self.relay.last_poll_ts
        if poll_age > 6:   # ~6 polls missed -> feed looks stalled
            self.relay_status.config(text=f"● stalled — no relay response ({self._fmt_age(poll_age)})", fg=RED)
        elif self.relay.last_signal_ts:
            self.relay_status.config(
                text=f"● connected · last signal {self._fmt_age(now - self.relay.last_signal_ts)} ago",
                fg=GREEN)
        else:
            self.relay_status.config(text="● connected · waiting for signals", fg=GREEN)

    def _apply_ui_event(self, msg: dict) -> None:
        kind = msg.get("kind")
        if kind == "status":
            self._set_connected(msg.get("connected", False), msg.get("exchange"))
        elif kind == "account":
            self._update_account(msg)
        elif kind == "log":
            self._add_log_row(msg)
        elif kind == "order":
            if not msg.get("ok"):
                # Surface rejected orders prominently.
                self.bal_label.config(fg=RED)
        elif kind == "ticker":
            self._update_mark(msg)
        elif kind == "signal_symbol":
            self._auto_fill_symbol(msg.get("symbol", ""))
        elif kind == "pairs":
            pairs = msg.get("pairs", [])
            if pairs:
                self.symbol_box.config(values=pairs)
        elif kind == "alert":
            self._handle_alert(msg)

    def _update_mark(self, msg: dict) -> None:
        price = msg.get("price")
        self._last_mark = price
        if price is None:
            self.mark_label.config(text="—", fg=GREY)
        else:
            self.mark_label.config(text=f"{price:,.4f}".rstrip("0").rstrip("."), fg=ACCENT)

    def _handle_alert(self, msg: dict) -> None:
        level = msg.get("level", "error")
        text = msg.get("message", "")
        if level == "ok":
            self.alert_label.config(text="")
            self.status_dot.config(fg=GREEN if self.connected else RED)
        else:
            self.alert_label.config(text="⚠ " + text)
            self.status_dot.config(fg="#e67e22")  # amber: connected but degraded
            if "loss limit" in text.lower():
                self.guardrail_status.config(text="⚠ HALTED — daily loss limit")
            messagebox.showwarning("Alert", text)

    def _set_connected(self, connected: bool, exchange) -> None:
        self.connected = connected
        # Activate/deactivate the built-in strategy with the connection.
        self._push_strategy()
        if connected:
            self.status_dot.config(fg=GREEN)
            self.conn_label.config(text="Connected", fg=GREEN)
            self.exch_label.config(text=f"  ·  {exchange_label(str(exchange))}")
            self.connect_btn.config(state="disabled")
            self.disconnect_btn.config(state="normal")
            self.exchange_combo.config(state="disabled")
            # Start streaming the symbol's current price right away.
            self._watch_manual_symbol()
            # On a user-initiated connect, alert the public IP to whitelist
            # (desktop + Telegram), not on silent auto-reconnects.
            if getattr(self, "_notify_ip_next", False):
                self._notify_ip_next = False
                self._notify_ip_after_connect(str(exchange))
        else:
            self.status_dot.config(fg=RED)
            self.conn_label.config(text="Disconnected", fg=TXT)
            # Keep Connect disabled while a required update is pending.
            self.connect_btn.config(
                state="disabled" if self._required_update else "normal")
            self.disconnect_btn.config(state="disabled")
            self.exchange_combo.config(state="readonly")
            self.mark_label.config(text="—", fg=GREY)
            if not self._required_update:
                self.alert_label.config(text="")
        # Sync the manual BUY/SELL buttons to the new connection state.
        self._update_manual_state()

    def _on_daily_limit_edit(self) -> None:
        # User edited a limit field: drop the backend's exact figures so the label
        # reflects the new value immediately (the next refresh restores exact).
        self._eff_loss_limit = 0.0
        self._eff_profit_limit = 0.0
        self._refresh_daily_limit_status()

    def _refresh_daily_limit_status(self) -> None:
        """Show the active daily stop/target in quote ccy. When connected, use the
        exact thresholds the backend resolved off the day's starting equity (and
        the day's realized P&L so far); otherwise estimate the % off the balance."""
        if not getattr(self, "daily_limit_lbl", None):
            return

        # Exact figures from the backend take precedence when present.
        exact_stop = getattr(self, "_eff_loss_limit", 0.0)
        exact_target = getattr(self, "_eff_profit_limit", 0.0)
        realized = getattr(self, "_daily_realized", None)
        if exact_stop > 0 or exact_target > 0:
            stop = f"-${exact_stop:,.0f}" if exact_stop > 0 else "off"
            target = f"+${exact_target:,.0f}" if exact_target > 0 else "off"
            extra = f"  (today {realized:+,.0f})" if realized is not None else ""
            self.daily_limit_lbl.config(text=f"Active daily — stop {stop} · target {target}{extra}")
            return

        # Not connected yet — estimate the % off the last known balance.
        bal = getattr(self, "_balance_val", 0.0)

        def fmt(pct, absv, sign):
            pct = self._float(pct, 0)
            absv = self._float(absv, 0)
            if pct > 0:
                return f"{sign}${bal * pct / 100:,.0f} ({pct:g}%)" if bal > 0 else f"{pct:g}% of balance"
            if absv > 0:
                return f"{sign}${absv:,.0f}"
            return "off"

        stop = fmt(self.daily_loss_pct_var.get(), self.daily_loss_var.get(), "-")
        target = fmt(self.daily_profit_pct_var.get(), self.daily_profit_var.get(), "+")
        self.daily_limit_lbl.config(text=f"Active daily — stop {stop} · target {target}")

    def _update_account(self, msg: dict) -> None:
        balance = msg.get("balance", 0.0)
        self._balance_val = balance
        self._eff_loss_limit = msg.get("daily_loss_limit", 0.0)
        self._eff_profit_limit = msg.get("daily_profit_limit", 0.0)
        self._daily_realized = msg.get("daily_realized")
        self._refresh_daily_limit_status()
        pnl = msg.get("pnl", 0.0)
        self.bal_label.config(text=f"  ·  ${balance:,.2f}", fg=TXT)
        sign = "+" if pnl >= 0 else "-"
        self.pnl_label.config(
            text=f"  ·  PnL {sign}${abs(pnl):,.2f}", fg=(GREEN if pnl >= 0 else RED)
        )
        for item in self.pos_tree.get_children():
            self.pos_tree.delete(item)
        for p in msg.get("positions", []):
            tag = "pos" if p["pnl"] >= 0 else "neg"
            self.pos_tree.insert(
                "", "end",
                values=(
                    p["pair"], p["side"], p["size"],
                    p["entry"], f"{p['current']:g}", f"{p['pnl']:+.2f}", p.get("status", "Active"),
                ),
                tags=(tag,),
            )

    # Cap on-screen log rows so a long-running session can't grow unbounded.
    MAX_LOG_ROWS = 500

    def _add_log_row(self, msg: dict) -> None:
        status = msg.get("status", "")
        tag = self._LOG_TAGS.get(status, "")
        self.log_tree.insert(
            "", 0,
            values=(
                msg.get("time", ""), msg.get("signal", ""), msg.get("pair", ""),
                status, msg.get("message", ""),
            ),
            tags=((tag,) if tag else ()),
        )
        # Trim oldest rows (inserted at the bottom) past the cap.
        children = self.log_tree.get_children()
        if len(children) > self.MAX_LOG_ROWS:
            self.log_tree.delete(*children[self.MAX_LOG_ROWS:])

    # ====================================================================
    @staticmethod
    def _float(value: str, fallback: float) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return fallback

    def _on_close(self) -> None:
        # If the user opted in (and tray support is present), hide to the tray
        # instead of quitting so the bot keeps trading unattended.
        if getattr(self, "tray_var", None) and self.tray_var.get() and tray_helper.available():
            self._hide_to_tray()
            return
        self._real_quit()

    def _hide_to_tray(self) -> None:
        if getattr(self, "_tray", None) is None:
            icon_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logo.png")
            self._tray = tray_helper.TrayController(
                self.root, on_show=self._restore_from_tray, on_quit=self._real_quit,
                image_path=icon_path, title="Prometheus AI Crypto Bot")
        if self._tray.start():
            self.root.withdraw()
        else:                       # tray failed to start — fall back to quitting
            self._real_quit()

    def _restore_from_tray(self) -> None:
        if getattr(self, "_tray", None) is not None:
            self._tray.stop()
            self._tray = None
        self.root.deiconify()
        self.root.lift()

    def _real_quit(self) -> None:
        try:
            if getattr(self, "_tray", None) is not None:
                self._tray.stop()
            if self.webhook.running:
                self.webhook.stop()
            if self.relay.running:
                self.relay.stop()
            if getattr(self, "strategy_runner", None) and self.strategy_runner.running:
                self.strategy_runner.stop()
            self.backend.stop()
        finally:
            self.root.destroy()

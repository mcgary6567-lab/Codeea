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
import webbrowser
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

import history
import security
import theme
from analytics_window import AnalyticsWindow
from backend import Backend
from relay_client import RelayClient
from config import (
    APP_TITLE,
    APP_VERSION,
    EXCHANGE_LABELS,
    exchange_id,
    exchange_label,
    resource_path,
    DEFAULT_AUTO_BRACKET,
    DEFAULT_LEVERAGE,
    DEFAULT_ORDER_TYPE,
    DEFAULT_RISK_PERCENT,
    DEFAULT_SAFE_MODE,
    DEFAULT_TRADE_SIZE,
    DEFAULT_RELAY_URL,
    DEFAULT_WEBHOOK_PASSPHRASE,
    ORDER_TYPES,
    QUOTE_CURRENCY,
    SIZING_MODE_LABELS,
    SIZING_MODES,
    SUPPORTED_EXCHANGES,
    SUPPORT_EMAIL,
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

        self.connected = False
        self._live_ack = bool(self.saved.get("live_ack", False))
        self._build_ui()
        self._load_saved_into_ui()
        self._push_settings()
        self._autostart_webhook()       # ready to receive signals out of the box
        self._autostart_relay()         # auto-connect cloud signals if licensed
        self.root.after(150, self._drain_ui_queue)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

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
        body.rowconfigure(1, weight=1)

        self._build_api_panel(body)
        self._build_trade_buttons(body)
        self._build_positions(body)
        self._build_trade_settings(body)
        self._build_trade_log(body)

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
            n = max(1, img.width() // 40)
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

        def link(parent, text, action):
            lbl = tk.Label(parent, text=text, bg=HEADER, fg=ACCENT, cursor="hand2",
                           font=("Segoe UI", 9, "underline"))
            lbl.pack(side="right", padx=10)
            lbl.bind("<Button-1>", lambda e: action())
            return lbl

        link(bar, "Support: " + SUPPORT_EMAIL, lambda: self._open_url(f"mailto:{SUPPORT_EMAIL}"))
        link(bar, "Website: prometheusai.tech", lambda: self._open_url(WEBSITE_URL))

    def _open_url(self, url: str) -> None:
        try:
            webbrowser.open(url)
        except Exception:  # noqa: BLE001
            messagebox.showinfo("Link", url)

    def _build_api_panel(self, parent) -> None:
        f = ttk.LabelFrame(parent, text="API Settings", padding=10)
        f.grid(row=0, column=0, sticky="nsew", padx=5, pady=5)
        f.columnconfigure(1, weight=1)

        ttk.Label(f, text="Select Exchange:").grid(row=0, column=0, sticky="w", pady=4)
        self._exchange_labels = [EXCHANGE_LABELS[e] for e in SUPPORTED_EXCHANGES]
        self.exchange_var = tk.StringVar(value=self._exchange_labels[0])
        self.exchange_combo = ttk.Combobox(
            f, textvariable=self.exchange_var, values=self._exchange_labels, state="readonly"
        )
        self.exchange_combo.grid(row=0, column=1, sticky="ew", pady=4)
        self.exchange_combo.bind("<<ComboboxSelected>>", lambda e: self._toggle_passphrase())

        ttk.Label(f, text="API Key:").grid(row=1, column=0, sticky="w", pady=4)
        self.api_key_var = tk.StringVar()
        ttk.Entry(f, textvariable=self.api_key_var, show="•").grid(row=1, column=1, sticky="ew", pady=4)

        ttk.Label(f, text="API Secret:").grid(row=2, column=0, sticky="w", pady=4)
        self.api_secret_var = tk.StringVar()
        ttk.Entry(f, textvariable=self.api_secret_var, show="•").grid(row=2, column=1, sticky="ew", pady=4)

        self.pass_label = ttk.Label(f, text="API Passphrase:")
        self.passphrase_var = tk.StringVar()
        self.pass_entry = ttk.Entry(f, textvariable=self.passphrase_var, show="•")

        btns = ttk.Frame(f)
        btns.grid(row=4, column=0, columnspan=2, pady=(10, 0))
        self.connect_btn = tk.Button(btns, text="Connect", width=14, command=self._on_connect)
        theme.style_button(self.connect_btn, "buy")
        self.connect_btn.pack(side="left", padx=5)
        self.disconnect_btn = tk.Button(btns, text="Disconnect", width=14,
                                        command=self._on_disconnect, state="disabled")
        theme.style_button(self.disconnect_btn, "sell")
        self.disconnect_btn.pack(side="left", padx=5)

        self._toggle_passphrase()

    def _toggle_passphrase(self) -> None:
        needs = exchange_id(self.exchange_var.get()) in PASSPHRASE_EXCHANGES
        if needs:
            self.pass_label.grid(row=3, column=0, sticky="w", pady=4)
            self.pass_entry.grid(row=3, column=1, sticky="ew", pady=4)
        else:
            self.pass_label.grid_remove()
            self.pass_entry.grid_remove()

    def _build_trade_buttons(self, parent) -> None:
        f = ttk.Frame(parent, padding=(5, 0))
        f.grid(row=2, column=0, sticky="ew", padx=5, pady=5)   # bottom of left column
        f.columnconfigure(0, weight=1)
        f.columnconfigure(1, weight=1)

        self.buy_btn = theme.RoundedButton(
            f, text="BUY", bg=GREEN, active=theme.GREEN, fg="white",
            radius=5, height=40, font=("Segoe UI", 15, "bold"),
            command=lambda: self._on_manual_trade("buy"),
        )
        self.buy_btn.grid(row=0, column=0, sticky="ew", padx=(0, 5))
        self.sell_btn = theme.RoundedButton(
            f, text="SELL", bg=RED, active=theme.RED, fg="white",
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
        ttk.Label(top, text="Manual symbol:").pack(side="left")
        self.symbol_var = tk.StringVar(value="BTC/USDT")
        sym_entry = ttk.Entry(top, textvariable=self.symbol_var, width=14)
        sym_entry.pack(side="left", padx=6)
        # Live mark price for the manual symbol (streamed even with no position).
        self.mark_label = tk.Label(top, text="Mark: —", fg=GREY, bg=PANEL,
                                   font=("Segoe UI", 10, "bold"))
        self.mark_label.pack(side="left", padx=4)
        # Re-subscribe the feed whenever the symbol is edited/committed.
        sym_entry.bind("<Return>", lambda e: self._watch_manual_symbol())
        sym_entry.bind("<FocusOut>", lambda e: self._watch_manual_symbol())
        tk.Button(top, text="Refresh Now", command=lambda: self.backend.submit({"cmd": "refresh"})).pack(side="right")
        self.feed_label = tk.Label(top, text="feed: off", fg=GREY, bg=PANEL)
        self.feed_label.pack(side="right", padx=8)

        cols = ("pair", "side", "size", "entry", "current", "pnl", "status")
        self.pos_tree = ttk.Treeview(f, columns=cols, show="headings", height=7)
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
        tk.Button(cbar, text="Close Selected", command=self._close_selected).pack(side="left", padx=4)
        tk.Button(
            cbar, text="PANIC: Close All", bg=RED, fg="white", activebackground=theme.RED,
            font=("Segoe UI", 9, "bold"), command=self._close_all,
        ).pack(side="left", padx=4)

    def _close_selected(self) -> None:
        sel = self.pos_tree.selection()
        if not sel:
            messagebox.showinfo("Close", "Select a position row first.")
            return
        pair = self.pos_tree.item(sel[0])["values"][0]
        if messagebox.askyesno("Close position", f"Close {pair} at market?"):
            self.backend.submit({"cmd": "close", "pair": pair})

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
        outer.grid(row=0, column=1, sticky="nsew", padx=5, pady=5)
        outer.columnconfigure(0, weight=1)

        nb = ttk.Notebook(outer)
        nb.grid(row=0, column=0, sticky="nsew")
        exec_tab = ttk.Frame(nb, padding=8)
        risk_tab = ttk.Frame(nb, padding=8)
        alert_tab = ttk.Frame(nb, padding=8)
        nb.add(exec_tab, text="Execution")
        nb.add(risk_tab, text="Modes & Risk")
        nb.add(alert_tab, text="Webhook & Alerts")
        for t in (exec_tab, risk_tab, alert_tab):
            t.columnconfigure(1, weight=1)

        self._build_exec_tab(exec_tab)
        self._build_risk_tab(risk_tab)
        self._build_alert_tab(alert_tab)

        btnrow = ttk.Frame(outer)
        btnrow.grid(row=1, column=0, sticky="ew", pady=(8, 0))
        tk.Button(btnrow, text="Save Settings (encrypted)", command=self._save_all).pack(
            side="left", expand=True, fill="x", padx=2
        )
        analytics_btn = tk.Button(btnrow, text="Analytics", command=self._open_analytics)
        theme.style_button(analytics_btn, "accent")
        analytics_btn.pack(side="left", expand=True, fill="x", padx=2)

    def _build_exec_tab(self, f) -> None:
        ttk.Label(f, text="Trade Size:").grid(row=0, column=0, sticky="w", pady=4)
        size_row = ttk.Frame(f)
        size_row.grid(row=0, column=1, sticky="w", pady=4)
        self.size_var = tk.StringVar(value=str(DEFAULT_TRADE_SIZE))
        ttk.Entry(size_row, textvariable=self.size_var, width=12).pack(side="left")
        ttk.Label(size_row, text="base (e.g. BTC)").pack(side="left", padx=6)

        ttk.Label(f, text="Sizing mode:").grid(row=1, column=0, sticky="w", pady=4)
        self.sizing_mode_var = tk.StringVar(value=SIZING_MODE_LABELS["fixed"])
        ttk.Combobox(
            f, textvariable=self.sizing_mode_var,
            values=[SIZING_MODE_LABELS[m] for m in SIZING_MODES], state="readonly",
        ).grid(row=1, column=1, sticky="ew", pady=4)
        self.sizing_mode_var.trace_add("write", lambda *a: self._push_settings())

        rr = ttk.Frame(f)
        rr.grid(row=2, column=0, columnspan=2, sticky="w")
        ttk.Label(rr, text="Risk %:").pack(side="left")
        self.risk_pct_var = tk.StringVar(value=str(DEFAULT_RISK_PERCENT))
        ttk.Entry(rr, textvariable=self.risk_pct_var, width=6).pack(side="left", padx=6)
        ttk.Label(rr, text="(risk-based modes)").pack(side="left")

        self.auto_bracket_var = tk.BooleanVar(value=DEFAULT_AUTO_BRACKET)
        ttk.Checkbutton(
            f, text="Auto-place SL/TP from alerts (scale out TP1/TP2)",
            variable=self.auto_bracket_var, command=self._push_settings,
        ).grid(row=3, column=0, columnspan=2, sticky="w", pady=2)

        ttk.Separator(f, orient="horizontal").grid(row=4, column=0, columnspan=2, sticky="ew", pady=6)

        ttk.Label(f, text="Order type:").grid(row=5, column=0, sticky="w", pady=4)
        otr = ttk.Frame(f)
        otr.grid(row=5, column=1, sticky="w", pady=4)
        self.order_type_var = tk.StringVar(value=DEFAULT_ORDER_TYPE)
        ttk.Combobox(
            otr, textvariable=self.order_type_var, values=ORDER_TYPES, state="readonly", width=8,
        ).pack(side="left")
        ttk.Label(otr, text="Limit px:").pack(side="left", padx=(8, 2))
        self.limit_price_var = tk.StringVar()
        ttk.Entry(otr, textvariable=self.limit_price_var, width=12).pack(side="left")
        self.order_type_var.trace_add("write", lambda *a: self._push_settings())

        ttk.Label(f, text="Leverage (x):").grid(row=6, column=0, sticky="w", pady=4)
        lvr = ttk.Frame(f)
        lvr.grid(row=6, column=1, sticky="w", pady=4)
        self.leverage_var = tk.StringVar(value=str(DEFAULT_LEVERAGE))
        ttk.Entry(lvr, textvariable=self.leverage_var, width=6).pack(side="left")
        ttk.Label(lvr, text="0 = leave as-is   Margin:").pack(side="left", padx=(6, 2))
        self.margin_mode_var = tk.StringVar(value="")
        ttk.Combobox(
            lvr, textvariable=self.margin_mode_var,
            values=["(default)", "cross", "isolated"], state="readonly", width=9,
        ).pack(side="left")

    def _build_risk_tab(self, f) -> None:
        self.manual_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(
            f, text="Enable Manual Trading", variable=self.manual_var,
            command=self._update_manual_state,
        ).grid(row=0, column=0, columnspan=2, sticky="w", pady=2)

        self.safe_var = tk.BooleanVar(value=DEFAULT_SAFE_MODE)
        ttk.Checkbutton(
            f, text="Safe Mode (simulate, no real orders)", variable=self.safe_var,
            command=self._push_settings,
        ).grid(row=1, column=0, columnspan=2, sticky="w", pady=2)

        self.readonly_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            f, text="Read-only monitoring (block all orders)", variable=self.readonly_var,
            command=self._push_settings,
        ).grid(row=2, column=0, columnspan=2, sticky="w", pady=2)

        ttk.Separator(f, orient="horizontal").grid(row=3, column=0, columnspan=2, sticky="ew", pady=6)
        ttk.Label(f, text="Guardrails (0 = off):", font=("Segoe UI", 9, "bold")).grid(
            row=4, column=0, columnspan=2, sticky="w"
        )

        self.max_open_var = tk.StringVar(value="0")
        self.daily_loss_var = tk.StringVar(value="0")
        self.daily_profit_var = tk.StringVar(value="0")
        self.cooldown_var = tk.StringVar(value="0")
        self.dedupe_var = tk.StringVar(value="0")
        rows = [
            ("Max open positions:", self.max_open_var),
            (f"Daily loss limit ({QUOTE_CURRENCY}):", self.daily_loss_var),
            (f"Daily profit limit ({QUOTE_CURRENCY}):", self.daily_profit_var),
            ("Cooldown / symbol (s):", self.cooldown_var),
            ("Dedupe window (s):", self.dedupe_var),
        ]
        for i, (text, var) in enumerate(rows, start=5):
            ttk.Label(f, text=text).grid(row=i, column=0, sticky="w", pady=2)
            e = ttk.Entry(f, textvariable=var, width=10)
            e.grid(row=i, column=1, sticky="w", pady=2)
            e.bind("<FocusOut>", lambda ev: self._push_settings())

        gr = ttk.Frame(f)
        gr.grid(row=10, column=0, columnspan=2, sticky="ew", pady=(8, 0))
        tk.Button(gr, text="Reset daily limit", command=self._reset_daily).pack(side="left")
        self.guardrail_status = tk.Label(gr, text="", fg=RED, bg=PANEL, font=("Segoe UI", 9, "bold"))
        self.guardrail_status.pack(side="left", padx=8)

        ttk.Separator(f, orient="horizontal").grid(row=11, column=0, columnspan=2, sticky="ew", pady=6)
        self.move_be_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            f, text="Move stop to breakeven on TP1 event (from indicator)",
            variable=self.move_be_var, command=self._push_settings,
        ).grid(row=12, column=0, columnspan=2, sticky="w", pady=2)

    def _build_alert_tab(self, f) -> None:
        ttk.Label(f, text=f"Webhook (TradingView) on port {WEBHOOK_PORT}:").grid(
            row=0, column=0, columnspan=2, sticky="w"
        )
        wr = ttk.Frame(f)
        wr.grid(row=1, column=0, columnspan=2, sticky="ew", pady=4)
        ttk.Label(wr, text="Passphrase:").pack(side="left")
        self.webhook_pass_var = tk.StringVar(value=DEFAULT_WEBHOOK_PASSPHRASE)
        ttk.Entry(wr, textvariable=self.webhook_pass_var, width=16).pack(side="left", padx=6)
        self.webhook_btn = tk.Button(wr, text="Start Webhook", command=self._toggle_webhook)
        self.webhook_btn.pack(side="left", padx=6)
        self.webhook_status = tk.Label(wr, text="● off", fg=GREY, bg=PANEL)
        self.webhook_status.pack(side="left")

        # Strategy filter — only act on alerts whose comment/strategy matches.
        ttk.Label(f, text="Strategy filter:").grid(row=2, column=0, sticky="w", pady=2)
        self.strategy_filter_var = tk.StringVar(value="Prometheus")
        ttk.Entry(f, textvariable=self.strategy_filter_var).grid(row=2, column=1, sticky="ew", pady=2)
        ttk.Label(f, text="Only act on this indicator (matches its Order Comment). Blank = accept all.",
                  style="Dim.TLabel", wraplength=380).grid(row=3, column=0, columnspan=2, sticky="w")

        ttk.Separator(f, orient="horizontal").grid(row=4, column=0, columnspan=2, sticky="ew", pady=6)

        self.sound_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(
            f, text="Sound on fills/signals", variable=self.sound_var,
            command=self._push_settings,
        ).grid(row=5, column=0, columnspan=2, sticky="w", pady=2)
        ttk.Label(f, text="Telegram bot token:").grid(row=6, column=0, sticky="w", pady=2)
        self.tg_token_var = tk.StringVar()
        ttk.Entry(f, textvariable=self.tg_token_var, show="•").grid(row=6, column=1, sticky="ew", pady=2)
        ttk.Label(f, text="Telegram chat id:").grid(row=7, column=0, sticky="w", pady=2)
        self.tg_chat_var = tk.StringVar()
        ttk.Entry(f, textvariable=self.tg_chat_var).grid(row=7, column=1, sticky="ew", pady=2)

        ttk.Separator(f, orient="horizontal").grid(row=8, column=0, columnspan=2, sticky="ew", pady=6)
        ttk.Label(f, text="Cloud signals (licence) — no ngrok needed",
                  font=("Segoe UI", 9, "bold")).grid(row=9, column=0, columnspan=2, sticky="w")
        ttk.Label(f, text="Relay URL:").grid(row=10, column=0, sticky="w", pady=2)
        self.relay_url_var = tk.StringVar(value=DEFAULT_RELAY_URL)
        ttk.Entry(f, textvariable=self.relay_url_var).grid(row=10, column=1, sticky="ew", pady=2)
        ttk.Label(f, text="Licence token:").grid(row=11, column=0, sticky="w", pady=2)
        tr = ttk.Frame(f)
        tr.grid(row=11, column=1, sticky="ew", pady=2)
        tr.columnconfigure(0, weight=1)
        self.relay_token_var = tk.StringVar()
        ttk.Entry(tr, textvariable=self.relay_token_var).grid(row=0, column=0, sticky="ew")
        self.relay_btn = tk.Button(tr, text="Connect", command=self._toggle_relay)
        self.relay_btn.grid(row=0, column=1, padx=(6, 0))
        self.relay_status = tk.Label(f, text="● off", fg=GREY, bg=PANEL, font=("Segoe UI", 9))
        self.relay_status.grid(row=12, column=0, columnspan=2, sticky="w")

    def _toggle_relay(self) -> None:
        if self.relay.running:
            self.relay.stop()
            self.relay_btn.config(text="Connect")
            self.relay_status.config(text="● off", fg=GREY)
        else:
            self.relay.start()
            if self.relay.running:
                self.relay_btn.config(text="Disconnect")
                self.relay_status.config(text="● connected (cloud signals)", fg=GREEN)

    def _autostart_relay(self) -> None:
        """Auto-connect cloud signals if a licence token was saved."""
        if self.relay_token_var.get().strip():
            self._toggle_relay()

    def _build_trade_log(self, parent) -> None:
        f = ttk.LabelFrame(parent, text="Trade Log", padding=8)
        f.grid(row=1, column=1, rowspan=2, sticky="nsew", padx=5, pady=5)
        f.rowconfigure(0, weight=1)
        f.columnconfigure(0, weight=1)

        cols = ("time", "signal", "pair", "status", "message")
        self.log_tree = ttk.Treeview(f, columns=cols, show="headings")
        for c, w in zip(cols, (70, 60, 90, 80, 260)):
            self.log_tree.heading(c, text=c.capitalize())
            self.log_tree.column(c, width=w, anchor="w")
        self.log_tree.grid(row=0, column=0, sticky="nsew")
        sb = ttk.Scrollbar(f, orient="vertical", command=self.log_tree.yview)
        self.log_tree.configure(yscrollcommand=sb.set)
        sb.grid(row=0, column=1, sticky="ns")

        bar = ttk.Frame(f)
        bar.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(6, 0))
        tk.Button(bar, text="Export Log", command=self._export_log).pack(side="left", padx=4)
        tk.Button(bar, text="Clear Log", command=self._clear_log).pack(side="left", padx=4)

    # ====================================================================
    # Settings persistence / prefill
    # ====================================================================
    def _load_saved_into_ui(self) -> None:
        s = self.saved
        if not s:
            return
        saved_ex = s.get("exchange", SUPPORTED_EXCHANGES[0])
        self.exchange_var.set(EXCHANGE_LABELS.get(saved_ex, saved_ex))
        self.api_key_var.set(s.get("api_key", ""))
        self.api_secret_var.set(s.get("api_secret", ""))
        self.passphrase_var.set(s.get("passphrase", ""))
        self.size_var.set(str(s.get("size", DEFAULT_TRADE_SIZE)))
        self.sizing_mode_var.set(SIZING_MODE_LABELS.get(s.get("sizing_mode", "fixed"), SIZING_MODE_LABELS["fixed"]))
        self.risk_pct_var.set(str(s.get("risk_percent", DEFAULT_RISK_PERCENT)))
        self.auto_bracket_var.set(s.get("auto_bracket", DEFAULT_AUTO_BRACKET))
        self.order_type_var.set(s.get("order_type", DEFAULT_ORDER_TYPE))
        self.limit_price_var.set(str(s.get("limit_price", "")))
        self.leverage_var.set(str(s.get("leverage", DEFAULT_LEVERAGE)))
        mm = s.get("margin_mode", "")
        self.margin_mode_var.set(mm if mm in ("cross", "isolated") else "(default)")
        self.move_be_var.set(s.get("move_be", False))
        self.max_open_var.set(str(s.get("max_open", 0)))
        self.daily_loss_var.set(str(s.get("daily_loss", 0)))
        self.daily_profit_var.set(str(s.get("daily_profit", 0)))
        self.cooldown_var.set(str(s.get("cooldown", 0)))
        self.dedupe_var.set(str(s.get("dedupe", 0)))
        self.safe_var.set(s.get("safe_mode", DEFAULT_SAFE_MODE))
        self.readonly_var.set(s.get("read_only", False))
        self.webhook_pass_var.set(s.get("webhook_passphrase", DEFAULT_WEBHOOK_PASSPHRASE))
        self.strategy_filter_var.set(s.get("strategy_filter", "Prometheus"))
        self.relay_url_var.set(s.get("relay_url", DEFAULT_RELAY_URL))
        self.relay_token_var.set(s.get("relay_token", ""))
        self.sound_var.set(s.get("sound", True))
        self.tg_token_var.set(s.get("telegram_token", ""))
        self.tg_chat_var.set(s.get("telegram_chat_id", ""))
        self._toggle_passphrase()

    def _collect_settings(self) -> dict:
        return {
            "exchange": self.exchange_var.get(),
            "api_key": self.api_key_var.get(),
            "api_secret": self.api_secret_var.get(),
            "passphrase": self.passphrase_var.get(),
            "size": self._float(self.size_var.get(), DEFAULT_TRADE_SIZE),
            "sizing_mode": self._sizing_mode_value(),
            "risk_percent": self._float(self.risk_pct_var.get(), DEFAULT_RISK_PERCENT),
            "auto_bracket": self.auto_bracket_var.get(),
            "order_type": self.order_type_var.get(),
            "limit_price": self.limit_price_var.get(),
            "leverage": int(self._float(self.leverage_var.get(), 0)),
            "margin_mode": self._margin_mode_value(),
            "move_be": self.move_be_var.get(),
            "max_open": int(self._float(self.max_open_var.get(), 0)),
            "daily_loss": self._float(self.daily_loss_var.get(), 0),
            "daily_profit": self._float(self.daily_profit_var.get(), 0),
            "cooldown": int(self._float(self.cooldown_var.get(), 0)),
            "dedupe": int(self._float(self.dedupe_var.get(), 0)),
            "safe_mode": self.safe_var.get(),
            "read_only": self.readonly_var.get(),
            "webhook_passphrase": self.webhook_pass_var.get(),
            "strategy_filter": self.strategy_filter_var.get(),
            "relay_url": self.relay_url_var.get(),
            "relay_token": self.relay_token_var.get(),
            "sound": self.sound_var.get(),
            "telegram_token": self.tg_token_var.get(),
            "telegram_chat_id": self.tg_chat_var.get(),
            "live_ack": self._live_ack,
        }

    def _save_all(self) -> None:
        security.save_credentials(self.pin, self._collect_settings())
        self._push_settings()
        messagebox.showinfo("Saved", "Settings encrypted and saved.")

    def _sizing_mode_value(self) -> str:
        label = self.sizing_mode_var.get()
        for value, lab in SIZING_MODE_LABELS.items():
            if lab == label:
                return value
        return "fixed"

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
            "order_type": self.order_type_var.get(),
            "leverage": int(self._float(self.leverage_var.get(), 0)),
            "margin_mode": self._margin_mode_value(),
            "move_be": self.move_be_var.get(),
            "max_open": int(self._float(self.max_open_var.get(), 0)),
            "daily_loss": self._float(self.daily_loss_var.get(), 0),
            "daily_profit": self._float(self.daily_profit_var.get(), 0),
            "cooldown": int(self._float(self.cooldown_var.get(), 0)),
            "dedupe": int(self._float(self.dedupe_var.get(), 0)),
            "safe_mode": self.safe_var.get(),
            "read_only": self.readonly_var.get(),
            "sound": self.sound_var.get(),
            "telegram_token": self.tg_token_var.get(),
            "telegram_chat_id": self.tg_chat_var.get(),
        })

    # ====================================================================
    # Actions
    # ====================================================================
    def _on_connect(self) -> None:
        ex = exchange_id(self.exchange_var.get())
        if not self.api_key_var.get() or not self.api_secret_var.get():
            messagebox.showwarning("Missing keys", "Enter API key and secret first.")
            return
        if ex in PASSPHRASE_EXCHANGES and not self.passphrase_var.get():
            messagebox.showwarning("Missing passphrase",
                                   f"{exchange_label(ex)} requires an API passphrase.")
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
        self.backend.submit({
            "cmd": "connect",
            "exchange_id": ex,
            "api_key": self.api_key_var.get(),
            "secret": self.api_secret_var.get(),
            "password": self.passphrase_var.get(),
            "testnet": False,
            "read_only": self.readonly_var.get(),
            "safe_mode": self.safe_var.get(),
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
        symbol = self.symbol_var.get().strip()
        size = self.size_var.get().strip()
        mode = "SIMULATED" if self.safe_var.get() else "LIVE"
        otype = self.order_type_var.get()
        limit_px = self.limit_price_var.get().strip()
        order_desc = f"{otype.upper()}" + (f" @ {limit_px}" if otype == "limit" and limit_px else "")
        if not messagebox.askyesno(
            "Confirm order",
            f"{mode} {order_desc} {side.upper()} {size} of {symbol} on "
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
        if signal.get("event"):
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

    def _toggle_webhook(self) -> None:
        if self.webhook.running:
            self.webhook.stop()
            self.webhook_btn.config(text="Start Webhook")
            self.webhook_status.config(text="● off", fg=GREY)
        else:
            try:
                self.webhook.start()
                self.webhook_btn.config(text="Stop Webhook")
                self.webhook_status.config(text="● listening", fg=GREEN)
            except OSError as exc:
                messagebox.showerror("Webhook", f"Could not start: {exc}")

    def _autostart_webhook(self) -> None:
        """Start the webhook receiver on launch so the app is ready to receive
        indicator signals as soon as the exchange is connected (plug-and-play)."""
        try:
            self.webhook.start()
            self.webhook_btn.config(text="Stop Webhook")
            self.webhook_status.config(text="● listening", fg=GREEN)
        except OSError as exc:  # port busy etc. — leave it stopped, user can retry
            self.webhook_status.config(text="● off (port busy)", fg=RED)
            self.backend.ui_queue.put({
                "kind": "log", "time": "", "signal": "", "pair": "", "status": "",
                "message": f"Webhook auto-start failed: {exc} — click Start Webhook to retry",
            })

    def _update_manual_state(self) -> None:
        state = "normal" if self.manual_var.get() else "disabled"
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

    # ====================================================================
    # UI queue draining (runs on Tk main loop)
    # ====================================================================
    def _drain_ui_queue(self) -> None:
        try:
            while True:
                msg = self.backend.ui_queue.get_nowait()
                self._apply_ui_event(msg)
        except Exception:
            pass
        self.root.after(150, self._drain_ui_queue)

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
        elif kind == "alert":
            self._handle_alert(msg)

    def _update_mark(self, msg: dict) -> None:
        price = msg.get("price")
        if price is None:
            self.mark_label.config(text="Mark: —", fg=GREY)
        else:
            self.mark_label.config(text=f"Mark: {price:,.4f}".rstrip("0").rstrip("."), fg=ACCENT)

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
        if connected:
            self.status_dot.config(fg=GREEN)
            self.conn_label.config(text="Connected", fg=GREEN)
            self.exch_label.config(text=f"  ·  {exchange_label(str(exchange))}")
            self.connect_btn.config(state="disabled")
            self.disconnect_btn.config(state="normal")
            self.exchange_combo.config(state="disabled")
            if self.safe_var.get():
                self.feed_label.config(text="feed: sim", fg=GREY)
            else:
                self.feed_label.config(text="feed: live", fg=GREEN)
                # Start streaming the manual symbol's mark price right away.
                self._watch_manual_symbol()
        else:
            self.status_dot.config(fg=RED)
            self.conn_label.config(text="Disconnected", fg=TXT)
            self.connect_btn.config(state="normal")
            self.disconnect_btn.config(state="disabled")
            self.exchange_combo.config(state="readonly")
            self.feed_label.config(text="feed: off", fg=GREY)
            self.alert_label.config(text="")

    def _update_account(self, msg: dict) -> None:
        balance = msg.get("balance", 0.0)
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

    def _add_log_row(self, msg: dict) -> None:
        self.log_tree.insert(
            "", 0,
            values=(
                msg.get("time", ""), msg.get("signal", ""), msg.get("pair", ""),
                msg.get("status", ""), msg.get("message", ""),
            ),
        )

    # ====================================================================
    @staticmethod
    def _float(value: str, fallback: float) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return fallback

    def _on_close(self) -> None:
        try:
            if self.webhook.running:
                self.webhook.stop()
            if self.relay.running:
                self.relay.stop()
            self.backend.stop()
        finally:
            self.root.destroy()

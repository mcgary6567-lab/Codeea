"""GUI wiring / integration tests.

These build the real :class:`gui.TradingBotGUI` and assert that the seams between
the UI, backend, guardrails, strategy runner and the secondary windows are wired
correctly — the kind of breakage unit tests on pure logic can't catch (settings
key drift, command-name mismatches, callback wiring, default propagation).

They need a Tk display, so the whole module **skips cleanly when none is
available** (e.g. plain CI without xvfb); run locally or under ``xvfb-run`` to
exercise them. One shared GUI instance is reused across tests to avoid rebinding
the webhook port.
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import tkinter as tk  # noqa: E402


def _tk_available() -> bool:
    try:
        r = tk.Tk()
        r.destroy()
        return True
    except Exception:  # noqa: BLE001 - no display / Tk not built
        return False


_TK_OK = _tk_available()


@unittest.skipUnless(_TK_OK, "no Tk display available")
class TestGuiWiring(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ["HOME"] = tempfile.mkdtemp()
        os.environ.pop("APPDATA", None)
        import theme
        import gui
        cls.gui = gui
        cls.root = tk.Tk()
        theme.apply(cls.root)
        cls.app = gui.TradingBotGUI(cls.root, pin="testpin", saved={})
        cls.root.update_idletasks()
        # Silence modal dialogs for any test that triggers one.
        import tkinter.messagebox as mb
        mb.askyesno = lambda *a, **k: True
        mb.showinfo = lambda *a, **k: None
        mb.showwarning = lambda *a, **k: None
        mb.showerror = lambda *a, **k: None
        gui.messagebox = mb

    @classmethod
    def tearDownClass(cls):
        try:
            cls.app._real_quit()
        except Exception:  # noqa: BLE001
            try:
                cls.root.destroy()
            except Exception:  # noqa: BLE001
                pass

    # -- defaults propagate UI -> params/settings --------------------------
    def test_strategy_defaults(self):
        p = self.app._strategy_params()
        self.assertEqual(
            (p.ma_confirm, p.ma_min_body, p.ma_trend_len, p.ma_atr_mult, p.ma_atr_len),
            (2, 0.20, 100, 2.5, 14))

    def test_execution_and_guardrail_defaults(self):
        s = self.app._collect_settings()
        self.assertEqual(s["sizing_mode"], "risk_stop")
        self.assertEqual(s["leverage"], 5)
        self.assertEqual(s["margin_mode"], "isolated")
        self.assertAlmostEqual(s["slippage_pct"], 0.4)
        self.assertEqual(s["max_open"], 4)
        self.assertAlmostEqual(s["daily_loss_pct"], 5.0)
        self.assertEqual(s["daily_profit_pct"], 0.0)

    def test_settings_round_trip_stable(self):
        s = self.app._collect_settings()
        self.app.saved = dict(s)
        self.app._load_saved_into_ui()
        s2 = self.app._collect_settings()
        diffs = [k for k in s if str(s.get(k)) != str(s2.get(k))]
        self.assertEqual(diffs, [], f"settings drifted on round-trip: {diffs}")

    # -- backend / guardrails seams ----------------------------------------
    def test_backend_handles_full_settings_command(self):
        cmd = dict(self.app._collect_settings())
        cmd["cmd"] = "settings"
        self.app.backend._handle_command(cmd)   # must not raise (signature match)
        gr = self.app.backend.guardrails
        self.assertEqual(gr.max_open_positions, 4)
        self.assertAlmostEqual(gr.daily_loss_pct, 5.0)
        self.assertEqual(self.app.backend.sizing_mode, "risk_stop")
        self.assertEqual(self.app.backend.margin_mode, "isolated")

    def test_signal_to_command_mapping(self):
        caps = []
        orig = self.app.backend.submit
        self.app.backend.submit = lambda c: caps.append(c)
        try:
            self.app._on_webhook_signal({"action": "buy", "event": "", "ticker": "BTC/USDT",
                                         "size": None, "entry": 100.0, "sl": 95.0, "tp1": None,
                                         "tp2": None, "price": 100.0, "source": "strategy"})
            self.app._on_webhook_signal({"event": "scale_out", "ticker": "BTC/USDT",
                                         "fraction": 0.5, "source": "strategy"})
            self.app._on_webhook_signal({"event": "exit", "ticker": "BTC/USDT", "source": "strategy"})
        finally:
            self.app.backend.submit = orig
        self.assertEqual([c.get("cmd") for c in caps], ["trade", "close", "signal_event"])
        self.assertEqual(caps[1].get("fraction"), 0.5)
        self.assertIs(caps[1].get("breakeven"), True)

    def test_runner_callbacks_wired(self):
        sr = self.app.strategy_runner
        for cb in (sr.on_signal, sr.get_token, sr.get_verify_url, sr.get_positions):
            self.assertTrue(callable(cb))
        # get_positions must reach the backend's reconciliation snapshot.
        self.assertEqual(sr.get_positions(), self.app.backend.open_position_sides())

    # -- status line uses the backend's exact resolved limits --------------
    def test_daily_limit_status_exact(self):
        self.app._update_account({"balance": 1000.0, "pnl": 0.0, "positions": [],
                                  "daily_loss_limit": 50.0, "daily_profit_limit": 0.0,
                                  "daily_realized": -7.0})
        txt = self.app.daily_limit_lbl.cget("text")
        self.assertIn("-$50", txt)
        self.assertIn("today", txt)

    def test_mode_badge_reflects_state(self):
        a = self.app
        a._set_connected(False, "binance")
        self.assertEqual(a.mode_badge.cget("text").strip(), "")
        a.safe_var.set(False); a.readonly_var.set(False); a._conn_testnet = False
        a._set_connected(True, "binance")
        self.assertIn("LIVE", a.mode_badge.cget("text"))
        a.safe_var.set(True); a._update_mode_badge()
        self.assertEqual(a.mode_badge.cget("text").strip(), "SAFE MODE")
        a.readonly_var.set(True); a._update_mode_badge()
        self.assertEqual(a.mode_badge.cget("text").strip(), "READ-ONLY")
        a.safe_var.set(False); a.readonly_var.set(False); a._conn_testnet = True
        a._update_mode_badge()
        self.assertEqual(a.mode_badge.cget("text").strip(), "TESTNET")
        a._set_connected(False, "binance")   # leave clean for other tests

    def test_windows_reuse_not_stack(self):
        # Clicking a window button twice focuses the existing one (no stacking).
        def count(kind):
            return sum(1 for w in self.root.winfo_children()
                       if isinstance(w, tk.Toplevel) and kind in w.title())
        for opener, kind in ((self.app._open_analytics, "Analytics"),
                             (self.app._open_strategy_chart, "Chart"),
                             (self.app._open_backtest, "Backtest")):
            opener(); self.root.update_idletasks()
            opener(); self.root.update_idletasks()
            self.assertEqual(count(kind), 1, f"{kind} should reuse one window")
        for w in list(self.root.winfo_children()):
            if isinstance(w, tk.Toplevel):
                w.destroy()

    # -- secondary windows open without error ------------------------------
    def test_secondary_windows_open(self):
        for opener in (self.app._open_backtest, self.app._open_strategy_chart,
                       self.app._open_analytics):
            opener()
            self.root.update_idletasks()
        # Clean up any Toplevels these created.
        for w in list(self.root.winfo_children()):
            if isinstance(w, tk.Toplevel):
                try:
                    w.destroy()
                except Exception:  # noqa: BLE001
                    pass

    # -- pair list propagates to every symbol picker -----------------------
    def test_pair_list_propagates_everywhere(self):
        self.app._open_strategy_chart()
        self.app._open_backtest()
        self.root.update_idletasks()
        pairs = ["BTC/USDT", "ETH/USDT", "FOO/USDT", "BAR/USDT"]
        self.app._set_pair_list(pairs)
        self.root.update_idletasks()
        for box in (self.app.symbol_box, self.app.strat_symbols_box,
                    self.app._chart_win._sym_box, self.app._bt_win._sym_box):
            self.assertIn("FOO/USDT", box.cget("values"))
        # picking a pair REPLACES the field and mirrors into Chart + Backtest
        self.app.strat_symbols_var.set("BTC/USDT")
        self.app.strat_symbols_box.set("ETH/USDT")
        self.app._on_strat_symbol_pick()
        self.assertEqual(self.app.strat_symbols_var.get(), "ETH/USDT")
        self.assertEqual(self.app._chart_win.symbol, "ETH/USDT")
        self.assertEqual(self.app._bt_win.symbol, "ETH/USDT")
        for w in list(self.root.winfo_children()):
            if isinstance(w, tk.Toplevel):
                w.destroy()

    def test_exchange_change_refreshes_pairs_keyless(self):
        import time
        import exchange as ex
        orig = ex.fetch_top_pairs
        ex.fetch_top_pairs = lambda eid, mkt, limit=20: [f"{eid.upper()}1/USDT", "BTC/USDT"]
        try:
            self.app.connected = False
            self.app.exchange_var.set("Kraken")
            self.app._refresh_pairs_for_selection()
            for _ in range(40):
                self.root.update()
                time.sleep(0.02)
                if "KRAKEN1/USDT" in self.app.symbol_box.cget("values"):
                    break
            self.assertIn("KRAKEN1/USDT", self.app.symbol_box.cget("values"))
            # while connected the live feed owns it -> no keyless refetch
            self.app.connected = True
            self.app.exchange_var.set("OKX")
            self.app._refresh_pairs_for_selection()
            for _ in range(10):
                self.root.update()
                time.sleep(0.02)
            self.assertNotIn("OKX1/USDT", self.app.symbol_box.cget("values"))
        finally:
            ex.fetch_top_pairs = orig
            self.app.connected = False

    # -- new UX: presets / advanced / preview / dirty / chips --------------
    def test_strategy_preset_applies(self):
        self.app._apply_strat_preset("Conservative")
        p = self.app._strategy_params()
        self.assertEqual(p.ma_confirm, 3)
        self.assertEqual(p.ma_adx_min, 25.0)
        self.app._apply_strat_preset("Aggressive")
        p = self.app._strategy_params()
        self.assertEqual(p.ma_confirm, 1)
        self.assertEqual(p.ma_trend_len, 0)
        self.assertGreater(p.ma_trail_atr, 0.0)
        self.app._apply_strat_preset("Balanced")   # leave defaults for other tests

    def test_active_preset_highlights_and_custom(self):
        import theme
        self.app._apply_strat_preset("Conservative")
        self.assertEqual(self.app._current_strat_preset(), "Conservative")
        self.assertEqual(self.app._strat_preset_btns["Conservative"].cget("bg"),
                         theme.ACCENT)
        self.assertEqual(self.app._strat_preset_btns["Balanced"].cget("bg"),
                         theme.ELEV)
        self.assertIn("Conservative", self.app._strat_preset_lbl.cget("text"))
        # Hand-editing a field drops it to "Custom" with nothing highlighted.
        self.app.strat_ma_confirm_var.set("7")
        self.app._push_strategy()
        self.assertIsNone(self.app._current_strat_preset())
        self.assertIn("Custom", self.app._strat_preset_lbl.cget("text"))
        for b in self.app._strat_preset_btns.values():
            self.assertEqual(b.cget("bg"), theme.ELEV)
        self.app._apply_strat_preset("Balanced")   # restore for other tests

    def test_advanced_section_toggles(self):
        start = self.app._strat_adv_open
        self.app._toggle_strat_advanced()
        self.assertNotEqual(self.app._strat_adv_open, start)
        self.app._toggle_strat_advanced()
        self.assertEqual(self.app._strat_adv_open, start)

    def test_trade_preview_reflects_sizing(self):
        self.app.connected = True
        self.app._last_mark = 50000.0
        self.app.sizing_mode_var.set("Fixed lot (coin)")
        self.app.size_var.set("0.01")
        self.app._update_trade_preview()
        txt = self.app.trade_preview_lbl.cget("text")
        self.assertIn("0.01", txt)
        self.assertIn("$500", txt)          # 0.01 * 50000
        self.app.connected = False
        self.app._update_trade_preview()
        self.assertIn("Connect", self.app.trade_preview_lbl.cget("text"))

    def test_dirty_marker_tracks_changes(self):
        self.app._load_saved_into_ui()      # baseline == clean
        self.assertEqual(self.app.dirty_lbl.cget("text"), "")
        self.app.daily_loss_var.set("999")
        self.app._refresh_dirty()
        self.assertIn("unsaved", self.app.dirty_lbl.cget("text"))
        self.app._set_saved_baseline()      # snapshot current -> clean again
        self.assertEqual(self.app.dirty_lbl.cget("text"), "")

    def test_status_chips_and_steps_exist(self):
        import theme
        for key in ("strategy", "webhook", "relay", "paused"):
            self.assertIn(key, self.app._status_chips)
        for key in ("connect", "license", "running"):
            self.assertIn(key, self.app._step_lbls)
        self.app.connected = False
        self.app._update_status_chips()
        # connect step not done when disconnected
        self.assertNotEqual(self.app._step_lbls["connect"].cget("bg"), theme.GREEN_HL)

    def test_invalid_number_flags_entry(self):
        bad = tk.StringVar(value="abc")     # keep refs: a GC'd Var unsets its Tcl value
        good = tk.StringVar(value="12.5")
        e = self.app._num_entry(self.root, bad)
        self.assertFalse(self.app._validate_num(e))
        self.assertEqual(str(e.cget("style")), "Invalid.TEntry")
        e2 = self.app._num_entry(self.root, good)
        self.assertTrue(self.app._validate_num(e2))
        e.destroy(); e2.destroy()

    # -- reset restores defaults -------------------------------------------
    def test_reset_restores_defaults(self):
        self.app.connected = False
        self.app.sizing_mode_var.set("Fixed lot (coin)")
        self.app.leverage_var.set("0")
        self.app.strat_ma_atrmult_var.set("9")
        self.app._reset_all_defaults()
        s = self.app._collect_settings()
        self.assertEqual(s["sizing_mode"], "risk_stop")
        self.assertEqual(s["leverage"], 5)
        self.assertEqual(self.app.strat_ma_atrmult_var.get(), "2.5")


if __name__ == "__main__":
    unittest.main(verbosity=2)

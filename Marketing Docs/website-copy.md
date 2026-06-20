# Website copy & asset manifest

Ready-to-paste copy and a map of which marketing asset goes where, for updating
**prometheusai.tech**. All assets live in this `Marketing Docs/` folder.

> Keep the disclaimer visible on any page that shows performance numbers:
> *Illustrative demo — not financial advice. Trading is risky; you can lose money.
> Simulated/backtested results don't guarantee future performance.*

---

## Hero

**Headline:** Automated crypto trading — your strategy, your keys.
**Sub:** A desktop bot that runs a proven EMA + RSI strategy on your exchange,
with backtesting, live charts, risk guardrails and Telegram alerts. Non-custodial.

- Hero image: `mockups/browser_main.png` or `website/hero/main.png`
- Hero video (optional): `video/showcase_reel.mp4` or `video/livechart_raw.mp4`
- Open-Graph / link preview: `website/og/main.png`

---

## Features (paste into a features grid — tiles in `features/`)

1. **Built-in strategy, long + short** — EMA20 + RSI-50 crossover with confirmation
   candles, ATR stops and TP1/TP2 — runs on your exchange, no TradingView needed.
2. **One-click presets** — Conservative / Balanced / Aggressive, or open Advanced
   to tune every parameter. (`features/03_live.png`, `raw/strategy` view)
3. **Backtest & optimize** — replay on real history, sweep parameters, walk-forward
   test, and benchmark vs buy-&-hold. (`features/01_backtest.png`, `raw/backtest.png`)
4. **Live chart with signals** — candles, EMAs, volume, RSI and every BUY/SELL/
   scale-out marker. (`raw/chart.png`, `video/livechart_raw.mp4`)
5. **Performance analytics** — win rate, profit factor, per-symbol stats and an
   equity curve. (`features/`, `raw/analytics.png`)
6. **Risk guardrails** — daily loss/profit caps, max exposure, cooldowns,
   loss-streak pause, drawdown halt, trading hours. (`features/02_risk.png`)
7. **Telegram & desktop alerts** — every fill, take-profit and halt. (`features/04_telegram.png`)
8. **Safe by default** — Safe Mode (simulate), exchange testnet, read-only mode,
   and a clear LIVE/TESTNET/SAFE badge.
9. **Non-custodial & encrypted** — API keys stay on your machine, PIN-encrypted.
   (`features/05_your.png`)
10. **Set up in minutes / runs 24/7** — guided setup, settings hub, autostart,
    tray, auto-update. (`features/06_set.png`)

---

## "What's new" (this update — for a changelog / announcement post)

- **Strategy presets** — Conservative / Balanced / Aggressive, one click, with the
  active preset highlighted (and "Custom" when you hand-tune).
- **Friendlier UI** — onboarding checklist + live status chips, hover tooltips
  everywhere, grouped risk guardrails, a manual-order size preview, and an
  "unsaved changes" indicator.
- **New ⚙ Settings hub** — change PIN, lock the app, adjust text size, open the
  data folder / log, and manage startup & update preferences.
- **Backtest matches your wallet** — Spot is long-only, Futures runs long + short.
- **Quality-of-life** — Backtest & Analytics on the main toolbar, a scrollable
  Modes & Risk tab, and a clickable IP list in the licence admin panel.

---

## Asset manifest (paths)

| Use | File(s) |
|---|---|
| Hero (web) | `website/hero/*.png`, `mockups/browser_main.png`, `mockups/laptop_chart.png` |
| Open-Graph / share | `website/og/*.png` |
| Feature grid | `features/01..06_*.png` |
| Screenshot gallery | `raw/main.png`, `raw/chart.png`, `raw/backtest.png`, `raw/analytics.png`, `raw/settings.png` |
| Social posts | `instagram/`, `x-twitter/`, `facebook/`, `linkedin/`, `youtube/` |
| Carousel | `carousel/01..04_*.png` |
| Video / reels | `video/showcase_reel.mp4`, `video/showcase_square.mp4`, `video/livechart_reel.mp4`, `video/livechart_raw.mp4` (+ `.gif` previews) |
| Cover / banner | `cover_banner.png` |
| Higher-numbers variant | `flashy/` (stronger disclaimer — organic posts, not paid ads) |

# Marketing Docs — Prometheus AI Crypto Bot

Ready-to-post screenshots of every app window, sized for each platform and
branded (wordmark + caption + disclaimer). Regenerate any time with the
generator described at the bottom.

> ⚠️ **Disclaimer (read before publishing).** All numbers shown in these images
> are an **illustrative demo on simulated data** — not a real trading account.
> They are **not financial advice**. Trading crypto is risky and you can lose
> money. Backtested / simulated results do **not** guarantee future performance.
> Keep this disclaimer visible wherever you publish these images (it is already
> baked into every branded image).

## What's inside

| Folder | Size (px) | Use for |
|---|---|---|
| `raw/` | native HD | clean window captures (docs, website galleries, your own edits) |
| `website/hero/` | 1920×1080 | website hero / banners |
| `website/og/` | 1200×630 | Open-Graph link preview (the image shown when a link is shared) |
| `instagram/square/` | 1080×1080 | Instagram feed post |
| `instagram/story/` | 1080×1920 | Instagram / Facebook story & reel cover |
| `x-twitter/` | 1600×900 | X (Twitter) post |
| `facebook/` | 1200×630 | Facebook post / link |
| `linkedin/` | 1200×627 | LinkedIn post |
| `youtube/` | 1280×720 | YouTube thumbnail |
| `cover_banner.png` | 1600×500 | generic cover / email header |
| `carousel/` | 1080×1080 ×4 | Instagram/X swipe carousel: hook → strategy → backtest → results |
| `mockups/` | 1920×1080 | browser-framed + laptop hero shots for the website |
| `features/` | 1080×1080 ×6 | feature highlight tiles for a website features section |
| `flashy/` | hero + square + X | higher-numbers variant with a **stronger** disclaimer (see note) |
| `video/` | MP4 + GIF | ~7s looping showcase reel (hook → main → chart → backtest → analytics) |

### `video/` — motion assets
- `showcase_reel.*` — **1080×1920** (Instagram/TikTok reels & stories, YouTube Shorts)
- `showcase_square.*` — **1080×1080** (feed video)
- Both as **`.mp4`** (use this for ads/reels — H.264, ~1 MB) and **`.gif`** (quick web/preview loops).
- ~7-second seamless loop with crossfades and a "Free trial →" call-to-action on the last scene.

### ⚠️ About the `flashy/` set
A second, higher-numbers variant (large gains, ~80%+ win) for maximum
scroll-stopping appeal. It carries a **stronger** disclaimer baked in
("Hypothetical simulated results… most traders lose money…"). Use with care:
big-gain creatives are more likely to be rejected by Meta/Google ad review.
The default credible set (root platform folders) is recommended for paid ads.

Each platform folder contains one image per window:

- **main** — main dashboard (live, positions in profit)
- **chart** — strategy chart with BUY/SELL/scale-out markers, EMA/Trend/RSI/volume
- **backtest** — backtest results + equity curve
- **analytics** — performance dashboard + equity curve
- **settings** — the ⚙ settings hub

## The demo data

Chosen to be **impressive but credible** (ad-policy friendly):

- Open Positions all in profit (BTC/ETH/SOL long, BNB short), header balance up.
- Backtest: ~**+11%**, profit factor **~2.0**, low drawdown — a believable edge.
- Analytics: high win rate with a smooth rising equity curve.

If you want different numbers (more conservative, or flashier with a stronger
disclaimer), re-run the generator with adjusted values.

## How to regenerate

These are produced headlessly (no real exchange) by a script that renders each
Tk window at high-DPI with demo data, then composites the branded canvases with
ImageMagick. Requirements: `python3`, `xvfb`, `imagemagick`, DejaVu fonts.

Ask the maintainer (or Claude) to re-run the marketing generator; it overwrites
this folder in place.

# Gold Scalpers — Complete Launch Package

Everything you need to launch and sell the **Gold Scalpers EA** — a fully automated MetaTrader 5 Expert Advisor for XAUUSD (Gold).

## 📁 Folder Structure

```
GoldScalpers/
│
├── README.md                          ← You are here
├── LAUNCH-CHECKLIST.md                ← Step-by-step launch plan
├── robots.txt                         ← Upload to domain root (AI crawler config)
├── sitemap.xml                        ← Upload to domain root (SEO)
│
├── index.html                         ← Main sales landing page
├── thank-you.html                     ← Post-purchase confirmation page
├── affiliate.html                     ← Affiliate program recruitment page
├── bio.html                           ← Mobile-first link-in-bio page
├── og-image.html                      ← Social share preview (1200x630)
│
├── assets/
│   └── logos/
│       ├── logo.svg                   ← Animated sniper crosshair
│       ├── logo-static.svg            ← Static version (favicon, app icons)
│       └── logo-wordmark.svg          ← Logo + text (header, email signature)
│
├── expert-advisor/
│   ├── gold-scalpers-ea.ex5           ← Main EA (install into MT5 Experts folder)
│   └── gold-scalpers-ea-manual.md     ← Inputs reference + install guide
│
├── docs/
│   ├── 01-marketplace-listing.md      ← Sales page / listing copy
│   ├── 02-pricing-strategy.md         ← Tier structure + psychology
│   ├── 03-content-calendar-30day.md   ← 30 days of social posts
│   ├── 04-ad-copy.md                  ← Twitter/IG/TikTok ads
│   ├── 05-hashtag-strategy.md         ← Platform-specific hashtag sets
│   ├── 06-email-welcome-sequence.md   ← 5-email onboarding sequence
│   ├── 07-support-faq.md              ← Customer support cheat sheet
│   └── 08-legal-disclaimer.md         ← Terms / Privacy / Refund / Disclaimer
│
└── customer-onboarding/
    ├── telegram-setup-guide.md        ← Send to Pro/Elite buyers
    └── ea-installation-guide.md       ← MT5 install + licence activation guide
```

## 🚀 Quick Start

1. **Preview the site** — double-click `index.html` to open in browser
2. **Customize** — search-replace `goldscalpers.com` with your real domain
3. **Read** `LAUNCH-CHECKLIST.md` for the step-by-step deployment plan
4. **Test** the EA — copy `expert-advisor/gold-scalpers-ea.ex5` into your MT5 `MQL5/Experts` folder, then attach it to an XAUUSD M1 chart in the Strategy Tester

## 🎯 What You're Selling

**Product**: Gold Scalpers EA — a fully automated **MetaTrader 5 Expert Advisor**, a pure **XAUUSD (Gold) M1 scalper** (also runs on major FX pairs). It enters when the **Slope Direction Line crosses the 50 EMA**, confirmed by the **ZeroLag MACD**, manages the trade with a **protective step-stop** and a **quick scalping take-profit** plus an **optional AutoScaler grid**. Includes a **live on-chart dashboard**, **BUY/SELL signal arrows**, **Telegram / mobile / terminal alerts**, and an **account-locked licence** (allow-list at `goldscalpers.com/licenses.txt`).

**Deliverable**: Buyers receive the `.ex5` EA file plus a licence key, installed directly in MetaTrader 5 (drop the `.ex5` into `MQL5/Experts` and activate the account-locked licence). No TradingView invite required.

**Pricing**:
- Free tier: Public Telegram channel signals
- Pro Lifetime: $220 (intro) → $297 (regular)
- Elite: $497 (Pro + 1-on-1 setup call)

**Year 1 revenue target**: $30,000 - $80,000 (solo creator with content engine)

## 🛠 Tech Stack

| Layer | Recommended Tool | Cost |
|---|---|---|
| Domain | Namecheap | $12/year |
| Hosting | Cloudflare Pages | Free |
| Payment | Stripe (or Gumroad backup) | 3% per sale |
| Email | Beehiiv (free up to 2,500 subs) | $0-39/mo |
| Analytics | Plausible.io | $9/mo |
| Customer support | Notion + Email | $0-12/mo |

## ⚠️ Critical Pre-Launch Steps

Before opening checkout:

1. **Forward-test the EA on a demo account for 30 days** — track every trade honestly
2. **Confirm net positive R after 30+ closed trades** — if negative, do NOT launch
3. **Build 90+ engaged Telegram subscribers** via free signal posts before paid launch
4. **Ship the legal pack** — Risk Disclaimer must be on every page

Skipping these steps is the #1 reason EA launches do under $1K.

## 📊 Reading the Files

- `*.html` — open in any browser to preview
- `*.svg` — open in browser or Inkscape to view; use as-is on your site
- `*.ex5` — the compiled EA; copy into your MT5 `MQL5/Experts` folder, then attach to an XAUUSD chart
- `*.md` — open in any text editor (VS Code recommended for syntax highlighting)

## 🔗 What Links to What

```
index.html
  ├─→ #pricing (anchor) → Stripe checkout
  ├─→ thank-you.html (after Stripe success)
  ├─→ affiliate.html (footer + nav)
  └─→ bio.html (mobile bio link variant)

bio.html (linked from Instagram/TikTok bio)
  ├─→ index.html (main site)
  ├─→ affiliate.html
  └─→ Telegram channel + socials

thank-you.html (Stripe success URL)
  ├─→ EA download (.ex5) + licence key
  ├─→ customer-onboarding/ea-installation-guide.md (PDF)
  ├─→ customer-onboarding/telegram-setup-guide.md (PDF)
  └─→ Elite upsell ($300)
```

## 📝 License Notes

This package is your launch toolkit. The Gold Scalpers EA (MQL5) source and compiled `.ex5` are yours to sell.

Attribution to libraries used:
- MQL5 / MetaTrader 5 (MetaQuotes) — Expert Advisor platform
- Inline SVG (no external dependencies)
- No JavaScript frameworks — vanilla JS only
- No external CSS frameworks — vanilla CSS

## 💬 Questions?

Refer to `LAUNCH-CHECKLIST.md` for the full deployment timeline.

For trading-specific questions about the EA's behavior, refer to `docs/07-support-faq.md`.

---

**Built with ❤️ for traders who refuse to miss reversals.**

🎯 Gold Scalpers · 2026

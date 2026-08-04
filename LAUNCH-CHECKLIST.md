# 🚀 Gold Scalpers — Launch Checklist

A step-by-step guide from "I have the package" to "first paying customer."

**Estimated total time:** 2-3 weeks of part-time work, 4-6 weeks of forward-testing.

---

## PHASE 1 — Validate (Week 1-4)

This is the most important phase. Skip it and you'll be refunding sales by week 2.

- [ ] **Add the indicator to your TradingView account**
  - Open `pine-script/nexttrade-gold-sniper.pine`
  - Copy contents → paste into TradingView Pine Editor
  - Save → Add to chart

- [ ] **Set up a demo account at your broker** (e.g. IC Markets, Pepperstone)

- [ ] **Forward-test on 1M timeframe for 30 days minimum**
  - Track every signal in a Google Sheet (Time, Pair, Entry, SL, TP, Outcome, R)
  - Take screenshots of wins AND losses (transparency = trust later)

- [ ] **Calculate your real edge after 50+ closed trades**
  - Win rate × Avg Win > Loss rate × Avg Loss?
  - Total R after 50 trades positive?

- [ ] **STOP if results are negative.** Do NOT launch a losing strategy.
  - Either tune the indicator
  - Or pivot to different pairs/timeframes
  - Or be honest and don't sell it

---

## PHASE 2 — Infrastructure (Week 3-4, parallel with validation)

- [ ] **Buy domain** — `namecheap.com` → search `goldscalpers.com` ($12/year)
- [ ] **Sign up for Cloudflare Pages** (free hosting)
- [ ] **Sign up for Stripe** — set up business profile, identity verify
  - Backup: also create Gumroad account (Stripe sometimes blocks "trading" products)
- [ ] **Create Stripe products:**
  - "Pro Lifetime" — $220 (or $220 intro / $297 regular)
  - "Elite" — $497
- [ ] **Sign up for email tool** (Beehiiv free tier — `beehiiv.com`)
- [ ] **Create Telegram bot** via @BotFather for customer support
- [ ] **Create public Telegram channel** — `@GoldScalpers` (free)
- [ ] **Create private Telegram channel** for paid customers

---

## PHASE 3 — Site Deployment (Week 4)

- [ ] **Customize all files** — search-replace these placeholders:
  - `goldscalpers.com` → your real domain
  - `support@goldscalpers.com` → your real email
  - `t.me/GoldScalpers` → your real channel link
  - `2384.50` etc. → your real demo numbers
  - `82%` → your real forward-test win rate
  - `27 spots remaining` → your real scarcity number

- [ ] **Convert OG image to PNG**
  - Open `og-image.html` in Chrome
  - F12 → Ctrl+Shift+M → set 1200x630
  - Right-click card → Capture node screenshot
  - Save as `og-image.png` in folder root

- [ ] **Deploy to Cloudflare Pages**
  - Drag the entire `GoldScalpers` folder into Cloudflare Pages dashboard
  - Set custom domain to your purchased domain

- [ ] **Verify routes work:**
  - `/` → index.html
  - `/affiliate` → affiliate.html
  - `/bio` → bio.html
  - `/thank-you` → thank-you.html
  - `/og-image.png` → loads the social image
  - `/robots.txt` → loads as plain text

- [ ] **Set Stripe success URL** to `https://goldscalpers.com/thank-you?session_id={CHECKOUT_SESSION_ID}`

- [ ] **Submit sitemap** to Google Search Console + Bing Webmaster Tools

- [ ] **Test on mobile** — open `bio.html` on your phone, verify all links work

---

## PHASE 4 — Pre-Launch Audience (Week 5-6)

You need an audience BEFORE you sell. Otherwise you're shouting into the void.

- [ ] **Post 3-5 free signals/week in public Telegram channel**
  - Use the format in `docs/03-content-calendar-30day.md`
  - Always post live, real-time — never delayed
  - Always post the result update 1-3 hours later

- [ ] **Start posting on Twitter/X**
  - Use `docs/03-content-calendar-30day.md` (Day 1 onwards)
  - 1 post per day minimum
  - Pin your Telegram channel link in your bio

- [ ] **Start posting on Instagram**
  - 3-4 posts per week (mix of educational + signal results)
  - Use `docs/04-ad-copy.md` formats
  - Use hashtag sets from `docs/05-hashtag-strategy.md` (rotate sets)

- [ ] **Make 5 short YouTube videos** (use scripts from earlier conversation):
  1. "How Gold Scalpers finds reversals" (3 min)
  2. "Live Gold trade walkthrough" (5 min)
  3. "Setup tutorial: 5 minutes to your first alert" (3 min)
  4. "Telegram bot setup guide" (4 min)
  5. "Auto-execution with PineConnector" (8 min)

- [ ] **Build to 90+ engaged Telegram subscribers** before opening checkout

---

## PHASE 5 — Soft Launch (Week 7)

- [ ] **Announce in Telegram channel** — "Indicator launching tomorrow at $220 — first 100 buyers only"

- [ ] **Send first email broadcast** to your list (if you've collected any)

- [ ] **Open the cart at $220 intro pricing**

- [ ] **Goal:** 10-20 sales in first week from existing audience

---

## PHASE 6 — Scale (Week 8+)

- [ ] **Run ads** at $20-30/day total budget
  - Twitter: target followers of TradingView, ICmarkets, PineConnector
  - Instagram: lookalike of your existing followers, "trading" interests

- [ ] **Cross-promote in trading communities** (carefully — most ban paid promo)
  - Reddit: r/Forex, r/Daytrading, r/algotrading (lead with value, not pitch)
  - Discord: DM trading server admins for partnership posts

- [ ] **Open affiliate program** — promote `affiliate.html` to creators

- [ ] **Raise price to $297** after first 100 buyers

- [ ] **Goal:** 50+ buyers in first 30 days = $9,850 revenue

---

## ⚠️ Critical Things People Mess Up

### Don't
- ❌ Launch without forward-testing first
- ❌ Promise "guaranteed returns" or "90% win rate"
- ❌ Hide losses from your public Telegram
- ❌ Skip the legal disclaimer pages
- ❌ Go past 30-day refund without auto-revoking TradingView access
- ❌ Use "get rich quick" language (Stripe will ban you)

### Do
- ✅ Forward-test publicly with full transparency
- ✅ Track your real metrics in Google Sheets
- ✅ Honor every refund request without arguing
- ✅ Update buyers monthly with new features and roadmap
- ✅ Build YouTube long-tail content for organic SEO
- ✅ Reply to every customer DM personally for first 100 customers

---

## 📊 Success Metrics

| Week | Target |
|---|---|
| 1-4 | 50+ closed trades on demo, net positive R |
| 5 | 50+ Telegram subscribers |
| 6 | 90+ subscribers, 5 YouTube videos live |
| 7 | First 10-20 paying customers |
| 8 | 30-50 customers, affiliate program live |
| 12 | 100+ customers (raise price), 500+ subscribers |
| 24 | 300+ customers (~$60K-$90K revenue) |

---

## 🆘 If You're Stuck

| Problem | Solution |
|---|---|
| Indicator losing on demo | Tune presets in `nexttrade-gold-sniper.pine` or pivot pairs |
| No Telegram subscribers | Post results in r/Forex with disclaimer (no spam tactics) |
| Stripe rejected business | Switch to Gumroad or Whop |
| Site not loading | Check Cloudflare DNS settings |
| Pine Script syntax error | Refer to `docs/07-support-faq.md` |
| Customer wants refund | Honor it within 60 days, no questions |

---

**Build slowly. Ship honestly. Scale only on what's working.**

🎯 Gold Scalpers · 2026

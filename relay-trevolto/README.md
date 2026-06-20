# Trevolto Relay (shared-hosting, PHP + MySQL)

Broadcasts signals from **your** TradingView account to **all** customers' apps.
Your private indicator stays on your chart; customers only need the app + a
licence token (no TradingView, no indicator, no ngrok).

```
YOUR TradingView alert ──POST──► hooks.trevolto.com/hook.php?key=SELLER_KEY   (stores signal)
Customer app ──GET poll.php?token=LICENCE every ~1.5s──►                           (gets new signals → trades)
Customer app ──GET verify.php?token=LICENCE on startup──►                          (gates the built-in strategy)
```

The same licence token also gates the app's **built-in strategy** (the bot's own
copy of the indicator): the app calls `verify.php` before running it, so a
revoked/expired token disables both the cloud feed *and* the built-in engine.

## Upload (one-time)

1. **Create a MySQL database + user** in cPanel; note the name/user/password.
2. **Edit `config.php`** — fill `DB_*`, and set long random `SELLER_KEY` and
   `ADMIN_KEY`.
3. **Point the subdomain** `hooks.trevolto.com` at a folder and **upload
   all files from this `relay/` folder** there (File Manager or FTP). Tables are
   created automatically on first request — no SQL import needed.
4. Test in a browser: `https://hooks.trevolto.com/` should print
   *"Trevolto relay is running."*

> Make sure the subdomain serves over **HTTPS** (free Let's Encrypt in cPanel) —
> TradingView requires `https://` webhook URLs.

## Connect your TradingView alert

- Indicator **Alert Format = "Webhook JSON"**.
- Alert **Condition = "Any alert() function call"** (not "Trevolto BUY").
- **Webhook URL:** `https://hooks.trevolto.com/hook.php?key=YOUR_SELLER_KEY`

## Issue customer licences

```
Create:   https://hooks.trevolto.com/admin.php?key=ADMIN_KEY&action=create&label=John&days=30
List:     https://hooks.trevolto.com/admin.php?key=ADMIN_KEY&action=list
Revoke:   https://hooks.trevolto.com/admin.php?key=ADMIN_KEY&action=revoke&token=XXXX
Extend:   https://hooks.trevolto.com/admin.php?key=ADMIN_KEY&action=extend&token=XXXX&days=30
```

`create` returns a token like `a1b2c3...` — give it to the customer. In the app
they enter it under **Webhook & Alerts → Cloud signals** (Relay URL is
pre-filled to `https://hooks.trevolto.com/poll.php`). `days=0` (or omit) =
never expires; `revoke` instantly cuts off a customer.

## Endpoints

| File | Who calls it | Purpose |
|------|--------------|---------|
| `hook.php?key=…` | your TradingView | store an incoming signal |
| `poll.php?token=…` | each customer app | fetch new signals since last poll |
| `verify.php?token=…` | each customer app | validate a licence (read-only) to gate the built-in strategy |
| `admin.php?key=…` | you | create / list / revoke / extend tokens |
| `index.php` | — | "running" status page |

## Security notes
- `SELLER_KEY` stops anyone but you injecting signals; `ADMIN_KEY` protects
  token management — keep both secret and long.
- Tokens are passed over HTTPS query strings; revoke any that leak.
- New tokens start from "now" — a customer never replays old signals.
- Old signals auto-purge after `SIGNAL_TTL` (24h).

# HillSignal mobile (Android first)

The Expo / React Native app for HillSignal, a tracker for congressional stock trades with AI features and paid plans.
The backend contract lives in [`../docs/API.md`](../docs/API.md). The app follows that contract.

- Expo SDK 57 (`expo@57.0.27`, React Native 0.86, React 19.2), TypeScript strict, expo-router (file-based routes, tabs)
- @tanstack/react-query (infinite lists, caching), expo-secure-store (JWT)
- Google Play Billing through **expo-iap** (the Expo build of react-native-iap / OpenIAP), wrapped in `src/billing/`
- expo-notifications for trade alerts, expo-sharing + expo-file-system for the premium CSV export

## Run in development

```bash
npm install
cp .env.example .env          # set EXPO_PUBLIC_API_URL if your backend isn't on the default
npx expo start                # Metro dev server
```

**Expo Go can't do in-app purchases or (on Android) remote push.** The app still runs in Expo Go: the paywall says
"billing is only available in a development/production build", and turning on alerts shows an explanation. Nothing crashes.
To test billing and push, use a development build:

```bash
npx expo run:android                                 # local build (needs Android SDK + JDK 17)
# or in the cloud:
npx eas build -p android --profile development       # installable dev-client APK
npx expo start --dev-client
```

Billing only works for an app installed from a Play track (internal testing is fine) whose package name matches the Play
Console listing, signed in with a Google account added as a **license tester**.

### API URL

| Where | Value |
|---|---|
| Android emulator → backend on your machine | `http://10.0.2.2:4000` (the default) |
| Physical device on the same Wi-Fi | `http://<your-LAN-IP>:4000` |
| Preview / production builds | set in `eas.json` → `build.<profile>.env.EXPO_PUBLIC_API_URL` (placeholder hosts now) |

The app calls `${EXPO_PUBLIC_API_URL}/api/v1/...`. `EXPO_PUBLIC_*` values are baked in at bundle time, so restart
Metro with `-c` after you change them. Plain `http://` works in debug builds. Release builds need `https://`.

### Environment variables

| Variable | Needed for | Notes |
|---|---|---|
| `EXPO_PUBLIC_API_URL` | everything | Backend base URL without `/api/v1`. Default `http://10.0.2.2:4000`. |
| `EAS_PROJECT_ID` | push notifications | From `eas init`. Read in `app.config.ts`, needed by `getExpoPushTokenAsync`. |
| `APP_VARIANT` | build name suffix | `development` / `preview` / `production`. The eas.json profiles set it. |

## Rebranding

The app name, URL scheme, Android package and brand colors all live in **`src/brand.json`**. `app.config.ts`, `src/config.ts`
and `src/theme.ts` read from it. Legal and support URLs (placeholders for now), product ids and the AI fallback disclaimer
are in `src/config.ts`. Palettes (dark first, light follows the system setting) are in `src/theme.ts`.

## Build for the Play Store

```bash
npm i -g eas-cli
eas login
eas init                                     # creates the EAS project; put the id in EAS_PROJECT_ID
eas build -p android --profile production    # signed .aab (app-bundle), versionCode auto-increments
eas submit -p android --profile production   # uploads to the internal track as a draft
```

`eas submit` needs a Google Play service-account JSON at `./play-service-account.json` (git-ignored). The very first
upload of a new app usually has to be done by hand in Play Console. Change `androidPackage` in `src/brand.json` **before**
that first upload, because the package id can't be changed afterwards.

Profiles (`eas.json`):

- `development`: dev client, internal APK
- `preview`: internal APK pointed at staging
- `production`: `app-bundle` for Play, `autoIncrement` versionCode

### Create the subscriptions in Play Console

The backend expects these ids. The app reads them from `GET /plans` → `googleProductIds`, and falls back to `src/config.ts`.

| Product id (subscription) | Base plans | Notes |
|---|---|---|
| `hillsignal_pro` | `monthly` (auto-renewing, 1 month), `yearly` (auto-renewing, 1 year) | |
| `hillsignal_premium` | `monthly`, `yearly` | |

1. Upload any build (internal testing track) that includes the billing library. Play only unlocks the subscriptions UI after that.
2. **Monetize → Products → Subscriptions → Create subscription**. Enter the product id exactly as above and a name.
3. In each subscription, **Add base plan**: id `monthly`, auto-renewing, billing period 1 month. Then `yearly`, 1 year.
   Set prices, then **Activate** each base plan.
4. Optional: add offers (for example a free trial) to a base plan. The paywall shows "N-day free trial" when Play reports an
   eligible offer.
5. **Setup → License testing**: add the tester Google accounts.
6. Backend side: Real-time developer notifications → a Pub/Sub topic pushing to `POST /api/v1/billing/google/rtdn`,
   plus a service account with access to the Android Publisher API for `/billing/google/verify`.

Purchase flow (`src/billing/BillingContext.tsx`): fetch products for the ids from `/plans`, then `requestPurchase` with the
chosen base plan's offer token (and `obfuscatedAccountId = user.id`), then `POST /billing/google/verify {productId, purchaseToken}`.
`finishTransaction` (acknowledge) runs **only after the server has verified the purchase**. If verification fails, the
purchase stays unacknowledged. The app retries it on the next launch, and Restore purchases also re-verifies. Play refunds
purchases that stay unacknowledged for 3 days.

## Project layout

```
app/                     expo-router routes
  _layout.tsx            providers (React Query, Auth, Billing), root stack, paywall-on-402, push tap routing
  index.tsx              gate: onboarding (once) → sign in → tabs
  onboarding.tsx         3 swipeable slides
  auth/sign-in|sign-up   includes "Browse without account"
  (tabs)/                index (Home), trades, ai, watchlist, account
  trade|politician|issuer/[id].tsx   detail screens
  paywall.tsx            modal: Free / Pro / Premium, monthly/yearly, Play prices, fine print
src/
  brand.json, config.ts, theme.ts
  api/                   types.ts (from API.md), client.ts (fetch + ApiError), endpoints.ts, hooks.ts, queryClient.ts
  auth/                  AuthContext (JWT in SecureStore, guest/free entitlements, delete account)
  billing/               the only code that touches expo-iap (native.ts lazily requires it)
  notifications/         permission + token registration, tap → trade
  components/            small reusable UI pieces
  lib/                   formatting, storage, navigation helpers, CSV export
```

## How the app handles API errors

| Response | Behavior |
|---|---|
| 401 on an authenticated request | Signs out (token cleared, cache cleared). A 401 on login shows "wrong email or password". |
| 402 `upgrade_required` from an action (follow beyond the limit, AI, export) | Opens the paywall, highlighting `requiredPlan`. |
| 402 from a background query (insights) | Shows a locked card with an Unlock button. The app doesn't open a modal on its own. |
| 429 `quota_exceeded` / `rate_limited` | Inline notice. Remaining AI searches are shown under the search box. |
| 422 `ai_declined` | "Couldn't help with that, try rephrasing." |
| 503 `ai_unavailable` / `billing_unavailable` | Inline notice. A purchase stays unacknowledged, so it can be retried. |

Every AI output (search, chat, insights) shows the `disclaimer` the server returns, or a built-in one if the server sends none.

## Known limitations

- Turning alerts off, or signing out, unregisters the device (`DELETE /devices`) so the server stops pushing to it.
- Delete account does not cancel a Google Play subscription. The confirm dialog tells the user to cancel it in Play first.
- iOS config is present but untested. StoreKit products and APNs are not set up.
- The icons and splash image in `assets/` are generated placeholders.

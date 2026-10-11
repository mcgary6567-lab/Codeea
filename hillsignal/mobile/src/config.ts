import { BRAND } from './brand';

/** Base URL of the backend, without the /api/v1 suffix. */
const rawApiUrl = process.env.EXPO_PUBLIC_API_URL ?? 'http://10.0.2.2:4000';

export const config = {
  APP_NAME: BRAND.appName,
  TAGLINE: BRAND.tagline,
  ANDROID_PACKAGE: BRAND.androidPackage,
  API_URL: rawApiUrl.replace(/\/+$/, ''),
  API_BASE: `${rawApiUrl.replace(/\/+$/, '')}/api/v1`,

  // Placeholder legal URLs: replace before publishing.
  PRIVACY_URL: 'https://hillsignal.example/privacy',
  TERMS_URL: 'https://hillsignal.example/terms',
  SUPPORT_EMAIL: 'support@hillsignal.example',

  /** Play Console subscription product ids (must match the backend). */
  PRODUCT_IDS: { pro: 'hillsignal_pro', premium: 'hillsignal_premium' },
  /** Play Console base plan ids, shared by both products. */
  BASE_PLANS: { monthly: 'monthly', yearly: 'yearly' },

  /** Shown under AI output when the API response has no disclaimer of its own. */
  AI_DISCLAIMER:
    'AI-generated from public disclosures. May be incomplete or wrong. Not financial, legal or investment advice.',

  PAGE_SIZE: 25,
  CHAT_MAX_MESSAGES: 20,
} as const;

export function playManageSubscriptionUrl(sku?: string | null, packageName: string = config.ANDROID_PACKAGE) {
  const base = 'https://play.google.com/store/account/subscriptions';
  if (!sku) return base;
  return `${base}?sku=${encodeURIComponent(sku)}&package=${encodeURIComponent(packageName)}`;
}

import type { Entitlements } from '@/api/types';

/**
 * Used for guests until GET /plans has loaded (then the server's "free" plan wins).
 * Real limits always come from the server; this only drives what the UI shows as locked.
 */
export const FREE_FALLBACK: Entitlements = {
  plan: 'free',
  delayDays: 0,
  historyDays: null,
  watchlistLimit: 3,
  aiSearchPerDay: 3,
  aiInsights: false,
  aiChat: false,
  alerts: false,
  csvExport: false,
};

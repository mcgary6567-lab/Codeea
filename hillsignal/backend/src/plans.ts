import { config } from "./config.js";

export type Plan = "free" | "pro" | "premium";

export interface Entitlements {
  plan: Plan;
  delayDays: number;
  historyDays: number | null;
  watchlistLimit: number;
  aiSearchPerDay: number;
  aiInsights: boolean;
  aiChat: boolean;
  alerts: boolean;
  csvExport: boolean;
}

const PLAN_RANK: Record<Plan, number> = { free: 0, pro: 1, premium: 2 };

export function planAtLeast(plan: Plan, required: Plan): boolean {
  return PLAN_RANK[plan] >= PLAN_RANK[required];
}

export function higherPlan(a: Plan, b: Plan): Plan {
  return PLAN_RANK[a] >= PLAN_RANK[b] ? a : b;
}

export function entitlementsFor(plan: Plan): Entitlements {
  switch (plan) {
    case "free":
      return {
        plan,
        delayDays: config.FREE_DELAY_DAYS,
        historyDays: config.FREE_HISTORY_DAYS,
        watchlistLimit: 3,
        aiSearchPerDay: 3,
        aiInsights: false,
        aiChat: false,
        alerts: false,
        csvExport: false,
      };
    case "pro":
      return {
        plan,
        delayDays: 0,
        historyDays: null,
        watchlistLimit: 50,
        aiSearchPerDay: 50,
        aiInsights: true,
        aiChat: false,
        alerts: true,
        csvExport: false,
      };
    case "premium":
      return {
        plan,
        delayDays: 0,
        historyDays: null,
        watchlistLimit: 500,
        aiSearchPerDay: 500,
        aiInsights: true,
        aiChat: true,
        alerts: true,
        csvExport: true,
      };
  }
}

// Google Play subscription product ids → plan. Each product has "monthly" and
// "yearly" base plans configured in Play Console.
export const GOOGLE_PRODUCTS: Record<string, Exclude<Plan, "free">> = {
  hillsignal_pro: "pro",
  hillsignal_premium: "premium",
};

export const PLAN_CATALOG = [
  {
    id: "free" as const,
    name: "Free",
    tagline: "Follow the basics",
    features: [
      `Trades delayed ${config.FREE_DELAY_DAYS} days`,
      `${Math.round(config.FREE_HISTORY_DAYS / 30)} months of history`,
      "Follow up to 3 politicians or stocks",
      "3 AI searches per day",
    ],
  },
  {
    id: "pro" as const,
    name: "Pro",
    tagline: "Real-time signals",
    features: [
      "Trades the moment they're disclosed",
      "Full trade history",
      "Push alerts for people and stocks you follow",
      "AI insights on every trade and politician",
      "50 AI searches per day",
    ],
  },
  {
    id: "premium" as const,
    name: "Premium",
    tagline: "Your AI research analyst",
    features: [
      "Everything in Pro",
      "Ask HillSignal: AI chat over all disclosures",
      "500 AI searches per day",
      "CSV export",
    ],
  },
];

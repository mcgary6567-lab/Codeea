import { config } from '@/config';
import type { Plan, PlanInfo } from '@/api/types';
import type { StoreSubscription } from './native';

export type BillingPeriod = 'monthly' | 'yearly';

/** A purchasable base plan of a Play subscription, with its localized price. */
export interface StoreOffer {
  productId: string;
  basePlanId: string;
  offerToken: string;
  /** Recurring localized price from Google Play, e.g. "$9.99". */
  price: string;
  /** e.g. "7-day free trial" when the offer starts with a free phase. */
  introLabel: string | null;
}

/**
 * GET /plans returns googleProductIds. Accept both "hillsignal_pro" and
 * "hillsignal_pro:monthly" styles and return the unique Play product ids.
 */
export function productIdsFromPlans(plans: PlanInfo[] | undefined): string[] {
  const ids = new Set<string>();
  for (const p of plans ?? []) for (const raw of p.googleProductIds) ids.add(raw.split(':')[0] ?? raw);
  if (ids.size === 0) Object.values(config.PRODUCT_IDS).forEach((id) => ids.add(id));
  return [...ids];
}

export function productIdForPlan(plan: Plan, plans: PlanInfo[] | undefined): string | null {
  const info = plans?.find((p) => p.id === plan);
  const raw = info?.googleProductIds[0];
  if (raw) return raw.split(':')[0] ?? raw;
  return plan === 'free' ? null : config.PRODUCT_IDS[plan];
}

function describeIntro(period: string, micros: string): string | null {
  if (micros !== '0') return null;
  // ISO-8601 period like P7D / P1M
  const m = /^P(\d+)([DWMY])$/.exec(period);
  if (!m) return 'Free trial';
  const unit = { D: 'day', W: 'week', M: 'month', Y: 'year' }[m[2] as 'D' | 'W' | 'M' | 'Y'];
  return `${m[1]}-${unit} free trial`;
}

/** Extracts base-plan offers (monthly / yearly) from a fetched Play subscription. */
export function offersFromProduct(product: StoreSubscription): StoreOffer[] {
  if (product.platform !== 'android') return [];
  const byBasePlan = new Map<string, StoreOffer>();
  for (const offer of product.subscriptionOffers ?? []) {
    const basePlanId = offer.basePlanIdAndroid;
    const offerToken = offer.offerTokenAndroid;
    if (!basePlanId || !offerToken) continue;
    const phases = offer.pricingPhasesAndroid?.pricingPhaseList ?? [];
    const recurring = phases[phases.length - 1];
    const first = phases[0];
    const candidate: StoreOffer = {
      productId: product.id,
      basePlanId,
      offerToken,
      price: recurring?.formattedPrice ?? offer.displayPrice,
      introLabel:
        first && phases.length > 1 ? describeIntro(first.billingPeriod, first.priceAmountMicros) : null,
    };
    const existing = byBasePlan.get(basePlanId);
    // Prefer an offer with an intro (trial) if Play says the user is eligible; else the plain base plan.
    if (!existing || (!existing.introLabel && candidate.introLabel)) byBasePlan.set(basePlanId, candidate);
  }
  return [...byBasePlan.values()];
}

export function findOffer(offers: StoreOffer[], productId: string, period: BillingPeriod): StoreOffer | undefined {
  const basePlanId = config.BASE_PLANS[period];
  return offers.find((o) => o.productId === productId && o.basePlanId === basePlanId);
}

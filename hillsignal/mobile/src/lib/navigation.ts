import { router } from 'expo-router';
import type { Plan } from '@/api/types';

export function openPaywall(requiredPlan?: Plan, reason?: string) {
  router.push({ pathname: '/paywall', params: { ...(requiredPlan ? { plan: requiredPlan } : {}), ...(reason ? { reason } : {}) } });
}

export const openTrade = (id: string) => router.push({ pathname: '/trade/[id]', params: { id } });
export const openPolitician = (id: string) => router.push({ pathname: '/politician/[id]', params: { id } });
export const openIssuer = (id: string) => router.push({ pathname: '/issuer/[id]', params: { id } });
export const openSignIn = () => router.push('/auth/sign-in');

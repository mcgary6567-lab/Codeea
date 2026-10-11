import { router, useLocalSearchParams } from 'expo-router';
import { useState } from 'react';
import { Linking, Pressable, ScrollView, View } from 'react-native';
import { usePlans } from '@/api/hooks';
import type { Plan } from '@/api/types';
import { useAuth } from '@/auth/AuthContext';
import { billingUnavailableMessage, findOffer, productIdForPlan, useBilling, type BillingPeriod } from '@/billing';
import { Notice } from '@/components/Notice';
import { PlanCard } from '@/components/PlanCard';
import { SegmentedControl } from '@/components/SegmentedControl';
import { CardSkeleton } from '@/components/Skeleton';
import { ErrorState } from '@/components/States';
import { Txt } from '@/components/Txt';
import * as WebBrowser from 'expo-web-browser';
import { config } from '@/config';
import { spacing, useTheme } from '@/theme';

const RANK: Record<Plan, number> = { free: 0, pro: 1, premium: 2 };

function FinePrint() {
  const billing = useBilling();
  return (
    <View style={{ gap: spacing.sm }}>
      <Txt variant="small" tone="faint" style={{ lineHeight: 18 }}>
        Subscriptions are billed through Google Play and renew automatically at the price shown until cancelled. Cancel at least 24
        hours before the renewal date to avoid being charged for the next period. Manage or cancel any time in Google Play
        → Payments & subscriptions. Prices include applicable taxes where required. Any free trial converts to a paid
        subscription unless cancelled before it ends.
      </Txt>
      <View style={{ flexDirection: 'row', flexWrap: 'wrap', gap: spacing.lg }}>
        <Pressable onPress={() => void billing.restore()} hitSlop={6}>
          <Txt variant="small" tone="accent" weight="700">
            {billing.restoring ? 'Restoring…' : 'Restore purchases'}
          </Txt>
        </Pressable>
        <Pressable onPress={() => void Linking.openURL(billing.manageSubscriptionUrl)} hitSlop={6}>
          <Txt variant="small" tone="accent" weight="700">
            Manage subscription
          </Txt>
        </Pressable>
        <Pressable onPress={() => void WebBrowser.openBrowserAsync(config.TERMS_URL)} hitSlop={6}>
          <Txt variant="small" tone="accent" weight="700">
            Terms
          </Txt>
        </Pressable>
        <Pressable onPress={() => void WebBrowser.openBrowserAsync(config.PRIVACY_URL)} hitSlop={6}>
          <Txt variant="small" tone="accent" weight="700">
            Privacy
          </Txt>
        </Pressable>
      </View>
    </View>
  );
}

export default function Paywall() {
  const t = useTheme();
  const params = useLocalSearchParams<{ plan?: string }>();
  const required: Plan = params.plan === 'premium' ? 'premium' : 'pro';
  const { isAuthed, entitlements } = useAuth();
  const billing = useBilling();
  const plans = usePlans();
  const [period, setPeriod] = useState<BillingPeriod>('yearly');
  const unavailable = billingUnavailableMessage(billing.availability);

  return (
    <ScrollView style={{ backgroundColor: t.bg }} contentContainerStyle={{ padding: spacing.lg, gap: spacing.lg, paddingBottom: spacing.xxl }}>
      <View style={{ gap: spacing.xs }}>
        <Txt mono tone="accent" weight="800">
          {config.APP_NAME.toUpperCase()} PLANS
        </Txt>
        <Txt variant="display">See trades as they land</Txt>
        <Txt tone="muted">Real-time disclosures, AI analysis and alerts. Cancel any time in Google Play.</Txt>
      </View>

      {!isAuthed ? <Notice message="Sign in first so your subscription is attached to your account." actionLabel="Sign in" onAction={() => router.push('/auth/sign-in')} /> : null}
      {unavailable ? <Notice kind="warning" message={unavailable} /> : null}
      {billing.notice ? <Notice kind={billing.notice.kind} message={billing.notice.message} onDismiss={billing.clearNotice} /> : null}

      <SegmentedControl<BillingPeriod>
        value={period}
        onChange={setPeriod}
        options={[
          { value: 'monthly', label: 'Monthly' },
          { value: 'yearly', label: 'Yearly' },
        ]}
      />

      {plans.isPending ? (
        <>
          <CardSkeleton lines={4} />
          <CardSkeleton lines={4} />
        </>
      ) : plans.isError ? (
        <ErrorState error={plans.error} onRetry={() => void plans.refetch()} />
      ) : (
        [...plans.data.plans]
          .sort((a, b) => RANK[a.id] - RANK[b.id])
          .map((plan) => {
            const productId = productIdForPlan(plan.id, plans.data.plans);
            const offer = productId ? findOffer(billing.offers, productId, period) : undefined;
            return (
              <PlanCard
                key={plan.id}
                plan={plan}
                offer={offer}
                period={period}
                isCurrent={entitlements.plan === plan.id}
                highlighted={plan.id === required && RANK[entitlements.plan] < RANK[plan.id]}
                loadingPrice={billing.loadingOffers || (!billing.ready && !unavailable)}
                busy={billing.busyProductId === productId}
                canBuy={isAuthed && !unavailable && billing.busyProductId === null && RANK[entitlements.plan] < RANK[plan.id]}
                onBuy={() => offer && void billing.purchase(offer)}
              />
            );
          })
      )}
      <FinePrint />
    </ScrollView>
  );
}

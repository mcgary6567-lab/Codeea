import { router } from 'expo-router';
import * as WebBrowser from 'expo-web-browser';
import { useState } from 'react';
import { Alert, Linking, ScrollView, View } from 'react-native';
import { errorMessage } from '@/api/client';
import { useAuth } from '@/auth/AuthContext';
import { PlanBadge } from '@/components/Badges';
import { Button } from '@/components/Button';
import { Card } from '@/components/Card';
import { ListRow } from '@/components/ListRow';
import { Notice } from '@/components/Notice';
import { SectionHeader } from '@/components/SectionHeader';
import { Txt } from '@/components/Txt';
import { billingUnavailableMessage, useBilling } from '@/billing';
import { config } from '@/config';
import type { Entitlements } from '@/api/types';
import { absoluteDate } from '@/lib/format';
import { openPaywall } from '@/lib/navigation';
import { spacing, useTheme } from '@/theme';

function entitlementRows(e: Entitlements): { label: string; value: string }[] {
  return [
    { label: 'Trade data', value: e.delayDays > 0 ? `Delayed ${e.delayDays} days` : 'Real-time' },
    { label: 'History', value: e.historyDays == null ? 'Full history' : `Last ${e.historyDays} days` },
    { label: 'Watchlist', value: `${e.watchlistLimit} items` },
    { label: 'AI search', value: e.aiSearchPerDay > 0 ? `${e.aiSearchPerDay} / day` : '—' },
    { label: 'AI insights', value: e.aiInsights ? 'Included' : '—' },
    { label: 'AI chat', value: e.aiChat ? 'Included' : '—' },
    { label: 'Push alerts', value: e.alerts ? 'Included' : '—' },
    { label: 'CSV export', value: e.csvExport ? 'Included' : '—' },
  ];
}

export default function AccountScreen() {
  const t = useTheme();
  const { isAuthed, user, entitlements, signOut, deleteAccount } = useAuth();
  const billing = useBilling();
  const [deleting, setDeleting] = useState(false);
  const plan = entitlements.plan;
  const unavailable = billingUnavailableMessage(billing.availability);

  const confirmDelete = () =>
    Alert.alert(
      'Delete account?',
      'This permanently deletes your account, watchlist and alert settings. Active Google Play subscriptions are NOT cancelled automatically; cancel them in Play first.',
      [
        { text: 'Cancel', style: 'cancel' },
        {
          text: 'Delete',
          style: 'destructive',
          onPress: async () => {
            setDeleting(true);
            try {
              await deleteAccount();
              Alert.alert('Account deleted', 'Your account and data have been removed.');
            } catch (e) {
              Alert.alert('Could not delete account', errorMessage(e));
            } finally {
              setDeleting(false);
            }
          },
        },
      ],
    );

  return (
    <ScrollView style={{ backgroundColor: t.bg }} contentContainerStyle={{ padding: spacing.lg, paddingBottom: spacing.xxl }}>
      <Card style={{ gap: spacing.md }}>
        <View style={{ flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between' }}>
          <View style={{ flex: 1 }}>
            <Txt variant="title" numberOfLines={1}>
              {isAuthed ? (user?.name ?? user?.email ?? 'Your account') : 'Guest'}
            </Txt>
            <Txt variant="small" tone="muted" numberOfLines={1}>
              {isAuthed ? user?.email : 'Browsing without an account'}
            </Txt>
          </View>
          <PlanBadge plan={plan} />
        </View>
        {user?.planExpiresAt ? (
          <Txt variant="small" tone="faint">
            {plan === 'free' ? 'Expired' : 'Renews / expires'} {absoluteDate(user.planExpiresAt)}
          </Txt>
        ) : null}
        {plan !== 'premium' ? <Button title={plan === 'free' ? 'Upgrade' : 'Upgrade to Premium'} icon="sparkles" onPress={() => openPaywall()} /> : null}
        {!isAuthed ? <Button title="Sign in or create account" variant="secondary" onPress={() => router.push('/auth/sign-in')} /> : null}
      </Card>

      <SectionHeader title="Your plan includes" />
      <Card padded={false}>
        {entitlementRows(entitlements).map((r, i, arr) => (
          <View
            key={r.label}
            style={{
              flexDirection: 'row',
              justifyContent: 'space-between',
              paddingHorizontal: spacing.lg,
              paddingVertical: spacing.md,
              borderBottomWidth: i === arr.length - 1 ? 0 : 1,
              borderColor: t.border,
            }}
          >
            <Txt tone="muted">{r.label}</Txt>
            <Txt mono weight="600">
              {r.value}
            </Txt>
          </View>
        ))}
      </Card>

      <SectionHeader title="Subscription" />
      {billing.notice ? (
        <View style={{ marginBottom: spacing.sm }}>
          <Notice kind={billing.notice.kind} message={billing.notice.message} onDismiss={billing.clearNotice} />
        </View>
      ) : null}
      {unavailable ? (
        <View style={{ marginBottom: spacing.sm }}>
          <Notice kind="warning" message={unavailable} />
        </View>
      ) : null}
      <Card padded={false}>
        <ListRow
          icon="refresh-outline"
          label={billing.restoring ? 'Restoring…' : 'Restore purchases'}
          detail="Re-link a Google Play subscription to this account"
          onPress={billing.restoring ? undefined : () => void billing.restore()}
        />
        <ListRow
          icon="card-outline"
          label="Manage subscription"
          detail="Change plan or cancel in Google Play"
          onPress={() => void Linking.openURL(billing.manageSubscriptionUrl)}
          last
        />
      </Card>

      <SectionHeader title="About" />
      <Card padded={false}>
        <ListRow icon="shield-outline" label="Privacy policy" onPress={() => void WebBrowser.openBrowserAsync(config.PRIVACY_URL)} />
        <ListRow icon="document-text-outline" label="Terms of service" onPress={() => void WebBrowser.openBrowserAsync(config.TERMS_URL)} />
        <ListRow icon="mail-outline" label="Contact support" detail={config.SUPPORT_EMAIL} onPress={() => void Linking.openURL(`mailto:${config.SUPPORT_EMAIL}`)} last />
      </Card>

      {isAuthed ? (
        <>
          <SectionHeader title="Account" />
          <Card padded={false}>
            <ListRow icon="log-out-outline" label="Sign out" onPress={() => void signOut()} />
            <ListRow icon="trash-outline" label={deleting ? 'Deleting…' : 'Delete account'} danger onPress={deleting ? undefined : confirmDelete} last />
          </Card>
        </>
      ) : null}

      <Txt variant="small" tone="faint" align="center" style={{ marginTop: spacing.xl }}>
        {config.APP_NAME} shows public financial disclosures. Nothing here is investment advice.
      </Txt>
    </ScrollView>
  );
}

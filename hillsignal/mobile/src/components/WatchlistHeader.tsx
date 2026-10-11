import { ActivityIndicator, Pressable, ScrollView, Switch, View } from 'react-native';
import { useWatchlist } from '@/api/hooks';
import type { Issuer, Politician } from '@/api/types';
import { useAuth } from '@/auth/AuthContext';
import { openIssuer, openPaywall, openPolitician } from '@/lib/navigation';
import { useAlerts } from '@/notifications/useAlerts';
import { radius, spacing, useTheme } from '@/theme';
import { Avatar } from './Avatar';
import { PartyChip } from './Badges';
import { Card } from './Card';
import { Notice } from './Notice';
import { SectionHeader } from './SectionHeader';
import { CardSkeleton } from './Skeleton';
import { ErrorState } from './States';
import { Txt } from './Txt';

function AlertsToggle() {
  const t = useTheme();
  const { entitlements } = useAuth();
  const alerts = useAlerts();
  const onChange = (on: boolean) => {
    if (on && !entitlements.alerts) return openPaywall('pro');
    void alerts.setAlerts(on);
  };
  return (
    <Card style={{ gap: spacing.sm }}>
      <View style={{ flexDirection: 'row', alignItems: 'center', gap: spacing.md }}>
        <View style={{ flex: 1 }}>
          <Txt weight="700">Trade alerts</Txt>
          <Txt variant="small" tone="muted">
            {entitlements.alerts ? 'Push notification when someone you follow files a trade.' : 'Push alerts are included with Pro.'}
          </Txt>
        </View>
        {alerts.busy ? (
          <ActivityIndicator color={t.accent} />
        ) : (
          <Switch
            value={alerts.enabled && entitlements.alerts}
            onValueChange={onChange}
            trackColor={{ true: t.accent, false: t.border }}
            thumbColor={t.surface}
            accessibilityLabel="Trade alerts"
          />
        )}
      </View>
      {alerts.error ? <Notice kind="error" message={alerts.error} /> : null}
    </Card>
  );
}

function PersonChip({ p }: { p: Politician }) {
  const t = useTheme();
  return (
    <Pressable
      onPress={() => openPolitician(p.id)}
      style={{ width: 96, alignItems: 'center', gap: 6, padding: spacing.sm, borderRadius: radius.md, backgroundColor: t.surface, borderWidth: 1, borderColor: t.border }}
    >
      <Avatar name={p.name} url={p.photoUrl} size={44} />
      <Txt variant="small" weight="600" numberOfLines={1}>
        {p.name}
      </Txt>
      <PartyChip party={p.party} />
    </Pressable>
  );
}

function IssuerChip({ i }: { i: Issuer }) {
  const t = useTheme();
  return (
    <Pressable
      onPress={() => openIssuer(i.id)}
      style={{ minWidth: 96, gap: 2, padding: spacing.md, borderRadius: radius.md, backgroundColor: t.surface, borderWidth: 1, borderColor: t.border }}
    >
      <Txt mono weight="800">
        {i.ticker ?? '—'}
      </Txt>
      <Txt variant="small" tone="muted" numberOfLines={1} style={{ maxWidth: 140 }}>
        {i.name}
      </Txt>
    </Pressable>
  );
}

export function WatchlistHeader() {
  const { entitlements } = useAuth();
  const q = useWatchlist();
  const count = (q.data?.politicians.length ?? 0) + (q.data?.issuers.length ?? 0);
  return (
    <View style={{ padding: spacing.lg, paddingBottom: 0 }}>
      <AlertsToggle />
      {q.isPending ? (
        <View style={{ marginTop: spacing.lg }}>
          <CardSkeleton lines={2} />
        </View>
      ) : q.isError ? (
        <ErrorState error={q.error} onRetry={() => void q.refetch()} />
      ) : (
        <>
          <SectionHeader title={`Following · ${count}/${entitlements.watchlistLimit}`} />
          {count === 0 ? (
            <Txt tone="muted" variant="small">
              Tap Follow on any politician or company to add it here.
            </Txt>
          ) : (
            <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={{ gap: spacing.sm }}>
              {q.data.politicians.map((p) => (
                <PersonChip key={p.id} p={p} />
              ))}
              {q.data.issuers.map((i) => (
                <IssuerChip key={i.id} i={i} />
              ))}
            </ScrollView>
          )}
          <SectionHeader title="Your feed" />
        </>
      )}
    </View>
  );
}

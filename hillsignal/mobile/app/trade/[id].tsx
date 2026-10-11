import { Ionicons } from '@expo/vector-icons';
import { Stack, useLocalSearchParams } from 'expo-router';
import * as WebBrowser from 'expo-web-browser';
import { Pressable, ScrollView, View } from 'react-native';
import { useTrade, useTradeInsight } from '@/api/hooks';
import type { Trade } from '@/api/types';
import { useAuth } from '@/auth/AuthContext';
import { Avatar } from '@/components/Avatar';
import { ChamberTag, PartyChip, TxBadge } from '@/components/Badges';
import { Button } from '@/components/Button';
import { Card } from '@/components/Card';
import { FactGrid } from '@/components/FactGrid';
import { InsightCard } from '@/components/InsightCard';
import { SectionHeader } from '@/components/SectionHeader';
import { CardSkeleton } from '@/components/Skeleton';
import { ErrorState } from '@/components/States';
import { Txt } from '@/components/Txt';
import { absoluteDate, amountRange, ownerLabel, relativeDate, txLabel } from '@/lib/format';
import { openIssuer, openPolitician } from '@/lib/navigation';
import { spacing, useTheme } from '@/theme';

function Header({ trade }: { trade: Trade }) {
  const t = useTheme();
  const { politician: p, issuer } = trade;
  return (
    <Card style={{ gap: spacing.lg }}>
      <View style={{ flexDirection: 'row', alignItems: 'center', gap: spacing.sm }}>
        <TxBadge txType={trade.txType} />
        <Txt mono variant="display" style={{ fontSize: 26 }}>
          {amountRange(trade)}
        </Txt>
      </View>
      <Pressable onPress={() => openPolitician(p.id)} accessibilityRole="button" style={{ flexDirection: 'row', alignItems: 'center', gap: spacing.md }}>
        <Avatar name={p.name} url={p.photoUrl} size={44} />
        <View style={{ flex: 1, gap: 4 }}>
          <Txt variant="heading">{p.name}</Txt>
          <View style={{ flexDirection: 'row', gap: 6, alignItems: 'center' }}>
            <PartyChip party={p.party} />
            <ChamberTag chamber={p.chamber} />
            <Txt variant="small" tone="muted">
              {p.state}
            </Txt>
          </View>
        </View>
        <Ionicons name="chevron-forward" size={18} color={t.textFaint} />
      </Pressable>
      <Pressable
        onPress={issuer ? () => openIssuer(issuer.id) : undefined}
        disabled={!issuer}
        accessibilityRole={issuer ? 'button' : undefined}
        style={{ flexDirection: 'row', alignItems: 'center', gap: spacing.md }}
      >
        <View style={{ width: 44, height: 44, borderRadius: 12, backgroundColor: t.surfaceAlt, alignItems: 'center', justifyContent: 'center' }}>
          <Txt mono weight="800" variant="small">
            {issuer?.ticker ?? '—'}
          </Txt>
        </View>
        <View style={{ flex: 1 }}>
          <Txt weight="700">{issuer?.name ?? trade.assetName}</Txt>
          <Txt variant="small" tone="muted">
            {[issuer?.sector, trade.assetType].filter(Boolean).join(' · ') || 'Asset'}
          </Txt>
        </View>
        {issuer ? <Ionicons name="chevron-forward" size={18} color={t.textFaint} /> : null}
      </Pressable>
    </Card>
  );
}

export default function TradeDetail() {
  const { id } = useLocalSearchParams<{ id: string }>();
  const { isAuthed, entitlements } = useAuth();
  const trade = useTrade(id);
  const unlocked = isAuthed && entitlements.aiInsights;
  const insight = useTradeInsight(id, unlocked && trade.isSuccess);

  if (trade.isPending)
    return (
      <View style={{ padding: spacing.lg, gap: spacing.md }}>
        <CardSkeleton lines={4} />
        <CardSkeleton lines={3} />
      </View>
    );
  if (trade.isError) return <ErrorState error={trade.error} onRetry={() => void trade.refetch()} />;

  const tr = trade.data;
  const late = tr.reportingGapDays > 45; // STOCK Act deadline
  return (
    <>
      <Stack.Screen options={{ title: tr.issuer?.ticker ? `${txLabel[tr.txType]} ${tr.issuer.ticker}` : 'Trade' }} />
      <ScrollView contentContainerStyle={{ padding: spacing.lg, gap: spacing.md, paddingBottom: spacing.xxl }}>
        <Header trade={tr} />
        <FactGrid
          facts={[
            { label: 'Traded', value: absoluteDate(tr.txDate), hint: relativeDate(tr.txDate) },
            { label: 'Disclosed', value: absoluteDate(tr.pubDate), hint: relativeDate(tr.pubDate) },
            { label: 'Reporting gap', value: `${tr.reportingGapDays} days`, hint: late ? 'Past the 45-day deadline' : 'Within 45 days', mono: true },
            { label: 'Owner', value: tr.owner ? ownerLabel[tr.owner] : 'Not stated' },
            { label: 'Amount (filed)', value: tr.amountLabel, mono: true },
            { label: 'Asset type', value: tr.assetType ?? '—' },
          ]}
        />
        <Card style={{ gap: 4 }}>
          <Txt variant="caption" tone="muted">
            Asset as written in the filing
          </Txt>
          <Txt selectable>{tr.assetName}</Txt>
        </Card>
        {tr.sourceUrl ? (
          <Button title="View official filing" icon="open-outline" variant="secondary" onPress={() => void WebBrowser.openBrowserAsync(tr.sourceUrl as string)} />
        ) : null}
        <SectionHeader title="Analysis" />
        <InsightCard unlocked={unlocked} query={insight} lockedMessage="Get AI context on this trade: timing, related news windows and the member's history with this stock." />
      </ScrollView>
    </>
  );
}

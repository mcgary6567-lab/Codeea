import { Stack, useLocalSearchParams } from 'expo-router';
import { View } from 'react-native';
import { usePolitician, usePoliticianInsight, useTradesInfinite } from '@/api/hooks';
import type { PoliticianDetail } from '@/api/types';
import { useAuth } from '@/auth/AuthContext';
import { Avatar } from '@/components/Avatar';
import { ChamberTag, PartyChip } from '@/components/Badges';
import { Card } from '@/components/Card';
import { Chip } from '@/components/Chip';
import { FollowButton } from '@/components/FollowButton';
import { InsightCard } from '@/components/InsightCard';
import { SectionHeader } from '@/components/SectionHeader';
import { CardSkeleton } from '@/components/Skeleton';
import { ErrorState } from '@/components/States';
import { RatioBar, StatCard } from '@/components/StatCard';
import { TradeList } from '@/components/TradeList';
import { Txt } from '@/components/Txt';
import { compactUsd, dualDate, formatInt, partyLabel } from '@/lib/format';
import { openIssuer } from '@/lib/navigation';
import { spacing } from '@/theme';

function Header({ p }: { p: PoliticianDetail }) {
  const { isAuthed, entitlements } = useAuth();
  const unlocked = isAuthed && entitlements.aiInsights;
  const insight = usePoliticianInsight(p.id, unlocked);
  return (
    <View style={{ padding: spacing.lg, gap: spacing.md }}>
      <Card style={{ gap: spacing.md }}>
        <View style={{ flexDirection: 'row', alignItems: 'center', gap: spacing.md }}>
          <Avatar name={p.name} url={p.photoUrl} size={60} />
          <View style={{ flex: 1, gap: 6 }}>
            <Txt variant="title">{p.name}</Txt>
            <View style={{ flexDirection: 'row', gap: 6, alignItems: 'center', flexWrap: 'wrap' }}>
              <PartyChip party={p.party} />
              <ChamberTag chamber={p.chamber} />
              <Txt variant="small" tone="muted">
                {partyLabel(p.party)} · {p.state}
                {p.district ? `-${p.district}` : ''}
              </Txt>
            </View>
          </View>
        </View>
        <FollowButton kind="politician" targetId={p.id} />
      </Card>
      <View style={{ flexDirection: 'row', gap: spacing.md }}>
        <StatCard label="Trades" value={formatInt(p.tradeCount)} hint={p.lastTradeDate ? `last ${dualDate(p.lastTradeDate)}` : undefined} />
        <StatCard label="Volume" value={`${compactUsd(p.volumeMin)}+`} hint="sum of lower bounds" />
      </View>
      <Card>
        <RatioBar buys={p.buyCount} sells={p.sellCount} />
      </Card>
      {p.topIssuers.length ? (
        <>
          <SectionHeader title="Most traded" />
          <View style={{ flexDirection: 'row', flexWrap: 'wrap', gap: spacing.sm }}>
            {p.topIssuers.slice(0, 8).map((i) => (
              <Chip key={i.id} label={`${i.ticker ?? i.name} · ${i.tradeCount}`} onPress={() => openIssuer(i.id)} />
            ))}
          </View>
        </>
      ) : null}
      <SectionHeader title="AI insight" />
      <InsightCard unlocked={unlocked} query={insight} lockedMessage="An AI summary of this member's trading patterns, sectors and committee overlaps." />
      <SectionHeader title="Trades" />
    </View>
  );
}

export default function PoliticianScreen() {
  const { id } = useLocalSearchParams<{ id: string }>();
  const politician = usePolitician(id);
  const trades = useTradesInfinite({ politicianId: id, sort: '-txDate' });

  if (politician.isPending)
    return (
      <View style={{ padding: spacing.lg, gap: spacing.md }}>
        <CardSkeleton lines={3} />
        <CardSkeleton lines={2} />
      </View>
    );
  if (politician.isError) return <ErrorState error={politician.error} onRetry={() => void politician.refetch()} />;

  return (
    <>
      <Stack.Screen options={{ title: politician.data.name }} />
      <TradeList query={trades} header={<Header p={politician.data} />} hidePolitician onRefreshExtra={() => void politician.refetch()} />
    </>
  );
}

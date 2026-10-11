import { router } from 'expo-router';
import { Pressable, View } from 'react-native';
import { useOverview } from '@/api/hooks';
import type { OverviewStats } from '@/api/types';
import { compactUsd, formatInt } from '@/lib/format';
import { openPolitician } from '@/lib/navigation';
import { spacing, useTheme } from '@/theme';
import { Avatar } from './Avatar';
import { PartyChip } from './Badges';
import { Card } from './Card';
import { SectionHeader } from './SectionHeader';
import { CardSkeleton } from './Skeleton';
import { ErrorState } from './States';
import { RatioBar, StatCard } from './StatCard';
import { Txt } from './Txt';

function TopTickers({ items }: { items: OverviewStats['topTickers'] }) {
  const t = useTheme();
  const max = Math.max(1, ...items.map((i) => i.tradeCount));
  return (
    <Card style={{ gap: spacing.md }}>
      {items.slice(0, 5).map((i) => (
        <Pressable
          key={i.ticker}
          onPress={() => router.push({ pathname: '/(tabs)/trades', params: { ticker: i.ticker } })}
          accessibilityRole="button"
          accessibilityLabel={`${i.ticker}, ${i.tradeCount} trades`}
          style={{ gap: 4 }}
        >
          <View style={{ flexDirection: 'row', justifyContent: 'space-between' }}>
            <Txt mono weight="800">
              {i.ticker}
              <Txt variant="small" tone="muted">
                {'  '}
                {i.name}
              </Txt>
            </Txt>
            <Txt mono variant="small" tone="muted">
              {i.tradeCount}
            </Txt>
          </View>
          <View style={{ height: 4, borderRadius: 2, backgroundColor: t.surfaceAlt }}>
            <View style={{ height: 4, borderRadius: 2, width: `${(i.tradeCount / max) * 100}%`, backgroundColor: t.accent }} />
          </View>
        </Pressable>
      ))}
    </Card>
  );
}

function MostActive({ items }: { items: OverviewStats['mostActive'] }) {
  return (
    <Card padded={false}>
      {items.slice(0, 5).map((p, idx) => (
        <Pressable
          key={p.id}
          onPress={() => openPolitician(p.id)}
          accessibilityRole="button"
          style={{ flexDirection: 'row', alignItems: 'center', gap: spacing.md, padding: spacing.md, paddingHorizontal: spacing.lg }}
        >
          <Txt mono tone="faint" style={{ width: 18 }}>
            {idx + 1}
          </Txt>
          <Avatar name={p.name} url={p.photoUrl} size={32} />
          <Txt weight="600" numberOfLines={1} style={{ flex: 1 }}>
            {p.name}
          </Txt>
          <PartyChip party={p.party} />
          <Txt mono variant="small" tone="muted">
            {p.tradeCount}
          </Txt>
        </Pressable>
      ))}
    </Card>
  );
}

/** Home dashboard built from GET /stats/overview. */
export function Dashboard() {
  const q = useOverview();
  if (q.isPending)
    return (
      <View style={{ gap: spacing.md }}>
        <CardSkeleton lines={2} />
        <CardSkeleton lines={4} />
      </View>
    );
  if (q.isError) return <ErrorState error={q.error} onRetry={() => void q.refetch()} />;
  const s = q.data;
  return (
    <View>
      <View style={{ flexDirection: 'row', gap: spacing.md }}>
        <StatCard label="Trades · 30d" value={formatInt(s.last30Days.trades)} hint={`${formatInt(s.totalTrades)} all time`} />
        <StatCard label="Volume · 30d" value={`${compactUsd(s.last30Days.volumeMin)}+`} hint="sum of lower bounds" />
      </View>
      <SectionHeader title="Buy / sell · last 30 days" />
      <Card>
        <RatioBar buys={s.last30Days.buys} sells={s.last30Days.sells} />
      </Card>
      {s.topTickers.length ? (
        <>
          <SectionHeader title="Top tickers" />
          <TopTickers items={s.topTickers} />
        </>
      ) : null}
      {s.mostActive.length ? (
        <>
          <SectionHeader title="Most active" />
          <MostActive items={s.mostActive} />
        </>
      ) : null}
    </View>
  );
}

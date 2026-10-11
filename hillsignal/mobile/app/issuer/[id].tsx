import { Stack, useLocalSearchParams } from 'expo-router';
import { View } from 'react-native';
import { useIssuer, useTradesInfinite } from '@/api/hooks';
import { Card } from '@/components/Card';
import { FollowButton } from '@/components/FollowButton';
import { SectionHeader } from '@/components/SectionHeader';
import { CardSkeleton } from '@/components/Skeleton';
import { ErrorState } from '@/components/States';
import { StatCard } from '@/components/StatCard';
import { TradeList } from '@/components/TradeList';
import { Txt } from '@/components/Txt';
import { dualDate, formatInt } from '@/lib/format';
import { spacing } from '@/theme';

export default function IssuerScreen() {
  const { id } = useLocalSearchParams<{ id: string }>();
  const issuer = useIssuer(id);
  const trades = useTradesInfinite({ issuerId: id });

  if (issuer.isPending)
    return (
      <View style={{ padding: spacing.lg }}>
        <CardSkeleton lines={3} />
      </View>
    );
  if (issuer.isError) return <ErrorState error={issuer.error} onRetry={() => void issuer.refetch()} />;
  const i = issuer.data;

  return (
    <>
      <Stack.Screen options={{ title: i.ticker ?? i.name }} />
      <TradeList
        query={trades}
        onRefreshExtra={() => void issuer.refetch()}
        header={
          <View style={{ padding: spacing.lg, gap: spacing.md }}>
            <Card style={{ gap: spacing.md }}>
              <View style={{ gap: 4 }}>
                {i.ticker ? (
                  <Txt mono variant="display" tone="accent">
                    {i.ticker}
                  </Txt>
                ) : null}
                <Txt variant="title">{i.name}</Txt>
                {i.sector ? (
                  <Txt tone="muted" variant="small">
                    {i.sector}
                  </Txt>
                ) : null}
              </View>
              <FollowButton kind="issuer" targetId={i.id} />
            </Card>
            <View style={{ flexDirection: 'row', gap: spacing.md }}>
              <StatCard label="Trades" value={formatInt(i.tradeCount)} />
              <StatCard label="Members" value={formatInt(i.politicianCount)} />
            </View>
            {i.lastTradeDate ? (
              <Txt variant="small" tone="faint">
                Last traded {dualDate(i.lastTradeDate)}
              </Txt>
            ) : null}
            <SectionHeader title="Trades" />
          </View>
        }
      />
    </>
  );
}

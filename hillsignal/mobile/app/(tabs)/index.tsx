import { router } from 'expo-router';
import { View } from 'react-native';
import { flattenPages, useOverview, useTradesInfinite } from '@/api/hooks';
import { Dashboard } from '@/components/Dashboard';
import { DelayBanner } from '@/components/DelayBanner';
import { SectionHeader } from '@/components/SectionHeader';
import { TradeList } from '@/components/TradeList';
import { spacing } from '@/theme';

export default function Home() {
  const trades = useTradesInfinite({ sort: '-pubDate' });
  const overview = useOverview();
  const { meta } = flattenPages(trades.data?.pages);

  return (
    <TradeList
      query={trades}
      onRefreshExtra={() => void overview.refetch()}
      emptyTitle="No trades published yet"
      header={
        <View style={{ padding: spacing.lg, paddingBottom: 0, gap: spacing.md }}>
          <DelayBanner delayDays={meta?.delayDays} />
          <Dashboard />
          <SectionHeader title="Latest disclosures" action="See all" onAction={() => router.push('/(tabs)/trades')} />
        </View>
      }
    />
  );
}

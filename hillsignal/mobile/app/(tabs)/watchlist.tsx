import { router } from 'expo-router';
import { View } from 'react-native';
import { keys, useFeedInfinite } from '@/api/hooks';
import { useQueryClient } from '@tanstack/react-query';
import { useAuth } from '@/auth/AuthContext';
import { EmptyState } from '@/components/States';
import { TradeList } from '@/components/TradeList';
import { WatchlistHeader } from '@/components/WatchlistHeader';
import { useTheme } from '@/theme';

export default function WatchlistScreen() {
  const t = useTheme();
  const qc = useQueryClient();
  const { isAuthed } = useAuth();
  const feed = useFeedInfinite(isAuthed);

  if (!isAuthed) {
    return (
      <View style={{ flex: 1, backgroundColor: t.bg, justifyContent: 'center' }}>
        <EmptyState
          icon="star-outline"
          title="Build your watchlist"
          message="Create a free account to follow politicians and companies and get a personal feed of their trades."
          actionLabel="Sign in or create account"
          onAction={() => router.push('/auth/sign-in')}
        />
      </View>
    );
  }

  return (
    <TradeList
      query={feed}
      header={<WatchlistHeader />}
      onRefreshExtra={() => void qc.invalidateQueries({ queryKey: keys.watchlist })}
      emptyTitle="Your feed is empty"
      emptyMessage="Trades from what you follow show up here."
    />
  );
}

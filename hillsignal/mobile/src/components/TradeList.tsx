import type { InfiniteData, UseInfiniteQueryResult } from '@tanstack/react-query';
import type { ReactElement } from 'react';
import { FlatList, RefreshControl, View } from 'react-native';
import { flattenPages } from '@/api/hooks';
import type { Paged, Trade } from '@/api/types';
import { spacing, useTheme } from '@/theme';
import { ListSkeleton } from './Skeleton';
import { EmptyState, ErrorState, FooterSpinner } from './States';
import { TradeRow } from './TradeRow';

interface Props {
  query: UseInfiniteQueryResult<InfiniteData<Paged<Trade>>, Error>;
  header?: ReactElement | null;
  emptyTitle?: string;
  emptyMessage?: string;
  hidePolitician?: boolean;
  /** Extra refresh work (e.g. refetch dashboard cards) on pull-to-refresh. */
  onRefreshExtra?: () => void;
}

/** Infinite, pull-to-refreshable trade list with skeleton, empty and error states. */
export function TradeList({ query, header, emptyTitle = 'No trades yet', emptyMessage, hidePolitician, onRefreshExtra }: Props) {
  const t = useTheme();
  const { items } = flattenPages(query.data?.pages);

  const listEmpty = query.isPending ? (
    <ListSkeleton />
  ) : query.isError ? (
    <ErrorState error={query.error} onRetry={() => void query.refetch()} />
  ) : (
    <EmptyState icon="search-outline" title={emptyTitle} message={emptyMessage} />
  );

  return (
    <FlatList
      data={items}
      keyExtractor={(item) => item.id}
      renderItem={({ item }) => <TradeRow trade={item} hidePolitician={hidePolitician} />}
      ListHeaderComponent={header ? <View style={{ paddingBottom: spacing.sm }}>{header}</View> : null}
      ListEmptyComponent={listEmpty}
      ListFooterComponent={<FooterSpinner visible={query.isFetchingNextPage} />}
      onEndReachedThreshold={0.5}
      onEndReached={() => {
        if (query.hasNextPage && !query.isFetchingNextPage) void query.fetchNextPage();
      }}
      refreshControl={
        <RefreshControl
          refreshing={query.isRefetching && !query.isFetchingNextPage}
          onRefresh={() => {
            onRefreshExtra?.();
            void query.refetch();
          }}
          tintColor={t.accent}
          colors={[t.accent]}
          progressBackgroundColor={t.surface}
        />
      }
      style={{ backgroundColor: t.bg }}
      contentContainerStyle={{ flexGrow: 1 }}
      initialNumToRender={12}
      windowSize={11}
      removeClippedSubviews
    />
  );
}

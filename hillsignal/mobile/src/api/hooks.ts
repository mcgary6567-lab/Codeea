import { useInfiniteQuery, useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { config } from '@/config';
import { useAuth } from '@/auth/AuthContext';
import { api } from './endpoints';
import { keys } from './queryKeys';
import type { Paged, Trade, TradeQuery, WatchKind } from './types';

export { keys };

function nextPage(last: Paged<unknown>): number | undefined {
  const { page, pageSize, total } = last.meta;
  return page * pageSize < total && last.data.length > 0 ? page + 1 : undefined;
}

/** Flattens infinite pages and exposes the latest meta (delayDays etc.). */
export function flattenPages<T>(pages: Paged<T>[] | undefined) {
  const items = pages?.flatMap((p) => p.data) ?? [];
  const meta = pages?.[pages.length - 1]?.meta;
  return { items, meta };
}

export function useTradesInfinite(q: TradeQuery, opts: { enabled?: boolean } = {}) {
  return useInfiniteQuery({
    queryKey: keys.trades(q),
    queryFn: ({ pageParam }) => api.trades({ ...q, page: pageParam, pageSize: config.PAGE_SIZE }),
    initialPageParam: 1,
    getNextPageParam: nextPage,
    enabled: opts.enabled ?? true,
  });
}

export function useFeedInfinite(enabled: boolean) {
  return useInfiniteQuery({
    queryKey: keys.feed,
    queryFn: ({ pageParam }) => api.feed({ page: pageParam, pageSize: config.PAGE_SIZE }),
    initialPageParam: 1,
    getNextPageParam: nextPage,
    enabled,
  });
}

export const useOverview = () => useQuery({ queryKey: keys.overview, queryFn: api.overview });
export const useTrade = (id: string) => useQuery({ queryKey: keys.trade(id), queryFn: () => api.trade(id) });
export const usePolitician = (id: string) => useQuery({ queryKey: keys.politician(id), queryFn: () => api.politician(id) });
export const useIssuer = (id: string) => useQuery({ queryKey: keys.issuer(id), queryFn: () => api.issuer(id) });
export const usePlans = () => useQuery({ queryKey: keys.plans, queryFn: api.plans, staleTime: 10 * 60_000 });

export function useTradeInsight(id: string, enabled: boolean) {
  return useQuery({ queryKey: keys.tradeInsight(id), queryFn: () => api.tradeInsight(id), enabled, staleTime: Infinity });
}
export function usePoliticianInsight(id: string, enabled: boolean) {
  return useQuery({
    queryKey: keys.politicianInsight(id),
    queryFn: () => api.politicianInsight(id),
    enabled,
    staleTime: Infinity,
  });
}

export function useWatchlist() {
  const { isAuthed } = useAuth();
  return useQuery({ queryKey: keys.watchlist, queryFn: api.watchlist, enabled: isAuthed });
}

/** Whether a politician/issuer is followed, plus a toggle mutation. */
export function useFollow(kind: WatchKind, targetId: string) {
  const qc = useQueryClient();
  const watchlist = useWatchlist();
  const list = kind === 'politician' ? watchlist.data?.politicians : watchlist.data?.issuers;
  const isFollowing = !!list?.some((x) => x.id === targetId);

  const mutation = useMutation({
    mutationFn: async () => {
      if (isFollowing) await api.unfollow(kind, targetId);
      else await api.follow(kind, targetId);
    },
    onSettled: () => {
      void qc.invalidateQueries({ queryKey: keys.watchlist });
      void qc.invalidateQueries({ queryKey: keys.feed });
    },
  });
  return { isFollowing, toggle: mutation.mutate, isPending: mutation.isPending, error: mutation.error };
}

export type TradePages = Paged<Trade>;

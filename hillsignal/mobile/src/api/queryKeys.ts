import type { TradeQuery } from './types';

export const keys = {
  overview: ['overview'] as const,
  trades: (q: TradeQuery) => ['trades', q] as const,
  trade: (id: string) => ['trade', id] as const,
  politician: (id: string) => ['politician', id] as const,
  issuer: (id: string) => ['issuer', id] as const,
  watchlist: ['watchlist'] as const,
  feed: ['feed'] as const,
  plans: ['plans'] as const,
  tradeInsight: (id: string) => ['insight', 'trade', id] as const,
  politicianInsight: (id: string) => ['insight', 'politician', id] as const,
};

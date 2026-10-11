import { request } from './client';
import type {
  AiChatResponse,
  AiSearchResponse,
  AuthResponse,
  ChatMessage,
  Insight,
  Issuer,
  LoginBody,
  MeResponse,
  OverviewStats,
  Paged,
  PlansResponse,
  Politician,
  PoliticianDetail,
  PoliticianQuery,
  RegisterBody,
  Trade,
  TradeQuery,
  WatchKind,
  Watchlist,
} from './types';

type PageParams = { page?: number; pageSize?: number };
const e = encodeURIComponent;

export const api = {
  // Auth
  register: (body: RegisterBody) => request<AuthResponse>('POST', '/auth/register', { body }),
  login: (body: LoginBody) => request<AuthResponse>('POST', '/auth/login', { body }),
  me: () => request<MeResponse>('GET', '/me'),
  deleteMe: () => request<void>('DELETE', '/me'),

  // Market data
  trades: (q: TradeQuery & PageParams) => request<Paged<Trade>>('GET', '/trades', { query: { ...q } }),
  trade: (id: string) => request<Trade>('GET', `/trades/${e(id)}`),
  /** Premium only (402 for lower plans). Same filters as /trades, up to 5,000 rows. */
  tradesCsv: (q: TradeQuery) => request<string>('GET', '/trades/export.csv', { query: { ...q }, as: 'text' }),
  politicians: (q: PoliticianQuery & PageParams) => request<Paged<Politician>>('GET', '/politicians', { query: { ...q } }),
  politician: (id: string) => request<PoliticianDetail>('GET', `/politicians/${e(id)}`),
  issuers: (q: { search?: string } & PageParams) => request<Paged<Issuer>>('GET', '/issuers', { query: { ...q } }),
  issuer: (id: string) => request<Issuer>('GET', `/issuers/${e(id)}`),
  overview: () => request<OverviewStats>('GET', '/stats/overview'),

  // Watchlist & alerts
  watchlist: () => request<Watchlist>('GET', '/watchlist'),
  follow: (kind: WatchKind, targetId: string) => request<{ ok: true }>('POST', '/watchlist', { body: { kind, targetId } }),
  unfollow: (kind: WatchKind, targetId: string) => request<void>('DELETE', `/watchlist/${kind}/${e(targetId)}`),
  feed: (q: PageParams) => request<Paged<Trade>>('GET', '/feed', { query: { ...q } }),
  registerDevice: (expoPushToken: string, platform: 'android' | 'ios') =>
    request<{ ok: true }>('POST', '/devices', { body: { expoPushToken, platform } }),
  unregisterDevice: (expoPushToken: string) => request<void>('DELETE', '/devices', { body: { expoPushToken } }),

  // AI
  aiSearch: (query: string) => request<AiSearchResponse>('POST', '/ai/search', { body: { query } }),
  tradeInsight: (id: string) => request<{ insight: Insight }>('GET', `/ai/trades/${e(id)}/insight`),
  politicianInsight: (id: string) => request<{ insight: Insight }>('GET', `/ai/politicians/${e(id)}/insight`),
  aiChat: (messages: ChatMessage[]) => request<AiChatResponse>('POST', '/ai/chat', { body: { messages } }),

  // Plans / billing
  plans: () => request<PlansResponse>('GET', '/plans'),
  verifyGooglePurchase: (productId: string, purchaseToken: string) =>
    request<MeResponse>('POST', '/billing/google/verify', { body: { productId, purchaseToken } }),
};

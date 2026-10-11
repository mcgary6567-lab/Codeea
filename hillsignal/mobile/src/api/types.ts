/**
 * Types copied from hillsignal/docs/API.md (contract v1). Keep in sync with the backend.
 */
export type Plan = 'free' | 'pro' | 'premium';
export type Chamber = 'house' | 'senate';
export type Party = 'D' | 'R' | 'I';
export type TxType = 'buy' | 'sell' | 'exchange';
export type Owner = 'self' | 'spouse' | 'joint' | 'dependent';

export interface PoliticianRef {
  id: string;
  name: string;
  /** null = politician not yet matched to a member record */
  party: Party | null;
  chamber: Chamber;
  state: string;
  photoUrl: string | null;
}

export interface IssuerRef {
  id: string;
  name: string;
  ticker: string | null;
  sector: string | null;
}

export interface Trade {
  id: string;
  politician: PoliticianRef;
  issuer: IssuerRef | null;
  /** as written in the filing */
  assetName: string;
  /** "stock", "option", "etf", "bond", ... */
  assetType: string | null;
  txType: TxType;
  /** YYYY-MM-DD */
  txDate: string;
  /** ISO timestamp the disclosure was published */
  pubDate: string;
  /** pubDate - txDate in days */
  reportingGapDays: number;
  owner: Owner | null;
  /** USD, disclosure range lower bound */
  amountMin: number | null;
  /** USD, upper bound (null = open-ended "over $X") */
  amountMax: number | null;
  /** "$1,001 - $15,000" */
  amountLabel: string;
  /** link to the official filing */
  sourceUrl: string | null;
}

export interface Politician extends PoliticianRef {
  district: string | null;
  tradeCount: number;
  lastTradeDate: string | null;
  /** sum of amountMin */
  volumeMin: number;
}

export interface PoliticianDetail extends Politician {
  topIssuers: (IssuerRef & { tradeCount: number })[];
  buyCount: number;
  sellCount: number;
}

export interface Issuer extends IssuerRef {
  tradeCount: number;
  politicianCount: number;
  lastTradeDate: string | null;
}

export interface PageMeta {
  page: number;
  pageSize: number;
  total: number;
  delayDays: number;
}

export interface Paged<T> {
  data: T[];
  meta: PageMeta;
}

export interface Entitlements {
  plan: Plan;
  /** trades published in the last N days are hidden (free plan) */
  delayDays: number;
  /** null = full history */
  historyDays: number | null;
  watchlistLimit: number;
  /** 0 = not available */
  aiSearchPerDay: number;
  aiInsights: boolean;
  aiChat: boolean;
  alerts: boolean;
  csvExport: boolean;
}

export interface User {
  id: string;
  email: string;
  name: string | null;
  plan: Plan;
  planExpiresAt: string | null;
  createdAt: string;
}

export interface ApiErrorBody {
  error: { code: ApiErrorCode | string; message: string };
  requiredPlan?: Plan;
}

export type ApiErrorCode =
  | 'invalid_request'
  | 'unauthorized'
  | 'upgrade_required'
  | 'not_found'
  | 'conflict'
  | 'ai_declined'
  | 'quota_exceeded'
  | 'rate_limited'
  | 'ai_unavailable'
  | 'billing_unavailable';

// ---- Auth ----
export interface AuthResponse {
  token: string;
  user: User;
  entitlements: Entitlements;
}
export interface MeResponse {
  user: User;
  entitlements: Entitlements;
}
export interface RegisterBody {
  email: string;
  password: string;
  name?: string;
}
export interface LoginBody {
  email: string;
  password: string;
}

// ---- Market data ----
export type TradeSort = '-pubDate' | '-txDate' | '-amount';

export interface TradeQuery {
  politicianId?: string;
  issuerId?: string;
  chamber?: Chamber;
  party?: Party;
  state?: string;
  ticker?: string;
  txType?: TxType;
  /** txDate, YYYY-MM-DD */
  from?: string;
  /** txDate, YYYY-MM-DD */
  to?: string;
  minAmount?: number;
  sort?: TradeSort;
}

export interface PoliticianQuery {
  search?: string;
  chamber?: Chamber;
  party?: Party;
  state?: string;
  sort?: '-lastTrade' | '-tradeCount' | 'name';
}

export interface OverviewStats {
  totalTrades: number;
  totalPoliticians: number;
  totalIssuers: number;
  last30Days: { trades: number; buys: number; sells: number; volumeMin: number };
  topTickers: { ticker: string; name: string; tradeCount: number }[];
  mostActive: (PoliticianRef & { tradeCount: number })[];
}

// ---- Watchlist ----
export type WatchKind = 'politician' | 'issuer';
export interface Watchlist {
  politicians: Politician[];
  issuers: Issuer[];
}

// ---- AI ----
export interface TradeFilters {
  politicianName?: string;
  chamber?: Chamber;
  party?: Party;
  state?: string;
  ticker?: string;
  sector?: string;
  txType?: TxType;
  from?: string;
  to?: string;
  minAmount?: number;
}

export interface Insight {
  headline: string;
  summary: string;
  keyPoints: string[];
  caveats: string[];
  disclaimer: string;
  generatedAt: string;
}

export interface AiSearchResponse {
  filters: TradeFilters;
  explanation: string;
  results: Paged<Trade>;
  remaining: number;
  disclaimer: string;
}

export interface AiChatResponse {
  reply: string;
  disclaimer: string;
}

export interface ChatMessage {
  role: 'user' | 'assistant';
  content: string;
}

// ---- Plans / billing ----
export interface PlanInfo {
  id: Plan;
  name: string;
  tagline: string;
  features: string[];
  entitlements: Entitlements;
  googleProductIds: string[];
}
export interface PlansResponse {
  plans: PlanInfo[];
}

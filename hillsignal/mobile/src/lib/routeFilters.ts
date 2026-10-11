import type { Chamber, Party, TradeFilters, TxType } from '@/api/types';
import type { TradeFilterState } from '@/components/FilterSheet';

type Params = Record<string, string | string[] | undefined>;
const one = (v: string | string[] | undefined) => (Array.isArray(v) ? v[0] : v) || undefined;
const oneOf = <T extends string>(v: string | undefined, allowed: readonly T[]) => (allowed.includes(v as T) ? (v as T) : undefined);

/** Route params (e.g. from Home "top tickers" or AI search) → Trades filters. */
export function filtersFromParams(p: Params): TradeFilterState {
  const min = Number(one(p.minAmount));
  const out: TradeFilterState = {
    ticker: one(p.ticker)?.toUpperCase(),
    chamber: oneOf<Chamber>(one(p.chamber), ['house', 'senate']),
    party: oneOf<Party>(one(p.party), ['D', 'R', 'I']),
    txType: oneOf<TxType>(one(p.txType), ['buy', 'sell', 'exchange']),
    state: one(p.state),
    from: one(p.from),
    to: one(p.to),
    minAmount: Number.isFinite(min) && min > 0 ? min : undefined,
  };
  return Object.fromEntries(Object.entries(out).filter(([, v]) => v !== undefined)) as TradeFilterState;
}

/** AI TradeFilters → route params for the Trades tab (drops fields /trades can't filter on). */
export function paramsFromAiFilters(f: TradeFilters): Record<string, string> {
  const out: Record<string, string> = {};
  for (const k of ['chamber', 'party', 'state', 'ticker', 'txType', 'from', 'to', 'minAmount'] as const) {
    const v = f[k];
    if (v !== undefined && v !== null && v !== '') out[k] = String(v);
  }
  return out;
}

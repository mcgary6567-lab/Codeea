import type { Chamber, Owner, Party, Trade, TxType } from '@/api/types';

const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

/** $1,001 → $1K, $15,000 → $15K, $1,500,000 → $1.5M */
export function compactUsd(n: number): string {
  const abs = Math.abs(n);
  const fmt = (v: number, suffix: string) => {
    const s = v >= 10 ? Math.round(v).toString() : (Math.round(v * 10) / 10).toString();
    return `$${s}${suffix}`;
  };
  if (abs >= 1e9) return fmt(n / 1e9, 'B');
  if (abs >= 1e6) return fmt(n / 1e6, 'M');
  if (abs >= 1e3) return fmt(n / 1e3, 'K');
  return `$${Math.round(n)}`;
}

/** Disclosure range → "$1K–$15K", open-ended → "$5M+". Falls back to the filing's label. */
export function amountRange(t: Pick<Trade, 'amountMin' | 'amountMax' | 'amountLabel'>): string {
  const { amountMin: min, amountMax: max } = t;
  if (min != null && max != null) return min === max ? compactUsd(min) : `${compactUsd(min)}–${compactUsd(max)}`;
  if (min != null) return `${compactUsd(min)}+`;
  if (max != null) return `≤${compactUsd(max)}`;
  return t.amountLabel || '—';
}

/** Parses "YYYY-MM-DD" as a local calendar date (no timezone shift); ISO timestamps as-is. */
export function parseDate(s: string): Date {
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(s);
  if (m) return new Date(Number(m[1]), Number(m[2]) - 1, Number(m[3]));
  return new Date(s);
}

export function absoluteDate(s: string): string {
  const d = parseDate(s);
  if (Number.isNaN(d.getTime())) return s;
  return `${MONTHS[d.getMonth()]} ${d.getDate()}, ${d.getFullYear()}`;
}

export function relativeDate(s: string, now: Date = new Date()): string {
  const d = parseDate(s);
  if (Number.isNaN(d.getTime())) return '';
  const startOf = (x: Date) => new Date(x.getFullYear(), x.getMonth(), x.getDate()).getTime();
  const days = Math.round((startOf(now) - startOf(d)) / 86_400_000);
  if (days < 0) return 'upcoming';
  if (days === 0) return 'today';
  if (days === 1) return 'yesterday';
  if (days < 7) return `${days}d ago`;
  if (days < 30) return `${Math.floor(days / 7)}w ago`;
  if (days < 365) return `${Math.floor(days / 30)}mo ago`;
  return `${Math.floor(days / 365)}y ago`;
}

/** "3d ago · Oct 8, 2026" */
export function dualDate(s: string): string {
  const rel = relativeDate(s);
  return rel ? `${rel} · ${absoluteDate(s)}` : absoluteDate(s);
}

export function toIsoDay(d: Date): string {
  const p = (n: number) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`;
}

export function daysAgo(n: number): string {
  const d = new Date();
  d.setDate(d.getDate() - n);
  return toIsoDay(d);
}

export const isIsoDay = (s: string) => /^\d{4}-\d{2}-\d{2}$/.test(s) && !Number.isNaN(parseDate(s).getTime());

export const txLabel: Record<TxType, string> = { buy: 'Buy', sell: 'Sell', exchange: 'Exchange' };
export const chamberLabel: Record<Chamber, string> = { house: 'House', senate: 'Senate' };
export const partyLabel = (p: Party | null) => (p === 'D' ? 'Democrat' : p === 'R' ? 'Republican' : p === 'I' ? 'Independent' : 'Unmatched');
export const ownerLabel: Record<Owner, string> = { self: 'Self', spouse: 'Spouse', joint: 'Joint', dependent: 'Dependent' };

export const formatInt = (n: number) => n.toString().replace(/\B(?=(\d{3})+(?!\d))/g, ',');

export function initials(name: string): string {
  return name
    .split(/\s+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((w) => w[0]?.toUpperCase() ?? '')
    .join('');
}

export const planLabel = (p: string) => p.charAt(0).toUpperCase() + p.slice(1);

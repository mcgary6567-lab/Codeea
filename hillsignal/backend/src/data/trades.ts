import { query } from "../db/pool.js";
import type { Entitlements } from "../plans.js";

export interface TradeFilters {
  politicianId?: string;
  politicianName?: string;
  issuerId?: string;
  chamber?: "house" | "senate";
  party?: "D" | "R" | "I";
  state?: string;
  ticker?: string;
  sector?: string;
  txType?: "buy" | "sell" | "exchange";
  from?: string;
  to?: string;
  minAmount?: number;
}

export type TradeSort = "-pubDate" | "-txDate" | "-amount";

const SORTS: Record<TradeSort, string> = {
  "-pubDate": "t.pub_date DESC, t.id",
  "-txDate": "t.tx_date DESC, t.id",
  "-amount": "t.amount_min DESC NULLS LAST, t.id",
};

interface TradeRow {
  id: string;
  asset_name: string;
  asset_type: string | null;
  tx_type: "buy" | "sell" | "exchange";
  tx_date: string;
  pub_date: Date;
  owner: string | null;
  amount_min: number | null;
  amount_max: number | null;
  amount_label: string;
  source_url: string | null;
  p_id: string;
  p_name: string;
  p_party: string;
  p_chamber: string;
  p_state: string;
  p_photo: string | null;
  i_id: number | null;
  i_name: string | null;
  i_ticker: string | null;
  i_sector: string | null;
}

const SELECT = `
  SELECT t.id, t.asset_name, t.asset_type, t.tx_type, t.tx_date, t.pub_date, t.owner,
         t.amount_min, t.amount_max, t.amount_label, t.source_url,
         p.id AS p_id, p.name AS p_name, p.party AS p_party, p.chamber AS p_chamber,
         p.state AS p_state, p.photo_url AS p_photo,
         i.id AS i_id, i.name AS i_name, i.ticker AS i_ticker, i.sector AS i_sector
  FROM trades t
  JOIN politicians p ON p.id = t.politician_id
  LEFT JOIN issuers i ON i.id = t.issuer_id`;

function toTrade(r: TradeRow) {
  const pub = r.pub_date;
  const gap = Math.max(0, Math.round((pub.getTime() - Date.parse(`${r.tx_date}T00:00:00Z`)) / 86_400_000));
  return {
    id: r.id,
    politician: {
      id: r.p_id,
      name: r.p_name,
      party: r.p_party,
      chamber: r.p_chamber,
      state: r.p_state,
      photoUrl: r.p_photo,
    },
    issuer: r.i_id === null ? null : { id: String(r.i_id), name: r.i_name!, ticker: r.i_ticker, sector: r.i_sector },
    assetName: r.asset_name,
    assetType: r.asset_type,
    txType: r.tx_type,
    txDate: r.tx_date,
    pubDate: pub.toISOString(),
    reportingGapDays: gap,
    owner: r.owner,
    amountMin: r.amount_min,
    amountMax: r.amount_max,
    amountLabel: r.amount_label,
    sourceUrl: r.source_url,
  };
}

export type Trade = ReturnType<typeof toTrade>;

/** WHERE clause for filters + plan visibility (delay and history window). */
function buildWhere(f: TradeFilters, ent: Pick<Entitlements, "delayDays" | "historyDays">, params: unknown[]) {
  const where: string[] = [];
  const add = (sql: string, value: unknown) => {
    params.push(value);
    where.push(sql.replaceAll("?", `$${params.length}`));
  };
  if (ent.delayDays > 0) add("t.pub_date <= now() - make_interval(days => ?::int)", ent.delayDays);
  if (ent.historyDays !== null) add("t.tx_date >= current_date - ?::int", ent.historyDays);
  if (f.politicianId) add("t.politician_id = ?", f.politicianId);
  if (f.politicianName) add("p.name ILIKE ?", `%${f.politicianName}%`);
  if (f.issuerId) add("t.issuer_id = ?::bigint", f.issuerId);
  if (f.chamber) add("p.chamber = ?", f.chamber);
  if (f.party) add("p.party = ?", f.party);
  if (f.state) add("p.state = ?", f.state.toUpperCase());
  if (f.ticker) add("i.ticker = ?", f.ticker.toUpperCase());
  if (f.sector) add("i.sector ILIKE ?", f.sector);
  if (f.txType) add("t.tx_type = ?", f.txType);
  if (f.from) add("t.tx_date >= ?::date", f.from);
  if (f.to) add("t.tx_date <= ?::date", f.to);
  if (f.minAmount !== undefined) add("COALESCE(t.amount_max, t.amount_min) >= ?", f.minAmount);
  return where.length ? `WHERE ${where.join(" AND ")}` : "";
}

export async function listTrades(
  f: TradeFilters,
  ent: Pick<Entitlements, "delayDays" | "historyDays">,
  opts: { page: number; pageSize: number; sort?: TradeSort },
) {
  const params: unknown[] = [];
  const where = buildWhere(f, ent, params);
  const [countRow] = await query<{ n: number }>(
    `SELECT count(*)::int AS n FROM trades t JOIN politicians p ON p.id = t.politician_id
     LEFT JOIN issuers i ON i.id = t.issuer_id ${where}`,
    params,
  );
  const rows = await query<TradeRow>(
    `${SELECT} ${where} ORDER BY ${SORTS[opts.sort ?? "-pubDate"]} LIMIT ${opts.pageSize} OFFSET ${(opts.page - 1) * opts.pageSize}`,
    params,
  );
  return {
    data: rows.map(toTrade),
    meta: { page: opts.page, pageSize: opts.pageSize, total: countRow?.n ?? 0, delayDays: ent.delayDays },
  };
}

export async function getTrade(id: string, ent: Pick<Entitlements, "delayDays" | "historyDays">) {
  const params: unknown[] = [];
  const where = buildWhere({}, ent, params);
  params.push(id);
  const clause = where ? `${where} AND t.id = $${params.length}` : `WHERE t.id = $${params.length}`;
  const [row] = await query<TradeRow>(`${SELECT} ${clause}`, params);
  return row ? toTrade(row) : null;
}

/** Trades for watched politicians/issuers. */
export async function feedTrades(
  userId: string,
  ent: Pick<Entitlements, "delayDays" | "historyDays">,
  opts: { page: number; pageSize: number },
) {
  const params: unknown[] = [];
  const where = buildWhere({}, ent, params);
  params.push(userId);
  const u = `$${params.length}`;
  const watched = `(t.politician_id IN (SELECT target_id FROM watchlist WHERE user_id = ${u} AND kind = 'politician')
     OR t.issuer_id::text IN (SELECT target_id FROM watchlist WHERE user_id = ${u} AND kind = 'issuer'))`;
  const clause = where ? `${where} AND ${watched}` : `WHERE ${watched}`;
  const [countRow] = await query<{ n: number }>(
    `SELECT count(*)::int AS n FROM trades t JOIN politicians p ON p.id = t.politician_id
     LEFT JOIN issuers i ON i.id = t.issuer_id ${clause}`,
    params,
  );
  const rows = await query<TradeRow>(
    `${SELECT} ${clause} ORDER BY t.pub_date DESC, t.id LIMIT ${opts.pageSize} OFFSET ${(opts.page - 1) * opts.pageSize}`,
    params,
  );
  return {
    data: rows.map(toTrade),
    meta: { page: opts.page, pageSize: opts.pageSize, total: countRow?.n ?? 0, delayDays: ent.delayDays },
  };
}

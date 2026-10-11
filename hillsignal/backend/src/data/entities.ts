import { query } from "../db/pool.js";
import type { Entitlements } from "../plans.js";

type Vis = Pick<Entitlements, "delayDays" | "historyDays">;

/** SQL predicate on alias `t` for trades the plan may see. Appends params. */
function visible(ent: Vis, params: unknown[]): string {
  const parts = ["true"];
  if (ent.delayDays > 0) {
    params.push(ent.delayDays);
    parts.push(`t.pub_date <= now() - make_interval(days => $${params.length}::int)`);
  }
  if (ent.historyDays !== null) {
    params.push(ent.historyDays);
    parts.push(`t.tx_date >= current_date - $${params.length}::int`);
  }
  return parts.join(" AND ");
}

interface PoliticianRow {
  id: string;
  name: string;
  party: string;
  chamber: string;
  state: string;
  district: string | null;
  photo_url: string | null;
  trade_count: number;
  last_trade_date: string | null;
  volume_min: number | null;
}

const toPolitician = (r: PoliticianRow) => ({
  id: r.id,
  name: r.name,
  party: r.party,
  chamber: r.chamber,
  state: r.state,
  district: r.district,
  photoUrl: r.photo_url,
  tradeCount: r.trade_count,
  lastTradeDate: r.last_trade_date,
  volumeMin: r.volume_min ?? 0,
});

export interface PoliticianFilters {
  search?: string;
  chamber?: "house" | "senate";
  party?: "D" | "R" | "I";
  state?: string;
  ids?: string[];
}

const POLITICIAN_SORTS = {
  "-lastTrade": "last_trade_date DESC NULLS LAST, p.name",
  "-tradeCount": "trade_count DESC, p.name",
  name: "p.last_name, p.first_name",
} as const;

export async function listPoliticians(
  f: PoliticianFilters,
  ent: Vis,
  opts: { page: number; pageSize: number; sort?: keyof typeof POLITICIAN_SORTS },
) {
  const params: unknown[] = [];
  const vis = visible(ent, params);
  const where: string[] = [];
  const add = (sql: string, v: unknown) => {
    params.push(v);
    where.push(sql.replaceAll("?", `$${params.length}`));
  };
  if (f.search) {
    // Substring match, plus trigram similarity so "pelosy" still finds "Pelosi".
    params.push(`%${f.search}%`, f.search);
    where.push(`(p.name ILIKE $${params.length - 1} OR word_similarity($${params.length}, p.name) > 0.5)`);
  }
  if (f.chamber) add("p.chamber = ?", f.chamber);
  if (f.party) add("p.party = ?", f.party);
  if (f.state) add("p.state = ?", f.state.toUpperCase());
  if (f.ids) add("p.id = ANY(?)", f.ids);
  const whereSql = where.length ? `WHERE ${where.join(" AND ")}` : "";
  const base = `
    FROM politicians p
    LEFT JOIN LATERAL (
      SELECT count(*)::int AS trade_count, max(t.tx_date) AS last_trade_date, sum(t.amount_min) AS volume_min
      FROM trades t WHERE t.politician_id = p.id AND ${vis}
    ) s ON true
    ${whereSql}`;
  const [c] = await query<{ n: number }>(`SELECT count(*)::int AS n ${base}`, params);
  const rows = await query<PoliticianRow>(
    `SELECT p.id, p.name, p.party, p.chamber, p.state, p.district, p.photo_url,
            s.trade_count, s.last_trade_date, s.volume_min
     ${base}
     ORDER BY ${POLITICIAN_SORTS[opts.sort ?? "-lastTrade"]}
     LIMIT ${opts.pageSize} OFFSET ${(opts.page - 1) * opts.pageSize}`,
    params,
  );
  return {
    data: rows.map(toPolitician),
    meta: { page: opts.page, pageSize: opts.pageSize, total: c?.n ?? 0, delayDays: ent.delayDays },
  };
}

export async function getPolitician(id: string, ent: Vis) {
  const { data } = await listPoliticians({ ids: [id] }, ent, { page: 1, pageSize: 1 });
  const p = data[0];
  if (!p) return null;
  const params: unknown[] = [id];
  const vis = visible(ent, params);
  const top = await query<{ id: number; name: string; ticker: string | null; sector: string | null; trade_count: number }>(
    `SELECT i.id, i.name, i.ticker, i.sector, count(*)::int AS trade_count
     FROM trades t JOIN issuers i ON i.id = t.issuer_id
     WHERE t.politician_id = $1 AND ${vis}
     GROUP BY i.id ORDER BY trade_count DESC, i.name LIMIT 10`,
    params,
  );
  const [counts] = await query<{ buys: number; sells: number }>(
    `SELECT count(*) FILTER (WHERE tx_type = 'buy')::int AS buys,
            count(*) FILTER (WHERE tx_type = 'sell')::int AS sells
     FROM trades t WHERE t.politician_id = $1 AND ${vis}`,
    params,
  );
  return {
    ...p,
    topIssuers: top.map((r) => ({ id: String(r.id), name: r.name, ticker: r.ticker, sector: r.sector, tradeCount: r.trade_count })),
    buyCount: counts?.buys ?? 0,
    sellCount: counts?.sells ?? 0,
  };
}

interface IssuerRow {
  id: number;
  name: string;
  ticker: string | null;
  sector: string | null;
  trade_count: number;
  politician_count: number;
  last_trade_date: string | null;
}

const toIssuer = (r: IssuerRow) => ({
  id: String(r.id),
  name: r.name,
  ticker: r.ticker,
  sector: r.sector,
  tradeCount: r.trade_count,
  politicianCount: r.politician_count,
  lastTradeDate: r.last_trade_date,
});

export async function listIssuers(
  f: { search?: string; ids?: string[] },
  ent: Vis,
  opts: { page: number; pageSize: number },
) {
  const params: unknown[] = [];
  const vis = visible(ent, params);
  const where: string[] = [];
  if (f.search) {
    params.push(`%${f.search}%`, f.search.toUpperCase());
    where.push(`(i.name ILIKE $${params.length - 1} OR i.ticker = $${params.length})`);
  }
  if (f.ids) {
    params.push(f.ids.map(Number).filter(Number.isFinite));
    where.push(`i.id = ANY($${params.length}::bigint[])`);
  }
  const base = `
    FROM issuers i
    JOIN LATERAL (
      SELECT count(*)::int AS trade_count, count(DISTINCT t.politician_id)::int AS politician_count,
             max(t.tx_date) AS last_trade_date
      FROM trades t WHERE t.issuer_id = i.id AND ${vis}
    ) s ON true
    ${where.length ? `WHERE ${where.join(" AND ")}` : ""}`;
  const [c] = await query<{ n: number }>(`SELECT count(*)::int AS n ${base}`, params);
  const rows = await query<IssuerRow>(
    `SELECT i.id, i.name, i.ticker, i.sector, s.trade_count, s.politician_count, s.last_trade_date ${base}
     ORDER BY s.trade_count DESC, i.name
     LIMIT ${opts.pageSize} OFFSET ${(opts.page - 1) * opts.pageSize}`,
    params,
  );
  return {
    data: rows.map(toIssuer),
    meta: { page: opts.page, pageSize: opts.pageSize, total: c?.n ?? 0, delayDays: ent.delayDays },
  };
}

export async function getIssuer(id: string, ent: Vis) {
  if (!/^\d+$/.test(id)) return null;
  const { data } = await listIssuers({ ids: [id] }, ent, { page: 1, pageSize: 1 });
  return data[0] ?? null;
}

export async function overview(ent: Vis) {
  const params: unknown[] = [];
  const vis = visible(ent, params);
  const [totals] = await query<{ trades: number; politicians: number; issuers: number }>(
    `SELECT count(*)::int AS trades, count(DISTINCT t.politician_id)::int AS politicians,
            count(DISTINCT t.issuer_id)::int AS issuers
     FROM trades t WHERE ${vis}`,
    params,
  );
  const [recent] = await query<{ trades: number; buys: number; sells: number; volume_min: number | null }>(
    `SELECT count(*)::int AS trades,
            count(*) FILTER (WHERE tx_type = 'buy')::int AS buys,
            count(*) FILTER (WHERE tx_type = 'sell')::int AS sells,
            sum(amount_min) AS volume_min
     FROM trades t WHERE ${vis} AND t.tx_date >= current_date - 30`,
    params,
  );
  const topTickers = await query<{ ticker: string; name: string; trade_count: number }>(
    `SELECT i.ticker, i.name, count(*)::int AS trade_count
     FROM trades t JOIN issuers i ON i.id = t.issuer_id
     WHERE ${vis} AND i.ticker IS NOT NULL AND t.tx_date >= current_date - 90
     GROUP BY i.id ORDER BY trade_count DESC, i.ticker LIMIT 10`,
    params,
  );
  const mostActive = await query<{ id: string; name: string; party: string; chamber: string; state: string; photo_url: string | null; trade_count: number }>(
    `SELECT p.id, p.name, p.party, p.chamber, p.state, p.photo_url, count(*)::int AS trade_count
     FROM trades t JOIN politicians p ON p.id = t.politician_id
     WHERE ${vis} AND t.tx_date >= current_date - 90
     GROUP BY p.id ORDER BY trade_count DESC, p.name LIMIT 10`,
    params,
  );
  return {
    totalTrades: totals?.trades ?? 0,
    totalPoliticians: totals?.politicians ?? 0,
    totalIssuers: totals?.issuers ?? 0,
    last30Days: {
      trades: recent?.trades ?? 0,
      buys: recent?.buys ?? 0,
      sells: recent?.sells ?? 0,
      volumeMin: recent?.volume_min ?? 0,
    },
    topTickers: topTickers.map((r) => ({ ticker: r.ticker, name: r.name, tradeCount: r.trade_count })),
    mostActive: mostActive.map((r) => ({
      id: r.id,
      name: r.name,
      party: r.party,
      chamber: r.chamber,
      state: r.state,
      photoUrl: r.photo_url,
      tradeCount: r.trade_count,
    })),
  };
}

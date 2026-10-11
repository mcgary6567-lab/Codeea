import { createHash } from "node:crypto";
import type pg from "pg";
import { sectorForTicker } from "./sectors.js";

/** One transaction row as read from a filing, before matching to our tables. */
export interface RawTrade {
  source: "house" | "senate" | "import" | "sample";
  filingId: string;
  rowIndex: number;
  member: { firstName: string; lastName: string; chamber: "house" | "senate"; state?: string | null; district?: string | null };
  owner: string | null; // raw: "SP", "JT", "DC", "Self", "Spouse", "Joint", "Child", ...
  assetName: string;
  ticker: string | null;
  assetType: string | null; // raw: "ST", "Stock", "Stock Option", ...
  txType: string; // raw: "P", "S", "S (partial)", "E", "Purchase", "Sale (Full)", ...
  txDate: string; // YYYY-MM-DD
  pubDate: Date;
  amount: string; // raw: "$1,001 - $15,000", "Over $50,000,000"
  sourceUrl: string | null;
}

export function parseUsDate(s: string): string | null {
  const m = /^(\d{1,2})\/(\d{1,2})\/(\d{4})$/.exec(s.trim());
  if (!m) return null;
  const [, mm, dd, yyyy] = m;
  const iso = `${yyyy}-${mm!.padStart(2, "0")}-${dd!.padStart(2, "0")}`;
  return Number.isNaN(Date.parse(iso)) ? null : iso;
}

export function parseAmount(raw: string): { min: number | null; max: number | null; label: string } {
  const label = raw.replace(/\s+/g, " ").trim();
  const nums = [...label.matchAll(/\$\s?([\d,]+)/g)].map((m) => Number(m[1]!.replace(/,/g, "")));
  if (/^over\b/i.test(label) || /\bover \$/i.test(label)) {
    return { min: nums[0] !== undefined ? nums[0] + 1 : null, max: null, label };
  }
  if (nums.length >= 2) return { min: nums[0]!, max: nums[1]!, label };
  if (nums.length === 1) return { min: nums[0]!, max: nums[0]!, label };
  return { min: null, max: null, label: label || "Unknown" };
}

export function normalizeTxType(raw: string): "buy" | "sell" | "exchange" | null {
  const s = raw.trim().toLowerCase();
  if (s === "p" || s.startsWith("purchase")) return "buy";
  if (s === "s" || s.startsWith("s (") || s.startsWith("sale")) return "sell";
  if (s === "e" || s.startsWith("exchange")) return "exchange";
  return null;
}

export function normalizeOwner(raw: string | null): "self" | "spouse" | "joint" | "dependent" | null {
  if (!raw) return null;
  const s = raw.trim().toLowerCase();
  if (s === "sp" || s === "spouse") return "spouse";
  if (s === "jt" || s === "joint") return "joint";
  if (s === "dc" || s === "child" || s === "dependent") return "dependent";
  if (s === "self" || s === "--" || s === "") return "self";
  return null;
}

const HOUSE_ASSET_CODES: Record<string, string> = {
  ST: "stock",
  OP: "option",
  EF: "etf",
  MF: "mutual_fund",
  GS: "government_security",
  CS: "corporate_bond",
  PS: "private_stock",
  CT: "crypto",
  OT: "other",
};

export function normalizeAssetType(raw: string | null): string | null {
  if (!raw) return null;
  const code = HOUSE_ASSET_CODES[raw.trim().toUpperCase()];
  if (code) return code;
  const s = raw.trim().toLowerCase();
  if (s.includes("option")) return "option";
  if (s.includes("stock")) return "stock";
  if (s.includes("etf") || s.includes("exchange traded")) return "etf";
  if (s.includes("mutual")) return "mutual_fund";
  if (s.includes("bond") || s.includes("note")) return "corporate_bond";
  if (s.includes("crypto")) return "crypto";
  return "other";
}

export function cleanTicker(raw: string | null | undefined): string | null {
  if (!raw) return null;
  const t = raw.trim().toUpperCase();
  return /^[A-Z][A-Z0-9.\-]{0,9}$/.test(t) ? t : null;
}

export function tradeId(r: Pick<RawTrade, "source" | "filingId" | "rowIndex">): string {
  return createHash("sha256").update(`${r.source}:${r.filingId}:${r.rowIndex}`).digest("hex").slice(0, 24);
}

const nameKey = (s: string) => s.toLowerCase().replace(/[^a-z0-9]+/g, " ").trim();

const slug = (s: string) =>
  s
    .normalize("NFKD")
    .replace(/[̀-ͯ]/g, "")
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-|-$/g, "");

export async function upsertIssuer(
  c: pg.PoolClient,
  assetName: string,
  ticker: string | null,
): Promise<number | null> {
  // Strip a trailing "(TICKER)" and the House asset-type tag from the display name.
  const name = assetName.replace(/\s*\[[A-Z]{2}\]\s*$/, "").replace(/\s*\([A-Z0-9.\-]{1,10}\)\s*$/, "").trim() || assetName;
  if (ticker) {
    const res = await c.query<{ id: number }>(
      `INSERT INTO issuers (ticker, name, sector) VALUES ($1, $2, $3)
       ON CONFLICT (ticker) DO UPDATE SET sector = COALESCE(issuers.sector, EXCLUDED.sector)
       RETURNING id`,
      [ticker, name, sectorForTicker(ticker)],
    );
    return res.rows[0]!.id;
  }
  const key = nameKey(name);
  if (!key) return null;
  const res = await c.query<{ id: number }>(
    `INSERT INTO issuers (name, name_key) VALUES ($1, $2)
     ON CONFLICT (name_key) DO UPDATE SET name = issuers.name
     RETURNING id`,
    [name, key],
  );
  return res.rows[0]!.id;
}

/**
 * Find the politician a filing belongs to. Matches chamber + last name (+ state when
 * known), using first name to break ties. Unknown filers get a placeholder record
 * with party = null that a later legislators sync can fill in.
 */
export async function matchPolitician(c: pg.PoolClient, m: RawTrade["member"]): Promise<string> {
  const last = m.lastName.trim();
  const first = m.firstName.trim();
  const params: unknown[] = [m.chamber, last.toLowerCase()];
  let sql = "SELECT id, first_name FROM politicians WHERE chamber = $1 AND lower(last_name) = $2";
  if (m.state) {
    params.push(m.state.toUpperCase());
    sql += " AND state = $3";
  }
  const candidates = (await c.query<{ id: string; first_name: string }>(sql, params)).rows;
  if (candidates.length === 1) return candidates[0]!.id;
  if (candidates.length > 1) {
    const f = first.toLowerCase();
    const hit = candidates.find((p) => {
      const pf = p.first_name.toLowerCase();
      return pf === f || pf.startsWith(f.slice(0, 3)) || f.startsWith(pf.slice(0, 3));
    });
    if (hit) return hit.id;
  }

  const id = `${m.chamber}-${m.state ? `${m.state.toLowerCase()}-` : ""}${slug(`${last}-${first}`)}`;
  await c.query(
    `INSERT INTO politicians (id, first_name, last_name, name, party, chamber, state, district)
     VALUES ($1, $2, $3, $4, NULL, $5, $6, $7) ON CONFLICT (id) DO NOTHING`,
    [id, first, last, `${first} ${last}`.trim(), m.chamber, (m.state ?? "").toUpperCase(), m.district ?? null],
  );
  return id;
}

/** Insert rows; returns ids of trades that were new. Rows that can't be parsed are skipped. */
export async function insertTrades(c: pg.PoolClient, rows: RawTrade[]): Promise<{ inserted: string[]; skipped: number }> {
  const inserted: string[] = [];
  let skipped = 0;
  for (const r of rows) {
    const txType = normalizeTxType(r.txType);
    if (!txType || !/^\d{4}-\d{2}-\d{2}$/.test(r.txDate)) {
      skipped++;
      continue;
    }
    const politicianId = await matchPolitician(c, r.member);
    const ticker = cleanTicker(r.ticker);
    const issuerId = await upsertIssuer(c, r.assetName, ticker);
    const amt = parseAmount(r.amount);
    const id = tradeId(r);
    const res = await c.query(
      `INSERT INTO trades (id, politician_id, issuer_id, asset_name, asset_type, tx_type, tx_date, pub_date,
                           owner, amount_min, amount_max, amount_label, source, source_url)
       VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14)
       ON CONFLICT (id) DO NOTHING`,
      [
        id,
        politicianId,
        issuerId,
        r.assetName,
        normalizeAssetType(r.assetType),
        txType,
        r.txDate,
        r.pubDate,
        normalizeOwner(r.owner),
        amt.min,
        amt.max,
        amt.label,
        r.source,
        r.sourceUrl,
      ],
    );
    if (res.rowCount) inserted.push(id);
  }
  return { inserted, skipped };
}

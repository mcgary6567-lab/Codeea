import { query, withTransaction } from "../db/pool.js";
import { notifyNewTrades } from "../notify.js";
import { fetchHouseIndex, fetchHousePtr } from "./house.js";
import { LEGISLATORS_URL, syncLegislators, type Legislator } from "./legislators.js";
import { insertTrades, type RawTrade } from "./normalize.js";
import { fetchSenateFilings, fetchSenatePtr } from "./senate.js";
import { politeFetch, sleep } from "./fetch.js";

type Log = (msg: string) => void;

export interface IngestSummary {
  filings: number;
  inserted: number;
  skippedRows: number;
  noText: number;
  errors: number;
}

async function alreadyProcessed(source: string): Promise<Set<string>> {
  const rows = await query<{ filing_id: string }>(
    "SELECT filing_id FROM filings WHERE source = $1 AND status <> 'error'",
    [source],
  );
  return new Set(rows.map((r) => r.filing_id));
}

/** Store one filing's rows atomically and remember the filing. Returns new trade ids. */
export async function storeFiling(
  source: string,
  filingId: string,
  rows: RawTrade[],
  status: "parsed" | "no_text" = "parsed",
): Promise<{ inserted: string[]; skipped: number }> {
  return withTransaction(async (c) => {
    const result = await insertTrades(c, rows);
    await c.query(
      `INSERT INTO filings (source, filing_id, status, trade_count) VALUES ($1,$2,$3,$4)
       ON CONFLICT (source, filing_id) DO UPDATE SET status = EXCLUDED.status, trade_count = EXCLUDED.trade_count,
         detail = NULL, processed_at = now()`,
      [source, filingId, status, result.inserted.length],
    );
    return result;
  });
}

async function recordError(source: string, filingId: string, err: unknown) {
  await query(
    `INSERT INTO filings (source, filing_id, status, detail) VALUES ($1,$2,'error',$3)
     ON CONFLICT (source, filing_id) DO UPDATE SET status = 'error', detail = EXCLUDED.detail, processed_at = now()`,
    [source, filingId, String(err).slice(0, 500)],
  );
}

export async function ingestLegislators(log: Log): Promise<number> {
  const res = await politeFetch(LEGISLATORS_URL);
  if (!res.ok) throw new Error(`legislators: HTTP ${res.status}`);
  const data = (await res.json()) as Legislator[];
  const n = await withTransaction((c) => syncLegislators(c, data));
  log(`legislators: synced ${n}`);
  return n;
}

export async function ingestHouse(year: number, log: Log, limit = Infinity): Promise<IngestSummary & { newIds: string[] }> {
  const sum = { filings: 0, inserted: 0, skippedRows: 0, noText: 0, errors: 0, newIds: [] as string[] };
  const done = await alreadyProcessed("house");
  const filings = (await fetchHouseIndex(year)).filter((f) => !done.has(f.docId)).slice(0, limit);
  log(`house ${year}: ${filings.length} new PTR filings`);
  for (const f of filings) {
    try {
      const { rows, hasText } = await fetchHousePtr(f);
      const r = await storeFiling("house", f.docId, rows, hasText ? "parsed" : "no_text");
      sum.filings++;
      if (!hasText) sum.noText++;
      sum.inserted += r.inserted.length;
      sum.skippedRows += r.skipped;
      sum.newIds.push(...r.inserted);
    } catch (err) {
      sum.errors++;
      log(`house ${f.docId}: ${String(err)}`);
      await recordError("house", f.docId, err);
    }
    await sleep(500); // be polite to the Clerk's server
  }
  return sum;
}

export async function ingestSenate(days: number, log: Log, limit = Infinity): Promise<IngestSummary & { newIds: string[] }> {
  const sum = { filings: 0, inserted: 0, skippedRows: 0, noText: 0, errors: 0, newIds: [] as string[] };
  const to = new Date();
  const from = new Date(to.getTime() - days * 86_400_000);
  const iso = (d: Date) => d.toISOString().slice(0, 10);
  const done = await alreadyProcessed("senate");
  const { session, filings } = await fetchSenateFilings(iso(from), iso(to));
  const todo = filings.filter((f) => !done.has(f.reportId)).slice(0, limit);
  log(`senate: ${todo.length} new PTR filings in the last ${days} days`);
  for (const f of todo) {
    try {
      if (f.kind === "paper") {
        // Scanned paper filings: no machine-readable rows.
        await storeFiling("senate", f.reportId, [], "no_text");
        sum.noText++;
      } else {
        const rows = await fetchSenatePtr(session, f);
        const r = await storeFiling("senate", f.reportId, rows);
        sum.inserted += r.inserted.length;
        sum.skippedRows += r.skipped;
        sum.newIds.push(...r.inserted);
      }
      sum.filings++;
    } catch (err) {
      sum.errors++;
      log(`senate ${f.reportId}: ${String(err)}`);
      await recordError("senate", f.reportId, err);
    }
    await sleep(1000);
  }
  return sum;
}

/** Full refresh used by the scheduler: members, then both chambers, then alerts. */
export async function runIngest(log: Log = console.log): Promise<void> {
  const year = new Date().getUTCFullYear();
  const newIds: string[] = [];
  const steps: [string, () => Promise<unknown>][] = [
    ["legislators", () => ingestLegislators(log)],
    ["house", async () => newIds.push(...(await ingestHouse(year, log)).newIds)],
    ["senate", async () => newIds.push(...(await ingestSenate(14, log)).newIds)],
  ];
  for (const [name, step] of steps) {
    try {
      await step();
    } catch (err) {
      log(`${name} failed: ${String(err)}`); // one source being down shouldn't stop the others
    }
  }
  const sent = await notifyNewTrades(newIds, log);
  log(`ingest done: ${newIds.length} new trades, ${sent} alerts`);
}

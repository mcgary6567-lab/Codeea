import { pathToFileURL } from "node:url";
import { config } from "../config.js";
import { insertTrades, type RawTrade } from "../ingest/normalize.js";
import { migrate } from "./migrate.js";
import { pool, withTransaction } from "./pool.js";

// FICTIONAL members for local development and screenshots. Never attach sample
// trades to real people: fabricated disclosures about real officials are misleading.
const MEMBERS = [
  { first: "Avery", last: "Lindqvist", party: "D", chamber: "senate", state: "MN", district: null },
  { first: "Jordan", last: "Okafor", party: "R", chamber: "senate", state: "TX", district: null },
  { first: "Riley", last: "Castellanos", party: "D", chamber: "house", state: "CA", district: "18" },
  { first: "Morgan", last: "Whitfield", party: "R", chamber: "house", state: "FL", district: "7" },
  { first: "Casey", last: "Nakamura", party: "I", chamber: "senate", state: "VT", district: null },
  { first: "Taylor", last: "Brennan", party: "R", chamber: "house", state: "OH", district: "12" },
  { first: "Quinn", last: "Adeyemi", party: "D", chamber: "house", state: "NY", district: "10" },
  { first: "Harper", last: "Volkov", party: "R", chamber: "house", state: "AZ", district: "5" },
] as const;

const ASSETS = [
  ["NVIDIA Corporation", "NVDA"], ["Apple Inc.", "AAPL"], ["Microsoft Corporation", "MSFT"],
  ["Amazon.com, Inc.", "AMZN"], ["Alphabet Inc. - Class A", "GOOGL"], ["Meta Platforms, Inc.", "META"],
  ["Tesla, Inc.", "TSLA"], ["Lockheed Martin Corporation", "LMT"], ["Exxon Mobil Corporation", "XOM"],
  ["JPMorgan Chase & Co.", "JPM"], ["UnitedHealth Group Incorporated", "UNH"], ["Pfizer Inc.", "PFE"],
  ["Broadcom Inc.", "AVGO"], ["Palantir Technologies Inc.", "PLTR"], ["The Boeing Company", "BA"],
  ["Costco Wholesale Corporation", "COST"], ["Visa Inc.", "V"], ["Eli Lilly and Company", "LLY"],
  ["Advanced Micro Devices, Inc.", "AMD"], ["Chevron Corporation", "CVX"],
] as const;

const AMOUNTS = ["$1,001 - $15,000", "$15,001 - $50,000", "$50,001 - $100,000", "$100,001 - $250,000", "$250,001 - $500,000", "$500,001 - $1,000,000", "$1,000,001 - $5,000,000"];
const AMOUNT_WEIGHTS = [50, 22, 12, 8, 4, 3, 1];

// Small deterministic PRNG so every seed run produces the same data.
function rng(seed: number) {
  return () => {
    seed = (seed * 1664525 + 1013904223) % 4294967296;
    return seed / 4294967296;
  };
}

export function sampleTrades(count = 400, now = new Date()): RawTrade[] {
  const rand = rng(42);
  const pick = <T>(xs: readonly T[]) => xs[Math.floor(rand() * xs.length)]!;
  const weighted = () => {
    let r = rand() * AMOUNT_WEIGHTS.reduce((a, b) => a + b, 0);
    for (let i = 0; i < AMOUNTS.length; i++) if ((r -= AMOUNT_WEIGHTS[i]!) < 0) return AMOUNTS[i]!;
    return AMOUNTS[0]!;
  };
  const day = 86_400_000;
  const rows: RawTrade[] = [];
  for (let i = 0; i < count; i++) {
    const m = pick(MEMBERS);
    const [assetName, ticker] = pick(ASSETS);
    const txAgo = Math.floor(rand() * 420) + 5;
    const gap = Math.floor(rand() * 50) + 3;
    const tx = new Date(now.getTime() - txAgo * day);
    const pub = new Date(Math.min(now.getTime() - day, tx.getTime() + gap * day));
    rows.push({
      source: "sample",
      filingId: `sample-${Math.floor(i / 4)}`,
      rowIndex: i % 4,
      member: { firstName: m.first, lastName: m.last, chamber: m.chamber, state: m.state, district: m.district },
      owner: pick(["self", "self", "spouse", "joint", "dependent"]),
      assetName: `${assetName} (${ticker})`,
      ticker,
      assetType: rand() < 0.08 ? "OP" : "ST",
      txType: rand() < 0.55 ? "P" : rand() < 0.85 ? "S" : "S (partial)",
      txDate: tx.toISOString().slice(0, 10),
      pubDate: pub,
      amount: weighted(),
      sourceUrl: null,
    });
  }
  return rows;
}

export async function seed(): Promise<number> {
  await migrate(() => {});
  return withTransaction(async (c) => {
    for (const m of MEMBERS) {
      const id = `sample-${m.last.toLowerCase()}`;
      await c.query(
        `INSERT INTO politicians (id, first_name, last_name, name, party, chamber, state, district)
         VALUES ($1,$2,$3,$4,$5,$6,$7,$8) ON CONFLICT (id) DO NOTHING`,
        [id, m.first, m.last, `${m.first} ${m.last} (Sample)`, m.party, m.chamber, m.state, m.district],
      );
    }
    const { inserted } = await insertTrades(c, sampleTrades());
    return inserted.length;
  });
}

if (import.meta.url === pathToFileURL(process.argv[1] ?? "").href) {
  if (config.NODE_ENV === "production" && !process.argv.includes("--force")) {
    console.error("Refusing to seed sample data in production (pass --force if you really mean it).");
    process.exit(1);
  }
  seed()
    .then((n) => console.log(`seeded ${n} sample trades (fictional members)`))
    .catch((err) => {
      console.error(err);
      process.exitCode = 1;
    })
    .finally(() => pool.end());
}

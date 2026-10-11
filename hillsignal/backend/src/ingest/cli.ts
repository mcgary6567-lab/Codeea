import { readFile } from "node:fs/promises";
import { parseArgs } from "node:util";
import { z } from "zod";
import { pool } from "../db/pool.js";
import { migrate } from "../db/migrate.js";
import { notifyNewTrades } from "../notify.js";
import { ingestHouse, ingestLegislators, ingestSenate, runIngest, storeFiling } from "./run.js";
import type { RawTrade } from "./normalize.js";

const USAGE = `Usage: npm run ingest -- <command> [options]

Commands:
  all                         members + House (this year) + Senate (last 14 days) + alerts
  legislators                 sync members of Congress (party, state, photo)
  house  [--year 2025] [--limit N]
  senate [--days 30]   [--limit N]
  import <file.json>          load trades from a licensed feed / export (see docs)`;

// Shape for `import`: an array of trades from any source you are licensed to use.
const ImportRow = z.object({
  filingId: z.string(),
  rowIndex: z.number().int().nonnegative(),
  member: z.object({
    firstName: z.string(),
    lastName: z.string(),
    chamber: z.enum(["house", "senate"]),
    state: z.string().length(2).optional(),
    district: z.string().optional(),
  }),
  owner: z.string().nullable().default(null),
  assetName: z.string().min(1),
  ticker: z.string().nullable().default(null),
  assetType: z.string().nullable().default(null),
  txType: z.string(),
  txDate: z.string().regex(/^\d{4}-\d{2}-\d{2}$/),
  pubDate: z.coerce.date(),
  amount: z.string(),
  sourceUrl: z.string().url().nullable().default(null),
});

async function main() {
  const { positionals, values } = parseArgs({
    allowPositionals: true,
    options: {
      year: { type: "string", default: String(new Date().getUTCFullYear()) },
      days: { type: "string", default: "30" },
      limit: { type: "string" },
    },
  });
  const [cmd, arg] = positionals;
  const limit = values.limit ? Number(values.limit) : Infinity;
  await migrate(() => {});

  switch (cmd) {
    case "all":
      await runIngest();
      break;
    case "legislators":
      await ingestLegislators(console.log);
      break;
    case "house": {
      const r = await ingestHouse(Number(values.year), console.log, limit);
      console.log(r);
      await notifyNewTrades(r.newIds, console.log);
      break;
    }
    case "senate": {
      const r = await ingestSenate(Number(values.days), console.log, limit);
      console.log(r);
      await notifyNewTrades(r.newIds, console.log);
      break;
    }
    case "import": {
      if (!arg) throw new Error("import needs a file path");
      const rows = z.array(ImportRow).parse(JSON.parse(await readFile(arg, "utf8")));
      const byFiling = new Map<string, RawTrade[]>();
      for (const r of rows) {
        const list = byFiling.get(r.filingId) ?? [];
        list.push({ ...r, source: "import" });
        byFiling.set(r.filingId, list);
      }
      const newIds: string[] = [];
      for (const [filingId, list] of byFiling) newIds.push(...(await storeFiling("import", filingId, list)).inserted);
      console.log(`imported ${newIds.length} new trades from ${byFiling.size} filings`);
      await notifyNewTrades(newIds, console.log);
      break;
    }
    default:
      console.log(USAGE);
      process.exitCode = cmd ? 1 : 0;
  }
}

main()
  .catch((err) => {
    console.error(err);
    process.exitCode = 1;
  })
  .finally(() => pool.end());

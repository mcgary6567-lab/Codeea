import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import os from "node:os";
import path from "node:path";
import fs from "node:fs";
import type { Server } from "node:http";

const dir = fs.mkdtempSync(path.join(os.tmpdir(), "pumpai-batchb-"));
process.env.DB_PATH = path.join(dir, "test.db");
process.env.NODE_ENV = "test";
process.env.ANTHROPIC_API_KEY = "";
process.env.WA_TOKEN = "";

let server: Server;
let base = "";
let db: typeof import("../src/db.js");
let token = "";
async function call(method: string, url: string, body?: unknown) {
  const res = await fetch(base + url, { method, headers: { "content-type": "application/json", ...(token ? { authorization: `Bearer ${token}` } : {}) }, body: body ? JSON.stringify(body) : undefined });
  return { status: res.status, data: (await res.json().catch(() => null)) as any };
}
const ok = (r: { status: number; data: any }, msg: string) => { assert.equal(r.status, 200, `${msg}: ${JSON.stringify(r.data)}`); return r.data; };

before(async () => {
  const { app } = await import("../src/index.js");
  db = await import("../src/db.js");
  await new Promise<void>((r) => { server = app.listen(0, () => r()); });
  base = `http://127.0.0.1:${(server.address() as any).port}`;
  token = (await call("POST", "/api/auth/login", { email: "admin@pumpai.pk", password: "demo1234" })).data.token;
});
after(() => server?.close());

test("dip with temperature stores a 15°C-corrected volume (petrol expands in heat); books still tally", async () => {
  const { tallyBooks } = await import("./helpers/tally.js");
  const tank = db.get("SELECT t.id, t.product, t.current_l FROM tanks t JOIN stations s ON s.id=t.station_id WHERE s.tenant_id=1 AND t.product='PMG' ORDER BY t.current_l DESC LIMIT 1")!;
  const measured = Math.round(tank.current_l); // dip ≈ book so no big-variance block
  const r = ok(await call("POST", "/api/stock/dip", { tank_id: tank.id, measured_l: measured, temperature: 35, confirm: true }), "dip 35C");
  assert.ok(r.corrected_l != null, "corrected volume stored");
  // at 35°C petrol has expanded, so the 15°C volume is LESS than measured
  assert.ok(r.corrected_l < measured, `15C volume ${r.corrected_l} < measured ${measured}`);
  // α(PMG)=0.00120 → factor 1 − 0.00120*(35−15)=0.976
  assert.ok(Math.abs(r.corrected_l - measured * 0.976) <= Math.max(1, measured * 0.0005), "VCF ≈ 2.4% for +20°C petrol");
  await tallyBooks("vcf");
});

test("dip-trend returns per-tank variance history", async () => {
  const tank = db.get("SELECT t.id FROM tanks t JOIN stations s ON s.id=t.station_id WHERE s.tenant_id=1 ORDER BY t.id LIMIT 1")!;
  const trend = ok(await call("GET", `/api/analysis/dip-trend?tank_id=${tank.id}&days=90`), "trend");
  assert.ok(Array.isArray(trend) && trend.length === 1, "one tank");
  assert.ok(Array.isArray(trend[0].dips), "dips array");
  assert.ok("avg_variance_pct" in trend[0] && "worst_pct" in trend[0]);
});

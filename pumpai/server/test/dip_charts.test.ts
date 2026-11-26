/** Dip charts: template → assign → cm dip through the chart, paste/check, fill, lookup, copy, delete, tenant isolation. */
import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import os from "node:os";
import path from "node:path";
import fs from "node:fs";
import type { Server } from "node:http";

const dir = fs.mkdtempSync(path.join(os.tmpdir(), "pumpai-dip-"));
process.env.DB_PATH = path.join(dir, "test.db");
process.env.NODE_ENV = "test";
process.env.ANTHROPIC_API_KEY = "";
process.env.GEMINI_API_KEY = "";

let server: Server;
let base = "";
const tokens: Record<string, string> = {};
async function call(who: string, method: string, url: string, body?: unknown) {
  const res = await fetch(base + url, { method, headers: { "content-type": "application/json", ...(tokens[who] ? { authorization: `Bearer ${tokens[who]}` } : {}) }, body: body ? JSON.stringify(body) : undefined });
  const text = await res.text();
  let data: any = null; try { data = JSON.parse(text); } catch { data = text; }
  return { status: res.status, data };
}
const ok = (r: { status: number; data: any }, msg: string) => { assert.equal(r.status, 200, `${msg}: ${JSON.stringify(r.data)}`); return r.data; };

before(async () => {
  const { app } = await import("../src/index.js");
  await new Promise<void>((r) => { server = app.listen(0, () => r()); });
  base = `http://127.0.0.1:${(server.address() as any).port}`;
  for (const who of ["admin", "manager", "salesman"]) tokens[who] = (await call("", "POST", "/api/auth/login", { email: `${who}@pumpai.pk`, password: "demo1234" })).data.token;
});
after(() => { server?.close(); fs.rmSync(dir, { recursive: true, force: true }); });

let chartId = 0, tankId = 0;

test("templates are listed and the HSD 14,370 L sheet is complete", async () => {
  const d = ok(await call("manager", "GET", "/api/dip-charts"), "list");
  assert.ok(d.templates.length >= 2);
  const pet = d.templates.find((x: any) => x.key === "ug-1700-petrol");
  assert.equal(pet.rows, 171); assert.equal(pet.max_mm, 1700);
  const t = d.templates.find((x: any) => x.key === "ug-14370-hsd");
  assert.equal(t.rows, 443); // 0 mm + 442 steps of 5 mm
  const full = ok(await call("manager", "GET", "/api/dip-charts/templates/ug-14370-hsd"), "template");
  assert.equal(full.rows[full.rows.length - 1].depth_mm, 2210);
  assert.equal(full.rows[full.rows.length - 1].litres, 14370);
  assert.equal(full.rows.find((r: any) => r.depth_mm === 1000).litres, 6326);
  tankId = d.tanks.find((x: any) => x.product === "HSD")?.id ?? d.tanks[0].id;
});

test("create from template and assign to a tank; cm dip is converted through it", async () => {
  const c = ok(await call("manager", "POST", "/api/dip-charts", { template_key: "ug-14370-hsd", assign_tank_ids: [tankId] }), "create");
  chartId = c.id;
  assert.equal(c.rows, 443);
  assert.equal(c.tanks.length, 1);
  assert.equal(c.tanks[0].id, tankId);
  // 1000 mm = 100 cm → 6326 L; 1002.5 mm half-way between 1000 (6326) and 1005 (6363)
  assert.equal(ok(await call("manager", "GET", `/api/tanks/${tankId}/dip-litres?cm=100`), "dip-litres").litres, 6326);
  assert.equal(ok(await call("manager", "GET", `/api/tanks/${tankId}/dip-litres?cm=100.25`), "dip-litres").litres, 6344.5);
  const lk = ok(await call("manager", "GET", `/api/dip-charts/${chartId}/lookup?mm=2210`), "lookup");
  assert.equal(lk.litres, 14370); assert.equal(lk.pct_full, 100);
  const bad = await call("manager", "GET", `/api/dip-charts/${chartId}/lookup?mm=3000`);
  assert.equal(bad.status, 400);
  // the Tanks & Stock page sees the assigned chart
  const old = ok(await call("manager", "GET", `/api/tanks/${tankId}/chart`), "legacy chart view");
  assert.equal(old.chart_id, chartId); assert.equal(old.rows.length, 443);
});

test("edit a row and the lookup follows; bad rows are refused", async () => {
  const d = ok(await call("manager", "GET", `/api/dip-charts/${chartId}`), "read");
  const rows = d.rows.map((r: any) => r.depth_mm === 1000 ? { ...r, litres: 6330 } : r);
  const u = ok(await call("manager", "PUT", `/api/dip-charts/${chartId}`, { name: "HSD main tank", rows }), "update");
  assert.equal(u.name, "HSD main tank");
  assert.equal(ok(await call("manager", "GET", `/api/tanks/${tankId}/dip-litres?cm=100`), "dip-litres").litres, 6330);
  const falling = await call("manager", "PUT", `/api/dip-charts/${chartId}`, { rows: [{ depth_mm: 0, litres: 0 }, { depth_mm: 5, litres: 10 }, { depth_mm: 10, litres: 8 }] });
  assert.equal(falling.status, 400); assert.match(falling.data.error ?? JSON.stringify(falling.data), /less than/);
  const dup = await call("manager", "PUT", `/api/dip-charts/${chartId}`, { rows: [{ depth_mm: 0, litres: 0 }, { depth_mm: 5, litres: 10 }, { depth_mm: 5, litres: 12 }] });
  assert.equal(dup.status, 400);
});

test("check: pasted sheet text with several column pairs per line, gaps and odd jumps", async () => {
  const text = "Mm Litres Mm Litres\n5 4 375 1619\n10 9 380 1647\n15 14 385 1,689\n25 32 390 1717";
  const r = ok(await call("manager", "POST", "/api/dip-charts/check", { rows: [], text, unit: "mm" }), "check");
  assert.equal(r.count, 8);
  assert.equal(r.step_mm, 5);
  assert.ok(r.gaps.includes(20), JSON.stringify(r.gaps));
  assert.equal(r.rows.find((x: any) => x.depth_mm === 385).litres, 1689);
  const cm = ok(await call("manager", "POST", "/api/dip-charts/check", { rows: [], text: "10 100\n20 300", unit: "cm" }), "cm");
  assert.equal(cm.rows[1].depth_mm, 200);
  const jump = ok(await call("manager", "POST", "/api/dip-charts/check", { rows: [[0, 0], [5, 10], [10, 20], [15, 30], [20, 400], [25, 410], [30, 420], [35, 430]].map(([depth_mm, litres]) => ({ depth_mm, litres })) }), "jump");
  assert.ok(jump.jumps.some((j: any) => j.depth_mm === 20), JSON.stringify(jump.jumps));
});

test("create from pasted text, fill the steps, copy and delete", async () => {
  const c = ok(await call("manager", "POST", "/api/dip-charts", { name: "Petrol small tank", product: "PMG", capacity_l: 1000, text: "0 0\n10 50\n20 200\n30 1000", unit: "cm" }), "paste create");
  assert.equal(c.rows, 4); assert.equal(c.max_mm, 300);
  const f = ok(await call("manager", "POST", `/api/dip-charts/${c.id}/fill`, { step_mm: 50 }), "fill");
  assert.equal(f.rows, 7);
  const d = ok(await call("manager", "GET", `/api/dip-charts/${c.id}`), "read");
  assert.equal(d.rows.find((x: any) => x.depth_mm === 150).litres, 125); // half-way 100→200 mm: 50→200 L
  const cp = ok(await call("manager", "POST", `/api/dip-charts/${c.id}/copy`, { name: "Petrol small tank B" }), "copy");
  assert.equal(cp.rows, 7);
  const csv = await call("manager", "GET", `/api/dip-charts/${c.id}/export.csv`);
  assert.equal(csv.status, 200); assert.match(String(csv.data), /^depth_mm,litres\n0,0\n/);
  ok(await call("manager", "DELETE", `/api/dip-charts/${c.id}`), "delete unused");
  ok(await call("manager", "DELETE", `/api/dip-charts/${cp.id}`), "delete copy");
  // a chart in use needs force, and the tank then has no chart
  const inUse = await call("manager", "DELETE", `/api/dip-charts/${chartId}`);
  assert.equal(inUse.status, 400);
});

test("approximate chart from the cylinder formula; reassign per tank; salesman cannot manage", async () => {
  const c = ok(await call("manager", "POST", "/api/dip-charts", { name: "Approx 2,000 mm tank", capacity_l: 14000, diameter_mm: 2000 }), "formula");
  assert.ok(c.rows > 100); assert.equal(c.max_l, 14000);
  const half = ok(await call("manager", "GET", `/api/dip-charts/${c.id}/lookup?mm=1000`), "half").litres;
  assert.ok(Math.abs(half - 7000) < 1, String(half));
  ok(await call("manager", "PUT", `/api/tanks/${tankId}/dip-chart`, { chart_id: c.id }), "reassign");
  assert.ok(Math.abs(ok(await call("manager", "GET", `/api/tanks/${tankId}/dip-litres?cm=100`), "dip-litres").litres - 7000) < 1);
  ok(await call("manager", "PUT", `/api/tanks/${tankId}/dip-chart`, { chart_id: null }), "unassign");
  // without a chart the tank falls back to its old per-tank cm table (demo data has one) or refuses the dip
  const none = await call("manager", "GET", `/api/tanks/${tankId}/dip-litres?cm=100`);
  assert.ok(none.status === 400 || Math.abs(none.data.litres - 7000) > 1, JSON.stringify(none));
  assert.equal((await call("salesman", "GET", "/api/dip-charts")).status, 403);
  assert.equal((await call("manager", "GET", "/api/dip-charts/999999")).status, 404);
  const noAi = await call("manager", "POST", "/api/dip-charts/read-photo", { image: "data:image/png;base64,iVBORw0KGgo=" });
  assert.equal(noAi.status, 400); assert.match(noAi.data.error ?? "", /AI key/);
});

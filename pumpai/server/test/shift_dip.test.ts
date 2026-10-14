/** Per-shift wet-stock reconciliation: opening + closing dip, meter sale vs dip sale, variance, tank set to dip, tally stays. */
import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import os from "node:os";
import path from "node:path";
import fs from "node:fs";
import type { Server } from "node:http";

const dir = fs.mkdtempSync(path.join(os.tmpdir(), "pumpai-shiftdip-"));
process.env.DB_PATH = path.join(dir, "test.db");
process.env.NODE_ENV = "test";
process.env.ANTHROPIC_API_KEY = "";
process.env.WA_TOKEN = "";

let server: Server; let base = ""; let db: typeof import("../src/db.js");
const tokens: Record<string, string> = {};
async function call(who: string, method: string, url: string, body?: unknown) {
  const res = await fetch(base + url, { method, headers: { "content-type": "application/json", ...(tokens[who] ? { authorization: `Bearer ${tokens[who]}` } : {}) }, body: body ? JSON.stringify(body) : undefined });
  return { status: res.status, data: (await res.json().catch(() => null)) as any };
}
const ok = (r: { status: number; data: any }, m: string) => { assert.equal(r.status, 200, `${m}: ${JSON.stringify(r.data)}`); return r.data; };
const near = (a: number, b: number, m?: string) => assert.ok(Math.abs(a - b) < 0.05, `${m ?? ""} ${a} ≈ ${b}`);

before(async () => {
  const { app } = await import("../src/index.js");
  db = await import("../src/db.js");
  await new Promise<void>((r) => { server = app.listen(0, () => r()); });
  base = `http://127.0.0.1:${(server.address() as any).port}`;
  for (const who of ["admin", "manager", "salesman"]) tokens[who] = (await call("", "POST", "/api/auth/login", { email: `${who}@pumpai.pk`, password: "demo1234" })).data.token;
});
after(() => { server?.close(); fs.rmSync(dir, { recursive: true, force: true }); });

test("opening + closing dip reconcile against the meter; variance flagged, tank set to the dip, books tally", async () => {
  // a station whose salesman shift has a PMG tank + nozzle
  const noz = db.get("SELECT n.id nozzle_id, n.tank_id, n.station_id, n.totalizer, t.product, t.current_l FROM nozzles n JOIN tanks t ON t.id=n.tank_id JOIN stations s ON s.id=n.station_id WHERE s.tenant_id=1 AND n.active=1 AND t.product='PMG' ORDER BY n.id LIMIT 1") as any;
  assert.ok(noz, "a PMG nozzle");
  db.run("UPDATE tanks SET current_l = 5000 WHERE id=?", noz.tank_id);
  // close any open shift on this station (demo seeds one)
  for (const sh of db.all("SELECT id FROM shifts WHERE station_id=? AND status='open'", noz.station_id) as any[]) db.run("UPDATE shifts SET status='closed', closed_at=? WHERE id=?", new Date().toISOString(), sh.id);

  const open = ok(await call("salesman", "POST", "/api/shifts/open", { station_id: noz.station_id, readings: { [noz.nozzle_id]: noz.totalizer }, dips: [{ tank_id: noz.tank_id, measured_l: 5000 }] }), "open shift with opening dip");
  const shid = open.id;
  assert.equal(db.get("SELECT measured_l FROM shift_dips WHERE shift_id=? AND phase='open'", shid).measured_l, 5000, "opening dip stored");

  // meter will show 100 L pumped; record 100 L cash so nothing extra is booked at close
  ok(await call("salesman", "POST", "/api/sales", { station_id: noz.station_id, product: "PMG", litres: 100, payment_method: "cash" }), "100 L cash sale");

  // closing: meter +100 (sold 100), closing dip 4890 → dip sold = 5000 − 4890 = 110 → variance = 100 − 110 = −10 (tank lost 10 more)
  const prev = ok(await call("salesman", "POST", `/api/shifts/${shid}/preview`, { readings: { [noz.nozzle_id]: noz.totalizer + 100 }, dips: [{ tank_id: noz.tank_id, measured_l: 4890 }] }), "preview");
  const prow = prev.dip_recon.find((d: any) => d.tank_id === noz.tank_id);
  near(prow.meter_l, 100, "preview meter"); near(prow.dip_sold_l, 110, "preview dip sold"); near(prow.variance_l, -10, "preview variance");
  // preview must not persist the closing dip or move the tank
  assert.equal(db.get("SELECT COUNT(*) n FROM shift_dips WHERE shift_id=? AND phase='close'", shid).n, 0, "preview saved nothing");

  const closed = ok(await call("salesman", "POST", `/api/shifts/${shid}/close`, { readings: { [noz.nozzle_id]: noz.totalizer + 100 }, cash_actual: 0, dips: [{ tank_id: noz.tank_id, measured_l: 4890 }] }), "close with closing dip");
  const row = closed.dip_recon.find((d: any) => d.tank_id === noz.tank_id);
  near(row.meter_l, 100); near(row.dip_sold_l, 110); near(row.variance_l, -10, "closed variance");
  near(db.get("SELECT current_l FROM tanks WHERE id=?", noz.tank_id).current_l, 4890, "tank set to the closing dip");

  // report endpoint
  const rep = ok(await call("manager", "GET", `/api/shifts/${shid}/dip-recon`), "dip-recon endpoint");
  near(rep.rows.find((d: any) => d.tank_id === noz.tank_id).variance_l, -10, "report variance");

  const { tallyBooks } = await import("./helpers/tally.js");
  await tallyBooks("shift dip recon");
  assert.equal(ok(await call("admin", "GET", "/api/books/check"), "books").ok, true, "books tally after dip close");
});

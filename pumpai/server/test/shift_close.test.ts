import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import os from "node:os";
import path from "node:path";
import fs from "node:fs";
import type { Server } from "node:http";

const dir = fs.mkdtempSync(path.join(os.tmpdir(), "pumpai-rushclose-"));
process.env.DB_PATH = path.join(dir, "test.db");
process.env.NODE_ENV = "test";
process.env.ANTHROPIC_API_KEY = "";
process.env.WA_TOKEN = "";

let server: Server;
let base = "";
let db: typeof import("../src/db.js");
const tokens: Record<string, string> = {};
async function call(who: string, method: string, url: string, body?: unknown) {
  const res = await fetch(base + url, { method, headers: { "content-type": "application/json", ...(tokens[who] ? { authorization: `Bearer ${tokens[who]}` } : {}) }, body: body ? JSON.stringify(body) : undefined });
  return { status: res.status, data: (await res.json().catch(() => null)) as any };
}
const ok = (r: { status: number; data: any }, msg: string) => { assert.equal(r.status, 200, `${msg}: ${JSON.stringify(r.data)}`); return r.data; };
const near = (a: number, b: number, msg?: string) => assert.ok(Math.abs(a - b) < 0.05, `${msg ?? ""} ${a} ≈ ${b}`);
const PNG = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==";
const photo = async (who: string) => ok(await call(who, "POST", "/api/ai/read-photo", { kind: "proof", image: PNG }), "photo").photo_id as number;
const day = (n: number) => new Date(Date.now() + 5 * 3600_000 + n * 86_400_000).toISOString().slice(0, 10);

before(async () => {
  const { app } = await import("../src/index.js");
  db = await import("../src/db.js");
  await new Promise<void>((r) => { server = app.listen(0, () => r()); });
  base = `http://127.0.0.1:${(server.address() as any).port}`;
  for (const who of ["admin", "manager", "wholesale", "salesman", "cashier"]) tokens[who] = (await call("", "POST", "/api/auth/login", { email: `${who}@pumpai.pk`, password: "demo1234" })).data.token;
});
after(() => server?.close());

const prices = async () => Object.fromEntries((ok(await call("manager", "GET", "/api/prices"), "prices").current ?? []).map((p: any) => [p.product, p.price]));
let shiftId = 0, live: any = null, khataCust: any = null;

test("setup: the salesman opens a shift and enters only one khata sale on the POS", async () => {
  db.run("UPDATE shifts SET status='closed', closed_at=? WHERE status='open'", new Date().toISOString());
  const open = ok(await call("salesman", "POST", "/api/shifts/open", {}), "open");
  shiftId = open.id;
  live = ok(await call("salesman", "GET", `/api/shifts/${shiftId}/live`), "live");
  const accts = ok(await call("salesman", "GET", "/api/pos/khata-accounts"), "accounts");
  khataCust = accts.find((a: any) => !a.khata_blocked) ?? accts[0];
  // room on the khata for the test fills
  db.run("UPDATE customers SET credit_limit = balance + 1000000, khata_blocked=0 WHERE id=?", khataCust.id);
  const hsd = live.readings.find((r: any) => r.product === "HSD");
  // the slip photo taken on the POS goes with the sale and shows on the khata statement
  const pic = await photo("salesman");
  const sale = ok(await call("salesman", "POST", "/api/sales", { station_id: live.shift.station_id, product: "HSD", litres: 20, payment_method: "khata", customer_id: khataCust.id, slip_no: "P-1", photo_id: pic }), "khata on POS");
  assert.equal(sale.photo_id, pic);
  assert.ok(hsd, "station has a diesel nozzle");
});

test("slip photo: on the khata statement, and can be added after the rush", async () => {
  const st = ok(await call("manager", "GET", `/api/customers/${khataCust.id}/statement`), "statement");
  const line = st.lines.find((l: any) => l.slip_no === "P-1");
  assert.ok(String(line.proof_ids ?? "").split(",").length === 1 && line.proof_ids, "statement shows the slip photo");
  // a second khata fill entered in the rush without a photo; the salesman adds it later
  const sale = ok(await call("salesman", "POST", "/api/sales", { station_id: live.shift.station_id, product: "HSD", litres: 5, payment_method: "khata", customer_id: khataCust.id, slip_no: "P-2" }), "no photo yet");
  assert.equal(sale.photo_id, null);
  const pic = await photo("salesman");
  ok(await call("salesman", "POST", `/api/sales/${sale.id}/slip-photo`, { photo_id: pic }), "add later");
  assert.equal(db.get("SELECT photo_id FROM sales WHERE id=?", sale.id).photo_id, pic);
  const st2 = ok(await call("manager", "GET", `/api/customers/${khataCust.id}/statement`), "statement");
  assert.equal(String(st2.lines.find((l: any) => l.slip_no === "P-2").proof_ids), String(pic));
  assert.equal((await call("salesman", "POST", `/api/sales/${sale.id}/slip-photo`, { photo_id: 999999 })).status, 404, "unknown photo");
  // the shift report lists the slip photos with the khata account
  const rep = ok(await call("salesman", "GET", `/api/shifts/${shiftId}/report`), "report");
  assert.ok(rep.khata.find((k: any) => k.id === khataCust.id).photo_ids.includes(pic));
  // keep the closing maths of the next tests as they were: undo this 5 L
  ok(await call("manager", "POST", `/api/sales/${sale.id}/undo`, {}), "undo");
});

test("customer PIN page: the customer sees their own slip photos, nobody else does", async () => {
  const pic = Number(String(ok(await call("manager", "GET", `/api/customers/${khataCust.id}/statement`), "statement").lines.find((l: any) => l.slip_no === "P-1").proof_ids));
  const portal = async (id: number) => {
    const p = ok(await call("manager", "GET", `/api/customers/${id}/portal`), "portal link");
    const path = new URL(p.url).pathname;
    const res = await fetch(base + path, { method: "POST", headers: { "content-type": "application/x-www-form-urlencoded" }, body: `pin=${p.pin}` });
    assert.equal(res.status, 200);
    return { path, cookie: res.headers.get("set-cookie")!.split(";")[0], html: await res.text() };
  };
  const mine = await portal(khataCust.id);
  assert.ok(mine.html.includes(`/slip/${pic}`), "slip photo on the page");
  const img = await fetch(base + `${mine.path}/slip/${pic}`, { headers: { cookie: mine.cookie } });
  assert.equal(img.status, 200); assert.equal(img.headers.get("content-type"), "image/png");
  assert.equal((await fetch(base + `${mine.path}/slip/${pic}`)).status, 404, "not without the PIN");
  // another customer, with their own PIN, cannot open this customer's slip
  const other = db.get("SELECT id FROM customers WHERE tenant_id=1 AND id<>? ORDER BY id LIMIT 1", khataCust.id).id;
  const theirs = await portal(other);
  assert.equal((await fetch(base + `${theirs.path}/slip/${pic}`, { headers: { cookie: theirs.cookie } })).status, 404, "not another customer's slip");
});

test("wholesale client PIN page: their own delivery / payment photos, nobody else's, not voided ones", async () => {
  const clients = ok(await call("wholesale", "GET", "/api/wholesale/clients"), "clients");
  const [a, b] = clients;
  const pic = await photo("wholesale");
  const pay = ok(await call("wholesale", "POST", `/api/wholesale/clients/${a.id}/payment`, { amount: 1000, method: "Cash", ref: "R-9", photo_ids: [pic] }), "payment with photo");
  const portal = async (id: number) => {
    const p = ok(await call("wholesale", "GET", `/api/wholesale/clients/${id}/portal`), "portal link");
    const path = new URL(p.url).pathname;
    const res = await fetch(base + path, { method: "POST", headers: { "content-type": "application/x-www-form-urlencoded" }, body: `pin=${p.pin}` });
    assert.equal(res.status, 200);
    return { path, cookie: res.headers.get("set-cookie")!.split(";")[0], html: await res.text() };
  };
  const mine = await portal(a.id);
  assert.ok(mine.html.includes(`/slip/${pic}`), "photo under the payment");
  const img = await fetch(base + `${mine.path}/slip/${pic}`, { headers: { cookie: mine.cookie } });
  assert.equal(img.status, 200); assert.equal(img.headers.get("content-type"), "image/png");
  assert.equal((await fetch(base + `${mine.path}/slip/${pic}`)).status, 404, "not without the PIN");
  const theirs = await portal(b.id);
  assert.equal((await fetch(base + `${theirs.path}/slip/${pic}`, { headers: { cookie: theirs.cookie } })).status, 404, "not another client's photo");
  // a voided entry's photo is not shown any more
  ok(await call("admin", "POST", `/api/wholesale/txns/${pay.id}/void`, { reason: "entered twice" }), "void");
  assert.equal((await fetch(base + `${mine.path}/slip/${pic}`, { headers: { cookie: mine.cookie } })).status, 404, "voided");
});

test("wholesale PIN page: tanker trip photos show to every client on that trip, and to no one else", async () => {
  const clients = ok(await call("wholesale", "GET", "/api/wholesale/clients"), "clients");
  const [a, b, c] = clients;
  const station = ok(await call("manager", "GET", "/api/stations"), "stations");
  const tripPic = await photo("wholesale");
  const trip = ok(await call("wholesale", "POST", "/api/wholesale/trips", { station_id: (station.stations ?? station)[0].id, product: "HSD", vehicle_no: "TLR-1", photo_ids: [tripPic],
    drops: [{ client_id: a.id, litres: 100, override_limit: true }, { client_id: b.id, litres: 100, override_limit: true }] }), "trip");
  const portal = async (id: number) => {
    const p = ok(await call("wholesale", "GET", `/api/wholesale/clients/${id}/portal`), "portal link");
    const path = new URL(p.url).pathname;
    const res = await fetch(base + path, { method: "POST", headers: { "content-type": "application/x-www-form-urlencoded" }, body: `pin=${p.pin}` });
    return { path, cookie: res.headers.get("set-cookie")!.split(";")[0], html: await res.text() };
  };
  for (const cl of [a, b]) {
    const pg = await portal(cl.id);
    assert.ok(pg.html.includes(`/slip/${tripPic}`), "trip photo under the delivery");
    assert.equal((await fetch(base + `${pg.path}/slip/${tripPic}`, { headers: { cookie: pg.cookie } })).status, 200);
  }
  const other = await portal(c.id);
  assert.ok(!other.html.includes(`/slip/${tripPic}`));
  assert.equal((await fetch(base + `${other.path}/slip/${tripPic}`, { headers: { cookie: other.cookie } })).status, 404, "client not on the trip");
  // once b's drop is voided, b no longer sees the trip photo
  const dropB = trip.drops.find((d: any) => d.client_id === b.id);
  ok(await call("admin", "POST", `/api/wholesale/txns/${dropB.id}/void`, { reason: "not delivered" }), "void drop");
  const pb = await portal(b.id);
  assert.equal((await fetch(base + `${pb.path}/slip/${tripPic}`, { headers: { cookie: pb.cookie } })).status, 404, "voided drop");
});

let posCardL = 0;
test("POS entries come in by themselves: online totals start from the POS, khata slips are listed", async () => {
  const card = ok(await call("salesman", "POST", "/api/sales", { station_id: live.shift.station_id, product: "HSD", amount: 1000, payment_method: "card" }), "card on POS");
  posCardL = card.litres;
  const l = ok(await call("salesman", "GET", `/api/shifts/${shiftId}/live`), "live");
  near(l.recorded.online.card, 1000, "card already on the POS");
  assert.ok(l.recorded.khata.some((k: any) => k.slip_no === "P-1" && k.photo_id), "POS khata slip listed with its photo");
  const hsd = live.readings.filter((r: any) => r.product === "HSD");
  const readings: Record<string, number> = Object.fromEntries(live.readings.map((r: any) => [r.nozzle_id, r.opening]));
  readings[hsd[0].nozzle_id] = hsd[0].opening + 200;
  // the card machine's slip says 5000 for the whole shift: 1000 is on the POS, so only 4000 more is added
  const p = ok(await call("salesman", "POST", `/api/shifts/${shiftId}/preview`, { readings, digital: { card: 5000 } }), "preview");
  near(p.digital_by_method.card, 5000, "card total = machine total, not 6000");
  // less than the POS already has is refused
  assert.equal((await call("salesman", "POST", `/api/shifts/${shiftId}/preview`, { readings, digital: { card: 500 } })).status, 400);
  // left empty: the POS amount stays as it is
  near(ok(await call("salesman", "POST", `/api/shifts/${shiftId}/preview`, { readings }), "preview").digital_by_method.card, 1000);
});

test("preview: meters minus khata, online, test and late slips = cash; nothing is saved", async () => {
  const hsd = live.readings.filter((r: any) => r.product === "HSD");
  const readings: Record<string, number> = Object.fromEntries(live.readings.map((r: any) => [r.nozzle_id, r.opening]));
  readings[hsd[0].nozzle_id] = hsd[0].opening + 200; // 200 L of diesel on the meter
  const rate = live.prices.HSD;
  const body = { readings, digital: { card: 5000, jazzcash: 2000 }, test: { [hsd[0].nozzle_id]: 3 },
    khata: [{ customer_id: khataCust.id, product: "HSD", litres: 10, vehicle_no: "LES-1234", slip_no: "S-77" }] };
  const before = db.get("SELECT COUNT(*) n FROM sales WHERE shift_id=?", shiftId).n;
  const p = ok(await call("salesman", "POST", `/api/shifts/${shiftId}/preview`, body), "preview");
  const d = p.fuels.find((f: any) => f.product === "HSD");
  near(d.meter_l, 200); near(d.test_l, 3); near(d.khata_l, 30, "20 on POS + 10 late slip");
  near(d.digital, 7000, "card + JazzCash");
  near(d.cash_l, 200 - 3 - 30 - d.digital_l, "rest of the meter is cash");
  near(d.cash, Math.round(d.cash_l * rate * 100) / 100, "cash at the rate");
  assert.equal(db.get("SELECT COUNT(*) n FROM sales WHERE shift_id=?", shiftId).n, before, "preview saved nothing");
  const again = ok(await call("salesman", "POST", `/api/shifts/${shiftId}/preview`, body), "preview again");
  near(again.cash_expected, p.cash_expected, "same answer twice");
  // guards
  assert.equal((await call("salesman", "POST", `/api/shifts/${shiftId}/preview`, { ...body, digital: { card: 99_000_000 } })).status, 400, "online more than the meter sale");
  assert.equal((await call("salesman", "POST", `/api/shifts/${shiftId}/preview`, { ...body, test: { [hsd[0].nozzle_id]: 15 } })).status, 403, "big test needs the manager");
  ok(await call("manager", "POST", `/api/shifts/${shiftId}/preview`, { ...body, test: { [hsd[0].nozzle_id]: 15 } }), "manager may");
  assert.equal((await call("salesman", "POST", `/api/shifts/${shiftId}/preview`, { ...body, khata: [{ customer_id: khataCust.id, product: "HSD", litres: 5, vehicle_no: "LES-1", slip_no: "" }] })).status, 400, "slip no. required");
});

test("close in one click: saved exactly as previewed; online money, khata and stock land in the right places", async () => {
  const hsd = live.readings.filter((r: any) => r.product === "HSD");
  const readings: Record<string, number> = Object.fromEntries(live.readings.map((r: any) => [r.nozzle_id, r.opening]));
  readings[hsd[0].nozzle_id] = hsd[0].opening + 200;
  const latePic = await photo("salesman");
  const body = { readings, digital: { card: 5000, jazzcash: 2000 }, test: { [hsd[0].nozzle_id]: 3 },
    khata: [{ customer_id: khataCust.id, product: "HSD", litres: 10, vehicle_no: "LES-1234", slip_no: "S-77", photo_id: latePic }] };
  const p = ok(await call("salesman", "POST", `/api/shifts/${shiftId}/preview`, body), "preview");
  assert.equal(db.get("SELECT ref FROM photos WHERE id=?", latePic).ref, null, "preview does not use up the photo");
  const tank = db.get("SELECT t.id, t.current_l FROM tanks t JOIN nozzles n ON n.tank_id=t.id WHERE n.id=?", hsd[0].nozzle_id);
  const kBefore = db.get("SELECT balance FROM customers WHERE id=?", khataCust.id).balance;
  const counted = Math.round(p.cash_expected) - 500;
  const c = ok(await call("salesman", "POST", `/api/shifts/${shiftId}/close`, { ...body, cash_actual: counted, cash_notes: { "1000": 3 } }), "close");
  near(c.cash_expected, p.cash_expected, "saved = preview");
  near(c.variance, counted - p.cash_expected);
  near(db.get("SELECT current_l FROM tanks WHERE id=?", tank.id).current_l, tank.current_l - (200 - 3 - 20 - posCardL), "tank down by what was sold now (the POS khata and card sales were already taken; 3 L test went back)");
  assert.ok(db.get("SELECT balance FROM customers WHERE id=?", khataCust.id).balance > kBefore, "late slip on the khata");
  assert.equal(db.get("SELECT photo_id FROM sales WHERE shift_id=? AND slip_no='S-77' AND vehicle_no='LES-1234'", shiftId).photo_id, latePic, "late slip keeps its photo");
  assert.match(String(db.get("SELECT ref FROM photos WHERE id=?", latePic).ref), /^khata:\d+$/, "on the khata statement");
  near(db.get("SELECT COALESCE(SUM(amount),0) v FROM sales WHERE shift_id=? AND payment_method='card'", shiftId).v, 5000, "card money recorded");
  assert.equal(db.get("SELECT test_l FROM meter_readings WHERE shift_id=? AND nozzle_id=?", shiftId, hsd[0].nozzle_id).test_l, 3);
  assert.equal(JSON.parse(db.get("SELECT cash_notes FROM shifts WHERE id=?", shiftId).cash_notes)["1000"], 3);
  assert.equal((await call("salesman", "POST", `/api/shifts/${shiftId}/close`, { ...body, cash_actual: 1 })).status, 400, "only once");
});

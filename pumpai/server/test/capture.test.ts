/** Photo proof for meters / invoices / receipts, and voice (spoken sentence) → POS sale. Runs without an AI key. */
import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import os from "node:os";
import path from "node:path";
import fs from "node:fs";
import type { Server } from "node:http";

const dir = fs.mkdtempSync(path.join(os.tmpdir(), "pumpai-capture-"));
process.env.DB_PATH = path.join(dir, "test.db");
process.env.NODE_ENV = "test";
process.env.ANTHROPIC_API_KEY = "";
process.env.WA_TOKEN = "";

let server: Server;
let base = "";
const tokens: Record<string, string> = {};
async function call(who: string, method: string, url: string, body?: unknown) {
  const res = await fetch(base + url, { method, headers: { "content-type": "application/json", ...(tokens[who] ? { authorization: `Bearer ${tokens[who]}` } : {}) }, body: body ? JSON.stringify(body) : undefined });
  return { status: res.status, data: (await res.json().catch(() => null)) as any, res };
}
const ok = (r: { status: number; data: any }, msg: string) => { assert.equal(r.status, 200, `${msg}: ${JSON.stringify(r.data)}`); return r.data; };
const PNG = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==";

before(async () => {
  const { app } = await import("../src/index.js");
  await new Promise<void>((r) => { server = app.listen(0, () => r()); });
  base = `http://127.0.0.1:${(server.address() as any).port}`;
  for (const who of ["admin", "manager", "salesman"]) tokens[who] = (await call("", "POST", "/api/auth/login", { email: `${who}@pumpai.pk`, password: "demo1234" })).data.token;
});
after(() => { server.close(); fs.rmSync(dir, { recursive: true, force: true }); });

test("meter photos are saved as proof on the shift even without AI reading", async () => {
  const p = ok(await call("salesman", "POST", "/api/ai/read-photo", { kind: "meter", image: PNG }), "photo");
  assert.equal(p.ai, false); assert.match(p.message, /type the numbers/);
  const shift = ok(await call("salesman", "POST", "/api/shifts/open", { photo_ids: [p.photo_id] }), "open");
  const img = await fetch(`${base}/api/photos/${p.photo_id}?token=${(await call("manager", "GET", "/api/me")).data.media_token}`);
  assert.equal(img.status, 200); assert.equal(img.headers.get("content-type"), "image/png");
  assert.ok((await img.arrayBuffer()).byteLength > 50);
  const rep = ok(await call("manager", "GET", `/api/shifts/${shift.id}/report`), "report");
  assert.deepEqual(rep.photos.map((x: any) => x.ref), [`shift-open:${shift.id}`]);
  // a photo can only be linked once
  const other = ok(await call("manager", "POST", "/api/stock/delivery", { tank_id: 1, invoice_l: 100, received_l: 100, photo_id: p.photo_id, supplier_id: 1, purchase_rate: 250 }), "delivery");
  assert.equal(other.photo_id, null);
  assert.equal((await call("salesman", "POST", "/api/ai/read-photo", { kind: "meter", image: "data:text/plain;base64,aGk=" })).status, 400);
});

test("invoice and receipt photos attach to the delivery and the expense", async () => {
  const inv = ok(await call("manager", "POST", "/api/ai/read-photo", { kind: "invoice", image: PNG }), "invoice");
  const d = ok(await call("manager", "POST", "/api/stock/delivery", { tank_id: 1, invoice_l: 50, received_l: 50, photo_id: inv.photo_id, supplier_id: 1, purchase_rate: 250 }), "delivery");
  assert.equal(d.photo_id, inv.photo_id);
  const rc = ok(await call("manager", "POST", "/api/ai/read-photo", { kind: "receipt", image: PNG }), "receipt");
  const e = ok(await call("manager", "POST", "/api/expenses", { category: "Tea & food", amount: 1200, photo_id: rc.photo_id }), "expense");
  assert.equal(e.photo_id, rc.photo_id);
});

test("only the CEO can delete a photo, and every row pointing at it is unlinked", async () => {
  const inv = ok(await call("manager", "POST", "/api/ai/read-photo", { kind: "invoice", image: PNG }), "invoice");
  const d = ok(await call("manager", "POST", "/api/stock/delivery", { tank_id: 1, invoice_l: 40, received_l: 40, photo_id: inv.photo_id, supplier_id: 1, purchase_rate: 250 }), "delivery");
  const rc = ok(await call("manager", "POST", "/api/ai/read-photo", { kind: "receipt", image: PNG }), "receipt");
  const e = ok(await call("manager", "POST", "/api/expenses", { category: "Tea & food", amount: 300, photo_id: rc.photo_id }), "expense");
  const slip = ok(await call("salesman", "POST", "/api/ai/read-photo", { kind: "slip", image: PNG }), "slip");
  const accts = ok(await call("salesman", "GET", "/api/pos/khata-accounts"), "accounts");
  const sale = ok(await call("salesman", "POST", "/api/sales", { station_id: 1, product: "PMG", litres: 5, payment_method: "khata", customer_id: accts[0].id, slip_no: "9001", photo_id: slip.photo_id }), "sale");
  assert.equal(sale.photo_id, slip.photo_id);
  // everyone else is view-only
  assert.equal((await call("manager", "DELETE", `/api/photos/${inv.photo_id}`)).status, 403);
  assert.equal((await call("salesman", "DELETE", `/api/photos/${slip.photo_id}`)).status, 403);
  const media = (await call("admin", "GET", "/api/me")).data.media_token;
  assert.equal((await fetch(`${base}/api/photos/${inv.photo_id}?token=${media}`)).status, 200);
  // the CEO deletes: the picture is gone and no row keeps a dangling link
  const r1 = ok(await call("admin", "DELETE", `/api/photos/${inv.photo_id}`), "delete invoice");
  assert.deepEqual(r1.unlinked, ["deliveries.photo_id"]);
  assert.deepEqual(ok(await call("admin", "DELETE", `/api/photos/${rc.photo_id}`), "delete receipt").unlinked, ["expenses.photo_id"]);
  assert.deepEqual(ok(await call("admin", "DELETE", `/api/photos/${slip.photo_id}`), "delete slip").unlinked, ["sales.photo_id"]);
  assert.equal((await fetch(`${base}/api/photos/${inv.photo_id}?token=${media}`)).status, 404);
  assert.equal((await call("admin", "DELETE", `/api/photos/${inv.photo_id}`)).status, 404);
  const stock = ok(await call("manager", "GET", "/api/stock"), "stock");
  assert.equal(stock.deliveries.find((x: any) => x.id === d.id).photo_id, null);
  assert.equal(ok(await call("manager", "GET", "/api/expenses"), "expenses").expenses.find((x: any) => x.id === e.id).photo_id, null);
  const sales = ok(await call("manager", "GET", "/api/sales?limit=500"), "sales");
  assert.equal(sales.find((x: any) => x.id === sale.id).photo_id, null);
});

test("voice: a spoken sentence becomes a POS sale ready to save", async () => {
  const accts = ok(await call("salesman", "GET", "/api/pos/khata-accounts"), "accounts");
  const police = accts.find((a: any) => a.type === "police");
  const said = `${police.name} 20 litre diesel slip 7781`;
  const r = ok(await call("salesman", "POST", "/api/ai/parse-sale", { text: said }), "parse");
  assert.equal(r.product, "HSD"); assert.equal(r.litres, 20); assert.equal(r.payment_method, "khata");
  assert.equal(r.customer_id, police.id); assert.equal(r.slip_no, "7781"); assert.equal(r.engine, "rules");
  const u = ok(await call("salesman", "POST", "/api/ai/parse-sale", { text: "پیٹرول دو ہزار ایزی پیسہ" }), "urdu");
  assert.equal(u.product, "PMG"); assert.equal(u.amount, 2000); assert.equal(u.payment_method, "easypaisa");
  // the parsed sale saves as is
  const sale = ok(await call("salesman", "POST", "/api/sales", { station_id: 1, product: r.product, litres: r.litres, payment_method: r.payment_method, customer_id: r.customer_id, slip_no: r.slip_no }), "sale");
  assert.equal(sale.slip_no, "7781");
  assert.equal((await call("wholesale", "POST", "/api/ai/parse-sale", { text: "petrol 500" })).status, 401);
});

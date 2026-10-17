/** The CEO's live "Hisaab check" (GET /books/check) proves every balance ties to the ledger — on fresh demo data it is all-green, 0 rupee. */
import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import os from "node:os";
import path from "node:path";
import fs from "node:fs";
import type { Server } from "node:http";

const dir = fs.mkdtempSync(path.join(os.tmpdir(), "pumpai-books-"));
process.env.DB_PATH = path.join(dir, "test.db");
process.env.NODE_ENV = "test";
process.env.ANTHROPIC_API_KEY = "";
process.env.WA_TOKEN = "";

let server: Server, base = "", adminTok = "", mgrTok = "";
async function call(tok: string, method: string, url: string, body?: unknown) {
  const res = await fetch(base + url, { method, headers: { "content-type": "application/json", ...(tok ? { authorization: `Bearer ${tok}` } : {}) }, body: body ? JSON.stringify(body) : undefined });
  return { status: res.status, data: (await res.json().catch(() => null)) as any };
}
const ok = (r: { status: number; data: any }, m: string) => { assert.equal(r.status, 200, `${m}: ${JSON.stringify(r.data)}`); return r.data; };

before(async () => {
  const { app } = await import("../src/index.js");
  await new Promise<void>((r) => { server = app.listen(0, () => r()); });
  base = `http://127.0.0.1:${(server.address() as any).port}`;
  adminTok = (await call("", "POST", "/api/auth/login", { email: "admin@pumpai.pk", password: "demo1234" })).data.token;
  mgrTok = (await call("", "POST", "/api/auth/login", { email: "manager@pumpai.pk", password: "demo1234" })).data.token;
});
after(() => { server.close(); fs.rmSync(dir, { recursive: true, force: true }); });

test("the live books check comes back all-green and tied to the rupee", async () => {
  const r = ok(await call(adminTok, "GET", "/api/books/check"), "books check");
  assert.ok(Array.isArray(r.checks) && r.checks.length > 5, "has the full checklist");
  assert.ok(r.max_diff <= 1, `max difference within a rupee (was ${r.max_diff})`);
  for (const c of r.checks) assert.ok(c.ok || c.advisory, `check failed: ${c.label} — book ${c.book} vs ledger ${c.ledger} (diff ${c.diff})${c.note ? ` — ${c.note}` : ""}`);
  assert.equal(r.ok, true, "overall ok");
  assert.equal(r.failed.length, 0, "nothing in the failed list");
});

test("the check survives a CEO correction — void a cash voucher, hisaab still 0", async () => {
  const kh = ok(await call(adminTok, "GET", "/api/cashier/parties?kind=khata"), "p").khata[0];
  const v = ok(await call(adminTok, "POST", "/api/cashier/receive", { party_type: "khata", party_id: kh.id, amount: 1234, method: "Cash" }), "receive").voucher;
  ok(await call(adminTok, "GET", "/api/books/check"), "check after receive");
  ok(await call(adminTok, "POST", `/api/cashier/vouchers/${v.id}/void`, { reason: "galat entry" }), "void");
  const r = ok(await call(adminTok, "GET", "/api/books/check"), "check after void");
  assert.equal(r.ok, true, `still tallied after correction: ${JSON.stringify(r.failed)}`);
});

test("the books check is CEO-only (a manager cannot run it)", async () => {
  assert.equal((await call(mgrTok, "GET", "/api/books/check")).status, 403);
});

test("one click books old shop stock against go-live equity — advisory gap closes, hisaab still 0", async () => {
  const st = ok(await call(adminTok, "GET", "/api/stations"), "stations");
  const station_id = (Array.isArray(st) ? st : st.stations)[0].id;
  // an item that was already on the shelf before go-live: stock typed in, no purchase / money entry anywhere
  ok(await call(adminTok, "POST", "/api/shop/items", { station_id, name: "Old shelf oil 1L", category: "lubricant", cost: 900, price: 1200, stock: 20 }), "item");
  const c1 = ok(await call(adminTok, "GET", "/api/books/check"), "check");
  const row1 = c1.checks.find((c: any) => /shop stock/i.test(c.label));
  assert.ok(row1.advisory && row1.diff >= 18_000 - 1, `shelf gap expected, got ${JSON.stringify(row1)}`);
  assert.equal(c1.ok, true, "advisory never fails the check");
  const mgr = await call(mgrTok, "POST", "/api/books/shop-stock/book", {});
  assert.equal(mgr.status, 403, "manager cannot book");
  const b = ok(await call(adminTok, "POST", "/api/books/shop-stock/book", { note: "go-live shelf" }), "book");
  assert.ok(Math.abs(b.booked - row1.diff) <= 1, `booked the whole gap: ${JSON.stringify(b)}`);
  assert.ok(b.items.some((i: any) => i.name === "Old shelf oil 1L" && Math.abs(i.amount - 18_000) <= 1), "itemised on the shelf item");
  assert.ok(Math.abs(b.after.gap) <= 1, `gap closed: ${JSON.stringify(b.after)}`);
  const c2 = ok(await call(adminTok, "GET", "/api/books/check"), "check again");
  assert.equal(c2.ok, true); assert.equal(c2.checks.find((c: any) => /shop stock/i.test(c.label)).ok, true, "shop stock row now green");
  const again = ok(await call(adminTok, "POST", "/api/books/shop-stock/book", {}), "second press");
  assert.equal(again.booked, 0, "pressing again books nothing");
  // the stock count itself never moved
  const items = ok(await call(adminTok, "GET", "/api/shop/items"), "items");
  assert.equal((Array.isArray(items) ? items : items.items).find((i: any) => i.name === "Old shelf oil 1L").stock, 20);
  const { tallyBooks } = await import("./helpers/tally.js");
  tallyBooks();
});

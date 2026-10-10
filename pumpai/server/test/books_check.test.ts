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
  for (const c of r.checks) assert.ok(c.ok, `check failed: ${c.label} — book ${c.book} vs ledger ${c.ledger} (diff ${c.diff})${c.note ? ` — ${c.note}` : ""}`);
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

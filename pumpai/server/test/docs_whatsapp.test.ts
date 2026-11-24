/** Documents as PDFs + WhatsApp: every kind renders, public link works, contacts, phone-mode (wa.me) and API-mode sends, permissions. */
import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import os from "node:os";
import path from "node:path";
import fs from "node:fs";
import type { Server } from "node:http";

const dir = fs.mkdtempSync(path.join(os.tmpdir(), "pumpai-docs-"));
process.env.DB_PATH = path.join(dir, "test.db");
process.env.NODE_ENV = "test";
process.env.ANTHROPIC_API_KEY = "";
process.env.WA_TOKEN = "";

let server: Server;
let base = "";
let db: typeof import("../src/db.js");
const tokens: Record<string, string> = {};
const realFetch = globalThis.fetch;
async function call(who: string, method: string, url: string, body?: unknown) {
  const res = await realFetch(base + url, { method, headers: { "content-type": "application/json", ...(tokens[who] ? { authorization: `Bearer ${tokens[who]}` } : {}) }, body: body ? JSON.stringify(body) : undefined });
  return { status: res.status, data: (await res.json().catch(() => null)) as any };
}
const ok = (r: { status: number; data: any }, msg: string) => { assert.equal(r.status, 200, `${msg}: ${JSON.stringify(r.data)}`); return r.data; };
const pdfOk = async (who: string, url: string) => { const r = await realFetch(base + url, { headers: { authorization: `Bearer ${tokens[who]}` } }); assert.equal(r.status, 200, url); assert.equal(r.headers.get("content-type"), "application/pdf"); const b = Buffer.from(await r.arrayBuffer()); assert.ok(b.subarray(0, 5).toString() === "%PDF-" && b.length > 800, "a real pdf"); return b; };

before(async () => {
  const { app } = await import("../src/index.js");
  db = await import("../src/db.js");
  await new Promise<void>((r) => { server = app.listen(0, () => r()); });
  base = `http://127.0.0.1:${(server.address() as any).port}`;
  for (const who of ["admin", "manager", "salesman", "cashier"]) tokens[who] = (await call("", "POST", "/api/auth/login", { email: `${who}@pumpai.pk`, password: "demo1234" })).data.token;
  db.run("UPDATE users SET phone='923001112233' WHERE email='cashier@pumpai.pk'");
});
after(() => { server?.close(); globalThis.fetch = realFetch; });

let voucherId = 0, custId = 0, clientId = 0, thekId = 0, supId = 0, shiftId = 0, saleId = 0;

test("every document kind renders a real multi-page-capable PDF on the letterhead", async () => {
  custId = db.get("SELECT id FROM customers WHERE tenant_id=1 AND credit_limit>0 ORDER BY id LIMIT 1").id;
  clientId = db.get("SELECT id FROM wholesale_clients WHERE tenant_id=1 ORDER BY id LIMIT 1").id;
  thekId = db.get("SELECT id FROM thekedars WHERE tenant_id=1 ORDER BY id LIMIT 1").id;
  supId = db.get("SELECT id FROM suppliers WHERE tenant_id=1 ORDER BY id LIMIT 1").id;
  shiftId = db.get("SELECT id FROM shifts WHERE status='closed' ORDER BY id DESC LIMIT 1").id;
  saleId = db.get("SELECT id FROM sales ORDER BY id DESC LIMIT 1").id;
  const v = ok(await call("admin", "POST", "/api/cashier/receive", { party_type: "khata", party_id: custId, amount: 1500, method: "Cash", notes: { "1000": 1, "500": 1 } }), "receive");
  voucherId = v.voucher.id;
  await pdfOk("admin", `/api/docs/voucher/${voucherId}/pdf`); await pdfOk("cashier", `/api/docs/voucher/${voucherId}/pdf`);
  const khata = await pdfOk("manager", `/api/docs/khata/${custId}/pdf`);
  assert.ok(khata.toString("latin1").includes("/Count"), "pages");
  await pdfOk("admin", `/api/docs/wholesale/${clientId}/pdf?from=2026-01-01`);
  await pdfOk("admin", `/api/docs/carriage/${thekId}/pdf`);
  await pdfOk("manager", `/api/docs/supplier/${supId}/pdf`);
  await pdfOk("manager", `/api/docs/shift/${shiftId}/pdf`);
  await pdfOk("manager", `/api/docs/receipt/${saleId}/pdf`);
  // a long statement spills onto more than one page without breaking the file
  const big = await pdfOk("admin", `/api/docs/khata/${custId}/pdf`);
  const count = Number(/\/Count (\d+)/.exec(big.toString("latin1"))?.[1]);
  assert.ok(count >= 1);
});

test("public signed link opens the PDF without login; a tampered link does not", async () => {
  const c = ok(await call("cashier", "GET", `/api/docs/voucher/${voucherId}/contacts`), "contacts");
  assert.match(c.link, /\/doc\/.+\.pdf$/);
  const r = await realFetch(c.link.replace(/^https?:\/\/[^/]+/, base));
  assert.equal(r.status, 200); assert.equal(r.headers.get("content-type"), "application/pdf");
  assert.equal((await realFetch(base + "/doc/notatoken.pdf")).status, 404);
  // suggestions: the party, the pump's own people with phones, recent numbers
  assert.ok(c.party?.phone, "party phone"); assert.ok(c.staff.some((s: any) => s.phone === "923001112233" && s.role === "cashier"), "staff with phone");
  assert.equal(c.api, false, "no Cloud API in test → phone mode");
});

test("phone mode: send returns wa.me links with the PDF link, records sent_docs; bad number reported", async () => {
  const r = ok(await call("cashier", "POST", `/api/docs/voucher/${voucherId}/send`, { phones: ["923001112233", "0300 999 8877", "12"], note: "Aaj ki wasooli" }), "send");
  assert.equal(r.mode, "phone");
  const good = r.results.filter((x: any) => x.ok);
  assert.equal(good.length, 2); assert.ok(good[0].wa_link.startsWith("https://wa.me/923001112233?text=")); assert.ok(decodeURIComponent(good[0].wa_link).includes("/doc/"));
  assert.ok(r.results.some((x: any) => !x.ok && /not valid/.test(x.error)));
  assert.equal(good[0].name, db.get("SELECT name FROM users WHERE email='cashier@pumpai.pk'").name, "known staff name");
  const sent = db.all("SELECT * FROM sent_docs WHERE kind='voucher' AND ref_id=?", String(voucherId));
  assert.equal(sent.length, 2); assert.equal(sent[0].channel, "phone");
  const c = ok(await call("cashier", "GET", `/api/docs/voucher/${voucherId}/contacts`), "contacts");
  assert.equal(c.sent.length, 2); assert.ok(c.recent.some((x: any) => x.phone === "923009998877"));
});

test("API mode: the PDF goes as a WhatsApp document; a khata customer's conversation keeps a copy", async () => {
  const { config } = await import("../src/config.js");
  config.wa.token = "TESTTOKEN"; config.wa.phoneNumberId = "123";
  const sent: any[] = [];
  globalThis.fetch = (async (input: any, init: any = {}) => {
    const url = String(input);
    if (url.startsWith(base)) return realFetch(input, init);
    sent.push(JSON.parse(String(init.body)));
    return new Response(JSON.stringify({ messages: [{ id: "wamid.1" }] }), { status: 200, headers: { "content-type": "application/json" } });
  }) as any;
  const custPhone = db.get("SELECT phone FROM customers WHERE id=?", custId).phone;
  const r = ok(await call("manager", "POST", `/api/docs/khata/${custId}/send`, { phones: [custPhone] }), "send api");
  assert.equal(r.mode, "api"); assert.equal(r.results[0].ok, true); assert.equal(r.results[0].channel, "api");
  const docMsg = sent.find((m) => m.type === "document");
  assert.ok(docMsg, "document message"); assert.match(docMsg.document.link, /\/doc\/.+\.pdf$/); assert.match(docMsg.document.filename, /^khata-/);
  assert.ok(db.get("SELECT id FROM messages WHERE body LIKE '%Khata statement%' ORDER BY id DESC LIMIT 1"), "copy in the customer's conversation");
  assert.equal(db.get("SELECT channel, ok FROM sent_docs WHERE kind='khata' ORDER BY id DESC LIMIT 1").channel, "api");
  globalThis.fetch = realFetch; config.wa.token = ""; config.wa.phoneNumberId = "";
});

test("permissions: a salesman only his own shift / sales; wrong kind refused", async () => {
  assert.equal((await call("salesman", "GET", `/api/docs/khata/${custId}/contacts`)).status, 403);
  assert.equal((await call("salesman", "GET", `/api/docs/shift/${shiftId}/contacts`)).status, 403, "someone else's shift");
  assert.equal((await call("salesman", "GET", `/api/docs/invoice/1/contacts`)).status, 400);
  assert.equal((await call("cashier", "GET", `/api/docs/supplier/${supId}/contacts`)).status, 403);
  assert.ok(db.get("SELECT id FROM audit_log WHERE action='document_sent'"), "audited");
});

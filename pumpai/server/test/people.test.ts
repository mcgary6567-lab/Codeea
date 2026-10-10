/** Staff loans with instalments, salary slip PDF, training records, daily coaching. */
import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import os from "node:os";
import path from "node:path";
import fs from "node:fs";
import type { Server } from "node:http";

const dir = fs.mkdtempSync(path.join(os.tmpdir(), "pumpai-people-"));
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
let imran = 0;

before(async () => {
  const { app } = await import("../src/index.js");
  db = await import("../src/db.js");
  await new Promise<void>((r) => { server = app.listen(0, () => r()); });
  base = `http://127.0.0.1:${(server.address() as any).port}`;
  for (const who of ["admin", "manager", "salesman"]) tokens[who] = (await call("", "POST", "/api/auth/login", { email: `${who}@pumpai.pk`, password: "demo1234" })).data.token;
  imran = db.get("SELECT id FROM users WHERE email='salesman@pumpai.pk'").id;
});
after(() => { server.close(); fs.rmSync(dir, { recursive: true, force: true }); });

test("loan: given from cash, instalments come off the salary by themselves; salary slip PDF is sent", async () => {
  const cashBefore = ok(await call("manager", "GET", "/api/cash"), "cash").outs.staff_advances;
  const loans = ok(await call("manager", "POST", `/api/staff/${imran}/loans`, { amount: 8000, instalment: 4000, note: "Wedding" }), "loan");
  assert.equal(loans.length, 2, "seeded loan + new one");
  assert.equal(ok(await call("manager", "GET", "/api/cash"), "cash").outs.staff_advances, cashBefore + 8000, "loan paid out of the office cash");
  assert.equal((await call("manager", "POST", `/api/staff/${imran}/loans`, { amount: 1000, instalment: 2000 })).status, 400);
  const st = ok(await call("manager", "GET", `/api/staff/${imran}`), "staff");
  assert.equal(st.loan_due, 9000, "5,000 + 4,000 this month");
  const month = db.pkDate().slice(0, 7);
  db.run("DELETE FROM staff_ledger WHERE user_id=? AND type='salary' AND month=?", imran, month);
  assert.equal((await call("manager", "POST", `/api/staff/${imran}/pay-salary`, { photo_ids: await salaryProof(),  month, deduct: 20000, absence_cut: 0, commission: 0 })).status, 400, "manual cut cannot take the loans");
  const paid = ok(await call("manager", "POST", `/api/staff/${imran}/pay-salary`, { photo_ids: await salaryProof(),  month, absence_cut: 0, commission: 0 }), "pay");
  assert.equal(paid.loan, 9000);
  assert.equal(paid.net, 32000 - 9000);
  assert.match(paid.slip_url, /\/slip\/.+\.pdf$/);
  const n = db.get("SELECT body FROM notifications WHERE user_id=? AND type='staff_ledger' ORDER BY id DESC LIMIT 1", imran);
  assert.match(n.body, /Salary slip \(PDF\): http/);
  const res = await fetch(base + new URL(paid.slip_url).pathname);
  assert.equal(res.headers.get("content-type"), "application/pdf");
  const pdf = Buffer.from(await res.arrayBuffer()).toString("latin1");
  assert.ok(pdf.startsWith("%PDF-1.4"));
  assert.match(pdf, /\(NET PAID\)/);
  assert.match(pdf, /\(Loan instalment\)/);
  assert.match(pdf, /%%EOF\n$/);
  // xref offsets point at the objects
  const xref = Number(pdf.match(/startxref\n(\d+)/)![1]);
  assert.equal(pdf.slice(xref, xref + 4), "xref");
  assert.equal((await fetch(`${base}/slip/bad-token.pdf`)).status, 404);
  const mine = ok(await call("salesman", "GET", "/api/me/account"), "me");
  assert.equal(mine.slips[0].month, month);
  const small = mine.loans.find((l: any) => l.note === "Wedding");
  assert.equal(small.remaining, 4000);
  // next month the small loan is finished and closes
  const next = month.slice(5) === "12" ? `${Number(month.slice(0, 4)) + 1}-01` : `${month.slice(0, 4)}-${String(Number(month.slice(5)) + 1).padStart(2, "0")}`;
  const paid2 = ok(await call("manager", "POST", `/api/staff/${imran}/pay-salary`, { photo_ids: await salaryProof(),  month: next, absence_cut: 0, commission: 0 }), "pay 2");
  assert.equal(paid2.loan, 9000);
  assert.equal(db.get("SELECT status FROM staff_loans WHERE id=?", small.id).status, "closed");
});

test("salary slip: a non-login staff member (night guard) is paid and gets a slip showing their designation", async () => {
  const month = db.pkDate().slice(0, 7);
  const guard = ok(await call("manager", "POST", "/api/staff/members", { name: "Watchman Akbar", job_title: "Chowkidar night", salary: 25000 }), "add guard");
  // the non-login staff member shows up in the staff accounts list, with a salary to pay
  const list = ok(await call("manager", "GET", "/api/staff"), "staff list");
  assert.ok(list.find((u: any) => u.id === guard.id && u.salary === 25000), "guard is in staff accounts");
  const paid = ok(await call("manager", "POST", `/api/staff/${guard.id}/pay-salary`, { photo_ids: await salaryProof(), month, absence_cut: 0, commission: 0 }), "pay guard");
  assert.equal(paid.net, 25000);
  assert.match(paid.slip_url, /\/slip\/.+\.pdf$/);
  const res = await fetch(base + new URL(paid.slip_url).pathname);
  assert.equal(res.headers.get("content-type"), "application/pdf");
  const pdf = Buffer.from(await res.arrayBuffer()).toString("latin1");
  assert.match(pdf, /\(Chowkidar night\)/, "slip shows the designation, not a generic 'Staff'");
  assert.match(pdf, /\(Watchman Akbar\)/);
  assert.match(pdf, /\(NET PAID\)/);
});

test("training: overdue fire safety shows, refreshed after training; weekly reminder", async () => {
  const m = ok(await call("manager", "GET", "/api/training"), "matrix");
  const row = m.staff.find((s: any) => s.id === imran);
  assert.equal(row.records["Fire safety & extinguisher use"].status, "overdue");
  assert.equal(row.records["Fuel quality: density & water check"].status, "none");
  const { runJob } = await import("../src/automation/scheduler.js");
  assert.match(await runJob(1, "training_due"), /\d+ staff need training/);
  assert.ok(db.get("SELECT id FROM notifications WHERE user_id=? AND type='training_due' AND body LIKE '%Fire safety%'", imran));
  ok(await call("manager", "POST", "/api/training", { user_ids: [imran], topic: "Fire safety & extinguisher use", trainer: "Rescue 1122" }), "train");
  const mine = ok(await call("salesman", "GET", "/api/me/training"), "mine");
  const fs1 = mine.staff[0].records["Fire safety & extinguisher use"];
  assert.equal(fs1.status, "done");
  assert.equal(fs1.next_due.slice(0, 4), String(Number(db.pkDate().slice(0, 4)) + 1));
  assert.equal((await call("salesman", "POST", "/api/training", { user_ids: [imran], topic: "Anything" })).status, 403);
});

test("daily coaching: built from yesterday's numbers, sent to the salesman", async () => {
  const day = db.pkDate(Date.now() - 86_400_000);
  const shift = db.get("SELECT sh.* FROM shifts sh WHERE sh.attendant='Imran' AND sh.closed_at IS NOT NULL ORDER BY sh.closed_at DESC LIMIT 1");
  // put a short shift on yesterday for Imran
  db.run("UPDATE shifts SET closed_at=?, variance=-650 WHERE id=?", new Date(Date.parse(`${day}T20:00:00+05:00`)).toISOString(), shift.id);
  const p = ok(await call("manager", "GET", `/api/coaching/${imran}?day=${day}`), "preview");
  assert.equal(p.stats.shifts >= 1, true);
  assert.equal(p.message.engine, "rules");
  assert.match(p.message.text, /Assalam-o-Alaikum Imran/);
  assert.match(p.message.text, /cash Rs 650 kam tha/);
  const { runJob } = await import("../src/automation/scheduler.js");
  assert.match(await runJob(1, "staff_coaching"), /[1-9]\d* messages sent/);
  assert.ok(db.get("SELECT id FROM notifications WHERE user_id=? AND type='coaching' AND body LIKE '%kam tha%'", imran));
  ok(await call("manager", "POST", `/api/coaching/${imran}/send`, { text: "Shabash Imran! Aaj bhi zabardast kaam karein." }), "send");
  assert.equal((await call("salesman", "GET", `/api/coaching/${imran}`)).status, 403);
});

/** Salary needs a photo of the signed salary sheet. */
async function salaryProof() {
  const r = await call("manager", "POST", "/api/ai/read-photo", { kind: "proof", image: "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==" });
  return [r.data.photo_id as number];
}

/**
 * Cashier desk — the cash counter of the pump:
 *  - money received (khata customers, wholesale clients, others) and paid (suppliers, expenses, staff advances, others),
 *    each with a voucher number for the slip
 *  - cheque register: cheques received (post-dated too) and cheques we issue — deposit, clear, bounce
 *  - cash from the salesmen after each shift (handover, counted by the cashier)
 *  - day book: every rupee in and out of the day, cash and bank, with opening and closing cash
 * The rights (cashier.desk, cash.receive, cash.pay, cheques.manage, shifts.handover, bank.*) are ticked per role by the admin.
 */
import { Router, type Request } from "express";
import { z } from "zod";
import { all, get, run, tx, now, pkDate, pkStart, pkEnd, pkDayStart, type Row } from "../db.js";
import { h, parse, tid, requirePerm, can } from "../auth.js";
import { AppError, round2, pkr, createAlert, khataEntry } from "../services.js";
import { sendDirect, sendWhatsApp } from "../whatsapp/cloud.js";
import { linkPhotos, proofPhotos, proofCol, requireProof, isCheque } from "./capture.js";
import { bankAccountFor, accountName, bankAccounts, accountIdField, posMap, POS_DIGITAL } from "./banks.js";
import { cashPosition, handoverMode } from "./backoffice.js";
import { chargeShortage } from "./staff.js";
import { clientDue, insertTxn } from "./wholesale.js";
import { promises } from "./wholesaleDesk.js";
import { supplierOwed } from "./suppliers.js";
import { staffBalance } from "./staff.js";
import { createExpense } from "./expenses.js";
import { notify, staff } from "../notifications.js";
import { shiftSummary } from "../shifts.js";

export const cashier = Router();

const day = z.string().regex(/^\d{4}-\d{2}-\d{2}$/);
const isCash = (m?: string | null) => !m || /^cash$/i.test(m.trim());
const vno = (dir: string, id: number) => `${dir === "in" ? "RV" : "PV"}-${String(id).padStart(5, "0")}`;

function voucher(req: Request, v: { direction: "in" | "out"; party_type: string; party_id?: number | null; party_name: string; amount: number; method: string;
  account_id?: number | null; category?: string | null; ref?: string | null; note?: string | null; src?: string | null }) {
  const { id } = run(`INSERT INTO cashier_vouchers (tenant_id,direction,party_type,party_id,party_name,amount,method,account_id,category,ref,note,src,created_by,created_at)
    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)`, tid(req), v.direction, v.party_type, v.party_id ?? null, v.party_name, v.amount, v.method, v.account_id ?? null,
    v.category ?? null, v.ref ?? null, v.note ?? null, v.src ?? null, req.user!.name, now());
  return { ...get("SELECT * FROM cashier_vouchers WHERE id=?", id)!, no: vno(v.direction, id) };
}

/* ---------------- cheques ---------------- */
/** Both registers in one list: wholesale cheques (src "w") and the cashier's own (src "c"). */
function allCheques(t: number) {
  const acc = new Map(all("SELECT * FROM bank_accounts WHERE tenant_id=?", t).map((a) => [a.id, accountName(a)]));
  const w = all(`SELECT q.*, ${proofCol("'wchq:'||q.id")}, c.name party_name, c.phone FROM wholesale_cheques q JOIN wholesale_clients c ON c.id=q.client_id WHERE q.tenant_id=?`, t)
    .map((q) => ({ src: "w", id: q.id, direction: "in", party_type: "wholesale", party_id: q.client_id, party_name: q.party_name, phone: q.phone, amount: q.amount, bank: q.bank,
      cheque_no: q.cheque_no, cheque_date: q.cheque_date, status: q.status, account_id: q.account_id, reason: q.bounce_reason, note: q.note, created_by: q.created_by,
      created_at: q.created_at, updated_at: q.updated_at, proof_ids: q.proof_ids }));
  const c = all(`SELECT q.*, ${proofCol("'chq:'||q.id")} FROM cheques q WHERE q.tenant_id=?`, t).map((q) => ({ src: "c", ...q }));
  const order: Record<string, number> = { in_hand: 0, issued: 0, deposited: 1, bounced: 2 };
  return [...w, ...c].map((q) => ({ ...q, account_name: q.account_id ? acc.get(q.account_id) ?? null : null }))
    .sort((a, b) => (order[a.status] ?? 3) - (order[b.status] ?? 3) || (a.cheque_date < b.cheque_date ? -1 : a.cheque_date > b.cheque_date ? 1 : a.id - b.id));
}

function chequeTotals(list: Row[]) {
  const today = pkDate(), soon = pkDate(Date.now() + 7 * 86400_000);
  const sum = (f: (q: Row) => boolean) => { const x = list.filter(f); return { n: x.length, amount: round2(x.reduce((a, q) => a + q.amount, 0)) }; };
  return {
    in_hand: sum((q) => q.status === "in_hand"),
    to_deposit: sum((q) => q.status === "in_hand" && q.cheque_date <= today),
    deposited: sum((q) => q.status === "deposited"),
    issued: sum((q) => q.status === "issued"),
    issued_due: sum((q) => q.status === "issued" && q.cheque_date <= soon),
    bounced: sum((q) => q.status === "bounced" && q.updated_at >= new Date(Date.now() - 30 * 86400_000).toISOString()),
  };
}

const chequeFields = z.object({ bank: z.string().trim().min(2).max(80), cheque_no: z.string().trim().min(2).max(30), cheque_date: day });

function newCheque(req: Request, c: { direction: "in" | "out"; party_type: string; party_id: number | null; party_name: string; amount: number; account_id: number | null; note?: string | null; category?: string | null } & z.infer<typeof chequeFields>, photos?: number[] | null) {
  const t = tid(req);
  if (get("SELECT id FROM cheques WHERE tenant_id=? AND direction=? AND bank=? AND cheque_no=? AND status NOT IN ('returned','cancelled')", t, c.direction, c.bank, c.cheque_no))
    throw new AppError(400, `Cheque ${c.cheque_no} of ${c.bank} is already in the register`);
  const { id } = run(`INSERT INTO cheques (tenant_id,direction,party_type,party_id,party_name,amount,bank,cheque_no,cheque_date,status,account_id,note,created_by,created_at,updated_at,category)
    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)`, t, c.direction, c.party_type, c.party_id, c.party_name, c.amount, c.bank, c.cheque_no, c.cheque_date,
    c.direction === "in" ? "in_hand" : "issued", c.account_id, c.note ?? null, req.user!.name, now(), now(), c.category ?? null);
  linkPhotos(t, photos, `chq:${id}`);
  return get("SELECT * FROM cheques WHERE id=?", id)!;
}

/* ---------------- parties ---------------- */
function party(t: number, type: string, id: number | null | undefined) {
  if (type === "other") return null;
  if (!id) throw new AppError(400, "Choose who it is");
  const sql: Record<string, string> = {
    khata: "SELECT id, name, phone FROM customers WHERE id=? AND tenant_id=?",
    wholesale: "SELECT id, name, phone FROM wholesale_clients WHERE id=? AND tenant_id=?",
    supplier: "SELECT id, name, phone FROM suppliers WHERE id=? AND tenant_id=?",
    staff: "SELECT id, name, phone FROM users WHERE id=? AND tenant_id=? AND active=1",
  };
  const p = sql[type] ? get(sql[type], id, t) : null;
  if (!p) throw new AppError(404, "Not found");
  return p;
}

const balanceOf = (type: string, id: number) =>
  type === "khata" ? round2(get("SELECT balance FROM customers WHERE id=?", id)!.balance) : type === "wholesale" ? round2(clientDue(id))
    : type === "supplier" ? round2(supplierOwed(id)) : type === "staff" ? staffBalance(id) : null;

cashier.get("/cashier/parties", requirePerm("cashier.desk"), h((req) => {
  const t = tid(req);
  const q = `%${String(req.query.q ?? "").trim()}%`;
  const kind = String(req.query.kind ?? "");
  const out: Record<string, Row[]> = {};
  if (!kind || kind === "khata") out.khata = all(`SELECT id, name, phone, balance, credit_limit FROM customers WHERE tenant_id=? AND (name LIKE ? OR COALESCE(phone,'') LIKE ?)
    ORDER BY (balance > 0) DESC, balance DESC, name LIMIT 30`, t, q, q);
  if (!kind || kind === "wholesale") out.wholesale = all("SELECT id, name, phone, business_name FROM wholesale_clients WHERE tenant_id=? AND active=1 AND (name LIKE ? OR COALESCE(business_name,'') LIKE ? OR COALESCE(phone,'') LIKE ?) ORDER BY name LIMIT 30", t, q, q, q)
    .map((c) => ({ ...c, balance: round2(clientDue(c.id)) })).sort((a, b) => b.balance - a.balance);
  if (!kind || kind === "supplier") out.supplier = all("SELECT id, name, phone FROM suppliers WHERE tenant_id=? AND name LIKE ? ORDER BY name LIMIT 30", t, q)
    .map((s) => ({ ...s, balance: round2(supplierOwed(s.id)) })).sort((a, b) => b.balance - a.balance);
  if (!kind || kind === "staff") out.staff = all("SELECT id, name, phone, role FROM users WHERE tenant_id=? AND active=1 AND role<>'admin' AND name LIKE ? ORDER BY name LIMIT 30", t, q)
    .map((u) => ({ ...u, balance: staffBalance(u.id) }));
  return out;
}));

/* ---------------- receive ---------------- */
const moneyBody = z.object({
  party_type: z.string(), party_id: z.number().int().optional().nullable(), party_name: z.string().trim().max(80).optional().nullable(),
  amount: z.number().positive().max(1_000_000_000), method: z.string().trim().min(2).max(30), account_id: accountIdField,
  ref: z.string().trim().max(60).optional().nullable(), note: z.string().trim().max(200).optional().nullable(), category: z.string().max(60).optional().nullable(),
  photo_ids: proofPhotos, notify: z.boolean().default(true), cheque: chequeFields.optional().nullable(),
});

cashier.post("/cashier/receive", requirePerm("cash.receive"), h(async (req) => {
  const t = tid(req);
  const b = parse(moneyBody.extend({ party_type: z.enum(["khata", "wholesale", "other"]) }), req.body);
  const p = party(t, b.party_type, b.party_id);
  const name = p?.name ?? b.party_name;
  if (!name) throw new AppError(400, "Write who gave the money");
  const cheque = isCheque(b.method);
  const account = cheque ? null : bankAccountFor(t, b.account_id, b.method);
  if (!cheque && !isCash(b.method) && b.party_type === "other" && !account) throw new AppError(400, "Choose the bank account the money came into");
  let src: string | null = null;
  let message: string | null = null;

  if (cheque) {
    // a cheque is not money until it clears: it goes into the register
    if (!b.cheque) throw new AppError(400, "Enter the cheque's bank, number and date");
    requireProof(t, b.photo_ids, "cheque");
    if (b.party_type === "wholesale") {
      if (get("SELECT id FROM wholesale_cheques WHERE tenant_id=? AND bank=? AND cheque_no=? AND status<>'returned'", t, b.cheque.bank, b.cheque.cheque_no))
        throw new AppError(400, `Cheque ${b.cheque.cheque_no} of ${b.cheque.bank} is already in the register`);
      const { id } = run(`INSERT INTO wholesale_cheques (tenant_id,client_id,amount,bank,cheque_no,cheque_date,status,note,created_by,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)`,
        t, p!.id, b.amount, b.cheque.bank, b.cheque.cheque_no, b.cheque.cheque_date, "in_hand", b.note ?? null, req.user!.name, now(), now());
      linkPhotos(t, b.photo_ids, `wchq:${id}`);
      src = `wchq:${id}`;
    } else {
      src = `chq:${newCheque(req, { direction: "in", party_type: b.party_type, party_id: p?.id ?? null, party_name: name, amount: b.amount, account_id: null, note: b.note, ...b.cheque }, b.photo_ids).id}`;
    }
    message = `Cheque ${b.cheque.cheque_no} (${pkr(b.amount)}) is in the register. It is counted as paid when it clears.`;
  } else if (b.party_type === "khata") {
    const updated = khataEntry(p!.id, "credit", b.amount, b.method, b.note || "Payment received at the cash counter", account);
    const kid = get("SELECT MAX(id) id FROM khata_ledger WHERE customer_id=?", p!.id)!.id;
    linkPhotos(t, b.photo_ids, `khata:${kid}`);
    src = `khata:${kid}`;
    if (b.notify) await sendWhatsApp(t, updated, `✅ Shukriya ${updated.name}! ${pkr(b.amount)} ki payment mil gayi (${b.method}). Naya khata balance: ${pkr(updated.balance)}.`, "system", { kind: "payment_receipt" }).catch(() => undefined);
  } else if (b.party_type === "wholesale") {
    const r = insertTxn(req, p!.id, { type: "payment", amount: b.amount, method: b.method, ref: b.ref, note: b.note, photo_ids: b.photo_ids, account_id: account });
    src = `wtx:${r.id}`;
  } else if (account) {
    const { id } = run("INSERT INTO bank_txns (tenant_id,account_id,kind,amount,party,ref,note,txn_date,created_by,created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
      t, account, "other_in", b.amount, name, b.ref ?? null, b.category || b.note || "Other money in", now(), req.user!.name, now());
    src = `bank:${id}`;
  }
  const v = voucher(req, { direction: "in", party_type: b.party_type, party_id: p?.id, party_name: name, amount: b.amount, method: cheque ? "Cheque" : b.method,
    account_id: account, category: b.category, ref: b.cheque?.cheque_no ?? b.ref, note: b.note, src });
  if (!src) linkPhotos(t, b.photo_ids, `voucher:${v.id}`);
  return { voucher: v, balance_after: p ? balanceOf(b.party_type, p.id) : null, cheque_pending: cheque, message };
}));

/* ---------------- pay ---------------- */
cashier.post("/cashier/pay", requirePerm("cash.pay"), h(async (req) => {
  const t = tid(req);
  const b = parse(moneyBody.extend({ party_type: z.enum(["supplier", "expense", "staff", "other"]) }), req.body);
  const p = b.party_type === "expense" ? null : party(t, b.party_type, b.party_id);
  const name = p?.name ?? b.party_name ?? (b.party_type === "expense" ? b.category : null);
  if (!name) throw new AppError(400, "Write who was paid");
  const cheque = isCheque(b.method);
  const account = bankAccountFor(t, b.account_id, cheque ? "bank" : b.method);
  if (!isCash(b.method) && !account) throw new AppError(400, cheque ? "Choose the bank account the cheque is drawn on" : "Choose the bank account the money is paid from");
  if (b.party_type === "staff" && cheque) throw new AppError(400, "Give a staff advance in cash or by bank transfer");
  if (isCash(b.method)) {
    const pos = cashPosition(t);
    if (pos.last_count && b.amount > pos.cash_in_hand + 0.01) throw new AppError(400, `Only ${pkr(pos.cash_in_hand)} cash should be in hand`);
  }
  let src: string | null = null;
  let message: string | null = null;
  if (cheque) {
    // a cheque leaves the bank only when it is paid: until then it waits in the register (an expense cheque too)
    if (!b.cheque) throw new AppError(400, "Enter the cheque's bank, number and date");
    if (b.party_type === "expense" && !b.category) throw new AppError(400, "Choose the expense head");
    src = `chq:${newCheque(req, { direction: "out", party_type: b.party_type, party_id: p?.id ?? null, party_name: name, amount: b.amount, account_id: account, note: b.note, category: b.category ?? null, ...b.cheque }, b.photo_ids).id}`;
    message = `Cheque ${b.cheque.cheque_no} is in the register. Mark it cleared when the bank pays it.`;
  } else if (b.party_type === "supplier") {
    const pid = run("INSERT INTO supplier_txns (tenant_id,supplier_id,type,amount,method,ref,note,created_by,txn_date,created_at,account_id) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
      t, p!.id, "payment", b.amount, b.method, b.ref ?? null, b.note ?? null, req.user!.name, now(), now(), account).id;
    linkPhotos(t, b.photo_ids, `stx:${pid}`);
    src = `stx:${pid}`;
  } else if (b.party_type === "expense") {
    if (!b.category) throw new AppError(400, "Choose the expense head");
    const method = (cheque ? "cheque" : b.method.toLowerCase().replace(/\s+transfer$/, "")) as any;
    const e = await createExpense(req, { category: b.category, amount: b.amount, paid_to: b.party_name ?? null, method: ["cash", "bank", "jazzcash", "easypaisa", "raast", "cheque", "card"].includes(method) ? method : "bank",
      note: b.note ?? null, receipt_ref: b.ref ?? b.cheque?.cheque_no ?? null, account_id: account, photo_id: b.photo_ids?.[0] ?? null });
    src = `expense:${e.id}`;
    if (e.status === "pending") message = "Above the approval limit — sent to the owner for approval.";
  } else if (b.party_type === "staff") {
    const lid = run("INSERT INTO staff_ledger (tenant_id,user_id,type,amount,note,created_by,created_at,method,account_id) VALUES (?,?,?,?,?,?,?,?,?)",
      t, p!.id, "advance", b.amount, b.note ?? "Advance from the cash counter", req.user!.name, now(), b.method, account).id;
    linkPhotos(t, b.photo_ids, `staff:${lid}`);
    src = `staff:${lid}`;
    await notify(t, [p!], { type: "staff_ledger", title: `Advance ${pkr(b.amount)} given`, body: `Your account: ${pkr(staffBalance(p!.id))} to be adjusted.` });
  } else if (account) {
    const { id } = run("INSERT INTO bank_txns (tenant_id,account_id,kind,amount,party,ref,note,txn_date,created_by,created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
      t, account, "other_out", -b.amount, name, b.ref ?? null, b.category || b.note || "Other payment", now(), req.user!.name, now());
    src = `bank:${id}`;
  }
  const v = voucher(req, { direction: "out", party_type: b.party_type, party_id: p?.id, party_name: name, amount: b.amount, method: cheque ? "Cheque" : b.method,
    account_id: account, category: b.category, ref: b.cheque?.cheque_no ?? b.ref, note: b.note, src });
  if (!src) linkPhotos(t, b.photo_ids, `voucher:${v.id}`);
  return { voucher: v, balance_after: p ? balanceOf(b.party_type, p.id) : null, cheque_pending: cheque, message };
}));

cashier.get("/cashier/vouchers", requirePerm("cashier.desk"), h((req) => {
  const d = req.query.date ? parse(day, req.query.date) : pkDate();
  return all(`SELECT v.*, ${proofCol("'voucher:'||v.id")} FROM cashier_vouchers v WHERE v.tenant_id=? AND v.created_at >= ? AND v.created_at < ? ORDER BY v.id DESC`, tid(req), pkStart(d), pkEnd(d))
    .map((v) => ({ ...v, no: vno(v.direction, v.id) }));
}));

/* ---------------- cheque register ---------------- */
cashier.get("/cashier/cheques", requirePerm("cheques.manage"), h((req) => {
  const list = allCheques(tid(req));
  return { cheques: list, totals: chequeTotals(list) };
}));

cashier.post("/cashier/cheques/:id/:action", requirePerm("cheques.manage"), h(async (req) => {
  const t = tid(req);
  const q = get("SELECT * FROM cheques WHERE id=? AND tenant_id=?", Number(req.params.id), t);
  if (!q) throw new AppError(404, "Cheque not found");
  const action = parse(z.enum(["deposit", "clear", "bounce", "return", "cancel"]), req.params.action);
  const b = parse(z.object({ account_id: accountIdField, reason: z.string().max(200).optional().nullable() }), req.body ?? {});
  const stamp = now();
  const set = (sql: string, ...args: unknown[]) => run(`UPDATE cheques SET ${sql}, updated_at=? WHERE id=?`, ...args, stamp, q.id);
  const open = q.direction === "in" ? ["in_hand", "deposited"] : ["issued"];
  if (!open.includes(q.status)) throw new AppError(400, "This cheque is already closed");
  if (q.direction === "in" && action === "cancel") throw new AppError(400, "Give a received cheque back with “return”");
  if (q.direction === "out" && ["deposit", "return"].includes(action)) throw new AppError(400, "That is for cheques received");

  if (action === "deposit") {
    if (q.status !== "in_hand") throw new AppError(400, "Only a cheque in hand can be deposited");
    if (q.cheque_date > pkDate()) throw new AppError(400, `This cheque is dated ${q.cheque_date} — deposit it on or after that day`);
    const acc = bankAccountFor(t, b.account_id ?? q.account_id, "cheque");
    if (!acc) throw new AppError(400, "Choose the bank account it is deposited in");
    set("status='deposited', account_id=?, deposited_at=?", acc, stamp);
  } else if (action === "clear") {
    const acc = bankAccountFor(t, b.account_id ?? q.account_id, "cheque");
    if (!acc) throw new AppError(400, "Choose the bank account");
    const note = `${q.bank} cheque ${q.cheque_no} dated ${q.cheque_date} — cleared`;
    tx(() => {
      let ref: string;
      if (q.direction === "in" && q.party_type === "khata" && q.party_id) {
        khataEntry(q.party_id, "credit", q.amount, "Cheque", note, acc);
        ref = `khata:${get("SELECT MAX(id) id FROM khata_ledger WHERE customer_id=?", q.party_id)!.id}`;
      } else if (q.direction === "out" && q.party_type === "supplier" && q.party_id) {
        ref = `stx:${run("INSERT INTO supplier_txns (tenant_id,supplier_id,type,amount,method,ref,note,created_by,txn_date,created_at,account_id) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
          t, q.party_id, "payment", q.amount, "Cheque", `CHQ ${q.cheque_no}`, note, req.user!.name, stamp, stamp, acc).id}`;
      } else if (q.direction === "out" && q.party_type === "staff" && q.party_id) {
        ref = `staff:${run("INSERT INTO staff_ledger (tenant_id,user_id,type,amount,note,created_by,created_at,method,account_id) VALUES (?,?,?,?,?,?,?,?,?)",
          t, q.party_id, "advance", q.amount, note, req.user!.name, stamp, "Cheque", acc).id}`;
      } else if (q.direction === "out" && q.party_type === "expense") {
        // the expense is booked the day the bank pays the cheque
        ref = `expense:${run(`INSERT INTO expenses (tenant_id,category,amount,paid_to,method,note,receipt_ref,status,created_by,approved_by,expense_date,created_at,account_id)
          VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)`, t, q.category ?? "Other", q.amount, q.party_name, "cheque", note, `CHQ ${q.cheque_no}`, "approved", q.created_by, req.user!.name, pkDate(), stamp, acc).id}`;
      } else {
        ref = `bank:${run("INSERT INTO bank_txns (tenant_id,account_id,kind,amount,party,ref,note,txn_date,created_by,created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
          t, acc, q.direction === "in" ? "other_in" : "other_out", q.direction === "in" ? q.amount : -q.amount, q.party_name, `CHQ ${q.cheque_no}`, note, stamp, req.user!.name, stamp).id}`;
      }
      set("status='cleared', account_id=?, txn_ref=?, cleared_at=?", acc, ref, stamp);
    });
  } else if (action === "bounce") {
    set("status='bounced', reason=?", b.reason ?? null);
    const ours = q.direction === "out";
    createAlert(t, { type: "cheque_bounced", severity: "critical",
      title: ours ? `Our cheque bounced — ${q.party_name} ${pkr(q.amount)}` : `Cheque bounced — ${q.party_name} ${pkr(q.amount)}`,
      body: `${q.bank} cheque ${q.cheque_no} dated ${q.cheque_date}${b.reason ? `. Reason: ${b.reason}` : ""}. Marked by ${req.user!.name}.` });
    await notify(t, staff(t, ["admin"], req.user!.id), { type: "cheque_bounced", title: `⚠️ Cheque bounced — ${q.party_name}`, body: `${q.bank} ${q.cheque_no} · ${pkr(q.amount)}${b.reason ? ` · ${b.reason}` : ""}` });
    if (!ours && q.party_type === "khata" && q.party_id) {
      const c = get("SELECT name, phone, balance FROM customers WHERE id=?", q.party_id)!;
      if (c.phone) await sendDirect(t, { phone: c.phone, name: c.name }, "cheque_bounced", `chq:${q.id}`,
        `Assalam o Alaikum ${c.name}, aap ka ${q.bank} cheque # ${q.cheque_no} (${pkr(q.amount)}) bank se wapas aa gaya hai${b.reason ? ` (${b.reason})` : ""}. Meharbani kar ke payment jald ada karein. Khata: ${pkr(c.balance)}.`).catch(() => undefined);
    }
  } else if (action === "return") {
    if (q.status !== "in_hand") throw new AppError(400, "Only a cheque in hand can be given back");
    set("status='returned', reason=?", b.reason ?? "Given back");
  } else {
    set("status='cancelled', reason=?", b.reason ?? "Cancelled");
  }
  return get("SELECT * FROM cheques WHERE id=?", q.id);
}));

/* ---------------- cash from the salesmen ---------------- */
// a shift's cash stays "to take" until the cashier receives it (a month back, so none slips out of the books unseen)
const HANDOVER_DAYS = 30;
function handovers(t: number) {
  const since = new Date(Date.now() - HANDOVER_DAYS * 86400_000).toISOString();
  const cols = `sh.id, sh.attendant, sh.opened_at, sh.closed_at, sh.cash_expected, sh.cash_actual, sh.variance, sh.handed_amount, sh.handed_to, sh.handed_at, sh.handover_note, s.name station_name`;
  return {
    pending: all(`SELECT ${cols} FROM shifts sh JOIN stations s ON s.id=sh.station_id WHERE s.tenant_id=? AND sh.status='closed' AND sh.handed_at IS NULL AND sh.closed_at >= ? ORDER BY sh.closed_at`, t, since),
    done: all(`SELECT ${cols} FROM shifts sh JOIN stations s ON s.id=sh.station_id WHERE s.tenant_id=? AND sh.handed_at IS NOT NULL ORDER BY sh.handed_at DESC LIMIT 20`, t),
  };
}

cashier.get("/cashier/handovers", requirePerm("shifts.handover"), h((req) => handovers(tid(req))));

/**
 * Cash still with the salesmen: what open shifts should have in the bag now, plus shifts closed but not yet handed to the
 * cashier. Without a cashier (no handover step) closed shifts' cash is already in the office cash book.
 */
export function cashWithSalesmen(t: number) {
  const open = all("SELECT sh.id FROM shifts sh JOIN stations s ON s.id=sh.station_id WHERE s.tenant_id=? AND sh.status='open'", t);
  const in_open_shifts = round2(open.reduce((a, s) => a + (shiftSummary(s.id).cash_expected ?? 0), 0));
  const not_handed = handoverMode(t) ? round2(handovers(t).pending.reduce((a, s) => a + (s.cash_actual ?? 0), 0)) : 0;
  return { in_open_shifts, not_handed, open_shifts: open.length, total: round2(in_open_shifts + not_handed) };
}

cashier.post("/cashier/handovers/:id", requirePerm("shifts.handover"), h(async (req) => {
  const t = tid(req);
  const b = parse(z.object({ amount: z.number().min(0).max(100_000_000), note: z.string().max(200).optional().nullable() }), req.body);
  const sh = get("SELECT sh.*, s.name station_name FROM shifts sh JOIN stations s ON s.id=sh.station_id WHERE sh.id=? AND s.tenant_id=?", Number(req.params.id), t);
  if (!sh) throw new AppError(404, "Shift not found");
  if (sh.status !== "closed") throw new AppError(400, "The shift is still open");
  if (sh.handed_at) throw new AppError(400, `Already received by ${sh.handed_to}`);
  run("UPDATE shifts SET handed_amount=?, handed_to=?, handed_at=?, handover_note=? WHERE id=?", b.amount, req.user!.name, now(), b.note ?? null, sh.id);
  const diff = round2(b.amount - (sh.cash_actual ?? 0));
  if (diff <= -100) {
    const a = createAlert(t, { station_id: sh.station_id, type: "handover_short", severity: diff <= -5000 ? "critical" : "warning",
      title: `Handover short ${pkr(-diff)} — ${sh.attendant}`, body: `Shift #${sh.id}: salesman said ${pkr(sh.cash_actual)}, cashier ${req.user!.name} got ${pkr(b.amount)}.`, dedupe_key: `handover-${sh.id}` });
    if (a) await notify(t, staff(t, ["admin", "manager"], req.user!.id), { type: "handover_short", title: a.title, body: a.body });
    await chargeShortage(t, { id: sh.id, attendant: sh.attendant, station_id: sh.station_id }, diff);
  }
  return { shift: get("SELECT * FROM shifts WHERE id=?", sh.id), difference: diff, ...handovers(t) };
}));

/* ---------------- day book ---------------- */
export function cashierDayBook(t: number, d: string) {
  const from = pkStart(d), to = pkEnd(d);
  // today: only what has happened so far (same cut-off as the cash book)
  const P = [t, from, to < now() ? to : now()] as const;
  const acc = new Map(all("SELECT * FROM bank_accounts WHERE tenant_id=?", t).map((a) => [a.id, accountName(a)]));
  const rows: Row[] = [
    ...(handoverMode(t)
      ? all(`SELECT sh.handed_at at, 'in' dir, 'Shift cash received' what, sh.attendant || ' · ' || s.name || ' · shift #' || sh.id party, 'Cash' method, sh.handed_amount amount, NULL account_id, sh.handed_to who
          FROM shifts sh JOIN stations s ON s.id=sh.station_id WHERE s.tenant_id=? AND sh.handed_at IS NOT NULL AND sh.handed_at >= ? AND sh.handed_at < ?`, ...P)
      : all(`SELECT sh.closed_at at, 'in' dir, 'Shift cash' what, sh.attendant || ' · ' || s.name || ' · shift #' || sh.id party, 'Cash' method, sh.cash_actual amount, NULL account_id, NULL who
          FROM shifts sh JOIN stations s ON s.id=sh.station_id WHERE s.tenant_id=? AND sh.status='closed' AND sh.closed_at >= ? AND sh.closed_at < ?`, ...P)),
    ...all(`SELECT x.created_at at, 'in' dir, 'Counter sale (no shift)' what, x.product || ' ' || ROUND(x.litres,1) || ' L' party, 'Cash' method, x.amount, NULL account_id, NULL who
      FROM sales x JOIN stations s ON s.id=x.station_id WHERE s.tenant_id=? AND x.shift_id IS NULL AND x.payment_method='cash' AND x.created_at >= ? AND x.created_at < ?`, ...P),
    ...all(`SELECT created_at at, 'in' dir, 'Shop sale (no shift)' what, '' party, 'Cash' method, total amount, NULL account_id, NULL who
      FROM shop_sales WHERE tenant_id=? AND shift_id IS NULL AND payment_method='cash' AND created_at >= ? AND created_at < ?`, ...P),
    ...all(`SELECT k.created_at at, 'in' dir, 'Khata payment' what, c.name party, COALESCE(k.ref,'') method, k.amount, k.account_id, NULL who
      FROM khata_ledger k JOIN customers c ON c.id=k.customer_id WHERE c.tenant_id=? AND k.type='credit' AND k.created_at >= ? AND k.created_at < ?`, ...P),
    ...all(`SELECT x.created_at at, 'in' dir, 'Wholesale payment' what, c.name party, COALESCE(x.method,'') method, x.amount, x.account_id, x.created_by who
      FROM wholesale_txns x JOIN wholesale_clients c ON c.id=x.client_id WHERE x.tenant_id=? AND x.type='payment' AND x.voided=0 AND x.created_at >= ? AND x.created_at < ?`, ...P),
    ...all(`SELECT x.created_at at, 'out' dir, 'Supplier payment' what, s.name party, COALESCE(x.method,'') method, x.amount, x.account_id, x.created_by who
      FROM supplier_txns x JOIN suppliers s ON s.id=x.supplier_id WHERE x.tenant_id=? AND x.type='payment' AND COALESCE(x.method,'')<>'WHT' AND x.created_at >= ? AND x.created_at < ?`, ...P),
    ...all(`SELECT created_at at, 'out' dir, 'Expense · ' || category what, COALESCE(paid_to,'') party, method, amount, account_id, created_by who
      FROM expenses WHERE tenant_id=? AND status='approved' AND shift_id IS NULL AND created_at >= ? AND created_at < ?`, ...P),
    ...all(`SELECT l.created_at at, CASE WHEN l.type='advance' OR l.type='bonus' THEN 'out' ELSE 'in' END dir,
      CASE l.type WHEN 'advance' THEN 'Staff advance' WHEN 'bonus' THEN 'Staff bonus' WHEN 'deduction' THEN 'Kept back from salary (advance / loan)' ELSE 'Staff paid back' END what,
      u.name party, COALESCE(l.method,'Cash') method, l.amount, l.account_id, l.created_by who FROM staff_ledger l JOIN users u ON u.id=l.user_id
      WHERE l.tenant_id=? AND (l.type IN ('advance','repayment') OR (l.type='bonus' AND l.month IS NULL) OR (l.type='deduction' AND l.month IS NOT NULL)) AND l.created_at >= ? AND l.created_at < ?`, ...P),
    ...all(`SELECT sold_at at, 'in' dir, 'Fuel coupons sold' what, COALESCE(buyer,'') party, 'Cash' method, value amount, NULL account_id, sold_by who
      FROM fuel_coupons WHERE tenant_id=? AND method='cash' AND sold_at >= ? AND sold_at < ?`, ...P),
    ...all(`SELECT created_at at, CASE WHEN type='refund' THEN 'out' ELSE 'in' END dir, CASE WHEN type='refund' THEN 'Wallet refund' ELSE 'Wallet top-up' END what, '' party, 'Cash' method,
      amount, NULL account_id, NULL who FROM wallet_ledger WHERE tenant_id=? AND type IN ('deposit','refund') AND method='cash' AND created_at >= ? AND created_at < ?`, ...P),
    ...all(`SELECT created_at at, 'contra' dir, 'Cash deposited in bank' what, COALESCE(slip_ref,'') party, 'Cash' method, amount, account_id, deposited_by who
      FROM bank_deposits WHERE tenant_id=? AND created_at >= ? AND created_at < ?`, ...P),
    ...all(`SELECT created_at at, CASE WHEN kind IN ('withdraw','transfer') THEN 'contra' WHEN amount > 0 THEN 'in' ELSE 'out' END dir, COALESCE(note, kind) what, COALESCE(party,'') party,
      'Bank' method, ABS(amount) amount, account_id, created_by who FROM bank_txns WHERE tenant_id=? AND created_at >= ? AND created_at < ?`, ...P),
    ...all(`SELECT created_at at, direction dir, COALESCE(category, CASE direction WHEN 'in' THEN 'Other money in' ELSE 'Other payment' END) what, party_name party, method, amount, account_id, created_by who
      FROM cashier_vouchers WHERE tenant_id=? AND party_type='other' AND LOWER(method)='cash' AND voided=0 AND created_at >= ? AND created_at < ?`, ...P),
  ].map((r) => ({ ...r, amount: round2(r.amount), account: r.account_id ? acc.get(r.account_id) ?? null : null, cash: isCash(r.method) }))
    .sort((a, b) => (a.at < b.at ? -1 : 1));
  // the day the cash book starts (first cash count): only what came after the count moves the book
  const first = get("SELECT MIN(created_at) v FROM cash_counts WHERE tenant_id=?", t)!.v as string | null;
  const startsToday = Boolean(first && first >= from && first < to);
  if (startsToday) for (const r of rows) if (r.at < first!) r.before_start = true;
  const sum = (f: (r: Row) => boolean, all = false) => round2(rows.filter((r) => (all || !r.before_start) && f(r)).reduce((a, r) => a + r.amount, 0));
  const totals = {
    in_cash: sum((r) => r.dir === "in" && r.cash), in_bank: sum((r) => r.dir === "in" && !r.cash, true),
    out_cash: sum((r) => r.dir === "out" && r.cash), out_bank: sum((r) => r.dir === "out" && !r.cash, true),
    deposited: sum((r) => r.dir === "contra" && r.what === "Cash deposited in bank"),
  };
  const withdrawn = round2(-(get("SELECT COALESCE(SUM(amount),0) v FROM bank_txns WHERE tenant_id=? AND kind='withdraw' AND created_at >= ? AND created_at < ?", t, startsToday ? first : from, P[2])!.v ?? 0));
  // opening and closing both from the cash book. A cash count during the day resets the book to what was counted:
  // that difference is its own line, so opening + in − out − bank + withdrawn ± counted difference = closing, always.
  // Before the very first cash count the book has not started (cash on hand was never known), so there is no opening.
  const end = to < now() ? new Date(Date.parse(to) - 1).toISOString() : now();
  const counts = all("SELECT * FROM cash_counts WHERE tenant_id=? AND created_at >= ? AND created_at < ?", ...P);
  const started = !first || first < from;
  const opening = started ? cashPosition(t, new Date(Date.parse(from) - 1).toISOString()).cash_in_hand
    : startsToday ? round2(get("SELECT amount FROM cash_counts WHERE tenant_id=? AND created_at=? ORDER BY id LIMIT 1", t, first)!.amount) : null;
  const closing = !first || first <= end ? cashPosition(t, end).cash_in_hand : null;
  let counted = 0;
  if (opening != null && closing != null) counted = round2(closing - (opening + totals.in_cash - totals.out_cash - totals.deposited + withdrawn));
  counts.forEach((c, i) => {
    if (c.created_at === first) {
      rows.push({ at: c.created_at, dir: "count", what: "Cash counted — the cash book starts here", party: `counted ${pkr(c.amount)} = opening`, method: "Cash", amount: round2(c.amount), signed: 0,
        account_id: null, account: null, who: c.counted_by, cash: true, start: true });
      return;
    }
    const diff = counts.length === 1 ? counted : round2(c.variance ?? 0);
    rows.push({ at: c.created_at, dir: "count", what: diff === 0 ? "Cash counted — matches the book" : `Cash counted — ${diff < 0 ? "short" : "over"} against the book`,
      party: `counted ${pkr(c.amount)}`, method: "Cash", amount: Math.abs(diff), signed: diff, account_id: null, account: null, who: c.counted_by, cash: true, n: i });
  });
  rows.sort((a, b) => (a.at < b.at ? -1 : 1));
  return { date: d, rows, totals: { ...totals, withdrawn, counted }, cash: { opening, closing, book_starts: started ? null : first, starts_today: startsToday } };
}

/**
 * Online money (card / JazzCash / Easypaisa / Raast) from \`since\`: it never comes in the cash bag, it lands in a bank account.
 * Per method: entered on the POS, added at shift close from the machine total, shop sales, and the linked account.
 */
export function onlineToday(t: number, since = pkDayStart()) {
  const map = posMap(t);
  const accName = (id?: number) => { const a = id ? get("SELECT * FROM bank_accounts WHERE id=? AND tenant_id=?", id, t) : null; return a ? accountName(a) : null; };
  const methods = POS_DIGITAL.map((m) => {
    const f = get(`SELECT COUNT(*) FILTER (WHERE s.at_close IS NULL) n, COALESCE(SUM(s.amount) FILTER (WHERE s.at_close IS NULL),0) on_pos, COALESCE(SUM(s.amount) FILTER (WHERE s.at_close=1),0) at_close
      FROM sales s JOIN stations st ON st.id=s.station_id WHERE st.tenant_id=? AND s.payment_method=? AND s.created_at >= ?`, t, m, since)!;
    const shop = get("SELECT COUNT(*) n, COALESCE(SUM(total),0) v FROM shop_sales WHERE tenant_id=? AND payment_method=? AND created_at >= ?", t, m, since)!;
    const on_pos = round2(f.on_pos), at_close = round2(f.at_close), shopV = round2(shop.v);
    return { method: m, on_pos, at_close, shop: shopV, n: f.n + shop.n, total: round2(on_pos + at_close + shopV), account_id: map[m] ?? null, account: accName(map[m]) };
  }).filter((o) => o.total > 0);
  const open_shifts = get("SELECT COUNT(*) n FROM shifts sh JOIN stations st ON st.id=sh.station_id WHERE st.tenant_id=? AND sh.status='open'", t)!.n;
  return { methods, total: round2(methods.reduce((a, o) => a + o.total, 0)), open_shifts };
}

cashier.get("/cashier/daybook", requirePerm("cashier.desk"), h((req) => cashierDayBook(tid(req), req.query.date ? parse(day, req.query.date) : pkDate())));

/* ---------------- the desk ---------------- */
cashier.get("/cashier/desk", requirePerm("cashier.desk"), h((req) => {
  const t = tid(req);
  const today = pkDate();
  const cash = cashPosition(t);
  const ho = handovers(t);
  const cheques = allCheques(t);
  const ct = chequeTotals(cheques);
  const banks = can(req.user, "bank.view") ? bankAccounts(t) : null;
  const promised = promises(t).filter((p) => p.state === "today" || p.state === "broken").map((p) => ({ client_id: p.client_id, name: p.client_name, amount: p.amount, promised_on: p.promised_on, state: p.state }));
  const book = cashierDayBook(t, today);
  const vouchers = all("SELECT * FROM cashier_vouchers WHERE tenant_id=? AND created_at >= ? ORDER BY id DESC LIMIT 8", t, pkDayStart()).map((v) => ({ ...v, no: vno(v.direction, v.id) }));
  const counted = cash.last_count && cash.last_count.at >= pkDayStart();
  const online = onlineToday(t);
  const payables = all("SELECT id, name FROM suppliers WHERE tenant_id=?", t).map((s) => ({ id: s.id, name: s.name, owed: round2(supplierOwed(s.id)) })).filter((s) => s.owed > 0).sort((a, b) => b.owed - a.owed).slice(0, 5);

  // what to do now, in English and Urdu (numbers and dates kept left-to-right inside the Urdu line)
  const L = (x: string | number) => `\u2066${x}\u2069`;
  const many = (n: number, one: string) => `${n} ${one}${n === 1 ? "" : "s"}`;
  type Tip = { key: string; tone: "red" | "amber" | "blue" | "green"; en: string; ur: string; tab: string };
  const tips: Tip[] = [];
  for (const s of ho.pending) tips.push({ key: `ho-${s.id}`, tone: "amber", tab: "handover",
    en: `Take ${pkr(s.cash_actual)} from ${s.attendant} (shift #${s.id})`, ur: `${s.attendant} سے ${L(pkr(s.cash_actual))} کیش وصول کریں` });
  if (ct.to_deposit.n) tips.push({ key: "deposit", tone: "blue", tab: "cheques",
    en: `${many(ct.to_deposit.n, "cheque")} ready to deposit — ${pkr(ct.to_deposit.amount)}`, ur: `${L(ct.to_deposit.n)} چیک بینک میں جمع کروانے ہیں — ${L(pkr(ct.to_deposit.amount))}` });
  for (const q of cheques.filter((x) => x.status === "issued" && x.cheque_date <= pkDate(Date.now() + 3 * 86400_000))) {
    const bal = banks?.accounts.find((a) => a.id === q.account_id)?.balance;
    const short = bal != null && bal < q.amount;
    tips.push({ key: `iss-${q.id}`, tone: short ? "red" : "amber", tab: "cheques",
      en: `Our cheque to ${q.party_name} ${pkr(q.amount)} is due ${q.cheque_date}${short ? ` — only ${pkr(bal!)} in ${q.account_name}` : ""}`,
      ur: `${q.party_name} کو دیا گیا چیک ${L(pkr(q.amount))} تاریخ ${L(q.cheque_date)} کو کیش ہوگا${short ? " — بینک میں رقم کم ہے" : ""}` });
  }
  if (ct.bounced.n) tips.push({ key: "bounced", tone: "red", tab: "cheques", en: `${many(ct.bounced.n, "cheque")} bounced this month — ${pkr(ct.bounced.amount)}`, ur: `اس مہینے ${L(ct.bounced.n)} چیک واپس آئے — ${L(pkr(ct.bounced.amount))}` });
  for (const p of promised.filter((x) => x.state === "today")) tips.push({ key: `pr-${p.client_id}`, tone: "blue", tab: "receive",
    en: `${p.name} promised ${pkr(p.amount)} today`, ur: `${p.name} نے آج ${L(pkr(p.amount))} دینے کا وعدہ کیا ہے` });
  const limit = 500_000;
  if (cash.cash_in_hand > limit) tips.push({ key: "bank", tone: "amber", tab: "bank",
    en: `${pkr(cash.cash_in_hand)} cash in hand — deposit some in the bank`, ur: `${L(pkr(cash.cash_in_hand))} کیش ہاتھ میں ہے — بینک میں جمع کروائیں` });
  if (!counted) tips.push({ key: "count", tone: "green", tab: "count", en: "Count the cash before you close the counter", ur: "کاؤنٹر بند کرنے سے پہلے کیش گنیں" });

  return {
    cash: { in_hand: cash.cash_in_hand, last_count: cash.last_count, counted_today: Boolean(counted) },
    today: { ...book.totals, opening: book.cash.opening, entries: book.rows.length },
    handovers: { pending: ho.pending, pending_amount: round2(ho.pending.reduce((a, s) => a + (s.cash_actual ?? 0), 0)) },
    cheques: ct, upcoming: cheques.filter((q) => ["in_hand", "issued", "deposited"].includes(q.status)).slice(0, 6),
    banks, promised, payables, vouchers, tips,
    online,
  };
}));

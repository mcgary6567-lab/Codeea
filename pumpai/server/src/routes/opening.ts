/**
 * CEO-only "Opening balances" (purana hisaab) — going live on a pump that was ALREADY running.
 *
 * The day the app starts, people already owe the pump (khata customers, wholesale clients, thekedars), the pump
 * already owes its depots, there is money in the banks and the drawer, fuel in the tanks and advances with the staff.
 * Those figures are not income or expense of the app's first day — they are brought forward. This page writes them
 * once, replaceable until the owner is happy, and the ledger books them against one equity account,
 * "Opening balances (go-live)", so the P&L stays clean and every reconciliation still ties to the rupee.
 *
 *  - khata customer: a khata_ledger row ref "open:" (debit = customer owes, credit = we owe / advance), dated as-of
 *  - supplier / wholesale client / thekedar: their own opening_balance column (their due already starts from it)
 *  - bank account: opening_balance + opening_date (the statement already starts from it)
 *  - staff advance: a staff_ledger 'advance' row ref "open:" (no cash moves)
 *  - tank stock: a dip reading (book stock = the dip)
 *  - cash in the drawer: the FIRST cash count on the Cash page (ledger books the difference to owner's capital)
 */
import { Router } from "express";
import { z } from "zod";
import { all, get, run, tx, now, getSetting, setSetting, pkDate, pkStart } from "../db.js";
import { h, parse, tid, requirePerm } from "../auth.js";
import { AppError, round2, audit, normalizePhone } from "../services.js";
import { bankAccounts } from "./banks.js";
import { supplierOwed } from "./suppliers.js";
import { clientDue } from "./wholesale.js";
import { thekedarDue } from "./carriage.js";
import { staffBalance } from "./staff.js";
import { cashPosition } from "./backoffice.js";
import { assertLookup } from "./lookups.js";

export const opening = Router();
export const OPEN_REF = "open:";
export const OPEN_NOTE = "Purana baqaya — opening balance (go-live)";
const day = z.string().regex(/^\d{4}-\d{2}-\d{2}$/);

/** The go-live date every opening entry is stamped with (set once, changeable until the first real day). */
function asOf(t: number, d?: string) {
  if (d) setSetting(t, "opening_date", d);
  return getSetting(t, "opening_date", pkDate());
}

const khataOpening = (customerId: number) => {
  const r = get("SELECT type, amount FROM khata_ledger WHERE customer_id=? AND ref=? ORDER BY id DESC LIMIT 1", customerId, OPEN_REF);
  return r ? (r.type === "debit" ? r.amount : -r.amount) : 0;
};
/** Replace a customer's opening row (and move the balance by the difference). amount>0 owes us, <0 we owe them. */
export function setKhataOpening(t: number, customerId: number, amount: number, at: string) {
  const c = get("SELECT id, name FROM customers WHERE id=? AND tenant_id=?", customerId, t);
  if (!c) throw new AppError(404, "Customer not found");
  const amt = round2(amount);
  for (const old of all("SELECT * FROM khata_ledger WHERE customer_id=? AND ref=?", c.id, OPEN_REF)) {
    run("DELETE FROM khata_ledger WHERE id=?", old.id);
    run("UPDATE customers SET balance = balance + ? WHERE id=?", old.type === "debit" ? -old.amount : old.amount, c.id);
  }
  if (Math.abs(amt) >= 0.005) {
    run("INSERT INTO khata_ledger (customer_id,type,amount,ref,note,created_at) VALUES (?,?,?,?,?,?)", c.id, amt > 0 ? "debit" : "credit", Math.abs(amt), OPEN_REF, OPEN_NOTE, at);
    run("UPDATE customers SET balance = balance + ? WHERE id=?", amt, c.id);
  }
  return get("SELECT id, name, balance FROM customers WHERE id=?", c.id)!;
}

opening.get("/opening", requirePerm("opening.manage"), h((req) => {
  const t = tid(req);
  const cash = cashPosition(t);
  return {
    as_of: asOf(t),
    khata: all("SELECT id, name, phone, type, city, credit_limit, balance FROM customers WHERE tenant_id=? AND active=1 ORDER BY (credit_limit > 0) DESC, name", t)
      .map((c) => ({ ...c, opening: khataOpening(c.id) })),
    suppliers: all("SELECT id, name, phone, opening_balance FROM suppliers WHERE tenant_id=? AND active=1 ORDER BY name", t).map((s) => ({ ...s, owed: supplierOwed(s.id) })),
    wholesale: all("SELECT id, name, business_name, phone, opening_balance FROM wholesale_clients WHERE tenant_id=? ORDER BY name", t).map((c) => ({ ...c, due: clientDue(c.id) })),
    carriage: all("SELECT id, name, phone, opening_balance FROM thekedars WHERE tenant_id=? ORDER BY name", t).map((k) => ({ ...k, due: thekedarDue(k.id) })),
    banks: bankAccounts(t).accounts.map((a: any) => ({ id: a.id, bank: a.bank, title: a.title, account_no: a.account_no, kind: a.kind, opening_balance: a.opening_balance, opening_date: a.opening_date, balance: a.balance })),
    staff: all("SELECT id, name, role FROM users WHERE tenant_id=? AND active=1 AND role<>'admin' ORDER BY name", t).map((u) => ({
      ...u, balance: staffBalance(u.id), opening: get("SELECT COALESCE(SUM(amount),0) v FROM staff_ledger WHERE user_id=? AND ref=?", u.id, OPEN_REF)!.v })),
    stock: all(`SELECT tk.id, tk.name, tk.product, tk.capacity_l, tk.current_l, s.name station FROM tanks tk JOIN stations s ON s.id=tk.station_id WHERE s.tenant_id=? AND COALESCE(tk.active,1)=1 ORDER BY s.id, tk.id`, t),
    cash: { first_count: get("SELECT amount, created_at, counted_by FROM cash_counts WHERE tenant_id=? ORDER BY created_at, id LIMIT 1", t) ?? null, cash_in_hand: cash.cash_in_hand },
  };
}));

/** Khata openings for many customers at once (a row with 0 clears that customer's opening). */
opening.post("/opening/khata", requirePerm("opening.manage"), h((req) => {
  const b = parse(z.object({ as_of: day.optional(), rows: z.array(z.object({ customer_id: z.number().int(), amount: z.number().min(-1e9).max(1e9) })).min(1).max(2000) }), req.body);
  const t = tid(req);
  const at = pkStart(asOf(t, b.as_of));
  const out = tx(() => b.rows.map((r) => setKhataOpening(t, r.customer_id, r.amount, at)));
  audit(t, req.user!, "opening_khata", "opening", { rows: b.rows.length, total: round2(b.rows.reduce((a, r) => a + r.amount, 0)) });
  return { ok: true, updated: out, as_of: asOf(t) };
}));

/**
 * Paste a list from the old register: one customer per line — "name, phone, amount[, credit limit]"
 * (comma, tab or semicolon separated). Matched by phone, then by exact name; new names are created as khata accounts.
 */
opening.post("/opening/khata/import", requirePerm("opening.manage"), h((req) => {
  const b = parse(z.object({ as_of: day.optional(), text: z.string().min(1).max(200_000), type: z.string().max(40).default("retail"), credit_limit: z.number().min(0).optional() }), req.body);
  const t = tid(req);
  assertLookup(t, "customer_type", b.type);
  const at = pkStart(asOf(t, b.as_of));
  const errors: { line: number; text: string; reason: string }[] = [];
  let created = 0, updated = 0, total = 0;
  const lines = b.text.split(/\r?\n/).map((l) => l.trim()).filter(Boolean);
  tx(() => {
    lines.forEach((line, i) => {
      const cells = line.split(/\t|;|,/).map((x) => x.trim());
      if (/^(name|naam)/i.test(cells[0] ?? "") && i === 0) return; // header row
      const [name, phoneRaw, amountRaw, limitRaw] = cells;
      const amount = Number(String(amountRaw ?? "").replace(/[^\d.-]/g, ""));
      if (!name || name.length < 2) return void errors.push({ line: i + 1, text: line, reason: "name missing" });
      if (!Number.isFinite(amount)) return void errors.push({ line: i + 1, text: line, reason: "amount missing" });
      let phone: string | null = null;
      try { phone = phoneRaw && phoneRaw.replace(/\D/g, "").length >= 10 ? normalizePhone(phoneRaw) : null; } catch { phone = null; }
      let c = phone ? get("SELECT id FROM customers WHERE tenant_id=? AND phone=?", t, phone) : null;
      if (!c) c = get("SELECT id FROM customers WHERE tenant_id=? AND LOWER(name)=LOWER(?) ORDER BY (credit_limit > 0) DESC, id LIMIT 1", t, name);
      const limit = limitRaw != null && limitRaw !== "" ? Number(String(limitRaw).replace(/[^\d.]/g, "")) : null;
      if (!c) {
        if (!phone) return void errors.push({ line: i + 1, text: line, reason: "new customer needs a phone number" });
        const { id } = run("INSERT INTO customers (tenant_id,name,phone,type,credit_limit,opt_in,created_at) VALUES (?,?,?,?,?,1,?)",
          t, name, phone, b.type, Math.max(limit ?? b.credit_limit ?? 0, amount > 0 ? amount : 0), now());
        c = { id }; created++;
      } else {
        updated++;
        if (limit != null && Number.isFinite(limit)) run("UPDATE customers SET credit_limit=? WHERE id=?", limit, c.id);
        else if (amount > 0) run("UPDATE customers SET credit_limit = MAX(credit_limit, ?) WHERE id=?", amount, c.id);
      }
      setKhataOpening(t, c.id, amount, at);
      total += amount;
    });
  });
  audit(t, req.user!, "opening_khata_import", "opening", { lines: lines.length, created, updated, errors: errors.length, total: round2(total) });
  return { ok: true, created, updated, total: round2(total), errors, as_of: asOf(t) };
}));

const PARTY: Record<string, { table: string; label: string }> = { supplier: { table: "suppliers", label: "Supplier" }, wholesale: { table: "wholesale_clients", label: "Wholesale client" }, carriage: { table: "thekedars", label: "Thekedar" } };
/** Supplier (we owe) / wholesale client / thekedar (they owe): their opening balance column. */
opening.post("/opening/party", requirePerm("opening.manage"), h((req) => {
  const b = parse(z.object({ kind: z.enum(["supplier", "wholesale", "carriage"]), id: z.number().int(), amount: z.number().min(-1e9).max(1e9) }), req.body);
  const t = tid(req);
  const p = PARTY[b.kind];
  const row = get(`SELECT id, name FROM ${p.table} WHERE id=? AND tenant_id=?`, b.id, t);
  if (!row) throw new AppError(404, `${p.label} not found`);
  run(`UPDATE ${p.table} SET opening_balance=? WHERE id=?`, round2(b.amount), row.id);
  audit(t, req.user!, "opening_party", `${b.kind}:${row.id}`, { name: row.name, amount: round2(b.amount) });
  return { ok: true, id: row.id, name: row.name, opening_balance: round2(b.amount),
    due: b.kind === "supplier" ? supplierOwed(row.id) : b.kind === "wholesale" ? clientDue(row.id) : thekedarDue(row.id) };
}));

opening.post("/opening/bank", requirePerm("opening.manage"), h((req) => {
  const b = parse(z.object({ id: z.number().int(), amount: z.number().min(-1e9).max(1e10), date: day.optional() }), req.body);
  const t = tid(req);
  const a = get("SELECT id, bank, title FROM bank_accounts WHERE id=? AND tenant_id=?", b.id, t);
  if (!a) throw new AppError(404, "Bank account not found");
  run("UPDATE bank_accounts SET opening_balance=?, opening_date=? WHERE id=?", round2(b.amount), b.date ?? asOf(t), a.id);
  audit(t, req.user!, "opening_bank", `bank:${a.id}`, { bank: a.bank, amount: round2(b.amount) });
  return { ok: true, account: bankAccounts(t).accounts.find((x: any) => x.id === a.id) };
}));

/** Advance already with a staff member on day one (no cash moves now; it is recovered like any advance). */
opening.post("/opening/staff", requirePerm("opening.manage"), h((req) => {
  const b = parse(z.object({ user_id: z.number().int(), amount: z.number().min(0).max(1e8) }), req.body);
  const t = tid(req);
  const u = get("SELECT id, name FROM users WHERE id=? AND tenant_id=?", b.user_id, t);
  if (!u) throw new AppError(404, "Staff member not found");
  const at = pkStart(asOf(t));
  tx(() => {
    run("DELETE FROM staff_ledger WHERE user_id=? AND ref=?", u.id, OPEN_REF);
    if (b.amount >= 0.005) run("INSERT INTO staff_ledger (tenant_id,user_id,type,amount,note,ref,created_by,created_at) VALUES (?,?,?,?,?,?,?,?)", t, u.id, "advance", round2(b.amount), OPEN_NOTE, OPEN_REF, req.user!.name, at);
  });
  audit(t, req.user!, "opening_staff", `staff:${u.id}`, { name: u.name, amount: round2(b.amount) });
  return { ok: true, user_id: u.id, name: u.name, balance: staffBalance(u.id) };
}));

/** Fuel in a tank on day one: recorded as a dip so the stock register starts from it. */
opening.post("/opening/stock", requirePerm("opening.manage"), h((req) => {
  const b = parse(z.object({ tank_id: z.number().int(), litres: z.number().min(0) }), req.body);
  const t = tid(req);
  const tank = get("SELECT tk.* FROM tanks tk JOIN stations s ON s.id=tk.station_id WHERE tk.id=? AND s.tenant_id=?", b.tank_id, t);
  if (!tank) throw new AppError(404, "Tank not found");
  if (b.litres > tank.capacity_l * 1.001) throw new AppError(400, `More than the tank's capacity (${tank.capacity_l} L)`);
  tx(() => {
    // the go-live figure IS the book: no variance, so the first month's P&L shows no phantom stock gain/loss
    run("INSERT INTO dip_readings (tank_id,measured_l,book_l,variance_pct,created_at) VALUES (?,?,?,?,?)", tank.id, b.litres, b.litres, 0, now());
    run("UPDATE tanks SET current_l=? WHERE id=?", b.litres, tank.id);
  });
  audit(t, req.user!, "opening_stock", `tank:${tank.id}`, { tank: tank.name, litres: b.litres, was: tank.current_l });
  return { ok: true, tank: get("SELECT * FROM tanks WHERE id=?", tank.id) };
}));

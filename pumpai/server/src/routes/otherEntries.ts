/**
 * CEO-only "Other income / expense / discount".
 *
 * The CEO records money the pump earned or spent outside the normal flows (misc income, a one-off expense),
 * or a concession (discount) given to a party — attributed to a khata customer, wholesale client, thekedar,
 * or a free-text name, with a reason. The money/balance movement is written to the SAME tables the rest of
 * the app uses (cashier_vouchers / bank_txns for cash & bank; the party's own ledger for a discount) so the
 * books tally to the rupee; this `other_entries` row is the record the CEO report groups by party.
 *
 * Ledger accounts (see ledger.ts specialSide / isOtherDisc):
 *   income   → Dr Cash/Bank            / Cr "Other income (CEO)"     (adds to P&L other income)
 *   expense  → Dr "Expense: Other (CEO)" / Cr Cash/Bank              (flows into P&L expenses)
 *   discount → Dr "Other discount (CEO)" / Cr the party's receivable (reduces P&L profit; party owes less)
 */
import { Router } from "express";
import { z } from "zod";
import { all, get, run, tx, now, pkDate, pkStart, pkEnd } from "../db.js";
import { h, parse, tid, requirePerm } from "../auth.js";
import { AppError, round2, audit } from "../services.js";
import { otherMoney, accountIdField } from "./banks.js";

export const otherEntries = Router();

const isCash = (m?: string | null) => !m || /^cash$/i.test(m.trim());
const PARTY_RECEIVABLE: Record<string, string> = { khata: "customers", wholesale: "wholesale_clients", carriage: "thekedars" };

/** Resolve the party's display name (and validate it belongs to this tenant). "other" → free-text name. */
function partyName(t: number, type: string, id: number | null | undefined, freeName: string | null | undefined): string {
  if (type === "other") {
    const n = (freeName ?? "").trim();
    if (!n) throw new AppError(400, "Type the name");
    return n;
  }
  const table = PARTY_RECEIVABLE[type];
  if (!table) throw new AppError(400, "Choose a valid party");
  if (!id) throw new AppError(400, "Choose who it is");
  const p = get(`SELECT name FROM ${table} WHERE id=? AND tenant_id=?`, id, t);
  if (!p) throw new AppError(404, "Party not found");
  return p.name as string;
}

/* ---------------- party picker (khata / wholesale / thekedar) ---------------- */
otherEntries.get("/other-entries/parties", requirePerm("other_entries.manage"), h((req) => {
  const t = tid(req);
  const q = `%${String(req.query.q ?? "").trim()}%`;
  return {
    khata: all("SELECT id, name, phone FROM customers WHERE tenant_id=? AND (name LIKE ? OR COALESCE(phone,'') LIKE ?) ORDER BY name LIMIT 30", t, q, q),
    wholesale: all("SELECT id, name, COALESCE(business_name,'') business_name FROM wholesale_clients WHERE tenant_id=? AND active=1 AND (name LIKE ? OR COALESCE(business_name,'') LIKE ?) ORDER BY name LIMIT 30", t, q, q),
    carriage: all("SELECT id, name, COALESCE(phone,'') phone FROM thekedars WHERE tenant_id=? AND name LIKE ? ORDER BY name LIMIT 30", t, q),
  };
}));

/* ---------------- create ---------------- */
const body = z.object({
  kind: z.enum(["income", "expense", "discount"]),
  party_type: z.enum(["khata", "wholesale", "carriage", "other"]),
  party_id: z.number().int().positive().optional().nullable(),
  party_name: z.string().trim().max(80).optional().nullable(),
  reason: z.string().trim().min(2, "Reason kis cheez ka — likhein").max(120),
  amount: z.number().positive().max(1_000_000_000),
  method: z.string().trim().max(30).optional().nullable(), // income/expense only: "Cash" or a bank/wallet account's method
  account_id: accountIdField,                              // income/expense only: the bank/wallet account money went to / came from
  note: z.string().trim().max(200).optional().nullable(),
  /** cash note breakdown counted, only for a cash income/expense: { "5000": 2, "1000": 10, ... } */
  notes: z.record(z.enum(["5000", "1000", "500", "100", "50", "20", "10"]), z.number().int().min(0).max(100_000)).optional().nullable(),
  /** income/expense with a real party: ALSO post it on the party's own khata (expense → they owe us more, income → they
   *  paid us). The money then moves against the party's receivable, not the CEO's P&L — it is the party's udhaar/wasooli. */
  to_party: z.boolean().optional().nullable(),
});

/** The entry is a money move on a party's own account (not the CEO's P&L). */
export const isToParty = (b: { kind: string; party_type: string; to_party?: boolean | null }) => (b.kind === "income" || b.kind === "expense") && b.party_type !== "other" && !!b.to_party;

/** Do the money / balance side of an other-entry (income+expense cash-bank, or a party discount). Returns its `src` tag. */
function applyOtherMoney(t: number, by: string, b: z.infer<typeof body>, oeId: number, name: string, at: string): string {
  const amount = round2(b.amount);
  if (b.kind === "income" || b.kind === "expense") {
    const toParty = isToParty(b);
    const ref = `${toParty ? "oth-pty" : b.kind === "income" ? "oth-inc" : "oth-exp"}:${oeId}`;
    const notes = isCash(b.method) ? Object.fromEntries(Object.entries(b.notes ?? {}).filter(([, n]) => Number(n) > 0).map(([k, n]) => [k, Number(n)])) : null;
    const money = otherMoney(t, { dir: b.kind === "income" ? "in" : "out", amount, method: b.method ?? "cash", account_id: b.account_id, party: name, category: b.reason, note: b.note, ref, by, at, notes });
    if (!toParty) return money;
    // the party's own khata: expense = we gave them money (debit, they owe us more); income = they paid us (credit).
    // The cash/bank side is the voucher/bank row above (ledger.ts books it against the party's receivable), so these rows
    // carry no method/account of their own — only the balance moves.
    const pid = b.party_id!, note = `${b.kind === "expense" ? "Paid to party (CEO)" : "Received from party (CEO)"}: ${b.reason}`;
    let party: string;
    if (b.party_type === "khata") {
      const id = run("INSERT INTO khata_ledger (customer_id,type,amount,ref,note,created_at) VALUES (?,?,?,?,?,?)", pid, b.kind === "expense" ? "debit" : "credit", amount, ref, note, at).id;
      run("UPDATE customers SET balance = balance + ? WHERE id=? AND tenant_id=?", b.kind === "expense" ? amount : -amount, pid, t);
      party = `khata:${id}`;
    } else if (b.party_type === "wholesale") {
      const id = run(`INSERT INTO wholesale_txns (tenant_id,client_id,type,amount,method,ref,note,voided,created_by,txn_date,created_at)
        VALUES (?,?,?,?,?,?,?,0,?,?,?)`, t, pid, b.kind === "expense" ? "adjustment" : "payment", amount, "CEO entry", ref, note, by, at, at).id;
      party = `wtx:${id}`;
    } else {
      const id = run(`INSERT INTO carriage_txns (tenant_id,thekedar_id,type,amount,method,account_id,ref,note,voided,created_by,txn_date,created_at)
        VALUES (?,?,?,?,?,?,?,?,0,?,?,?)`, t, pid, b.kind === "expense" ? "adjustment" : "payment", amount, "CEO entry", null, ref, note, by, at, at).id;
      party = `ctx:${id}`;
    }
    return `${money}+${party}`;
  }
  // discount: reduce the party's receivable in its own ledger; the contra is "Other discount (CEO)" (routed in ledger.ts)
  const ref = `oth-disc:${oeId}`, pid = b.party_id!; // guaranteed by partyName() for a non-"other" party
  if (b.party_type === "khata") {
    const id = run("INSERT INTO khata_ledger (customer_id,type,amount,ref,note,created_at) VALUES (?,?,?,?,?,?)", pid, "credit", amount, ref, `Discount: ${b.reason}`, at).id;
    run("UPDATE customers SET balance = balance - ? WHERE id=? AND tenant_id=?", amount, pid, t);
    return `khata:${id}`;
  } else if (b.party_type === "wholesale") {
    const id = run(`INSERT INTO wholesale_txns (tenant_id,client_id,type,amount,method,ref,note,voided,created_by,txn_date,created_at)
      VALUES (?,?,?,?,?,?,?,0,?,?,?)`, t, pid, "payment", amount, "Discount", ref, `Discount: ${b.reason}`, by, at, at).id;
    return `wtx:${id}`;
  } else { // carriage / thekedar
    const id = run(`INSERT INTO carriage_txns (tenant_id,thekedar_id,type,amount,method,account_id,ref,note,voided,created_by,txn_date,created_at)
      VALUES (?,?,?,?,?,?,?,?,0,?,?,?)`, t, pid, "payment", amount, "Discount", null, ref, `Discount: ${b.reason}`, by, at, at).id;
    return `ctx:${id}`;
  }
}

/** Reverse the money / balance an other-entry created, by its `src` tag (leaves the record for the caller to void/update). */
function reverseOtherMoney(t: number, e: any) {
  // a to-party entry has two legs ("voucher:12+khata:34"): the money row and the party's own row — undo both
  for (const tag of String(e.src ?? "").split("+")) {
    const [kind, idStr] = tag.split(":");
    const srcId = Number(idStr);
    if (kind === "voucher") run("DELETE FROM cashier_vouchers WHERE id=? AND tenant_id=?", srcId, t);
    else if (kind === "bank") run("DELETE FROM bank_txns WHERE id=? AND tenant_id=?", srcId, t);
    else if (kind === "khata") {
      const k = get("SELECT type, amount FROM khata_ledger WHERE id=?", srcId);
      if (k) { run("DELETE FROM khata_ledger WHERE id=?", srcId); run("UPDATE customers SET balance = balance + ? WHERE id=? AND tenant_id=?", k.type === "credit" ? k.amount : -k.amount, e.party_id, t); }
    }
    else if (kind === "wtx") run("DELETE FROM wholesale_txns WHERE id=? AND tenant_id=?", srcId, t);
    else if (kind === "ctx") run("DELETE FROM carriage_txns WHERE id=? AND tenant_id=?", srcId, t);
  }
}

/** Write the `other_entries` record's own columns (not the money side). */
function writeOtherRow(t: number, b: z.infer<typeof body>, name: string, by: string, at: string, id: number | null): number {
  const method = b.kind === "discount" ? null : (b.method ?? "cash");
  const account_id = b.kind === "discount" ? null : (isCash(b.method) ? null : b.account_id ?? null);
  const pid = b.party_type === "other" ? null : b.party_id ?? null;
  const toParty = isToParty(b) ? 1 : 0;
  if (id) {
    run(`UPDATE other_entries SET kind=?, party_type=?, party_id=?, party_name=?, reason=?, amount=?, method=?, account_id=?, note=?, to_party=? WHERE id=?`,
      b.kind, b.party_type, pid, name, b.reason, round2(b.amount), method, account_id, b.note ?? null, toParty, id);
    return id;
  }
  return run(`INSERT INTO other_entries (tenant_id,kind,party_type,party_id,party_name,reason,amount,method,account_id,src,note,to_party,created_by,created_at)
    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)`, t, b.kind, b.party_type, pid, name, b.reason, round2(b.amount), method, account_id, null, b.note ?? null, toParty, by, at).id;
}

otherEntries.post("/other-entries", requirePerm("other_entries.manage"), h((req) => {
  const t = tid(req), by = req.user!.name;
  const b = parse(body, req.body);
  // a discount must reduce a real party's outstanding — it cannot apply to a free-text "other"
  if (b.kind === "discount" && b.party_type === "other") throw new AppError(400, "Discount ke liye koi khata / wholesale / thekedar party chunein (free-text par discount nahi)");
  const name = partyName(t, b.party_type, b.party_id, b.party_name);
  const at = now();
  const entry = tx(() => {
    const oe = writeOtherRow(t, b, name, by, at, null);
    const src = applyOtherMoney(t, by, b, oe, name, at);
    run("UPDATE other_entries SET src=? WHERE id=?", src, oe);
    return get("SELECT * FROM other_entries WHERE id=?", oe)!;
  });
  audit(t, req.user!, "other_entry_add", `other:${entry.id}`, { kind: b.kind, party: name, amount: round2(b.amount) });
  return entry;
}));

/* ---------------- undo (hard-reverse the money/balance, keep the record voided) ---------------- */
otherEntries.delete("/other-entries/:id", requirePerm("other_entries.manage"), h((req) => {
  const t = tid(req);
  const e = get("SELECT * FROM other_entries WHERE id=? AND tenant_id=? AND voided=0", Number(req.params.id), t);
  if (!e) throw new AppError(404, "Entry not found");
  tx(() => {
    reverseOtherMoney(t, e);
    run("UPDATE other_entries SET voided=1 WHERE id=?", e.id);
  });
  audit(t, req.user!, "other_entry_undo", `other:${e.id}`, { kind: e.kind, party: e.party_name, amount: e.amount });
  return { ok: true };
}));

/* ---------------- edit (reverse the old money/balance, then re-apply with the new values) ---------------- */
otherEntries.post("/other-entries/:id/edit", requirePerm("other_entries.manage"), h((req) => {
  const t = tid(req), by = req.user!.name;
  const e = get("SELECT * FROM other_entries WHERE id=? AND tenant_id=? AND voided=0", Number(req.params.id), t);
  if (!e) throw new AppError(404, "Entry nahi mili");
  const b = parse(body.extend({ why: z.string().trim().min(3, "Wajah likhein (kam se kam 3 harf)") }), req.body);
  if (b.kind === "discount" && b.party_type === "other") throw new AppError(400, "Discount ke liye koi khata / wholesale / thekedar party chunein");
  const name = partyName(t, b.party_type, b.party_id, b.party_name);
  const before = { kind: e.kind, party: e.party_name, amount: e.amount, reason: e.reason };
  const entry = tx(() => {
    reverseOtherMoney(t, e);                       // undo the old money / balance
    writeOtherRow(t, b, name, by, e.created_at, e.id); // update the record in place (keep its date)
    const src = applyOtherMoney(t, by, b, e.id, name, e.created_at); // re-apply with the new values
    run("UPDATE other_entries SET src=? WHERE id=?", src, e.id);
    return get("SELECT * FROM other_entries WHERE id=?", e.id)!;
  });
  audit(t, req.user!, "other_entry_edit", `other:${e.id}`, { before, after: { kind: b.kind, party: name, amount: round2(b.amount) }, reason: b.why });
  return entry;
}));

/* ---------------- report (per kind, grouped by party) ---------------- */
otherEntries.get("/other-entries", requirePerm("other_entries.manage"), h((req) => {
  const t = tid(req);
  const q = parse(z.object({
    kind: z.enum(["income", "expense", "discount"]).default("income"),
    from: z.string().regex(/^\d{4}-\d{2}-\d{2}$/).optional(), to: z.string().regex(/^\d{4}-\d{2}-\d{2}$/).optional(),
  }), req.query);
  const to = q.to ?? pkDate(), from = q.from ?? `${to.slice(0, 7)}-01`;
  const rows = all("SELECT * FROM other_entries WHERE tenant_id=? AND kind=? AND voided=0 AND created_at >= ? AND created_at < ? ORDER BY created_at DESC",
    t, q.kind, pkStart(from), pkEnd(to));
  const byParty = new Map<string, { party_name: string; party_type: string; total: number; count: number }>();
  for (const r of rows) {
    const k = `${r.party_type}:${r.party_name}`;
    const g = byParty.get(k) ?? { party_name: r.party_name, party_type: r.party_type, total: 0, count: 0 };
    g.total = round2(g.total + r.amount); g.count++; byParty.set(k, g);
  }
  return {
    kind: q.kind, from, to, total: round2(rows.reduce((a, r) => a + r.amount, 0)), count: rows.length,
    by_party: [...byParty.values()].sort((a, b) => b.total - a.total),
    list: rows,
  };
}));

/* ---------------- totals for the P&L (income adds, discount reduces; expense flows via the ledger) ---------------- */
// to-party rows are the party's own udhaar/wasooli (booked against their receivable), never the CEO's P&L
const sumKind = (t: number, kind: string, fromIso: string, toIso: string) =>
  round2(get("SELECT COALESCE(SUM(amount),0) v FROM other_entries WHERE tenant_id=? AND kind=? AND voided=0 AND COALESCE(to_party,0)=0 AND created_at >= ? AND created_at < ?", t, kind, fromIso, toIso)!.v as number);
export const otherEntriesIncome = (t: number, fromIso: string, toIso: string) => sumKind(t, "income", fromIso, toIso);
export const otherEntriesDiscount = (t: number, fromIso: string, toIso: string) => sumKind(t, "discount", fromIso, toIso);

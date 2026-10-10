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
});

otherEntries.post("/other-entries", requirePerm("other_entries.manage"), h((req) => {
  const t = tid(req), by = req.user!.name;
  const b = parse(body, req.body);
  const amount = round2(b.amount);
  // a discount must reduce a real party's outstanding — it cannot apply to a free-text "other"
  if (b.kind === "discount" && b.party_type === "other") throw new AppError(400, "Discount ke liye koi khata / wholesale / thekedar party chunein (free-text par discount nahi)");
  const name = partyName(t, b.party_type, b.party_id, b.party_name);
  const at = now();

  const entry = tx(() => {
    const oe = run(`INSERT INTO other_entries (tenant_id,kind,party_type,party_id,party_name,reason,amount,method,account_id,src,note,created_by,created_at)
      VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)`, t, b.kind, b.party_type, b.party_type === "other" ? null : b.party_id ?? null, name, b.reason, amount,
      b.kind === "discount" ? null : (b.method ?? "cash"), b.kind === "discount" ? null : (isCash(b.method) ? null : b.account_id ?? null), null, b.note ?? null, by, at).id;

    let src: string;
    if (b.kind === "income" || b.kind === "expense") {
      const ref = `${b.kind === "income" ? "oth-inc" : "oth-exp"}:${oe}`;
      const notes = isCash(b.method) ? Object.fromEntries(Object.entries(b.notes ?? {}).filter(([, n]) => Number(n) > 0).map(([k, n]) => [k, Number(n)])) : null;
      src = otherMoney(t, { dir: b.kind === "income" ? "in" : "out", amount, method: b.method ?? "cash", account_id: b.account_id, party: name, category: b.reason, note: b.note, ref, by, at, notes });
    } else {
      // discount: reduce the party's receivable in its own ledger; the contra is "Other discount (CEO)" (routed in ledger.ts)
      const ref = `oth-disc:${oe}`, pid = b.party_id!; // guaranteed by partyName() for a non-"other" party
      if (b.party_type === "khata") {
        const id = run("INSERT INTO khata_ledger (customer_id,type,amount,ref,note,created_at) VALUES (?,?,?,?,?,?)", pid, "credit", amount, ref, `Discount: ${b.reason}`, at).id;
        run("UPDATE customers SET balance = balance - ? WHERE id=? AND tenant_id=?", amount, pid, t);
        src = `khata:${id}`;
      } else if (b.party_type === "wholesale") {
        const id = run(`INSERT INTO wholesale_txns (tenant_id,client_id,type,amount,method,ref,note,voided,created_by,txn_date,created_at)
          VALUES (?,?,?,?,?,?,?,0,?,?,?)`, t, pid, "payment", amount, "Discount", ref, `Discount: ${b.reason}`, by, pkDate(), at).id;
        src = `wtx:${id}`;
      } else { // carriage / thekedar
        const id = run(`INSERT INTO carriage_txns (tenant_id,thekedar_id,type,amount,method,account_id,ref,note,voided,created_by,txn_date,created_at)
          VALUES (?,?,?,?,?,?,?,?,0,?,?,?)`, t, pid, "payment", amount, "Discount", null, ref, `Discount: ${b.reason}`, by, pkDate(), at).id;
        src = `ctx:${id}`;
      }
    }
    run("UPDATE other_entries SET src=? WHERE id=?", src, oe);
    return get("SELECT * FROM other_entries WHERE id=?", oe)!;
  });
  audit(t, req.user!, "other_entry_add", `other:${entry.id}`, { kind: b.kind, party: name, amount });
  return entry;
}));

/* ---------------- undo (hard-reverse the money/balance, keep the record voided) ---------------- */
otherEntries.delete("/other-entries/:id", requirePerm("other_entries.manage"), h((req) => {
  const t = tid(req);
  const e = get("SELECT * FROM other_entries WHERE id=? AND tenant_id=? AND voided=0", Number(req.params.id), t);
  if (!e) throw new AppError(404, "Entry not found");
  const [kind, idStr] = String(e.src ?? "").split(":");
  const srcId = Number(idStr);
  tx(() => {
    if (kind === "voucher") run("DELETE FROM cashier_vouchers WHERE id=? AND tenant_id=?", srcId, t);
    else if (kind === "bank") run("DELETE FROM bank_txns WHERE id=? AND tenant_id=?", srcId, t);
    else if (kind === "khata") { run("DELETE FROM khata_ledger WHERE id=?", srcId); run("UPDATE customers SET balance = balance + ? WHERE id=? AND tenant_id=?", e.amount, e.party_id, t); }
    else if (kind === "wtx") run("DELETE FROM wholesale_txns WHERE id=? AND tenant_id=?", srcId, t);
    else if (kind === "ctx") run("DELETE FROM carriage_txns WHERE id=? AND tenant_id=?", srcId, t);
    run("UPDATE other_entries SET voided=1 WHERE id=?", e.id);
  });
  audit(t, req.user!, "other_entry_undo", `other:${e.id}`, { kind: e.kind, party: e.party_name, amount: e.amount });
  return { ok: true };
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
const sumKind = (t: number, kind: string, fromIso: string, toIso: string) =>
  round2(get("SELECT COALESCE(SUM(amount),0) v FROM other_entries WHERE tenant_id=? AND kind=? AND voided=0 AND created_at >= ? AND created_at < ?", t, kind, fromIso, toIso)!.v as number);
export const otherEntriesIncome = (t: number, fromIso: string, toIso: string) => sumKind(t, "income", fromIso, toIso);
export const otherEntriesDiscount = (t: number, fromIso: string, toIso: string) => sumKind(t, "discount", fromIso, toIso);

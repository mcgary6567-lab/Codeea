/**
 * The owner's bank accounts (HBL, Meezan, Easypaisa …) and how much is in each.
 *
 * A balance is never typed in: it is worked out from the opening balance plus every entry that names
 * the account — cash deposited, khata / wholesale / wallet payments received, supplier payments and
 * expenses paid from it, card / Raast / Easypaisa / JazzCash sales at the POS (each method is linked
 * to one account) and the bank-only entries kept here (cash taken out, transfers, charges, profit).
 * Voiding or deleting the source entry takes it out of the bank too.
 */
import { Router } from "express";
import { z } from "zod";
import { all, get, run, tx, now, pkDate, pkStart, pkEnd, getSetting, setSetting } from "../db.js";
import { h, parse, tid, requirePerm, requireAny } from "../auth.js";
import { AppError, round2 } from "../services.js";
import { linkPhotos, proofPhotos, proofCol } from "./capture.js";

export const banks = Router();

type Row = Record<string, any>;
/** POS payment methods whose money lands in a bank account rather than the cash bag. */
export const POS_DIGITAL = ["card", "raast", "easypaisa", "jazzcash"] as const;

export const accountName = (a: Row) =>
  `${a.bank}${a.branch ? ` ${a.branch}` : ""}${a.account_no ? ` ··${String(a.account_no).replace(/[^0-9A-Za-z]/g, "").slice(-4)}` : ""}`;

const isCash = (m?: string | null) => !m || /^cash$/i.test(m.trim());
/**
 * A wholesale client paid our supplier (depot) straight: the client's due and what we owe the depot both go down,
 * no money touches our cash or banks. Both entries carry this method.
 */
export const DEPOT_PAY = "Paid to depot";
export const notDepot = (col: string) => `LOWER(COALESCE(${col},''))<>'paid to depot'`;
// a wholesale client settled a bypass supplier direct (no money through us): lowers the client's due and our bypass payable
export const BYPASS_PAY = "Paid bypass supplier";

/**
 * The account a payment names, checked to belong to this pump. Cash never goes to a bank account;
 * a non-cash payment without an account is still allowed (it shows up under "not linked to a bank").
 */
export function bankAccountFor(t: number, accountId: number | null | undefined, method?: string | null): number | null {
  if (!accountId || isCash(method)) return null;
  const a = get("SELECT id, active FROM bank_accounts WHERE id=? AND tenant_id=?", accountId, t);
  if (!a) throw new AppError(400, "Bank account not found");
  if (!a.active) throw new AppError(400, "That bank account is closed");
  return a.id;
}
export const accountIdField = z.number().int().positive().optional().nullable();

export const posMap = (t: number): Record<string, number> => {
  try { return JSON.parse(getSetting(t, "bank_pos_map", "{}")) ?? {}; } catch { return {}; }
};

/** The last moment of a Pakistan day (a backdated entry belongs to that day, not to the next midnight). */
export const dayEnd = (d: string) => new Date(Date.parse(pkEnd(d)) - 1).toISOString();

/**
 * Money in / out that belongs to no party account (claims recovered, tax deposited, coupon refunds, other income):
 * cash goes through a cash-counter voucher (the cash book reads it), anything else is a bank entry on the chosen account.
 * `ref` (e.g. "wht:12") lets the accounting journal put it against the right account.
 */
export function otherMoney(t: number, m: { dir: "in" | "out"; amount: number; method?: string | null; account_id?: number | null; party: string; category: string; note?: string | null; ref: string; by: string; at?: string }) {
  const at = m.at ?? now();
  if (isCash(m.method)) {
    const id = run(`INSERT INTO cashier_vouchers (tenant_id,direction,party_type,party_id,party_name,amount,method,account_id,category,ref,note,src,created_by,created_at)
      VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)`, t, m.dir, "other", null, m.party, m.amount, "Cash", null, m.category, m.ref, m.note ?? null, m.ref, m.by, at).id;
    return `voucher:${id}`;
  }
  const acc = bankAccountFor(t, m.account_id, m.method ?? "bank");
  if (!acc) throw new AppError(400, "Choose the bank account");
  const id = run("INSERT INTO bank_txns (tenant_id,account_id,kind,amount,party,ref,note,txn_date,created_by,created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
    t, acc, m.dir === "in" ? "other_in" : "other_out", m.dir === "in" ? m.amount : -m.amount, m.party, m.ref, m.note || m.category, at, m.by, now()).id;
  return `bank:${id}`;
}

/**
 * A cheque that has not reached the bank yet goes into the cheque register instead of the party's account
 * (received: counted when it clears; issued: counted when the bank pays it). Wholesale cheques have their own register.
 */
export function chequeToRegister(t: number, c: { direction: "in" | "out"; party_type: string; party_id: number; party_name: string; amount: number;
  bank?: string | null; cheque_no?: string | null; cheque_date?: string | null; account_id?: number | null; note?: string | null; by: string }) {
  const no = c.cheque_no?.trim() || "—";
  if (no !== "—" && c.bank && get("SELECT id FROM cheques WHERE tenant_id=? AND direction=? AND bank=? AND cheque_no=? AND status NOT IN ('returned','cancelled')", t, c.direction, c.bank, no))
    throw new AppError(400, `Cheque ${no} of ${c.bank} is already in the register`);
  return run(`INSERT INTO cheques (tenant_id,direction,party_type,party_id,party_name,amount,bank,cheque_no,cheque_date,status,account_id,note,created_by,created_at,updated_at)
    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)`, t, c.direction, c.party_type, c.party_id, c.party_name, c.amount, c.bank || "Bank not given", no, c.cheque_date || pkDate(),
    c.direction === "in" ? "in_hand" : "issued", c.account_id ?? null, c.note ?? null, c.by, now(), now()).id;
}

/** Every movement of money in the owner's accounts (one account, or all of them). */
export function bankMoves(t: number, accountId?: number | null): Row[] {
  const acc = accountId ? " AND x.account_id=?" : " AND x.account_id IS NOT NULL";
  const A = accountId ? [accountId] : [];
  const rows: Row[] = [
    ...all(`SELECT x.account_id, x.created_at at, x.amount, 'deposit' kind, 'Cash deposited' || COALESCE(' · slip ' || x.slip_ref, '') text, x.deposited_by who, 'deposit:' || x.id ref
      FROM bank_deposits x WHERE x.tenant_id=?${acc}`, t, ...A),
    ...all(`SELECT x.account_id, x.txn_date at, x.amount, 'receipt' kind, 'Wholesale payment — ' || c.name || ' (' || COALESCE(x.method,'') || COALESCE(' ' || x.ref, '') || ')' text, x.created_by who, 'wtx:' || x.id ref
      FROM wholesale_txns x JOIN wholesale_clients c ON c.id=x.client_id WHERE x.tenant_id=? AND x.type='payment' AND x.voided=0${acc}`, t, ...A),
    ...all(`SELECT x.account_id, x.created_at at, x.amount, 'receipt' kind, 'Khata payment — ' || c.name || ' (' || COALESCE(x.ref,'') || ')' text, NULL who, 'khata:' || x.id ref
      FROM khata_ledger x JOIN customers c ON c.id=x.customer_id WHERE c.tenant_id=? AND x.type='credit'${acc}`, t, ...A),
    ...all(`SELECT x.account_id, x.txn_date at, -x.amount amount, 'payment' kind, 'Paid supplier — ' || s.name || ' (' || COALESCE(x.method,'') || COALESCE(' ' || x.ref, '') || ')' text, x.created_by who, 'stx:' || x.id ref
      FROM supplier_txns x JOIN suppliers s ON s.id=x.supplier_id WHERE x.tenant_id=? AND x.type='payment' AND COALESCE(x.method,'')<>'WHT'${acc}`, t, ...A),
    ...all(`SELECT x.account_id, x.txn_date at, -x.amount amount, 'payment' kind, 'Bypass supplier paid — ' || s.name || COALESCE(' ' || x.ref, '') text, x.created_by who, 'byppay:' || x.id ref
      FROM bypass_supplier_payments x JOIN suppliers s ON s.id=x.supplier_id WHERE x.tenant_id=? AND x.mode IN ('we_pay','through_us') AND x.voided=0${acc}`, t, ...A),
    ...all(`SELECT x.account_id, x.created_at at, -x.amount amount, 'expense' kind, 'Expense — ' || x.category || COALESCE(' · ' || x.paid_to, '') text, x.created_by who, 'expense:' || x.id ref
      FROM expenses x WHERE x.tenant_id=? AND x.status='approved'${acc}`, t, ...A),
    ...all(`SELECT x.account_id, x.created_at at, CASE WHEN x.type='refund' THEN -x.amount ELSE x.amount END amount, 'receipt' kind,
        'Wallet ' || x.type || ' — ' || c.name text, x.created_by who, 'wallet:' || x.id ref
      FROM wallet_ledger x JOIN customers c ON c.id=x.customer_id WHERE x.tenant_id=? AND x.type IN ('deposit','refund')${acc}`, t, ...A),
    ...all(`SELECT x.account_id, x.txn_date at, x.amount, x.kind, COALESCE(x.note, x.kind) || COALESCE(' — ' || x.party, '') text, x.created_by who, 'bank:' || x.id ref, x.id txn_id
      FROM bank_txns x WHERE x.tenant_id=?${acc}`, t, ...A),
    // staff advances / bonus paid, or advances paid back, through a bank or wallet
    ...all(`SELECT x.account_id, x.created_at at, CASE WHEN x.type='repayment' THEN x.amount ELSE -x.amount END amount, 'staff' kind,
        CASE x.type WHEN 'repayment' THEN 'Advance paid back — ' ELSE 'Staff ' || x.type || ' — ' END || u.name text, x.created_by who, 'staff:' || x.id ref
      FROM staff_ledger x JOIN users u ON u.id=x.user_id WHERE x.tenant_id=? AND x.type IN ('advance','bonus','repayment')${acc}`, t, ...A),
    // fuel coupons sold by bank / wallet: one line per book
    ...all(`SELECT x.account_id, MIN(x.sold_at) at, SUM(x.value) amount, 'receipt' kind, 'Fuel coupons sold — ' || COUNT(*) || ' (' || x.batch || ')' || COALESCE(' to ' || x.buyer, '') text,
        MIN(x.sold_by) who, 'coupons:' || x.batch ref FROM fuel_coupons x WHERE x.tenant_id=?${acc} GROUP BY x.batch, x.account_id`, t, ...A),
  ];
  // card / Raast / Easypaisa / JazzCash sales: one line per day per account per method, from the day the account was opened.
  // A fuel sale can name its own bank's POS machine (sales.account_id); untagged sales fall to the method's pos-map default.
  const map = posMap(t);
  const posLabel = (m: string) => (m === "raast" ? "Raast" : m === "card" ? "card" : m === "easypaisa" ? "Easypaisa" : "JazzCash");
  for (const m of POS_DIGITAL) {
    const mapped = map[m] ?? null;
    // every account that receives this method's money: the mapped default + any explicitly chosen on a sale
    const explicit = all("SELECT DISTINCT s.account_id id FROM sales s JOIN stations st ON st.id=s.station_id WHERE st.tenant_id=? AND s.payment_method=? AND s.account_id IS NOT NULL", t, m).map((r) => r.id as number);
    const accts = [...new Set([...(mapped ? [mapped] : []), ...explicit])];
    for (const id of accts) {
      if (accountId && id !== accountId) continue;
      const a = get("SELECT opening_date FROM bank_accounts WHERE id=? AND tenant_id=?", id, t);
      if (!a) continue;
      const since = pkStart(a.opening_date);
      const isDefault = id === mapped;
      // fuel sales tagged to this account, plus (only for the default account) the untagged ones
      const saleCond = isDefault ? "(s.account_id=? OR s.account_id IS NULL)" : "s.account_id=?";
      const rowsFor = all(`SELECT date(s.created_at, '+5 hours') day, SUM(s.amount) v, COUNT(*) n FROM sales s JOIN stations st ON st.id=s.station_id
        WHERE st.tenant_id=? AND s.payment_method=? AND ${saleCond} AND s.created_at >= ? GROUP BY day`, t, m, id, since);
      // shop sales have no per-machine bank, so they only ever settle into the default account
      const shopRows = isDefault ? all("SELECT date(created_at, '+5 hours') day, SUM(total) v, COUNT(*) n FROM shop_sales WHERE tenant_id=? AND payment_method=? AND created_at >= ? GROUP BY day", t, m, since) : [];
      const byDay: Record<string, { v: number; n: number }> = {};
      for (const r of [...rowsFor, ...shopRows]) { (byDay[r.day] ??= { v: 0, n: 0 }).v += r.v; byDay[r.day].n += r.n; }
      for (const [day, agg] of Object.entries(byDay))
        rows.push({ account_id: id, at: dayEnd(day), amount: round2(agg.v), kind: "pos", text: `POS ${posLabel(m)} sales ${day} (${agg.n})`, who: null, ref: `pos:${m}:${id}:${day}` });
    }
  }
  return rows.sort((a, b) => String(a.at).localeCompare(String(b.at)));
}

/** Each account with its balance now and this month's money in / out. */
export function bankAccounts(t: number) {
  const accounts = all("SELECT * FROM bank_accounts WHERE tenant_id=? ORDER BY active DESC, bank, id", t);
  const moves = bankMoves(t);
  const month = pkStart(pkDate().slice(0, 7) + "-01"), today = pkStart(pkDate());
  const out = accounts.map((a) => {
    const mine = moves.filter((m) => m.account_id === a.id);
    const sum = (f: (m: Row) => boolean) => round2(mine.filter(f).reduce((s, m) => s + m.amount, 0));
    return {
      ...a, name: accountName(a),
      balance: round2(a.opening_balance + sum(() => true)),
      month_in: sum((m) => m.at >= month && m.amount > 0), month_out: round2(-sum((m) => m.at >= month && m.amount < 0)),
      today_in: sum((m) => m.at >= today && m.amount > 0), today_out: round2(-sum((m) => m.at >= today && m.amount < 0)),
      pos_today: sum((m) => m.kind === "pos" && m.at >= today), pos_month: sum((m) => m.kind === "pos" && m.at >= month),
      last_at: mine.length ? mine[mine.length - 1].at : null,
    };
  });
  return { accounts: out, total: round2(out.filter((a) => a.active).reduce((s, a) => s + a.balance, 0)) };
}

/** Non-cash money in the last 30 days that does not say which bank it went to — so the owner can fix the tally. */
export function unlinkedMoney(t: number) {
  const since = new Date(Date.now() - 30 * 86400_000).toISOString();
  const one = (sql: string, ...a: unknown[]) => round2(get(sql, ...(a as []))!.v ?? 0);
  const nonCash = (col: string) => `${col} IS NOT NULL AND LOWER(TRIM(${col})) NOT IN ('cash','wht','','paid to depot')`;
  const map = posMap(t);
  const unmapped = POS_DIGITAL.filter((m) => !map[m]);
  // an unmapped-method sale that still names its own bank's POS machine IS linked, so exclude those
  const pos = unmapped.length ? one(`SELECT COALESCE(SUM(s.amount),0) v FROM sales s JOIN stations st ON st.id=s.station_id
    WHERE st.tenant_id=? AND s.payment_method IN (${unmapped.map(() => "?").join(",")}) AND s.account_id IS NULL AND s.created_at >= ?`, t, ...unmapped, since) : 0;
  const items = {
    received: round2(
      one(`SELECT COALESCE(SUM(amount),0) v FROM wholesale_txns WHERE tenant_id=? AND type='payment' AND voided=0 AND account_id IS NULL AND ${nonCash("method")} AND created_at >= ?`, t, since)
      + one(`SELECT COALESCE(SUM(k.amount),0) v FROM khata_ledger k JOIN customers c ON c.id=k.customer_id WHERE c.tenant_id=? AND k.type='credit' AND k.account_id IS NULL AND ${nonCash("k.ref")} AND k.created_at >= ?`, t, since)
      + one(`SELECT COALESCE(SUM(amount),0) v FROM wallet_ledger WHERE tenant_id=? AND type='deposit' AND account_id IS NULL AND ${nonCash("method")} AND created_at >= ?`, t, since)
      + one(`SELECT COALESCE(SUM(value),0) v FROM fuel_coupons WHERE tenant_id=? AND account_id IS NULL AND ${nonCash("method")} AND sold_at >= ?`, t, since)),
    paid: round2(
      one(`SELECT COALESCE(SUM(amount),0) v FROM supplier_txns WHERE tenant_id=? AND type='payment' AND account_id IS NULL AND ${nonCash("method")} AND created_at >= ?`, t, since)
      + one(`SELECT COALESCE(SUM(amount),0) v FROM expenses WHERE tenant_id=? AND status='approved' AND account_id IS NULL AND ${nonCash("method")} AND created_at >= ?`, t, since)
      + one(`SELECT COALESCE(SUM(amount),0) v FROM staff_ledger WHERE tenant_id=? AND type IN ('advance','bonus') AND account_id IS NULL AND method IS NOT NULL AND ${nonCash("method")} AND created_at >= ?`, t, since)),
    pos, pos_methods: unmapped,
  };
  return items;
}

/* ================= routes ================= */
const ownAccount = (t: number, id: number) => {
  const a = get("SELECT * FROM bank_accounts WHERE id=? AND tenant_id=?", id, t);
  if (!a) throw new AppError(404, "Bank account not found");
  return a;
};
const day = z.string().regex(/^\d{4}-\d{2}-\d{2}$/);
const accountBody = z.object({
  bank: z.string().trim().min(2).max(80), branch: z.string().trim().max(80).optional().nullable(), title: z.string().trim().max(80).optional().nullable(),
  account_no: z.string().trim().max(40).optional().nullable(), kind: z.enum(["current", "savings", "wallet"]).default("current"),
  opening_balance: z.number().min(-1_000_000_000).max(10_000_000_000).default(0), opening_date: day.optional(), note: z.string().max(200).optional().nullable(),
});

/** Names only, for the "which bank?" picker on payment forms (anyone who records money can pick). */
banks.get("/bank/accounts/pick", requireAny("expenses.create", "khata.manage", "wholesale.manage", "suppliers.manage", "bank.view"), h((req) => ({
  accounts: all("SELECT * FROM bank_accounts WHERE tenant_id=? AND active=1 ORDER BY bank, id", tid(req)).map((a) => ({ id: a.id, name: accountName(a), bank: a.bank, kind: a.kind })),
})));

banks.get("/bank/accounts", requirePerm("bank.view"), h(async (req) => {
  const t = tid(req);
  // loaded here: the cash book itself uses this module
  const { cashPosition } = await import("./backoffice.js");
  return { ...bankAccounts(t), cash_in_hand: cashPosition(t).cash_in_hand, pos_map: posMap(t), unlinked: unlinkedMoney(t) };
}));

banks.post("/bank/accounts", requirePerm("bank.manage"), h((req) => {
  const b = parse(accountBody, req.body);
  const { id } = run(`INSERT INTO bank_accounts (tenant_id,bank,branch,title,account_no,kind,opening_balance,opening_date,note,created_by,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)`,
    tid(req), b.bank, b.branch ?? null, b.title ?? null, b.account_no ?? null, b.kind, b.opening_balance, b.opening_date ?? pkDate(), b.note ?? null, req.user!.name, now());
  return bankAccounts(tid(req)).accounts.find((a) => a.id === id);
}));

banks.patch("/bank/accounts/:id", requirePerm("bank.manage"), h((req) => {
  const a = ownAccount(tid(req), Number(req.params.id));
  const b = parse(accountBody.partial().extend({ active: z.boolean().optional() }), req.body);
  const m = { ...a, ...b, active: b.active === undefined ? a.active : b.active ? 1 : 0 };
  run("UPDATE bank_accounts SET bank=?, branch=?, title=?, account_no=?, kind=?, opening_balance=?, opening_date=?, note=?, active=? WHERE id=?",
    m.bank, m.branch ?? null, m.title ?? null, m.account_no ?? null, m.kind, m.opening_balance, m.opening_date, m.note ?? null, m.active, a.id);
  return bankAccounts(tid(req)).accounts.find((x) => x.id === a.id);
}));

/** Card / Raast / Easypaisa / JazzCash sales → which account they settle into. */
banks.put("/bank/pos-map", requirePerm("bank.manage"), h((req) => {
  const t = tid(req);
  const b = parse(z.record(z.enum(POS_DIGITAL), z.number().int().positive().nullable()), req.body);
  const map = posMap(t);
  for (const [k, v] of Object.entries(b)) { if (v) { ownAccount(t, v); map[k] = v; } else delete map[k]; }
  setSetting(t, "bank_pos_map", JSON.stringify(map));
  return { pos_map: map };
}));

/** Statement of one account: opening, every line with running balance, closing. */
banks.get("/bank/accounts/:id/statement", requirePerm("bank.view"), h((req) => {
  const t = tid(req);
  const a = ownAccount(t, Number(req.params.id));
  const to = String(req.query.to ?? pkDate());
  const from = String(req.query.from ?? `${to.slice(0, 7)}-01`);
  const moves = bankMoves(t, a.id);
  const start = pkStart(from), end = pkEnd(to);
  let balance = round2(a.opening_balance + moves.filter((m) => m.at < start).reduce((s, m) => s + m.amount, 0));
  const opening = balance;
  const lines: Row[] = moves.filter((m) => m.at >= start && m.at < end).map((m) => ({ ...m, balance: (balance = round2(balance + m.amount)) }));
  const ids = lines.filter((l) => l.txn_id).map((l) => l.txn_id);
  const proofs = new Map(ids.length ? all(`SELECT x.id, ${proofCol("'bank:'||x.id")} FROM bank_txns x WHERE x.id IN (${ids.map(() => "?").join(",")})`, ...ids).map((r) => [r.id, r.proof_ids]) : []);
  for (const l of lines as Row[]) if (l.txn_id) l.proof_ids = proofs.get(l.txn_id) ?? null;
  return {
    account: { ...a, name: accountName(a) }, from, to, opening, closing: balance,
    money_in: round2(lines.filter((l) => l.amount > 0).reduce((s, l) => s + l.amount, 0)),
    money_out: round2(-lines.filter((l) => l.amount < 0).reduce((s, l) => s + l.amount, 0)),
    lines: lines.reverse(),
  };
}));

const ENTRY_KINDS = { withdraw: -1, charges: -1, owner_out: -1, other_out: -1, profit: 1, owner_in: 1, other_in: 1 } as const;
const KIND_LABEL: Record<string, string> = {
  withdraw: "Cash taken out of bank", charges: "Bank charges", owner_out: "Owner took money", other_out: "Other payment",
  profit: "Bank profit", owner_in: "Owner put money in", other_in: "Other money in", transfer: "Transfer",
};

/** A bank-only entry. Cash taken out of the bank goes into the office cash book. */
banks.post("/bank/entries", requirePerm("bank.manage"), h((req) => {
  const t = tid(req);
  const b = parse(z.object({ account_id: z.number().int(), kind: z.enum(Object.keys(ENTRY_KINDS) as [keyof typeof ENTRY_KINDS]), amount: z.number().positive().max(10_000_000_000),
    party: z.string().max(80).optional().nullable(), ref: z.string().max(60).optional().nullable(), note: z.string().max(200).optional().nullable(),
    txn_date: day.optional(), photo_ids: proofPhotos }), req.body);
  const a = ownAccount(t, b.account_id);
  if (!a.active) throw new AppError(400, "That bank account is closed");
  const at = b.txn_date && b.txn_date !== pkDate() ? dayEnd(b.txn_date) : now();
  const { id } = run("INSERT INTO bank_txns (tenant_id,account_id,kind,amount,party,ref,note,txn_date,created_by,created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
    t, a.id, b.kind, ENTRY_KINDS[b.kind] * b.amount, b.party ?? null, b.ref ?? null, b.note || KIND_LABEL[b.kind], at, req.user!.name, now());
  linkPhotos(t, b.photo_ids, `bank:${id}`);
  return { entry: get("SELECT * FROM bank_txns WHERE id=?", id), ...bankAccounts(t) };
}));

/** Move money between two of the owner's accounts. */
banks.post("/bank/transfers", requirePerm("bank.manage"), h((req) => {
  const t = tid(req);
  const b = parse(z.object({ from_id: z.number().int(), to_id: z.number().int(), amount: z.number().positive().max(10_000_000_000),
    ref: z.string().max(60).optional().nullable(), note: z.string().max(200).optional().nullable(), txn_date: day.optional(), photo_ids: proofPhotos }), req.body);
  if (b.from_id === b.to_id) throw new AppError(400, "Choose two different accounts");
  const from = ownAccount(t, b.from_id), to = ownAccount(t, b.to_id);
  if (!from.active || !to.active) throw new AppError(400, "That bank account is closed");
  const at = b.txn_date && b.txn_date !== pkDate() ? dayEnd(b.txn_date) : now();
  // both halves carry the same ref, so deleting one removes the pair
  const key = `${b.ref ? `${b.ref} ` : ""}#T${Date.now().toString(36)}`;
  tx(() => {
    const ins = (acc: number, amount: number, party: string) => run("INSERT INTO bank_txns (tenant_id,account_id,kind,amount,party,ref,note,txn_date,created_by,created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
      t, acc, "transfer", amount, party, key, b.note || (amount < 0 ? "Transfer out" : "Transfer in"), at, req.user!.name, now()).id;
    const out = ins(from.id, -b.amount, `to ${accountName(to)}`);
    ins(to.id, b.amount, `from ${accountName(from)}`);
    linkPhotos(t, b.photo_ids, `bank:${out}`);
  });
  return bankAccounts(t);
}));

/** Remove a wrong bank-only entry (a transfer goes with its other half). */
banks.delete("/bank/txns/:id", requirePerm("bank.manage"), h((req) => {
  const t = tid(req);
  const e = get("SELECT * FROM bank_txns WHERE id=? AND tenant_id=?", Number(req.params.id), t);
  if (!e) throw new AppError(404, "Entry not found");
  // an entry made by a voucher or a cleared cheque is cancelled there, so the books stay in step
  if (get("SELECT id FROM cashier_vouchers WHERE tenant_id=? AND src=?", t, `bank:${e.id}`) || get("SELECT id FROM cheques WHERE tenant_id=? AND txn_ref=?", t, `bank:${e.id}`))
    throw new AppError(400, "This entry came from a cashier voucher or a cleared cheque — it can't be deleted here");
  if (e.kind === "transfer") run("DELETE FROM bank_txns WHERE tenant_id=? AND kind='transfer' AND ref=?", t, e.ref);
  else run("DELETE FROM bank_txns WHERE id=?", e.id);
  return bankAccounts(t);
}));

export const bankKindLabel = KIND_LABEL;

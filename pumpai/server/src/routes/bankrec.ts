/**
 * Bank reconciliation: upload the bank statement (CSV from internet banking) and every deposit, cheque
 * and payment is matched with the books. What is left over is shown on two sides:
 *  - in the bank but not in the books (bank charges, profit, a payment nobody entered)
 *  - in the books but not yet in the bank (cheques not cleared, deposits in transit)
 */
import { Router } from "express";
import { z } from "zod";
import { all, run, now } from "../db.js";
import { h, parse, tid, requirePerm } from "../auth.js";
import { AppError, round2 } from "../services.js";

export const bankrec = Router();
const DAY = 86_400_000;

interface BankLine { at: string | null; text: string; ref: string; debit: number; credit: number; balance: number | null }
const num = (v: string | undefined) => { const n = Number(String(v ?? "").replace(/[^0-9.\-]/g, "")); return isNaN(n) ? 0 : n; };

/** Read a bank statement CSV: Date, Description, Cheque/Ref, Debit/Withdrawal, Credit/Deposit, Balance (or one signed Amount column). */
export function parseBankStatement(csv: string): BankLine[] {
  const rows = csv.replace(/^﻿/, "").split(/\r?\n/).filter((l) => l.trim())
    .map((l) => (l.match(/("([^"]|"")*"|[^,]*)(,|$)/g) ?? []).map((c) => c.replace(/,$/, "").replace(/^"|"$/g, "").replace(/""/g, '"').trim()));
  // the header is the first row that has a date column and a money column (banks often put the account details on top)
  const hi = rows.findIndex((r) => r.some((c) => /date/i.test(c)) && r.some((c) => /debit|credit|withdraw|deposit|amount/i.test(c)));
  if (hi < 0) throw new AppError(400, "Could not find the header row (Date, Debit, Credit…) in the file");
  const head = rows[hi].map((x) => x.toLowerCase());
  // names in order of preference; very short names (dr / cr) must be the whole heading
  const col = (...n: string[]) => { for (const k of n) { const i = head.findIndex((x) => (k.length <= 3 ? x === k || x === `${k}.` : x.includes(k))); if (i >= 0) return i; } return -1; };
  const iDate = col("value date", "date"), iText = col("description", "narration", "particular", "detail", "remark");
  const iRef = col("cheque", "chq", "ref", "instrument"), iDr = col("debit", "withdraw", "dr"), iCr = col("credit", "deposit", "cr");
  const iAmt = col("amount"), iBal = col("balance");
  if (iDr < 0 && iCr < 0 && iAmt < 0) throw new AppError(400, "Could not find Debit / Credit (or Amount) columns");
  return rows.slice(hi + 1).map((r) => {
    let debit = iDr >= 0 ? Math.abs(num(r[iDr])) : 0, credit = iCr >= 0 ? Math.abs(num(r[iCr])) : 0;
    if (iDr < 0 && iCr < 0) { const a = num(r[iAmt]); if (a < 0) debit = -a; else credit = a; }
    const d = r[iDate] ?? "";
    const m = d.match(/^(\d{1,2})[/\-. ]([A-Za-z]{3}|\d{1,2})[/\-. ](\d{2,4})/);
    let iso: string | null = null;
    if (/^\d{4}-\d{2}-\d{2}/.test(d)) iso = new Date(`${d.slice(0, 10)}T12:00:00+05:00`).toISOString();
    else if (m) {
      const mon = /^\d+$/.test(m[2]) ? Number(m[2]) : ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"].indexOf(m[2].toLowerCase()) + 1;
      const y = m[3].length === 2 ? 2000 + Number(m[3]) : Number(m[3]);
      if (mon > 0) iso = new Date(`${y}-${String(mon).padStart(2, "0")}-${m[1].padStart(2, "0")}T12:00:00+05:00`).toISOString();
    }
    return { at: iso, text: iText >= 0 ? r[iText] ?? "" : r.join(" "), ref: iRef >= 0 ? r[iRef] ?? "" : "", debit: round2(debit), credit: round2(credit), balance: iBal >= 0 && r[iBal] ? num(r[iBal]) : null };
  }).filter((l) => l.at && (l.debit > 0 || l.credit > 0));
}

interface BookEntry { key: string; side: "in" | "out"; at: string; amount: number; what: string; ref: string }
/** Money that should pass through the bank: everything not paid in cash. */
function bookEntries(t: number, from: string, to: string, bank?: string): BookEntry[] {
  const P = [t, from, to] as const;
  const like = bank ? `%${bank}%` : "%";
  const notCash = "LOWER(COALESCE(method,'')) NOT IN ('cash','wht','')";
  return [
    ...all("SELECT id, amount, created_at, bank, slip_ref FROM bank_deposits WHERE tenant_id=? AND created_at >= ? AND created_at < ? AND bank LIKE ?", ...P, like)
      .map((r) => ({ key: `dep${r.id}`, side: "in" as const, at: r.created_at, amount: r.amount, what: `Cash deposited (${r.bank})`, ref: r.slip_ref ?? "" })),
    ...all(`SELECT k.id, k.amount, k.created_at, k.ref, c.name FROM khata_ledger k JOIN customers c ON c.id=k.customer_id
      WHERE c.tenant_id=? AND k.type='credit' AND k.created_at >= ? AND k.created_at < ? AND LOWER(COALESCE(k.ref,'')) IN ('bank','cheque','online','raast','pay order','bank transfer','ibft')`, ...P)
      .map((r) => ({ key: `kh${r.id}`, side: "in" as const, at: r.created_at, amount: r.amount, what: `Khata payment — ${r.name} (${r.ref})`, ref: "" })),
    ...all(`SELECT w.id, w.amount, w.created_at, w.method, w.ref, c.name FROM wholesale_txns w JOIN wholesale_clients c ON c.id=w.client_id
      WHERE w.tenant_id=? AND w.type='payment' AND w.voided=0 AND w.created_at >= ? AND w.created_at < ? AND ${notCash.replaceAll("method", "w.method")}`, ...P)
      .map((r) => ({ key: `ws${r.id}`, side: "in" as const, at: r.created_at, amount: r.amount, what: `Wholesale payment — ${r.name} (${r.method})`, ref: r.ref ?? "" })),
    ...all(`SELECT w.id, w.amount, w.created_at, w.method, w.ref, c.name FROM wallet_ledger w JOIN customers c ON c.id=w.customer_id
      WHERE w.tenant_id=? AND w.type='deposit' AND w.created_at >= ? AND w.created_at < ? AND LOWER(COALESCE(w.method,'')) IN ('bank','raast','cheque')`, ...P)
      .map((r) => ({ key: `wl${r.id}`, side: "in" as const, at: r.created_at, amount: r.amount, what: `Wallet deposit — ${r.name} (${r.method})`, ref: r.ref ?? "" })),
    ...all(`SELECT s.id, s.amount, s.created_at, s.method, s.ref, p.name FROM supplier_txns s JOIN suppliers p ON p.id=s.supplier_id
      WHERE s.tenant_id=? AND s.type='payment' AND s.created_at >= ? AND s.created_at < ? AND ${notCash.replaceAll("method", "s.method")}`, ...P)
      .map((r) => ({ key: `sp${r.id}`, side: "out" as const, at: r.created_at, amount: r.amount, what: `Paid ${r.name} (${r.method})`, ref: r.ref ?? "" })),
    ...all(`SELECT id, amount, created_at, method, category, paid_to, receipt_ref FROM expenses WHERE tenant_id=? AND status='approved' AND created_at >= ? AND created_at < ?
      AND LOWER(method) IN ('bank','cheque','card','raast')`, ...P)
      .map((r) => ({ key: `ex${r.id}`, side: "out" as const, at: r.created_at, amount: r.amount, what: `${r.category}${r.paid_to ? ` — ${r.paid_to}` : ""} (${r.method})`, ref: r.receipt_ref ?? "" })),
  ].sort((a, b) => a.at.localeCompare(b.at));
}

bankrec.post("/bank/reconcile", requirePerm("expenses.approve"), h((req) => {
  const b = parse(z.object({ csv: z.string().min(10).max(3_000_000), bank: z.string().max(60).optional(), window_days: z.number().min(0).max(15).default(4), save: z.boolean().default(true) }), req.body);
  const t = tid(req);
  const lines = parseBankStatement(b.csv);
  if (!lines.length) throw new AppError(400, "No money lines found in the statement");
  const times = lines.map((l) => Date.parse(l.at!));
  const pFrom = new Date(Math.min(...times) - 12 * 3600_000).toISOString(), pTo = new Date(Math.max(...times) + 12 * 3600_000).toISOString();
  const win = b.window_days * DAY;
  const books = bookEntries(t, new Date(Date.parse(pFrom) - win).toISOString(), new Date(Date.parse(pTo) + win).toISOString(), b.bank || undefined);
  const used = new Set<string>();
  const matched: { line: BankLine; entry: BookEntry }[] = [];
  const bankOnly: BankLine[] = [];
  for (const l of lines) {
    const side = l.credit > 0 ? "in" : "out", amount = l.credit || l.debit;
    const near = (e: BookEntry) => Math.abs(Date.parse(e.at) - Date.parse(l.at!));
    const cands = books.filter((e) => !used.has(e.key) && e.side === side && Math.abs(e.amount - amount) <= 1 && near(e) <= win + DAY)
      // a matching cheque / reference number wins, then the closest date
      .sort((x, y) => Number(Boolean(y.ref && (l.ref + l.text).includes(y.ref))) - Number(Boolean(x.ref && (l.ref + l.text).includes(x.ref))) || near(x) - near(y));
    if (cands[0]) { used.add(cands[0].key); matched.push({ line: l, entry: cands[0] }); } else bankOnly.push(l);
  }
  // in the books within the statement dates but not seen in the bank (yet)
  const booksOnly = books.filter((e) => !used.has(e.key) && e.at >= pFrom && e.at <= pTo);
  const sum = (a: number[]) => round2(a.reduce((x, y) => x + y, 0));
  const withBal = lines.filter((l) => l.balance != null);
  const closing = withBal.length ? withBal[withBal.length - 1].balance : null;
  const r = {
    period: { from: lines[0].at, to: lines[lines.length - 1].at }, statement_lines: lines.length, matched: matched.length,
    money_in: sum(lines.map((l) => l.credit)), money_out: sum(lines.map((l) => l.debit)), statement_closing: closing,
    matched_lines: matched,
    bank_only: bankOnly.map((l) => ({ ...l, hint: /charge|fee|fed|excise|sms|commission/i.test(l.text) ? "Bank charges — add as expense" : /profit|interest/i.test(l.text) ? "Bank profit" : l.credit ? "Money received — who paid?" : "Payment not in the books" })),
    bank_only_total: { in: sum(bankOnly.map((l) => l.credit)), out: sum(bankOnly.map((l) => l.debit)) },
    books_only: booksOnly, books_only_total: { in: sum(booksOnly.filter((e) => e.side === "in").map((e) => e.amount)), out: sum(booksOnly.filter((e) => e.side === "out").map((e) => e.amount)) },
  };
  if (b.save) run("INSERT INTO bank_recons (tenant_id,bank,period_from,period_to,statement_closing,lines,matched,bank_only,books_only,created_by,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
    t, b.bank ?? null, r.period.from, r.period.to, closing, lines.length, matched.length, r.bank_only_total.in + r.bank_only_total.out, r.books_only_total.in + r.books_only_total.out, req.user!.name, now());
  return r;
}));
bankrec.get("/bank/reconciliations", requirePerm("expenses.approve"), h((req) => all("SELECT * FROM bank_recons WHERE tenant_id=? ORDER BY id DESC LIMIT 20", tid(req))));

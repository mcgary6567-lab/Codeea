import { useState } from "react";
import { Download, Printer, Send, QrCode, ExternalLink } from "lucide-react";
import { api, getToken, useApi } from "../lib/api";
import { ProofThumbs } from "./Capture";
import { PortalCard } from "./PortalCard";
import { Field, Loading, Modal, useAction } from "./ui";
import { PRODUCTS, num, pkr } from "../lib/format";

const pkToday = () => new Date(Date.now() + 5 * 3600_000).toISOString().slice(0, 10);
const monthStart = () => pkToday().slice(0, 8) + "01";
const today = pkToday;

/** Khata bill / statement: every fuel slip with litres and the rate on that day, payments, running balance. */
export default function KhataStatement({ customerId, onClose }: { customerId: number; onClose: () => void }) {
  const [range, setRange] = useState({ from: monthStart(), to: today() });
  const qs = new URLSearchParams(Object.entries(range).filter(([, v]) => v)).toString();
  const { data: s, reload } = useApi<any>(`/customers/${customerId}/statement?${qs}`);
  const csv = `/api/customers/${customerId}/statement.csv?${qs}&token=${encodeURIComponent(getToken() ?? "")}`;
  const { busy, run } = useAction();
  const month = (range.from || pkToday()).slice(0, 7);
  const openBill = async () => { const r = await run(() => api(`/customers/${customerId}/bill-link?month=${month}`)); if (r) window.open(r.url, "_blank"); };

  return (
    <Modal open onClose={onClose} title="Khata bill / statement" wide>
      <div className="flex flex-wrap items-end gap-2 print:hidden">
        <Field label="From"><input className="input" type="date" value={range.from} onChange={(e) => setRange({ ...range, from: e.target.value })} /></Field>
        <Field label="To"><input className="input" type="date" value={range.to} onChange={(e) => setRange({ ...range, to: e.target.value })} /></Field>
        <button className="btn-secondary" onClick={() => setRange({ from: monthStart(), to: today() })}>This month</button>
        <button className="btn-secondary" onClick={() => setRange({ from: "", to: "" })}>All time</button>
        <div className="ml-auto flex gap-2">
          <button className="btn-secondary" disabled={busy} onClick={() => run(() => api(`/customers/${customerId}/send-bill`, { body: { month } }), `${month} bill sent on WhatsApp`)}><Send size={15} /> WhatsApp bill</button>
          <button className="btn-secondary" onClick={openBill}><ExternalLink size={15} /> Bill link</button>
          <a className="btn-secondary" href={`/cards/${customerId}`} target="_blank" rel="noreferrer"><QrCode size={15} /> QR cards</a>
          <a className="btn-secondary" href={csv}><Download size={15} /> Excel</a>
          <button className="btn-primary" onClick={() => window.print()}><Printer size={15} /> Print bill</button>
        </div>
      </div>
      {!s ? <Loading /> : (
        <div className="mt-4 space-y-3 print:mt-0">
          <div className="flex flex-wrap justify-between gap-2 border-b border-slate-200 pb-3">
            <div>
              <div className="text-xl font-bold">{s.customer.name}</div>
              <div className="text-sm text-slate-600">Fuel account statement · {s.from ?? "start"} to {s.to ?? "today"}</div>
            </div>
            <div className="text-right text-sm">
              <div>Opening balance: <b className="tabular-nums">{pkr(s.opening_balance)}</b></div>
              <div>Charged: <b className="tabular-nums">{pkr(s.totals.charged)}</b> · Paid: <b className="tabular-nums">{pkr(s.totals.paid)}</b></div>
              <div className="text-lg">Amount due: <b className="tabular-nums">{pkr(s.closing_balance)}</b></div>
            </div>
          </div>
          <div className="flex flex-wrap items-center gap-2 rounded-lg bg-slate-50 p-2 text-sm print:hidden">
            <a className="btn-secondary !py-1" href={`/api/customers/${customerId}/notice?token=${encodeURIComponent(getToken() ?? "")}`} target="_blank" rel="noreferrer">📜 Payment notice</a>
            <label className="ml-auto flex items-center gap-2"><input type="checkbox" checked={Boolean(s.customer.khata_blocked)} onChange={(e) => run(() => api(`/customers/${customerId}/khata-hold`, { body: { blocked: e.target.checked } }), e.target.checked ? "Khata on hold" : "Khata open again").then(reload)} />
              <span className={s.customer.khata_blocked ? "font-semibold text-red-600" : ""}>{s.customer.khata_blocked ? "On hold (overdue)" : "Hold khata"}</span></label>
          </div>
          <div className="print:hidden"><PortalCard base={`/customers/${customerId}/portal`} name={s.customer.name} phone={s.customer.phone} canManage /></div>
          {["police", "school", "government", "hospital"].includes(s.customer.type) && <GovtBills customerId={customerId} />}
          <div className="flex flex-wrap gap-2">
            {s.totals.by_product.map((p: any) => (
              <div key={p.product} className="rounded-lg bg-slate-50 px-3 py-2 text-sm"><b>{PRODUCTS[p.product] ?? p.product}</b>: {num(p.litres, 2)} L · {pkr(p.amount)} · {p.entries} slips</div>
            ))}
          </div>
          <div className="max-h-[55vh] overflow-auto rounded-lg border border-slate-200 print:max-h-none">
            <table className="w-full">
              <thead className="sticky top-0"><tr>{["Date & time", "Vehicle", "Slip no.", "Fuel", "Litres", "Rate / L", "Charged", "Paid", "Balance"].map((h, i) => <th key={h} className={`th whitespace-nowrap ${i >= 4 ? "text-right" : ""}`}>{h}</th>)}</tr></thead>
              <tbody>
                {s.lines.map((l: any) => (
                  <tr key={l.id}>
                    <td className="td whitespace-nowrap text-xs">{new Date(l.created_at).toLocaleString("en-PK", { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit" })}</td>
                    <td className="td text-xs">{l.vehicle_no ?? "—"}</td>
                    <td className="td text-xs font-medium">{l.slip_no ?? (l.type === "credit" ? l.ref ?? "Payment" : "—")}</td>
                    <td className="td text-sm">{l.product ? PRODUCTS[l.product] : l.type === "credit" ? <span className="text-emerald-700">Payment</span> : l.note} <span className="print:hidden"><ProofThumbs ids={l.proof_ids} /></span></td>
                    <td className="td whitespace-nowrap text-right tabular-nums">{l.litres != null ? num(l.litres, 2) : ""}</td>
                    <td className="td whitespace-nowrap text-right tabular-nums">{l.rate != null ? `Rs ${Number(l.rate).toFixed(2)}` : ""}</td>
                    <td className="td whitespace-nowrap text-right tabular-nums">{l.type === "debit" ? pkr(l.amount) : ""}</td>
                    <td className="td whitespace-nowrap text-right tabular-nums text-emerald-700">{l.type === "credit" ? pkr(l.amount) : ""}</td>
                    <td className="td whitespace-nowrap text-right font-medium tabular-nums">{pkr(l.balance)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            {!s.lines.length && <div className="p-6 text-center text-sm text-slate-500">No entries in this period</div>}
          </div>
        </div>
      )}
    </Modal>
  );
}

/** Monthly bills for an office: PO number, when submitted, cheque received. */
function GovtBills({ customerId }: { customerId: number }) {
  const { data, reload } = useApi<any[]>("/govt-bills");
  const { busy, run } = useAction();
  const [month, setMonth] = useState(() => { const d = new Date(Date.now() + 5 * 3600_000); d.setUTCDate(1); d.setUTCMonth(d.getUTCMonth() - 1); return d.toISOString().slice(0, 7); });
  const bills = (data ?? []).filter((b) => b.customer_id === customerId);
  return (
    <div className="rounded-lg border border-slate-200 p-3 print:hidden">
      <div className="mb-2 flex flex-wrap items-end gap-2">
        <span className="font-semibold">Government bills</span>
        <input className="input ml-auto w-auto" type="month" value={month} onChange={(e) => setMonth(e.target.value)} aria-label="Bill month" />
        <button className="btn-primary !py-1.5" disabled={busy} onClick={() => run(() => api(`/customers/${customerId}/govt-bills`, { body: { month } }), "Bill made").then(reload)}>Make bill</button>
      </div>
      {bills.map((b) => (
        <div key={b.id} className="flex flex-wrap items-center gap-2 border-t border-slate-100 py-2 text-sm">
          <span className="font-medium">{b.bill_no}</span><span>{b.month}</span><span className="tabular-nums">{pkr(b.amount)}</span>
          <span className={`rounded px-1.5 text-xs ${b.status === "paid" ? "bg-emerald-100 text-emerald-800" : b.status === "draft" ? "bg-slate-100" : "bg-amber-100 text-amber-800"}`}>{b.status}{b.days_waiting != null ? ` · ${b.days_waiting} days` : ""}</span>
          {b.po_number && <span className="text-xs text-slate-500">PO {b.po_number}</span>}
          <span className="ml-auto flex gap-1">
            {b.status === "draft" && <button className="btn-secondary !px-2 !py-1 text-xs" onClick={() => { const po = prompt("PO / reference number (optional)") ?? ""; run(() => api(`/govt-bills/${b.id}`, { method: "PATCH", body: { po_number: po || null, submitted_on: new Date(Date.now() + 5 * 3600_000).toISOString().slice(0, 10) } }), "Marked submitted").then(reload); }}>Submitted today</button>}
            {b.status !== "paid" && <button className="btn-secondary !px-2 !py-1 text-xs" onClick={() => { const a = prompt(`Cheque / transfer amount (outstanding ${pkr(b.outstanding)})`, String(b.outstanding)); if (a && Number(a) > 0) run(() => api(`/govt-bills/${b.id}/paid`, { body: { amount: Number(a), method: "cheque" } }), "Payment recorded").then(reload); }}>Payment received</button>}
          </span>
        </div>
      ))}
      {!bills.length && <p className="text-xs text-slate-500">No bills yet. Make the bill for a month, write the PO number when you submit it, and record the cheque when it comes.</p>}
    </div>
  );
}

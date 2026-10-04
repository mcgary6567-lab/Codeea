import { useState } from "react";
import { Download, Printer } from "lucide-react";
import { getToken, useApi } from "../lib/api";
import { Field, Loading, Modal } from "./ui";
import { PRODUCTS, num, pkr } from "../lib/format";

const monthStart = () => new Date().toISOString().slice(0, 8) + "01";
const today = () => new Date().toISOString().slice(0, 10);

/** Khata bill / statement: every fuel slip with litres and the rate on that day, payments, running balance. */
export default function KhataStatement({ customerId, onClose }: { customerId: number; onClose: () => void }) {
  const [range, setRange] = useState({ from: monthStart(), to: today() });
  const qs = new URLSearchParams(Object.entries(range).filter(([, v]) => v)).toString();
  const { data: s } = useApi<any>(`/customers/${customerId}/statement?${qs}`);
  const csv = `/api/customers/${customerId}/statement.csv?${qs}&token=${encodeURIComponent(getToken() ?? "")}`;

  return (
    <Modal open onClose={onClose} title="Khata bill / statement" wide>
      <div className="flex flex-wrap items-end gap-2 print:hidden">
        <Field label="From"><input className="input" type="date" value={range.from} onChange={(e) => setRange({ ...range, from: e.target.value })} /></Field>
        <Field label="To"><input className="input" type="date" value={range.to} onChange={(e) => setRange({ ...range, to: e.target.value })} /></Field>
        <button className="btn-secondary" onClick={() => setRange({ from: monthStart(), to: today() })}>This month</button>
        <button className="btn-secondary" onClick={() => setRange({ from: "", to: "" })}>All time</button>
        <div className="ml-auto flex gap-2">
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
                    <td className="td text-sm">{l.product ? PRODUCTS[l.product] : l.type === "credit" ? <span className="text-emerald-700">Payment</span> : l.note}</td>
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

import { useState } from "react";
import { useNavigate } from "react-router-dom";
import KhataStatement from "../components/KhataStatement";
import { TYPE_ICON } from "./Pos";
import { BellRing } from "lucide-react";
import { api, useApi } from "../lib/api";
import { Empty, Loading, PageHeader, Stat, useAction } from "../components/ui";
import { ago, phone, pkr, pkrShort } from "../lib/format";

export default function Khata() {
  const { data, reload } = useApi<any[]>("/khata");
  const nav = useNavigate();
  const { busy, run } = useAction();
  const [group, setGroup] = useState("");
  const [bill, setBill] = useState<number | null>(null);
  if (!data) return <Loading />;
  const INST = ["police", "school", "government", "hospital"];
  const rows = data.filter((c) => !group || (group === "institution" ? INST.includes(c.type) : c.type === group));
  const total = data.reduce((a, c) => a + Math.max(0, c.balance), 0);
  const nearLimit = data.filter((c) => c.credit_limit && c.balance >= 0.8 * c.credit_limit).length;
  const highRisk = data.filter((c) => c.risk_score >= 60).length;

  return (
    <div>
      <PageHeader title="Khata (credit accounts)" subtitle="Automatic WhatsApp reminders with JazzCash / Easypaisa / Raast links run daily at 11am"
        actions={<button className="btn-secondary" disabled={busy} onClick={() => run(() => api("/automations/khata_reminders/run", { body: {} }), (r: any) => r.result).then(reload)}><BellRing size={15} /> Send reminders now</button>} />
      <div className="mb-4 grid grid-cols-2 gap-3 md:grid-cols-4">
        <Stat label="Total outstanding" value={pkrShort(total)} />
        <Stat label="Credit customers" value={data.length} />
        <Stat label="Near limit (80%+)" value={nearLimit} tone="amber" />
        <Stat label="High credit risk" value={highRisk} tone="red" />
      </div>
      <div className="mb-3 flex flex-wrap gap-2">
        {[["", "All"], ["institution", "🏛️ Police / Govt / Schools"], ["fleet", "🚚 Fleets"], ["farmer", "🚜 Farmers"], ["business", "🏢 Businesses"], ["retail", "🚗 Retail"]].map(([k, l]) => (
          <button key={k} onClick={() => setGroup(k)} className={`rounded-full px-3 py-1.5 text-sm ${group === k ? "bg-brand-600 text-white" : "border border-slate-300 bg-white"}`}>
            {l} <span className="opacity-70">{pkrShort(data.filter((c) => !k || (k === "institution" ? INST.includes(c.type) : c.type === k)).reduce((a, c) => a + Math.max(0, c.balance), 0))}</span>
          </button>
        ))}
      </div>
      <div className="card overflow-x-auto">
        <table className="w-full">
          <thead><tr><th className="th">Customer</th><th className="th text-right">Balance</th><th className="th text-right">Limit</th><th className="th">Utilisation</th><th className="th">Risk</th><th className="th">Last payment</th><th className="th" /></tr></thead>
          <tbody>
            {rows.map((c) => {
              const util = c.credit_limit ? Math.min(100, (c.balance / c.credit_limit) * 100) : 0;
              return (
                <tr key={c.id} className="cursor-pointer hover:bg-slate-50" onClick={() => nav(`/customers/${c.id}`)}>
                  <td className="td"><div className="font-medium">{TYPE_ICON[c.type] && c.type !== "retail" ? `${TYPE_ICON[c.type]} ` : ""}{c.name}</div><div className="text-xs text-slate-500">{phone(c.phone)} · {c.type}</div></td>
                  <td className="td text-right font-medium tabular-nums">{pkr(c.balance)}</td>
                  <td className="td text-right tabular-nums text-slate-600">{pkr(c.credit_limit)}</td>
                  <td className="td"><div className="h-1.5 w-28 rounded-full bg-slate-100"><div className="h-full rounded-full" style={{ width: `${util}%`, background: util >= 80 ? "#e34948" : "#2a78d6" }} /></div><span className="text-xs text-slate-500">{Math.round(util)}%</span></td>
                  <td className={`td text-sm tabular-nums ${c.risk_score >= 60 ? "font-semibold text-red-600" : ""}`}>{Math.round(c.risk_score)}</td>
                  <td className="td text-xs text-slate-500">{ago(c.last_payment)}</td>
                  <td className="td"><button className="btn-secondary !px-2 !py-1 text-xs" onClick={(e) => { e.stopPropagation(); setBill(c.id); }}>📄 Bill</button></td>
                </tr>
              );
            })}
          </tbody>
        </table>
        {!rows.length && <Empty>No credit customers</Empty>}
      </div>
      {bill && <KhataStatement customerId={bill} onClose={() => setBill(null)} />}
    </div>
  );
}

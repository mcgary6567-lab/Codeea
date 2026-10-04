import { useNavigate } from "react-router-dom";
import { BellRing } from "lucide-react";
import { api, useApi } from "../lib/api";
import { Empty, Loading, PageHeader, Stat, useAction } from "../components/ui";
import { ago, phone, pkr, pkrShort } from "../lib/format";

export default function Khata() {
  const { data, reload } = useApi<any[]>("/khata");
  const nav = useNavigate();
  const { busy, run } = useAction();
  if (!data) return <Loading />;
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
      <div className="card overflow-x-auto">
        <table className="w-full">
          <thead><tr><th className="th">Customer</th><th className="th text-right">Balance</th><th className="th text-right">Limit</th><th className="th">Utilisation</th><th className="th">Risk</th><th className="th">Last payment</th></tr></thead>
          <tbody>
            {data.map((c) => {
              const util = c.credit_limit ? Math.min(100, (c.balance / c.credit_limit) * 100) : 0;
              return (
                <tr key={c.id} className="cursor-pointer hover:bg-slate-50" onClick={() => nav(`/customers/${c.id}`)}>
                  <td className="td"><div className="font-medium">{c.name}</div><div className="text-xs text-slate-500">{phone(c.phone)} · {c.type}</div></td>
                  <td className="td text-right font-medium tabular-nums">{pkr(c.balance)}</td>
                  <td className="td text-right tabular-nums text-slate-600">{pkr(c.credit_limit)}</td>
                  <td className="td"><div className="h-1.5 w-28 rounded-full bg-slate-100"><div className="h-full rounded-full" style={{ width: `${util}%`, background: util >= 80 ? "#e34948" : "#2a78d6" }} /></div><span className="text-xs text-slate-500">{Math.round(util)}%</span></td>
                  <td className={`td text-sm tabular-nums ${c.risk_score >= 60 ? "font-semibold text-red-600" : ""}`}>{Math.round(c.risk_score)}</td>
                  <td className="td text-xs text-slate-500">{ago(c.last_payment)}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
        {!data.length && <Empty>No credit customers</Empty>}
      </div>
    </div>
  );
}

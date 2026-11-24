import { useState } from "react";
import { AlertTriangle, Info, AlertOctagon, Check } from "lucide-react";
import { api, useApi } from "../lib/api";
import { Badge, Empty, ListTools, Loading, PageHeader, severityTone, useAction, useListFilter } from "../components/ui";
import { ago } from "../lib/format";

const icon = { critical: AlertOctagon, warning: AlertTriangle, info: Info } as Record<string, any>;

export default function Alerts() {
  const { data, reload } = useApi<any[]>("/alerts");
  const { busy, run } = useAction();
  const [showAll, setShowAll] = useState(false);
  const [q, setQ] = useState("");
  const [sev, setSev] = useState("");
  const [type, setType] = useState("");
  const base = useListFilter(data, { keep: (a) => showAll || !a.acknowledged });
  const sevChips = [{ k: "", label: "All", n: base.length }, ...["critical", "warning", "info"].map((k) => ({ k, label: k, n: base.filter((a) => a.severity === k).length })).filter((c) => c.n > 0)];
  const typeChips = [{ k: "", label: "All types", n: base.length }, ...Array.from(new Set(base.map((a) => String(a.type)))).sort().map((k) => ({ k, label: k.replace(/_/g, " "), n: base.filter((a) => a.type === k).length }))];
  const rows = useListFilter(base, { q, keep: (a) => (!sev || a.severity === sev) && (!type || a.type === type), text: (a) => [a.title, a.body, a.type, a.severity, a.station_name].filter(Boolean).join(" ") });
  if (!data) return <Loading />;

  return (
    <div>
      <PageHeader title="Alerts" subtitle="Raised by AI loss-detection, stock forecasting, complaints and WhatsApp hand-offs"
        actions={<>
          <label className="flex min-h-9 items-center gap-2 text-sm"><input type="checkbox" checked={showAll} onChange={(e) => setShowAll(e.target.checked)} /> Show acknowledged</label>
          <button className="btn-secondary" disabled={busy} onClick={() => run(() => api("/alerts/ack-all", { body: {} }), "All alerts acknowledged").then(reload)}><Check size={15} /> Acknowledge all</button>
        </>} />
      <ListTools q={q} onQ={setQ} placeholder="Alert dhoondein… · تلاش" chips={sevChips} chip={sev} onChip={setSev} shown={rows.length} total={base.length} />
      {typeChips.length > 2 && <div className="mb-3 flex flex-wrap gap-1 print:hidden">{typeChips.map((c) => (
        <button type="button" key={c.k} onClick={() => setType(c.k)} aria-pressed={type === c.k} className={`rounded-full px-2.5 py-1 text-xs font-semibold ${type === c.k ? "bg-slate-800 text-white" : "bg-slate-100 text-slate-600 hover:bg-slate-200"}`}>{c.label} {c.n}</button>))}</div>}
      <div className="card divide-y divide-slate-100">
        {rows.map((a) => {
          const I = icon[a.severity] ?? Info;
          return (
            <div key={a.id} className={`flex flex-wrap items-start gap-3 p-4 sm:flex-nowrap ${a.acknowledged ? "opacity-60" : ""}`}>
              <I size={18} className={`shrink-0 ${a.severity === "critical" ? "text-red-600" : a.severity === "warning" ? "text-amber-600" : "text-blue-600"}`} />
              <div className="min-w-0 flex-1 basis-[calc(100%-2.25rem)] sm:basis-0">
                <div className="flex flex-wrap items-center gap-2"><span className="font-medium">{a.title}</span><Badge tone={severityTone(a.severity)}>{a.severity}</Badge><Badge>{a.type.replace("_", " ")}</Badge></div>
                {a.body && <p className="mt-1 whitespace-pre-wrap text-sm text-slate-600">{a.body}</p>}
                <div className="mt-1 text-xs text-slate-400">{a.station_name ?? "All stations"} · {ago(a.created_at)}</div>
              </div>
              {!a.acknowledged && <button className="btn-secondary ml-[30px] min-h-9 !px-3 !py-1 text-xs sm:ml-0 sm:min-h-0" disabled={busy} onClick={() => run(() => api(`/alerts/${a.id}/ack`, { body: {} })).then(reload)}>Acknowledge</button>}
            </div>
          );
        })}
        {!rows.length && <Empty>{base.length ? "Kuch nahi mila · کچھ نہیں ملا" : "No open alerts 🎉"}</Empty>}
      </div>
    </div>
  );
}

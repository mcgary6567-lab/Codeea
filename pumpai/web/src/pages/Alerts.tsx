import { useState } from "react";
import { AlertTriangle, Info, AlertOctagon, Check } from "lucide-react";
import { api, useApi } from "../lib/api";
import { Badge, Empty, Loading, PageHeader, severityTone, useAction } from "../components/ui";
import { ago } from "../lib/format";

const icon = { critical: AlertOctagon, warning: AlertTriangle, info: Info } as Record<string, any>;

export default function Alerts() {
  const { data, reload } = useApi<any[]>("/alerts");
  const { busy, run } = useAction();
  const [showAll, setShowAll] = useState(false);
  if (!data) return <Loading />;
  const rows = data.filter((a) => showAll || !a.acknowledged);

  return (
    <div>
      <PageHeader title="Alerts" subtitle="Raised by AI loss-detection, stock forecasting, complaints and WhatsApp hand-offs"
        actions={<>
          <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={showAll} onChange={(e) => setShowAll(e.target.checked)} /> Show acknowledged</label>
          <button className="btn-secondary" disabled={busy} onClick={() => run(() => api("/alerts/ack-all", { body: {} }), "All alerts acknowledged").then(reload)}><Check size={15} /> Acknowledge all</button>
        </>} />
      <div className="card divide-y divide-slate-100">
        {rows.map((a) => {
          const I = icon[a.severity] ?? Info;
          return (
            <div key={a.id} className={`flex items-start gap-3 p-4 ${a.acknowledged ? "opacity-60" : ""}`}>
              <I size={18} className={a.severity === "critical" ? "text-red-600" : a.severity === "warning" ? "text-amber-600" : "text-blue-600"} />
              <div className="min-w-0 flex-1">
                <div className="flex flex-wrap items-center gap-2"><span className="font-medium">{a.title}</span><Badge tone={severityTone(a.severity)}>{a.severity}</Badge><Badge>{a.type.replace("_", " ")}</Badge></div>
                {a.body && <p className="mt-1 whitespace-pre-wrap text-sm text-slate-600">{a.body}</p>}
                <div className="mt-1 text-xs text-slate-400">{a.station_name ?? "All stations"} · {ago(a.created_at)}</div>
              </div>
              {!a.acknowledged && <button className="btn-secondary !px-2 !py-1 text-xs" disabled={busy} onClick={() => run(() => api(`/alerts/${a.id}/ack`, { body: {} })).then(reload)}>Acknowledge</button>}
            </div>
          );
        })}
        {!rows.length && <Empty>No open alerts 🎉</Empty>}
      </div>
    </div>
  );
}

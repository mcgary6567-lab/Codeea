import { useState } from "react";
import { Play, Bot, FileText } from "lucide-react";
import { api, useApi } from "../lib/api";
import { Loading, Modal, PageHeader, useAction } from "../components/ui";
import { ago } from "../lib/format";

const cronText: Record<string, string> = {
  "0 2 * * *": "Daily 2:00 am", "*/30 * * * *": "Every 30 minutes", "0 * * * *": "Every hour",
  "0 11 * * *": "Daily 11:00 am", "0 12 * * 1": "Mondays 12:00 pm", "0 21 * * *": "Daily 9:00 pm",
};

export default function Automations() {
  const { data, reload } = useApi<any[]>("/automations");
  const { busy, run } = useAction();
  const [brief, setBrief] = useState<string | null>(null);
  if (!data) return <Loading />;

  return (
    <div>
      <PageHeader title="AI automations" subtitle="Background jobs that run the business on autopilot (Pakistan time). Toggle or run any job now."
        actions={<button className="btn-secondary" disabled={busy} onClick={() => run(() => api("/ai/brief")).then((r: any) => r && setBrief(r.text))}><FileText size={15} /> Preview daily brief</button>} />
      <div className="grid gap-4 md:grid-cols-2">
        {data.map((a) => (
          <div key={a.key} className="card flex flex-col p-4">
            <div className="flex items-start gap-3">
              <div className="rounded-lg bg-violet-100 p-2 text-violet-700"><Bot size={18} /></div>
              <div className="flex-1">
                <div className="font-semibold">{a.name}</div>
                <div className="text-xs text-slate-500">{cronText[a.cron] ?? a.cron}</div>
              </div>
              <label className="relative inline-flex cursor-pointer items-center">
                <input type="checkbox" className="peer sr-only" checked={!!a.enabled} onChange={(e) => run(() => api(`/automations/${a.key}`, { method: "PATCH", body: { enabled: e.target.checked } }), e.target.checked ? "Enabled" : "Paused").then(reload)} />
                <div className="h-6 w-11 rounded-full bg-slate-200 after:absolute after:left-0.5 after:top-0.5 after:h-5 after:w-5 after:rounded-full after:bg-white after:transition peer-checked:bg-brand-600 peer-checked:after:translate-x-5" />
                <span className="sr-only">Enabled</span>
              </label>
            </div>
            <p className="mt-3 flex-1 text-sm text-slate-600">{a.description}</p>
            <div className="mt-3 flex items-center justify-between border-t border-slate-100 pt-3">
              <div className="text-xs text-slate-500">{a.last_run_at ? <>Last run {ago(a.last_run_at)}: <span className="text-slate-700">{a.last_result}</span></> : "Not run yet"}</div>
              <button className="btn-secondary !px-2 !py-1 text-xs" disabled={busy} onClick={() => run(() => api(`/automations/${a.key}/run`, { body: {} }), (r: any) => r.result).then(reload)}><Play size={13} /> Run now</button>
            </div>
          </div>
        ))}
      </div>
      <Modal open={!!brief} onClose={() => setBrief(null)} title="Owner's daily WhatsApp brief">
        <div className="whitespace-pre-wrap rounded-lg bg-wa-out p-3 text-sm">{brief}</div>
      </Modal>
    </div>
  );
}

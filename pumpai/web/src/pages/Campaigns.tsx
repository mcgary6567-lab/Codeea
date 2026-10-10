import { useState } from "react";
import { Sparkles, Send } from "lucide-react";
import { api, useApi } from "../lib/api";
import { Badge, Empty, Field, Loading, PageHeader, statusTone, useAction } from "../components/ui";
import { dt } from "../lib/format";

export default function Campaigns() {
  const { data, reload } = useApi<any>("/campaigns");
  const { busy, run } = useAction();
  const [f, setF] = useState({ name: "", segment: "all", goal: "", message: "" });
  if (!data) return <Loading />;
  const seg = data.segments.find((s: any) => s.key === f.segment);

  const aiWrite = async () => {
    const r: any = await run(() => api("/campaigns/ai-write", { body: { goal: f.goal || f.name || "Special weekend offer", segment: f.segment } }), "Message drafted by AI");
    if (r) setF({ ...f, message: r.message });
  };
  const create = async (send_now: boolean) => {
    const r = await run(() => api("/campaigns", { body: { name: f.name, segment: f.segment, message: f.message, send_now } }), send_now ? `Sent to ${seg?.count ?? 0} customers` : "Saved as draft");
    if (r) { setF({ name: "", segment: "all", goal: "", message: "" }); reload(); }
  };

  return (
    <div>
      <PageHeader title="WhatsApp campaigns" subtitle="AI-written broadcasts to opted-in customer segments. Placeholders: {name} {balance} {points}" />
      <div className="grid grid-cols-1 gap-5 lg:grid-cols-[400px_1fr]">
        <div className="card space-y-3 p-4">
          <h2 className="font-semibold">New campaign</h2>
          <Field label="Campaign name"><input className="input" value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} placeholder="Weekend car wash offer" /></Field>
          <Field label="Audience segment"><select className="input" value={f.segment} onChange={(e) => setF({ ...f, segment: e.target.value })}>
            {data.segments.map((s: any) => <option key={s.key} value={s.key}>{s.key} ({s.count} opted-in)</option>)}</select></Field>
          <Field label="Goal (for AI)"><input className="input" value={f.goal} onChange={(e) => setF({ ...f, goal: e.target.value })} placeholder="Free car wash on 40L+ fill this Sunday" /></Field>
          <button className="btn-secondary w-full" onClick={aiWrite} disabled={busy}><Sparkles size={15} className="text-violet-500" /> Write with AI</button>
          <Field label="Message"><textarea className="input h-36" value={f.message} onChange={(e) => setF({ ...f, message: e.target.value })} /></Field>
          <div className="text-xs text-slate-500">{f.message.length}/1000 · Outside the 24h window this goes out as your approved WhatsApp template.</div>
          <div className="flex gap-2">
            <button className="btn-secondary flex-1" disabled={busy || !f.name || !f.message} onClick={() => create(false)}>Save draft</button>
            <button className="btn-primary flex-1" disabled={busy || !f.name || !f.message} onClick={() => create(true)}><Send size={15} /> Send to {seg?.count ?? 0}</button>
          </div>
        </div>
        <div className="card">
          <table className="w-full">
            <thead><tr><th className="th">Campaign</th><th className="th">Segment</th><th className="th">Status</th><th className="th text-right">Sent</th><th className="th" /></tr></thead>
            <tbody>
              {data.campaigns.map((c: any) => (
                <tr key={c.id}>
                  <td className="td"><div className="font-medium">{c.name}</div><div className="line-clamp-2 max-w-md text-xs text-slate-500">{c.message}</div></td>
                  <td className="td text-sm">{c.segment}</td>
                  <td className="td"><Badge tone={statusTone(c.status)}>{c.status}</Badge><div className="text-xs text-slate-400">{dt(c.sent_at ?? c.created_at)}</div></td>
                  <td className="td text-right tabular-nums">{c.sent_count}</td>
                  <td className="td">{c.status === "draft" && <button className="btn-primary !px-2 !py-1 text-xs" disabled={busy} onClick={() => run(() => api(`/campaigns/${c.id}/send`, { body: {} }), "Campaign sent").then(reload)}>Send</button>}</td>
                </tr>
              ))}
            </tbody>
          </table>
          {!data.campaigns.length && <Empty>No campaigns yet</Empty>}
        </div>
      </div>
    </div>
  );
}

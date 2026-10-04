import { useState } from "react";
import { Link } from "react-router-dom";
import { api, useApi } from "../lib/api";
import { Badge, Empty, Loading, Modal, PageHeader, statusTone, useAction } from "../components/ui";
import { ago } from "../lib/format";

export default function Complaints() {
  const { data, reload } = useApi<any[]>("/complaints");
  const { busy, run } = useAction();
  const [replying, setReplying] = useState<any>(null);
  const [text, setText] = useState("");
  if (!data) return <Loading />;
  return (
    <div>
      <PageHeader title="Complaints" subtitle="Logged automatically by the AI agent from WhatsApp chats, with sentiment" />
      <div className="card divide-y divide-slate-100">
        {data.map((k) => (
          <div key={k.id} className="flex flex-wrap items-start gap-3 p-4">
            <div className="min-w-0 flex-1">
              <div className="flex flex-wrap items-center gap-2 text-sm">
                <span className="font-medium">C-{k.id}</span>
                <Badge tone={k.category === "short_measure" ? "red" : "slate"}>{String(k.category).replace("_", " ")}</Badge>
                <Badge tone={k.sentiment === "very_negative" ? "red" : k.sentiment === "negative" ? "amber" : "slate"}>{k.sentiment}</Badge>
                <Badge tone={statusTone(k.status)}>{k.status.replace("_", " ")}</Badge>
                <span className="text-xs text-slate-400">{ago(k.created_at)}</span>
              </div>
              <p className="mt-1 text-sm">{k.message}</p>
              {k.customer_id && <Link to={`/customers/${k.customer_id}`} className="text-xs text-brand-600 hover:underline">{k.customer_name}</Link>}
            </div>
            {k.status !== "resolved" && (
              <div className="flex gap-2">
                {k.status === "open" && <button className="btn-secondary text-xs" disabled={busy} onClick={() => run(() => api(`/complaints/${k.id}`, { method: "PATCH", body: { status: "in_progress" } })).then(reload)}>Start</button>}
                <button className="btn-primary text-xs" onClick={() => { setReplying(k); setText(`Aap ki shikayat C-${k.id} ki tehqeeq ho gayi hai. `); }}>Resolve & reply</button>
              </div>
            )}
          </div>
        ))}
        {!data.length && <Empty>No complaints 🎉</Empty>}
      </div>
      <Modal open={!!replying} onClose={() => setReplying(null)} title={`Resolve C-${replying?.id}`}>
        <textarea className="input h-28" value={text} onChange={(e) => setText(e.target.value)} />
        <div className="mt-3 flex justify-end gap-2">
          <button className="btn-secondary" onClick={() => setReplying(null)}>Cancel</button>
          <button className="btn-primary" disabled={busy} onClick={async () => { if (await run(() => api(`/complaints/${replying.id}`, { method: "PATCH", body: { status: "resolved", reply: text } }), "Resolved — customer notified on WhatsApp")) { setReplying(null); reload(); } }}>Send & resolve</button>
        </div>
      </Modal>
    </div>
  );
}

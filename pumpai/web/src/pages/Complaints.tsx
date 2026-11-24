import { useState } from "react";
import { Link } from "react-router-dom";
import { api, useApi } from "../lib/api";
import { Badge, Empty, FilterChips, Loading, Modal, PageHeader, SearchBar, matchQuery, statusTone, useAction } from "../components/ui";
import { ago } from "../lib/format";

export default function Complaints() {
  const { data, reload } = useApi<any[]>("/complaints");
  const { busy, run } = useAction();
  const [replying, setReplying] = useState<any>(null);
  const [text, setText] = useState("");
  const [q, setQ] = useState("");
  const [status, setStatus] = useState("");
  if (!data) return <Loading />;
  const countOf = (s: string) => data.filter((k) => k.status === s).length;
  const chips: [string, string][] = [["", `All (${data.length})`], ...["open", "in_progress", "resolved"].filter((s) => countOf(s) > 0).map((s) => [s, `${s.replace("_", " ")} (${countOf(s)})`] as [string, string])];
  const rows = data.filter((k) => (!status || k.status === status) && matchQuery(q, `C-${k.id}`, k.message, k.customer_name, k.category, k.sentiment, k.status, k.phone));
  return (
    <div>
      <PageHeader title="Complaints" subtitle="Logged automatically by the AI agent from WhatsApp chats, with sentiment" />
      <div className="mb-4 flex flex-wrap items-center gap-2">
        <SearchBar value={q} onChange={setQ} placeholder="Shikayat / customer / category dhoondein… · تلاش" className="min-w-[220px] flex-1" />
        <FilterChips value={status} onChange={setStatus} opts={chips} />
      </div>
      <div className="card divide-y divide-slate-100">
        {rows.map((k) => (
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
              {k.customer_id && <Link to={`/customers/${k.customer_id}`} className="inline-flex min-h-9 items-center text-xs text-brand-600 hover:underline sm:min-h-0">{k.customer_name}</Link>}
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
        {data.length > 0 && !rows.length && <Empty>Kuch nahi mila · کچھ نہیں ملا</Empty>}
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

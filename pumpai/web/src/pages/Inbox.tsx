import { useEffect, useRef, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { Bot, User, Send, Wand2, Smartphone, Search, CheckCheck, Headset } from "lucide-react";
import { api, useApi, useLiveEvents } from "../lib/api";
import { Badge, Empty, Loading, Modal, PageHeader, useAction } from "../components/ui";
import { ago, phone, pkr } from "../lib/format";

export default function Inbox() {
  const { id } = useParams();
  const nav = useNavigate();
  const convs = useApi<any[]>("/whatsapp/conversations");
  const thread = useApi<any>(id ? `/whatsapp/conversations/${id}/messages` : null);
  const [filter, setFilter] = useState<"all" | "human" | "unread">("all");
  const [q, setQ] = useState("");
  const [simOpen, setSimOpen] = useState(false);

  useLiveEvents((e) => {
    if (["message", "conversation", "needs_human"].includes(e.type)) {
      convs.reload();
      if (id && String(e.conversation_id) === id) thread.reload();
    }
  });

  const list = (convs.data ?? []).filter((c) =>
    (filter === "all" || (filter === "human" ? c.mode === "human" : c.unread > 0)) &&
    (!q || c.name.toLowerCase().includes(q.toLowerCase()) || c.phone.includes(q)));

  return (
    <div>
      <PageHeader title="WhatsApp Inbox" subtitle="AI answers customers 24/7. Take over any chat with one click."
        actions={<button className="btn-secondary" onClick={() => setSimOpen(true)}><Smartphone size={16} /> WhatsApp simulator</button>} />
      <div className="card grid h-[calc(100vh-11rem)] min-h-[520px] overflow-hidden md:grid-cols-[320px_1fr]">
        <div className={`flex flex-col border-r border-slate-200 ${id ? "hidden md:flex" : "flex"}`}>
          <div className="space-y-2 border-b border-slate-200 p-3">
            <div className="relative"><Search size={15} className="absolute left-2.5 top-2.5 text-slate-400" /><input className="input pl-8" placeholder="Search name or number" value={q} onChange={(e) => setQ(e.target.value)} /></div>
            <div className="flex gap-1 text-xs">
              {(["all", "unread", "human"] as const).map((f) => (
                <button key={f} onClick={() => setFilter(f)} className={`rounded-full px-3 py-1 ${filter === f ? "bg-brand-600 text-white" : "bg-slate-100 text-slate-600"}`}>
                  {f === "human" ? "Needs human" : f[0].toUpperCase() + f.slice(1)}
                </button>
              ))}
            </div>
          </div>
          <div className="flex-1 overflow-y-auto">
            {convs.loading && !convs.data && <Loading />}
            {list.map((c) => (
              <Link key={c.id} to={`/inbox/${c.id}`} className={`flex gap-3 border-b border-slate-100 px-3 py-3 hover:bg-slate-50 ${String(c.id) === id ? "bg-emerald-50" : ""}`}>
                <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-full bg-emerald-100 font-semibold text-emerald-800">{c.name[0]}</div>
                <div className="min-w-0 flex-1">
                  <div className="flex items-center justify-between gap-2">
                    <span className="truncate text-sm font-medium">{c.name}</span>
                    <span className="shrink-0 text-[11px] text-slate-400">{ago(c.last_message_at)}</span>
                  </div>
                  <div className="flex items-center gap-1 text-xs text-slate-500">
                    {c.last_sender === "ai" && <Bot size={12} className="text-violet-500" />}
                    <span className="truncate">{c.last_body}</span>
                  </div>
                  <div className="mt-1 flex gap-1">
                    {c.mode === "human" ? <Badge tone="amber">Human</Badge> : <Badge tone="violet">AI</Badge>}
                    {c.unread > 0 && <Badge tone="green">{c.unread} new</Badge>}
                  </div>
                </div>
              </Link>
            ))}
            {convs.data && !list.length && <Empty>No conversations</Empty>}
          </div>
        </div>
        <div className={`${id ? "flex" : "hidden md:flex"} min-w-0 flex-col`}>
          {id ? <Thread key={id} data={thread.data} reload={() => { thread.reload(); convs.reload(); }} onBack={() => nav("/inbox")} /> :
            <div className="flex flex-1 items-center justify-center bg-wa-bg text-sm text-slate-500">Select a conversation</div>}
        </div>
      </div>
      <Simulator open={simOpen} onClose={() => setSimOpen(false)} onSent={(convId) => { convs.reload(); if (convId) nav(`/inbox/${convId}`); }} />
    </div>
  );
}

function Thread({ data, reload, onBack }: { data: any; reload: () => void; onBack: () => void }) {
  const [text, setText] = useState("");
  const { busy, run } = useAction();
  const bottom = useRef<HTMLDivElement>(null);
  useEffect(() => { bottom.current?.scrollIntoView({ behavior: "smooth" }); }, [data?.messages?.length]);
  if (!data) return <Loading />;
  const { conversation: c, messages } = data;
  const cust = c.customer;

  const send = async () => {
    if (!text.trim()) return;
    await run(() => api(`/whatsapp/conversations/${c.id}/reply`, { body: { text } }));
    setText("");
    reload();
  };
  const toggle = () => run(() => api(`/whatsapp/conversations/${c.id}`, { method: "PATCH", body: { mode: c.mode === "ai" ? "human" : "ai" } }), c.mode === "ai" ? "You took over this chat" : "AI is handling this chat again").then(reload);
  const suggest = () => run(() => api(`/whatsapp/conversations/${c.id}/suggest`, { body: {} })).then((r: any) => r && setText(r.reply));

  return (
    <>
      <div className="flex items-center gap-3 border-b border-slate-200 bg-white px-4 py-2.5">
        <button className="md:hidden text-sm text-brand-600" onClick={onBack}>←</button>
        <div className="min-w-0 flex-1">
          <Link to={`/customers/${cust.id}`} className="font-medium hover:underline">{cust.name}</Link>
          <div className="text-xs text-slate-500">{phone(cust.phone)} · {cust.type} {cust.balance > 0 && `· Khata ${pkr(cust.balance)}`}</div>
        </div>
        <button onClick={toggle} disabled={busy} className={c.mode === "ai" ? "btn-secondary" : "btn-primary"}>
          {c.mode === "ai" ? <><Headset size={15} /> Take over</> : <><Bot size={15} /> Hand back to AI</>}
        </button>
      </div>
      {c.mode === "human" && c.handoff_reason && <div className="bg-amber-50 px-4 py-1.5 text-xs text-amber-800">Handed to human: {c.handoff_reason}</div>}
      <div className="flex-1 space-y-2 overflow-y-auto bg-wa-bg p-4">
        {messages.map((m: any) => (
          <div key={m.id} className={`flex ${m.direction === "out" ? "justify-end" : "justify-start"}`}>
            <div className={`max-w-[80%] rounded-lg px-3 py-2 text-sm shadow-sm ${m.direction === "out" ? "bg-wa-out" : "bg-white"}`}>
              {m.direction === "out" && (
                <div className="mb-0.5 flex items-center gap-1 text-[11px] font-medium text-slate-500">
                  {m.sender === "ai" ? <><Bot size={11} className="text-violet-600" /> AI {m.meta?.engine === "claude" ? "(Claude)" : "(rules)"}</> :
                    m.sender === "agent" ? <><User size={11} /> {m.meta?.by ?? "Agent"}</> : m.sender === "campaign" ? "📣 Campaign" : "⚙ Automation"}
                </div>
              )}
              <div className="whitespace-pre-wrap break-words">{m.body}</div>
              {m.meta?.actions?.length > 0 && (
                <div className="mt-1 flex flex-wrap gap-1">{m.meta.actions.map((a: string) => <Badge key={a} tone="violet">{a}</Badge>)}</div>
              )}
              <div className="mt-0.5 flex items-center justify-end gap-1 text-[10px] text-slate-400">
                {new Date(m.created_at).toLocaleTimeString("en-PK", { hour: "2-digit", minute: "2-digit" })}
                {m.direction === "out" && <CheckCheck size={12} className={m.meta?.delivery?.simulated ? "text-slate-400" : "text-sky-500"} />}
              </div>
            </div>
          </div>
        ))}
        <div ref={bottom} />
      </div>
      <form className="flex items-end gap-2 border-t border-slate-200 bg-white p-3" onSubmit={(e) => { e.preventDefault(); send(); }}>
        <button type="button" onClick={suggest} disabled={busy} className="btn-secondary" title="AI drafts a reply for you"><Wand2 size={16} /></button>
        <textarea rows={1} className="input max-h-32 resize-none" placeholder={c.mode === "ai" ? "AI is replying automatically — type to send a manual message" : "Type a reply…"}
          value={text} onChange={(e) => setText(e.target.value)} onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(); } }} />
        <button className="btn-primary" disabled={busy || !text.trim()}><Send size={16} /></button>
      </form>
    </>
  );
}

const SAMPLES = [
  "Assalam o alaikum, aaj petrol ka rate kya hai?",
  "Mera khata balance kitna hai? payment link bhej dein",
  "Kal subah 1500 litre diesel chahiye farm par, Chak 45 Okara",
  "Kal raat aap ke pump par petrol kam dala gaya, shikayat darj karni hai",
  "Mere loyalty points kitne hain?",
  "Manager se baat karni hai",
];

function Simulator({ open, onClose, onSent }: { open: boolean; onClose: () => void; onSent: (convId?: number) => void }) {
  const [ph, setPh] = useState("03001234599");
  const [name, setName] = useState("Demo Customer");
  const [text, setText] = useState(SAMPLES[0]);
  const [log, setLog] = useState<{ in: string; out?: string; engine?: string }[]>([]);
  const { busy, run } = useAction();
  const send = async (t = text) => {
    const r: any = await run(() => api("/whatsapp/simulate", { body: { phone: ph, name, text: t } }));
    if (!r) return;
    setLog((l) => [...l, { in: t, out: r.reply ?? "(queued for human agent)", engine: r.engine }]);
    onSent(r.message?.conversation_id);
  };
  return (
    <Modal open={open} onClose={onClose} title="WhatsApp simulator" wide>
      <p className="mb-3 text-sm text-slate-600">Messages go through the exact same pipeline as real WhatsApp webhooks: AI agent → tools → reply → CRM records.</p>
      <div className="grid gap-3 sm:grid-cols-2">
        <label><span className="label">Customer number</span><input className="input" value={ph} onChange={(e) => setPh(e.target.value)} /></label>
        <label><span className="label">Profile name</span><input className="input" value={name} onChange={(e) => setName(e.target.value)} /></label>
      </div>
      <div className="mt-3 flex flex-wrap gap-1.5">
        {SAMPLES.map((s) => <button key={s} onClick={() => send(s)} disabled={busy} className="rounded-full bg-emerald-50 px-3 py-1 text-xs text-emerald-800 hover:bg-emerald-100">{s}</button>)}
      </div>
      <div className="mt-3 max-h-72 space-y-2 overflow-y-auto rounded-lg bg-wa-bg p-3">
        {log.map((l, i) => (
          <div key={i} className="space-y-1">
            <div className="ml-auto w-fit max-w-[80%] rounded-lg bg-wa-out px-3 py-1.5 text-sm">{l.in}</div>
            <div className="w-fit max-w-[80%] whitespace-pre-wrap rounded-lg bg-white px-3 py-1.5 text-sm"><span className="text-[11px] text-violet-600">AI ({l.engine ?? "—"})</span><br />{l.out}</div>
          </div>
        ))}
        {!log.length && <div className="text-center text-xs text-slate-500">Send a message as the customer</div>}
      </div>
      <form className="mt-3 flex gap-2" onSubmit={(e) => { e.preventDefault(); send(); setText(""); }}>
        <input className="input" value={text} onChange={(e) => setText(e.target.value)} placeholder="Type as customer (Roman Urdu / Urdu / English)" />
        <button className="btn-primary" disabled={busy || !text}><Send size={15} /> Send</button>
      </form>
    </Modal>
  );
}

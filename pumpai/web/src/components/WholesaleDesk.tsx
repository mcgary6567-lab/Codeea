import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { CalendarClock, CheckCircle2, ClipboardList, HandCoins, Landmark, MessageCircle, Phone, Plus, Truck, Undo2, XCircle, Banknote } from "lucide-react";
import { api, useApi } from "../lib/api";
import { Badge, Empty, Field, Loading, Modal, useAction } from "./ui";
import { PRODUCTS, num, pkr, pkrShort } from "../lib/format";
import { ProofPhotos, ProofThumbs } from "./Capture";
import { AccountPicker, BankLogo, BankNamePicker } from "./BankParts";
import { useAuth } from "../App";

const Ur = ({ children }: { children: React.ReactNode }) => <span lang="ur" dir="rtl" className="font-urdu">{children}</span>;
const today = (n = 0) => new Date(Date.now() + 5 * 3600_000 + n * 86_400_000).toISOString().slice(0, 10);
const nice = (d: string) => (d === today() ? "Today" : d === today(1) ? "Tomorrow" : d === today(-1) ? "Yesterday" : new Date(d + "T00:00:00").toLocaleDateString("en-PK", { weekday: "short", day: "numeric", month: "short" }));
const wa = (p?: string | null) => (p ? `https://wa.me/${p.replace(/\D/g, "")}` : null);
const tel = (p?: string | null) => (p ? `tel:+${p.replace(/\D/g, "")}` : null);

/** Choose a client inside a form when the form was opened from a list rather than from the client's page. */
function ClientSelect({ value, onChange }: { value: string; onChange: (v: string) => void }) {
  const { data } = useApi<any[]>("/wholesale/clients?q=");
  return (
    <Field label="Client · کلائنٹ"><select className="input" required value={value} onChange={(e) => onChange(e.target.value)}>
      <option value="">— Choose client —</option>{(data ?? []).filter((c) => c.active).map((c) => <option key={c.id} value={c.id}>{c.name}{c.city ? ` · ${c.city}` : ""}</option>)}
    </select></Field>
  );
}

/* ================= Forms ================= */
export function OrderForm({ client, onClose, onDone }: { client?: any; onClose: () => void; onDone: () => void }) {
  const [f, setF] = useState({ client_id: client ? String(client.id) : "", product: "HSD", litres: "", needed_on: today(1), location: client?.city ?? "", note: "" });
  const { busy, run } = useAction();
  return (
    <Modal open onClose={onClose} title={`New order${client ? ` — ${client.name}` : ""} · آرڈر`}>
      <form className="space-y-3" onSubmit={async (e) => {
        e.preventDefault();
        if (await run(() => api(`/wholesale/clients/${f.client_id}/orders`, { body: { product: f.product, litres: Number(f.litres), needed_on: f.needed_on, location: f.location || null, note: f.note || null } }), "Order booked")) onDone();
      }}>
        {!client && <ClientSelect value={f.client_id} onChange={(client_id) => setF({ ...f, client_id })} />}
        <div className="grid grid-cols-3 gap-2">{Object.entries(PRODUCTS).map(([k, v]) => (
          <button type="button" key={k} onClick={() => setF({ ...f, product: k })} className={`rounded-xl py-2.5 font-semibold ${f.product === k ? "bg-brand-600 text-white" : "bg-slate-100 text-slate-700"}`}>{v}</button>))}</div>
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
          <Field label="Litres"><input className="input py-2.5 text-xl" type="number" min={1} required value={f.litres} onChange={(e) => setF({ ...f, litres: e.target.value })} /></Field>
          <Field label="Deliver on"><input className="input" type="date" min={today()} required value={f.needed_on} onChange={(e) => setF({ ...f, needed_on: e.target.value })} /></Field>
          <Field label="Drop location"><input className="input" value={f.location} onChange={(e) => setF({ ...f, location: e.target.value })} /></Field>
          <Field label="Note"><input className="input" placeholder="e.g. gate 2, call before coming" value={f.note} onChange={(e) => setF({ ...f, note: e.target.value })} /></Field>
        </div>
        <div className="flex gap-2">{[0, 1, 2].map((n) => <button type="button" key={n} className="btn-secondary min-h-9 !py-1 text-xs sm:min-h-0" onClick={() => setF({ ...f, needed_on: today(n) })}>{nice(today(n))}</button>)}</div>
        <div className="flex justify-end gap-2"><button type="button" className="btn-secondary" onClick={onClose}>Cancel</button><button className="btn-primary" disabled={busy}>Book order</button></div>
      </form>
    </Modal>
  );
}

export function PromiseForm({ client, onClose, onDone }: { client?: any; onClose: () => void; onDone: () => void }) {
  const [f, setF] = useState({ client_id: client ? String(client.id) : "", amount: client?.summary?.due > 0 ? String(Math.round(client.summary.due)) : "", promised_on: today(1), note: "" });
  const { busy, run } = useAction();
  return (
    <Modal open onClose={onClose} title={`Payment promise${client ? ` — ${client.name}` : ""} · وعدہ`}>
      <form className="space-y-3" onSubmit={async (e) => {
        e.preventDefault();
        if (await run(() => api(`/wholesale/clients/${f.client_id}/promises`, { body: { amount: Number(f.amount), promised_on: f.promised_on, note: f.note || null } }), "Promise noted — you'll be reminded on the day")) onDone();
      }}>
        <p className="text-sm text-slate-600">When the client says "I will pay on …", note it here. On that day it shows in the call list; if the money does not come it is marked <b>broken</b>.</p>
        {!client && <ClientSelect value={f.client_id} onChange={(client_id) => setF({ ...f, client_id })} />}
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
          <Field label="Amount promised (Rs)"><input className="input py-2.5 text-xl" type="number" min={1} required value={f.amount} onChange={(e) => setF({ ...f, amount: e.target.value })} /></Field>
          <Field label="Will pay on"><input className="input" type="date" min={today()} required value={f.promised_on} onChange={(e) => setF({ ...f, promised_on: e.target.value })} /></Field>
        </div>
        <Field label="Note"><input className="input" placeholder="e.g. said on the phone, will send by IBFT" value={f.note} onChange={(e) => setF({ ...f, note: e.target.value })} /></Field>
        <div className="flex justify-end gap-2"><button type="button" className="btn-secondary" onClick={onClose}>Cancel</button><button className="btn-primary" disabled={busy}>Save promise</button></div>
      </form>
    </Modal>
  );
}

export function ChequeForm({ client, onClose, onDone }: { client?: any; onClose: () => void; onDone: () => void }) {
  const [f, setF] = useState({ client_id: client ? String(client.id) : "", amount: "", bank: "", cheque_no: "", cheque_date: today(), note: "" });
  const [photos, setPhotos] = useState<number[]>([]);
  const { busy, run } = useAction();
  return (
    <Modal open onClose={onClose} title={`Cheque received${client ? ` — ${client.name}` : ""} · چیک`}>
      <form className="space-y-3" onSubmit={async (e) => {
        e.preventDefault();
        if (await run(() => api(`/wholesale/clients/${f.client_id}/cheques`, { body: { amount: Number(f.amount), bank: f.bank, cheque_no: f.cheque_no, cheque_date: f.cheque_date, note: f.note || null, photo_ids: photos } }),
          f.cheque_date > today() ? "Post-dated cheque saved — you'll be told when to deposit it" : "Cheque saved — deposit it and mark cleared")) onDone();
      }}>
        <p className="rounded-lg bg-sky-50 p-2 text-sm text-sky-900">The cheque is <b>not counted as payment</b> until it clears. Then the payment is added by itself.</p>
        {!client && <ClientSelect value={f.client_id} onChange={(client_id) => setF({ ...f, client_id })} />}
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
          <Field label="Amount (Rs)"><input className="input py-2.5 text-xl" type="number" min={1} required value={f.amount} onChange={(e) => setF({ ...f, amount: e.target.value })} /></Field>
          <Field label="Cheque date"><input className="input" type="date" required value={f.cheque_date} onChange={(e) => setF({ ...f, cheque_date: e.target.value })} /></Field>
          <Field label="Cheque no."><input className="input" required value={f.cheque_no} onChange={(e) => setF({ ...f, cheque_no: e.target.value })} /></Field>
          <Field label="Note"><input className="input" value={f.note} onChange={(e) => setF({ ...f, note: e.target.value })} /></Field>
        </div>
        <div><span className="label">Client's bank (on the cheque)</span><BankNamePicker required value={f.bank} onChange={(bank) => setF({ ...f, bank })} /></div>
        <ProofPhotos value={photos} onChange={setPhotos} required hint="photo of the cheque (front)" />
        <div className="flex justify-end gap-2"><button type="button" className="btn-secondary" onClick={onClose}>Cancel</button><button className="btn-primary" disabled={busy || !photos.length}>Save cheque</button></div>
      </form>
    </Modal>
  );
}

/* ================= Order book tab ================= */
export function OrdersTab({ onTrip }: { onTrip: () => void }) {
  const { can } = useAuth();
  const nav = useNavigate();
  const { data, reload } = useApi<any>("/wholesale/orders");
  const [form, setForm] = useState(false);
  const { busy, run } = useAction();
  if (!data) return <Loading />;
  const manage = can("wholesale.manage");
  const groups = [
    { k: "late", label: "Late — not delivered", ur: "دیر ہو گئی", rows: data.open.filter((o: any) => o.late), tone: "border-l-red-500" },
    { k: "today", label: "Deliver today", ur: "آج", rows: data.open.filter((o: any) => o.today), tone: "border-l-amber-500" },
    { k: "next", label: "Coming days", ur: "اگلے دن", rows: data.open.filter((o: any) => !o.late && !o.today), tone: "border-l-sky-500" },
  ];
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap gap-2">
        {manage && <button className="btn-primary" onClick={() => setForm(true)}><Plus size={15} /> New order · آرڈر</button>}
        {manage && <button className="btn-secondary" onClick={onTrip}><Truck size={15} /> Tanker trip from orders</button>}
      </div>
      <ClientRequests />

      <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
        {Object.entries(PRODUCTS).map(([p, name]) => {
          const need = data.need_3_days[p] ?? 0, stock = data.stock[p] ?? 0;
          const short = need > stock;
          return (
            <div key={p} className={`card p-4 ${short ? "ring-2 ring-red-300" : ""}`}>
              <div className="text-xs font-medium uppercase tracking-wide text-slate-500">{name} · next 3 days</div>
              <div className="mt-1 text-2xl font-bold tabular-nums">{num(need)} L <span className="text-sm font-normal text-slate-500">booked</span></div>
              <div className={`text-sm ${short ? "font-semibold text-red-600" : "text-slate-500"}`}>{num(stock)} L in tanks{short ? " — order a tanker" : ""}</div>
            </div>
          );
        })}
      </div>
      {groups.map((g) => g.rows.length > 0 && (
        <div key={g.k} className="card overflow-hidden">
          <h2 className="border-b border-slate-100 px-4 py-2.5 font-semibold">{g.label} · <Ur>{g.ur}</Ur> <span className="text-sm font-normal text-slate-500">({g.rows.length})</span></h2>
          <ul className="divide-y divide-slate-100">
            {g.rows.map((o: any) => (
              <li key={o.id} className={`flex flex-wrap items-center gap-3 border-l-4 px-4 py-3 ${g.tone}`}>
                <div className="min-w-0 flex-1 basis-60">
                  <div className="font-semibold">{o.client_name} <span className="font-normal text-slate-500">· {nice(o.needed_on)}</span></div>
                  <div className="text-sm text-slate-600"><b>{num(o.litres)} L</b> {PRODUCTS[o.product]}{o.location ? ` · ${o.location}` : ""}{o.note ? ` · ${o.note}` : ""}</div>
                </div>
                {manage && <div className="flex gap-2">
                  <button className="btn-primary !py-1.5 text-sm" onClick={() => nav(`/wholesale/${o.client_id}?do=supply&order=${o.id}`)}><Truck size={14} /> Supply</button>
                  {o.phone && <a className="btn-secondary !px-2.5 !py-1.5" href={tel(o.phone)!} aria-label="Call"><Phone size={14} /></a>}
                  <button className="btn-secondary !px-2.5 !py-1.5 text-red-600" disabled={busy} aria-label="Cancel order" onClick={async () => {
                    const reason = prompt("Why is this order cancelled?"); if (!reason) return;
                    if (await run(() => api(`/wholesale/orders/${o.id}`, { method: "PATCH", body: { cancel: reason } }), "Order cancelled")) reload();
                  }}><XCircle size={14} /></button>
                </div>}
              </li>
            ))}
          </ul>
        </div>
      ))}
      {!data.open.length && <div className="card"><Empty>No open orders. When a client calls to book fuel, add it here so nothing is forgotten.</Empty></div>}
      {data.done.length > 0 && <details className="card p-4 text-sm"><summary className="cursor-pointer font-semibold">Recently closed ({data.done.length})</summary>
        <ul className="mt-2 divide-y divide-slate-100">{data.done.map((o: any) => (
          <li key={o.id} className="flex justify-between py-1.5"><span>{o.client_name} · {num(o.litres)} L {PRODUCTS[o.product]} · {o.needed_on}</span>
            <Badge tone={o.status === "done" ? "green" : "slate"}>{o.status === "done" ? "Delivered" : "Cancelled"}</Badge></li>))}</ul></details>}
      {form && <OrderForm onClose={() => setForm(false)} onDone={() => { setForm(false); reload(); }} />}
    </div>
  );
}

/** Supply requests clients placed from their own portal link — approve (pings them on WhatsApp) or decline. */
function ClientRequests() {
  const { can } = useAuth();
  const { data, reload } = useApi<any>("/wholesale/requests");
  const { busy, run } = useAction();
  const manage = can("wholesale.manage");
  if (!data || (!data.pending.length && !data.recent.length)) return null;
  const decide = async (id: number, decision: "approve" | "reject") => {
    const reply = decision === "reject" ? (prompt("Message to the client (optional — reason):") ?? "") : (prompt("Message to the client (optional):") ?? "");
    if (await run(() => api(`/wholesale/requests/${id}/${decision}`, { body: { reply: reply || null } }), decision === "approve" ? "Approved — client told on WhatsApp" : "Declined — client told")) reload();
  };
  return (
    <div className="card overflow-hidden ring-1 ring-brand-200">
      <h2 className="flex items-center gap-2 border-b border-slate-100 bg-brand-50 px-4 py-2.5 font-semibold">📥 Order requests from clients · <Ur>کلائنٹ کے آرڈر</Ur>
        {data.pending.length > 0 && <span className="rounded-full bg-amber-500 px-2 py-0.5 text-xs font-semibold text-white">{data.pending.length} new</span>}</h2>
      {data.pending.length === 0 ? <Empty>No pending requests. Clients can send orders from their khata link.</Empty>
        : <ul className="divide-y divide-slate-100">
          {data.pending.map((r: any) => (
            <li key={r.id} className="flex flex-wrap items-center gap-3 border-l-4 border-l-amber-500 px-4 py-3">
              <div className="min-w-0 flex-1 basis-60">
                <div className="font-semibold">{r.client_name} <span className="font-normal text-slate-500">· wants {nice(r.want_date)}</span></div>
                <div className="text-sm text-slate-600"><b>{num(r.litres)} L</b> {PRODUCTS[r.product]}{r.city ? ` · ${r.city}` : ""}{r.note ? ` · ${r.note}` : ""}</div>
              </div>
              {manage ? <div className="flex gap-2">
                <button className="btn-primary !py-1.5 text-sm" disabled={busy} onClick={() => decide(r.id, "approve")}><CheckCircle2 size={14} /> Approve</button>
                <button className="btn-secondary !py-1.5 text-sm text-red-600" disabled={busy} onClick={() => decide(r.id, "reject")}><XCircle size={14} /> Decline</button>
                {r.phone && <a className="btn-secondary !px-2.5 !py-1.5" href={tel(r.phone)!} aria-label="Call"><Phone size={14} /></a>}
              </div> : <Badge tone="amber">Pending</Badge>}
            </li>
          ))}
        </ul>}
      {data.recent.length > 0 && <details className="p-4 text-sm"><summary className="cursor-pointer font-semibold">Recently decided ({data.recent.length})</summary>
        <ul className="mt-2 divide-y divide-slate-100">{data.recent.map((r: any) => (
          <li key={r.id} className="flex flex-wrap justify-between gap-2 py-1.5"><span>{r.client_name} · {num(r.litres)} L {PRODUCTS[r.product]} · {nice(r.want_date)}{r.reply ? ` · 💬 ${r.reply}` : ""}</span>
            <Badge tone={r.status === "approved" ? "green" : r.status === "rejected" ? "red" : "slate"}>{r.status === "approved" ? "Approved" : r.status === "rejected" ? "Declined" : r.status}</Badge></li>))}</ul></details>}
    </div>
  );
}

/* ================= Recovery tab: call list, promises, cheques ================= */
const CHQ: Record<string, { label: string; tone: string }> = {
  in_hand: { label: "In hand", tone: "amber" }, deposited: { label: "Deposited", tone: "blue" }, cleared: { label: "Cleared", tone: "green" },
  bounced: { label: "Bounced", tone: "red" }, returned: { label: "Given back", tone: "slate" },
};
const PROM: Record<string, { label: string; tone: string }> = { open: { label: "Coming", tone: "blue" }, today: { label: "Due today", tone: "amber" }, kept: { label: "Kept ✓", tone: "green" }, broken: { label: "Broken", tone: "red" } };

export function CollectTab() {
  const { can } = useAuth();
  const nav = useNavigate();
  const { data, reload } = useApi<any>("/wholesale/collect");
  const [form, setForm] = useState<null | "promise" | "cheque" | { promiseFor: any }>(null);
  const [act, setAct] = useState<null | { q: any; kind: "deposit" | "clear" | "bounce" }>(null);
  const { busy, run } = useAction();
  if (!data) return <Loading />;
  const manage = can("wholesale.manage");
  const t = data.totals;
  const done = () => { setForm(null); setAct(null); reload(); };
  return (
    <div className="space-y-5">
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <Tile icon={Phone} label="To call today" value={String(t.to_call)} sub={`${pkrShort(t.to_collect)} due with them`} tone="text-red-600" />
        <Tile icon={HandCoins} label="Promised today" value={pkrShort(t.promised_today)} sub="money that should come today" tone="text-amber-700" />
        <Tile icon={ClipboardList} label="Cheques in hand" value={pkrShort(t.cheques_in_hand)} sub={t.cheques_to_deposit ? `${t.cheques_to_deposit} ready to deposit` : "none ready yet"} />
        <Tile icon={Landmark} label="In bank, not cleared" value={pkrShort(t.cheques_deposited)} sub="mark cleared or bounced" />
      </div>
      {manage && <div className="flex flex-wrap gap-2">
        <button className="btn-primary" onClick={() => setForm("cheque")}><Plus size={15} /> Cheque received · چیک</button>
        <button className="btn-secondary" onClick={() => setForm("promise")}><CalendarClock size={15} /> Payment promise · وعدہ</button>
      </div>}

      <div className="card overflow-hidden">
        <h2 className="border-b border-slate-100 px-4 py-2.5 font-semibold">Call list — who to ask for money today · <Ur>آج کس سے رقم مانگنی ہے</Ur></h2>
        {data.calls.length ? <ul className="divide-y divide-slate-100">{data.calls.map((c: any, i: number) => (
          <li key={c.client_id} className="flex flex-wrap items-center gap-3 px-4 py-3">
            <span className={`flex h-8 w-8 shrink-0 items-center justify-center rounded-full text-sm font-bold ${i < 3 ? "bg-red-100 text-red-700" : "bg-slate-100 text-slate-600"}`}>{i + 1}</span>
            <div className="min-w-0 flex-1 basis-56">
              <button className="font-semibold hover:underline" onClick={() => nav(`/wholesale/${c.client_id}`)}>{c.name}</button>
              <span className="ml-2 font-bold tabular-nums text-amber-700">{pkr(c.due)}</span>
              <div className="text-sm text-slate-600">{c.reasons.join(" · ")}</div>
              {(c.cheques_in_hand > 0 || c.promise) && <div className="text-xs text-slate-500">{c.cheques_in_hand > 0 && `Cheques in hand ${pkr(c.cheques_in_hand)}`}{c.promise && ` · promised ${pkr(c.promise.amount)} on ${c.promise.on}`}</div>}
            </div>
            <div className="flex gap-2">
              {c.phone && <a className="btn-secondary !py-1.5 text-sm" href={tel(c.phone)!}><Phone size={14} /> Call</a>}
              {c.phone && <a className="btn-secondary !py-1.5 text-sm" target="_blank" rel="noreferrer" href={`${wa(c.phone)}?text=${encodeURIComponent(`Assalam o Alaikum ${c.name}, aap ka baqaya ${pkr(c.due)} hai. Meharbani kar ke payment ka bata dein. Shukriya.`)}`}><MessageCircle size={14} /> WhatsApp</a>}
              {manage && <button className="btn-secondary !py-1.5 text-sm" onClick={() => setForm({ promiseFor: { id: c.client_id, name: c.name, summary: { due: c.due } } })}><CalendarClock size={14} /> Promise</button>}
            </div>
          </li>))}</ul> : <Empty>Nobody to chase today 🎉</Empty>}
      </div>

      <div className="grid grid-cols-1 gap-5 xl:grid-cols-2">
        <div className="card overflow-hidden">
          <h2 className="border-b border-slate-100 px-4 py-2.5 font-semibold">Cheque register · <Ur>چیک رجسٹر</Ur></h2>
          {data.cheques.length ? <ul className="divide-y divide-slate-100">{data.cheques.map((q: any) => {
            const ready = q.status === "in_hand" && q.cheque_date <= today();
            return (
              <li key={q.id} className="flex flex-wrap items-center gap-3 px-4 py-3">
                <BankLogo name={q.bank} size={36} />
                <div className="min-w-0 flex-1 basis-48">
                  <div className="font-semibold">{q.client_name} <span className="tabular-nums">{pkr(q.amount)}</span> <Badge tone={CHQ[q.status].tone}>{CHQ[q.status].label}</Badge></div>
                  <div className="text-xs text-slate-500">{q.bank} · #{q.cheque_no} · dated <b className={ready ? "text-amber-700" : ""}>{q.cheque_date}</b>{q.bounce_reason ? ` · ${q.bounce_reason}` : ""} <ProofThumbs ids={q.proof_ids} /></div>
                </div>
                {manage && <div className="flex flex-wrap gap-1.5">
                  {q.status === "in_hand" && <button className="btn-secondary min-h-9 !py-1 text-xs sm:min-h-0" disabled={!ready} title={ready ? "" : `Dated ${q.cheque_date}`} onClick={() => setAct({ q, kind: "deposit" })}><Landmark size={13} /> Deposit</button>}
                  {(q.status === "in_hand" || q.status === "deposited") && <button className="btn-primary min-h-9 !py-1 text-xs sm:min-h-0" onClick={() => setAct({ q, kind: "clear" })}><CheckCircle2 size={13} /> Cleared</button>}
                  {["in_hand", "deposited", "cleared"].includes(q.status) && <button className="btn-secondary min-h-9 !py-1 text-xs text-red-600 sm:min-h-0" onClick={() => setAct({ q, kind: "bounce" })}>Bounced</button>}
                  {q.status === "in_hand" && <button className="btn-secondary min-h-9 !px-2 !py-1 text-xs sm:min-h-0" title="Give back to the client" disabled={busy} onClick={async () => {
                    const reason = prompt("Why is the cheque given back? (e.g. paid in cash instead)"); if (!reason) return;
                    if (await run(() => api(`/wholesale/cheques/${q.id}/return`, { body: { reason } }), "Cheque marked as given back")) reload();
                  }}><Undo2 size={13} /></button>}
                </div>}
              </li>
            );
          })}</ul> : <Empty>No cheques in the register</Empty>}
        </div>

        <div className="card overflow-hidden">
          <h2 className="border-b border-slate-100 px-4 py-2.5 font-semibold">Payment promises · <Ur>ادائیگی کے وعدے</Ur></h2>
          {data.promises.length ? <ul className="divide-y divide-slate-100">{data.promises.map((p: any) => (
            <li key={p.id} className="flex flex-wrap items-center gap-3 px-4 py-2.5 text-sm">
              <div className="min-w-0 flex-1"><b>{p.client_name}</b> · {pkr(p.amount)} on {nice(p.promised_on)}
                <div className="text-xs text-slate-500">{p.paid > 0 ? `Received ${pkr(p.paid)} since` : "Nothing received yet"}{p.note ? ` · ${p.note}` : ""}</div></div>
              <Badge tone={PROM[p.state].tone}>{PROM[p.state].label}</Badge>
              {manage && p.state !== "kept" && <button className="text-xs text-slate-400 hover:text-red-600" disabled={busy} onClick={async () => { if (confirm("Remove this promise?") && await run(() => api(`/wholesale/promises/${p.id}`, { method: "DELETE" }), "Removed")) reload(); }}>remove</button>}
            </li>))}</ul> : <Empty>No promises noted</Empty>}
        </div>
      </div>

      {form === "promise" && <PromiseForm onClose={() => setForm(null)} onDone={done} />}
      {form === "cheque" && <ChequeForm onClose={() => setForm(null)} onDone={done} />}
      {form && typeof form === "object" && <PromiseForm client={form.promiseFor} onClose={() => setForm(null)} onDone={done} />}
      {act && <ChequeAction q={act.q} kind={act.kind} onClose={() => setAct(null)} onDone={done} />}
    </div>
  );
}

const Tile = ({ icon: Icon, label, value, sub, tone }: { icon: any; label: string; value: string; sub?: string; tone?: string }) => (
  <div className="card p-4">
    <div className="flex items-center gap-1.5 text-xs font-medium uppercase tracking-wide text-slate-500"><Icon size={14} /> {label}</div>
    <div className={`mt-1 text-2xl font-bold tabular-nums ${tone ?? "text-slate-900"}`}>{value}</div>
    {sub && <div className="text-xs text-slate-500">{sub}</div>}
  </div>
);

export function ChequeAction({ q, kind, onClose, onDone }: { q: any; kind: "deposit" | "clear" | "bounce"; onClose: () => void; onDone: () => void }) {
  const [account, setAccount] = useState<number | null>(q.account_id ?? null);
  const [f, setF] = useState({ reason: "Insufficient funds", charge: "" });
  const { busy, run } = useAction();
  const title = { deposit: "Deposit cheque in bank", clear: "Cheque cleared — money received", bounce: "Cheque bounced" }[kind];
  return (
    <Modal open onClose={onClose} title={title}>
      <form className="space-y-3" onSubmit={async (e) => {
        e.preventDefault();
        const body = kind === "bounce" ? { reason: f.reason || null, charge: Number(f.charge) || 0 } : { account_id: account };
        if (await run(() => api(`/wholesale/cheques/${q.id}/${kind}`, { body }), (r: any) => kind === "deposit" ? "Marked deposited" : `${kind === "clear" ? "Payment added" : "Bounce recorded — client told on WhatsApp"}. Due now ${pkr(r.due_after)}`)) onDone();
      }}>
        <div className="flex items-center gap-3 rounded-xl bg-slate-50 p-3"><BankLogo name={q.bank} size={40} />
          <div><b>{q.client_name}</b> · {pkr(q.amount)}<div className="text-xs text-slate-500">{q.bank} · #{q.cheque_no} · dated {q.cheque_date}</div></div></div>
        {kind !== "bounce" && <AccountPicker method="cheque" label="Our bank account · ہمارا بینک" value={account} onChange={setAccount} />}
        {kind === "clear" && <p className="text-sm text-slate-600">The payment of {pkr(q.amount)} is added to the client's account, dated today.</p>}
        {kind === "bounce" && <>
          {q.status === "cleared" && <p className="rounded-lg bg-red-50 p-2 text-sm text-red-700">This cheque was marked cleared — its payment will be taken back off the account.</p>}
          <Field label="Reason"><select className="input" value={f.reason} onChange={(e) => setF({ ...f, reason: e.target.value })}>
            {["Insufficient funds", "Signature mismatch", "Payment stopped", "Account closed", "Date / amount error", "Other"].map((r) => <option key={r}>{r}</option>)}</select></Field>
          <Field label="Bank charges to add to the client (Rs, optional)"><input className="input" type="number" min={0} value={f.charge} onChange={(e) => setF({ ...f, charge: e.target.value })} /></Field>
        </>}
        <div className="flex justify-end gap-2"><button type="button" className="btn-secondary" onClick={onClose}>Cancel</button>
          <button className={kind === "bounce" ? "btn-primary !bg-red-600" : "btn-primary"} disabled={busy}>{kind === "deposit" ? "Deposited" : kind === "clear" ? "Yes, cleared" : "Record bounce"}</button></div>
      </form>
    </Modal>
  );
}

/* ================= Client page card ================= */
export function ClientDeskCard({ client, onAction, refreshKey, onChanged }: { client: any; onAction: (a: "order" | "promise" | "cheque" | "supply-order", orderId?: number) => void; refreshKey: number; onChanged: () => void }) {
  const { can } = useAuth();
  const { data, reload } = useApi<any>(`/wholesale/clients/${client.id}/desk?k=${refreshKey}`);
  const [act, setAct] = useState<null | { q: any; kind: "deposit" | "clear" | "bounce" }>(null);
  if (!data) return null;
  const manage = can("wholesale.manage");
  const live = data.cheques.filter((q: any) => ["in_hand", "deposited"].includes(q.status));
  const proms = data.promises.filter((p: any) => p.state !== "kept");
  return (
    <div className="card p-4 print:hidden">
      <div className="mb-3 flex flex-wrap items-center gap-2">
        <h2 className="mr-auto font-semibold">Orders, promises & cheques</h2>
        {manage && <>
          <button className="btn-secondary min-h-10 !py-1.5 text-sm" onClick={() => onAction("order")}><ClipboardList size={14} /> New order</button>
          <button className="btn-secondary min-h-10 !py-1.5 text-sm" onClick={() => onAction("promise")}><CalendarClock size={14} /> Promise</button>
          <button className="btn-secondary min-h-10 !py-1.5 text-sm" onClick={() => onAction("cheque")}><Banknote size={14} /> Cheque</button>
        </>}
      </div>
      <div className="grid grid-cols-1 gap-4 text-sm md:grid-cols-3">
        <div><div className="mb-1 text-xs font-semibold uppercase tracking-wide text-slate-500">Open orders</div>
          {data.orders.length ? data.orders.map((o: any) => (
            <div key={o.id} className="flex items-center justify-between gap-2 border-b border-slate-100 py-1.5">
              <span><b>{num(o.litres)} L</b> {PRODUCTS[o.product]} · {nice(o.needed_on)}</span>
              {manage && <button className="-my-1.5 min-h-9 shrink-0 px-1 text-brand-700 hover:underline" onClick={() => onAction("supply-order", o.id)}>Supply</button>}
            </div>)) : <p className="text-slate-400">None</p>}</div>
        <div><div className="mb-1 text-xs font-semibold uppercase tracking-wide text-slate-500">Promises</div>
          {proms.length ? proms.map((p: any) => (
            <div key={p.id} className="flex items-center justify-between gap-2 border-b border-slate-100 py-1.5"><span>{pkr(p.amount)} · {nice(p.promised_on)}</span><Badge tone={PROM[p.state].tone}>{PROM[p.state].label}</Badge></div>)) : <p className="text-slate-400">None</p>}</div>
        <div><div className="mb-1 text-xs font-semibold uppercase tracking-wide text-slate-500">Cheques not cleared</div>
          {live.length ? live.map((q: any) => (
            <div key={q.id} className="flex items-center gap-2 border-b border-slate-100 py-1.5"><BankLogo name={q.bank} size={24} />
              <span className="flex-1">{pkr(q.amount)} · {q.cheque_date}</span><Badge tone={CHQ[q.status].tone}>{CHQ[q.status].label}</Badge>
              {manage && <button className="-my-1.5 min-h-9 shrink-0 px-1 text-brand-700 hover:underline" onClick={() => setAct({ q: { ...q, client_name: client.name }, kind: "clear" })}>Cleared</button>}
            </div>)) : <p className="text-slate-400">None</p>}</div>
      </div>
      {act && <ChequeAction q={act.q} kind={act.kind} onClose={() => setAct(null)} onDone={() => { setAct(null); reload(); onChanged(); }} />}
    </div>
  );
}

import { useState } from "react";
import { Plus } from "lucide-react";
import { api, useApi } from "../lib/api";
import { Badge, Empty, Field, Loading, Modal, PageHeader, useAction } from "../components/ui";

const SERVICE: Record<string, string> = { car_wash: "🚿 Car wash", oil_change: "🛢️ Oil change", tyre: "🛞 Tyre / puncture", service: "🔧 Service / tuning" };

/** Car wash / oil change / tyre bookings (customers also book themselves on WhatsApp). */
export default function Bookings() {
  const { data, reload } = useApi<any[]>("/bookings", 60_000);
  const [adding, setAdding] = useState(false);
  const { run } = useAction();
  if (!data) return <Loading />;
  const day = (iso: string) => new Date(iso).toLocaleDateString("en-PK", { timeZone: "Asia/Karachi", weekday: "long", day: "numeric", month: "short" });
  const groups = data.reduce((a: Record<string, any[]>, b) => { (a[day(b.at)] ??= []).push(b); return a; }, {});
  const act = (id: number, s: string, msg: string) => run(() => api(`/bookings/${id}/${s}`, { body: {} }), msg).then(reload);
  return (
    <div className="space-y-5">
      <PageHeader title="Bookings · بکنگ" subtitle="Car wash, oil change and tyre slots. Customers can book on WhatsApp and get a reminder an hour before."
        actions={<button className="btn-primary" onClick={() => setAdding(true)}><Plus size={15} /> New booking</button>} />
      {Object.entries(groups).map(([d, list]) => (
        <div key={d} className="card p-4">
          <h2 className="mb-2 font-semibold">{d}</h2>
          {list.map((b) => (
            <div key={b.id} className="flex flex-wrap items-center gap-3 border-b border-slate-100 py-2">
              <span className="w-16 text-lg font-bold tabular-nums">{new Date(b.at).toLocaleTimeString("en-PK", { timeZone: "Asia/Karachi", hour: "2-digit", minute: "2-digit" })}</span>
              <span className="min-w-[180px] flex-1"><b>{SERVICE[b.service]}</b> · {b.customer_name}<span className="block text-xs text-slate-500">+{b.phone}{b.vehicle_no ? ` · ${b.vehicle_no}` : ""} · {b.station_name.replace("Al-Madina ", "")}{b.created_by === "WhatsApp AI" ? " · booked on WhatsApp" : ""}</span></span>
              {b.status === "booked" ? <span className="flex gap-1">
                <button className="rounded-lg bg-emerald-600 px-3 py-2 text-sm font-semibold text-white" onClick={() => act(b.id, "done", "Done ✓")}>Done</button>
                <button className="btn-secondary !py-2 text-sm" onClick={() => act(b.id, "no_show", "Marked no-show")}>No-show</button>
                <button className="btn-secondary !py-2 text-sm" onClick={() => act(b.id, "cancelled", "Cancelled")}>Cancel</button>
              </span> : <Badge tone={b.status === "done" ? "green" : "slate"}>{b.status.replace("_", "-")}</Badge>}
            </div>
          ))}
        </div>
      ))}
      {!data.length && <Empty>No bookings yet.</Empty>}
      {adding && <BookingForm onClose={() => setAdding(false)} onDone={() => { setAdding(false); reload(); }} />}
    </div>
  );
}

function BookingForm({ onClose, onDone }: { onClose: () => void; onDone: () => void }) {
  const soon = new Date(Date.now() + 5 * 3600_000 + 3600_000);
  const [f, setF] = useState({ phone: "", name: "", service: "car_wash", day: soon.toISOString().slice(0, 10), time: `${String(soon.getUTCHours()).padStart(2, "0")}:00`, vehicle_no: "" });
  const { busy, run } = useAction();
  return (
    <Modal open onClose={onClose} title="New booking">
      <form className="space-y-3" onSubmit={async (e) => {
        e.preventDefault();
        if (await run(() => api("/bookings", { body: { phone: f.phone, name: f.name || undefined, service: f.service, at: `${f.day}T${f.time}:00+05:00`, vehicle_no: f.vehicle_no || null } }), "Booked — customer told on WhatsApp")) onDone();
      }}>
        <div className="grid grid-cols-2 gap-2">{Object.entries(SERVICE).map(([k, l]) => <button type="button" key={k} onClick={() => setF({ ...f, service: k })} className={`rounded-xl border-2 p-3 text-left font-medium ${f.service === k ? "border-brand-600 bg-emerald-50" : "border-slate-200"}`}>{l}</button>)}</div>
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
          <Field label="Customer WhatsApp"><input className="input" required placeholder="03xx xxxxxxx" value={f.phone} onChange={(e) => setF({ ...f, phone: e.target.value })} /></Field>
          <Field label="Name"><input className="input" value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} /></Field>
          <Field label="Day"><input className="input" type="date" required value={f.day} onChange={(e) => setF({ ...f, day: e.target.value })} /></Field>
          <Field label="Time"><input className="input" type="time" required value={f.time} onChange={(e) => setF({ ...f, time: e.target.value })} /></Field>
          <Field label="Vehicle no."><input className="input uppercase" value={f.vehicle_no} onChange={(e) => setF({ ...f, vehicle_no: e.target.value })} /></Field>
        </div>
        <div className="flex justify-end gap-2"><button type="button" className="btn-secondary" onClick={onClose}>Cancel</button><button className="btn-primary" disabled={busy}>Book</button></div>
      </form>
    </Modal>
  );
}

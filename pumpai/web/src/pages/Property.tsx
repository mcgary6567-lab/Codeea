import { useState } from "react";
import { Building2, Plus, Home, Wallet, Pencil } from "lucide-react";
import { api, useApi } from "../lib/api";
import { Badge, Empty, Field, Loading, Modal, PageHeader, Stat, useAction } from "../components/ui";
import { pkr, pkrShort } from "../lib/format";
import { useAuth } from "../App";
import { AccountPicker } from "../components/BankParts";

const KINDS = [["shop", "Shop / دکان"], ["hotel", "Hotel / ہوٹل"], ["tyre", "Tyre shop"], ["service", "Service bay"], ["atm", "ATM"], ["tuckshop", "Tuck shop"], ["office", "Office"], ["other", "Other"]] as const;
const kindLabel = (k: string) => KINDS.find(([v]) => v === k)?.[1] ?? k;
const thisMonth = () => new Date(Date.now() + 5 * 3600_000).toISOString().slice(0, 7);

export default function Property() {
  const { can } = useAuth();
  const [month, setMonth] = useState(thisMonth());
  const { data, reload } = useApi<any>(`/property?month=${month}`);
  const [settings, setSettings] = useState(false);
  const [unit, setUnit] = useState<any>(null);
  const [pay, setPay] = useState<any>(null);
  if (!data) return <Loading />;
  const s = data.summary;
  const admin = can("settings.manage");
  return (
    <div className="space-y-5">
      <PageHeader title="Property & rent" subtitle="Is the pump owned or on rent, and the shops / hotel inside it that are rented out"
        actions={admin && <>
          <button className="btn-secondary" onClick={() => setSettings(true)}><Building2 size={15} /> Owned / rented</button>
          <button className="btn-primary" onClick={() => setUnit({})}><Plus size={16} /> Add a rented unit</button>
        </>} />

      <div className="flex flex-wrap items-center gap-3">
        <div className={`rounded-2xl px-5 py-4 ring-1 ${data.ownership === "rented" ? "bg-amber-50 ring-amber-200" : "bg-emerald-50 ring-emerald-200"}`}>
          <div className="flex items-center gap-2 text-sm text-slate-600"><Home size={15} /> Pump</div>
          <div className="text-2xl font-bold">{data.ownership === "rented" ? "On rent · کرائے پر" : "Owned · اپنا"}</div>
          {data.ownership === "rented" && data.pump_rent > 0 && <div className="text-sm text-amber-800">Rent {pkr(data.pump_rent)}/month — booked as an expense by itself</div>}
        </div>
        <Field label="Month"><input type="month" className="input" value={month} max={thisMonth()} onChange={(e) => e.target.value && setMonth(e.target.value)} /></Field>
      </div>

      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <Stat label="Rented units" value={String(s.units)} tone="blue" />
        <Stat label="Rent expected (month)" value={pkrShort(s.expected)} hint="Income from shops / hotel" />
        <Stat label="Collected" value={pkrShort(s.collected)} tone="green" />
        <Stat label="Still to collect" value={pkrShort(s.due)} tone={s.due > 0 ? "amber" : "slate"} />
      </div>

      {!data.rentals.length ? <Empty>No rented units yet. {admin ? "Add a shop, hotel or service bay that someone rents from you." : ""}</Empty> : (
        <ul className="grid gap-3 sm:grid-cols-2">
          {data.rentals.map((r: any) => (
            <li key={r.id} className={`card p-4 ${r.active ? "" : "opacity-60"}`}>
              <div className="flex items-start justify-between gap-2">
                <div className="min-w-0">
                  <div className="flex flex-wrap items-center gap-2"><span className="font-semibold">{r.name}</span><Badge tone="slate">{kindLabel(r.kind)}</Badge>{!r.active && <Badge tone="red">closed</Badge>}</div>
                  <div className="text-sm text-slate-500">{[r.tenant_name, r.phone].filter(Boolean).join(" · ") || "No tenant set"}</div>
                </div>
                <div className="shrink-0 text-right"><div className="font-bold tabular-nums">{pkr(r.monthly_rent)}</div><div className="text-xs text-slate-500">per month</div></div>
              </div>
              <div className="mt-3 flex items-center justify-between rounded-lg bg-slate-50 px-3 py-2 text-sm">
                <span>This month: <b className={r.month_settled ? "text-emerald-700" : "text-amber-700"}>{pkr(r.month_paid)}</b>{!r.month_settled && <span className="text-slate-500"> / {pkr(r.monthly_rent)}</span>}</span>
                {r.month_settled ? <Badge tone="green">paid</Badge> : <Badge tone="amber">due {pkr(r.month_due)}</Badge>}
              </div>
              <div className="mt-3 flex flex-wrap gap-2">
                {can("cash.receive") && r.active && <button className="btn-primary min-h-10 flex-1" onClick={() => setPay(r)}><Wallet size={15} /> Receive rent</button>}
                {admin && <button className="btn-secondary min-h-10" aria-label="Edit" onClick={() => setUnit(r)}><Pencil size={15} /></button>}
              </div>
            </li>
          ))}
        </ul>
      )}

      {settings && <SettingsForm data={data} onClose={() => setSettings(false)} onSaved={() => { setSettings(false); reload(); }} />}
      {unit && <UnitForm initial={unit.id ? unit : null} onClose={() => setUnit(null)} onSaved={() => { setUnit(null); reload(); }} />}
      {pay && <RentPayment r={pay} onClose={() => setPay(null)} onDone={() => { setPay(null); reload(); }} />}
    </div>
  );
}

function SettingsForm({ data, onClose, onSaved }: { data: any; onClose: () => void; onSaved: () => void }) {
  const [f, setF] = useState({ ownership: data.ownership as "owned" | "rented", pump_rent: String(data.pump_rent || "") });
  const { busy, run } = useAction();
  return (
    <Modal open onClose={onClose} title="Pump — owned or on rent?">
      <form className="space-y-4" onSubmit={async (e) => { e.preventDefault(); if (await run(() => api("/property/settings", { body: { ownership: f.ownership, pump_rent: Number(f.pump_rent) || 0 } }), "Saved")) onSaved(); }}>
        <div className="grid grid-cols-2 gap-3">
          {(["owned", "rented"] as const).map((o) => (
            <button type="button" key={o} onClick={() => setF({ ...f, ownership: o })} className={`rounded-xl border-2 p-4 text-center ${f.ownership === o ? "border-brand-600 bg-brand-50" : "border-slate-200"}`}>
              <div className="text-2xl">{o === "owned" ? "🏠" : "🔑"}</div><div className="font-semibold">{o === "owned" ? "Owned · اپنا" : "On rent · کرائے پر"}</div>
            </button>
          ))}
        </div>
        {f.ownership === "rented" && <Field label="Pump rent per month (Rs) — goes into expenses by itself">
          <input className="input" type="number" min={0} value={f.pump_rent} onChange={(e) => setF({ ...f, pump_rent: e.target.value })} placeholder="e.g. 50000" /></Field>}
        <p className="text-xs text-slate-500">Owned: the shops / hotel inside the pump that you rent out bring in income. On rent: the pump's own rent is an expense each month.</p>
        <div className="flex justify-end gap-2"><button type="button" className="btn-secondary" onClick={onClose}>Cancel</button><button className="btn-primary" disabled={busy}>Save</button></div>
      </form>
    </Modal>
  );
}

function UnitForm({ initial, onClose, onSaved }: { initial: any; onClose: () => void; onSaved: () => void }) {
  const [f, setF] = useState({ name: initial?.name ?? "", kind: initial?.kind ?? "shop", tenant_name: initial?.tenant_name ?? "", phone: initial?.phone ?? "",
    monthly_rent: String(initial?.monthly_rent ?? ""), deposit: String(initial?.deposit ?? ""), note: initial?.note ?? "", active: initial ? Boolean(initial.active) : true });
  const { busy, run } = useAction();
  const save = async () => {
    const body = { name: f.name, kind: f.kind, tenant_name: f.tenant_name || null, phone: f.phone || null, monthly_rent: Number(f.monthly_rent) || 0, deposit: Number(f.deposit) || 0, note: f.note || null, active: f.active };
    const ok = initial ? await run(() => api(`/rentals/${initial.id}`, { method: "PATCH", body }), "Saved") : await run(() => api("/rentals", { body }), "Unit added");
    if (ok) onSaved();
  };
  return (
    <Modal open onClose={onClose} title={initial ? "Edit rented unit" : "Add a rented unit"}>
      <form className="space-y-3" onSubmit={(e) => { e.preventDefault(); save(); }}>
        <div className="grid gap-3 sm:grid-cols-2">
          <Field label="Name (e.g. Shop 1, Hotel)"><input className="input" required value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} /></Field>
          <Field label="Type"><select className="input" value={f.kind} onChange={(e) => setF({ ...f, kind: e.target.value })}>{KINDS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}</select></Field>
          <Field label="Rented to (name)"><input className="input" value={f.tenant_name} onChange={(e) => setF({ ...f, tenant_name: e.target.value })} /></Field>
          <Field label="Phone"><input className="input" value={f.phone} onChange={(e) => setF({ ...f, phone: e.target.value })} /></Field>
          <Field label="Rent per month (Rs)"><input className="input" type="number" min={0} required value={f.monthly_rent} onChange={(e) => setF({ ...f, monthly_rent: e.target.value })} /></Field>
          <Field label="Security deposit (Rs)"><input className="input" type="number" min={0} value={f.deposit} onChange={(e) => setF({ ...f, deposit: e.target.value })} /></Field>
        </div>
        <Field label="Note"><input className="input" value={f.note} onChange={(e) => setF({ ...f, note: e.target.value })} /></Field>
        {initial && <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={f.active} onChange={(e) => setF({ ...f, active: e.target.checked })} /> Active (unticked = closed / vacated)</label>}
        <div className="flex justify-end gap-2"><button type="button" className="btn-secondary" onClick={onClose}>Cancel</button><button className="btn-primary" disabled={busy || !f.name}>Save</button></div>
      </form>
    </Modal>
  );
}

function RentPayment({ r, onClose, onDone }: { r: any; onClose: () => void; onDone: () => void }) {
  const [f, setF] = useState({ amount: String(r.month_due || r.monthly_rent || ""), for_month: thisMonth(), method: "cash", ref: "" });
  const [account, setAccount] = useState<number | null>(null);
  const { busy, run } = useAction();
  return (
    <Modal open onClose={onClose} title={`Receive rent — ${r.name}`}>
      <form className="space-y-3" onSubmit={async (e) => {
        e.preventDefault();
        if (await run(() => api(`/rentals/${r.id}/pay`, { body: { amount: Number(f.amount), for_month: f.for_month, method: f.method, account_id: account, ref: f.ref || null } }), (x: any) => `Rent received ${pkr(x.received)}`)) onDone();
      }}>
        <div className="grid grid-cols-2 gap-3">
          <Field label="Amount (Rs)"><input className="input py-2.5 text-xl" type="number" min={1} required value={f.amount} onChange={(e) => setF({ ...f, amount: e.target.value })} /></Field>
          <Field label="For month"><input className="input" type="month" value={f.for_month} onChange={(e) => setF({ ...f, for_month: e.target.value })} /></Field>
          <Field label="How"><select className="input" value={f.method} onChange={(e) => setF({ ...f, method: e.target.value })}>{["cash", "bank", "jazzcash", "easypaisa", "raast", "card"].map((m) => <option key={m}>{m}</option>)}</select></Field>
          <Field label="Ref / slip"><input className="input" value={f.ref} onChange={(e) => setF({ ...f, ref: e.target.value })} /></Field>
        </div>
        <AccountPicker method={f.method} value={account} onChange={setAccount} label="Into which bank account? · کس بینک میں" />
        <div className="rounded-lg bg-emerald-50 p-2 text-sm text-emerald-800">This is counted as income, and the money goes into {f.method === "cash" ? "the office cash" : "the bank account"}.</div>
        <div className="flex justify-end gap-2"><button type="button" className="btn-secondary" onClick={onClose}>Cancel</button><button className="btn-primary" disabled={busy}>Save</button></div>
      </form>
    </Modal>
  );
}

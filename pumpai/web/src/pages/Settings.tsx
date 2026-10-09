import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, linkToken, useApi } from "../lib/api";
import { Field, Loading, Modal, PageHeader, useAction } from "../components/ui";
import { StationForm, TankForm } from "../components/QuickAdd";
import { BusinessProfile, Integrations, About } from "../components/BusinessSettings";
import { PRODUCTS, num } from "../lib/format";


export default function SettingsPage() {
  const { data, reload } = useApi<any>("/settings");
  if (!data) return <Loading />;
  const i = data.integrations;

  return (
    <div className="space-y-5">
      <PageHeader title="Settings" />
      <About />
      <BusinessProfile />
      <AutoSwitches values={data.automation ?? {}} review={data.google_review_url} onSaved={reload} />
      <KhataRules r={data.khata_rules} onSaved={reload} />
      <Limits l={data.limits} onSaved={reload} />
      <AlertSensitivity s={data.sensitivity} onSaved={reload} />
      <Safety twofa={data.admin_2fa} onTwofa={reload} />
      <Backups />
      <Hardware />
      <Integrations ai={i.claude.connected} wa={i.whatsapp.connected} />
      <StationsSection />
      <div className="card flex items-center justify-between p-4">
        <div><h2 className="font-semibold">Team & access</h2><p className="text-sm text-slate-600">Create Admin, Manager and Salesman logins and control what each can do.</p></div>
        <Link to="/users" className="btn-secondary">Manage users →</Link>
      </div>
      <div className="card flex items-center justify-between p-4">
        <div><h2 className="font-semibold">Lists</h2><p className="text-sm text-slate-600">Customer types, machine types, shop categories, utility bills, booking services, training topics, job titles, complaint categories — add or change anything the dropdowns offer.</p></div>
        <Link to="/lists" className="btn-secondary">Manage lists →</Link>
      </div>
    </div>
  );
}

/** Everything on the forecourt the admin can change: stations, tanks and meters — add, edit, close/retire, delete. */
function StationsSection() {
  const { data, reload } = useApi<any[]>("/stations?all=1");
  const { busy, run } = useAction();
  const [addStation, setAddStation] = useState(false);
  const [editStation, setEditStation] = useState<any | null>(null);
  const [tankFor, setTankFor] = useState<number | null>(null);
  const [editTank, setEditTank] = useState<any | null>(null);
  const [meterFor, setMeterFor] = useState<any | null>(null);
  if (!data) return <Loading />;
  const small = "btn-secondary min-h-9 !px-2.5 !py-1 text-xs sm:min-h-0";
  const danger = `${small} !text-rose-700`;
  return (
    <div className="card p-4">
      <div className="mb-3 flex items-center justify-between"><h2 className="font-semibold">Stations, tanks & meters</h2><button className="btn-secondary" onClick={() => setAddStation(true)}>+ Add station</button></div>
      <div className="space-y-3">
        {data.map((s) => (
          <div key={s.id} className={`rounded-lg border border-slate-200 p-3 ${s.active ? "" : "bg-slate-50 opacity-75"}`}>
            <div className="flex flex-wrap items-center justify-between gap-2">
              <div className="min-w-0"><div className="font-medium">{s.name}{!s.active && <span className="ml-2 rounded bg-slate-200 px-1.5 py-0.5 text-[11px] font-semibold text-slate-600">CLOSED</span>}</div><div className="text-xs text-slate-500">{[s.omc, s.city, s.address, s.timings].filter(Boolean).join(" · ")}</div></div>
              <div className="flex flex-wrap gap-1.5">
                <button className={small} onClick={() => setEditStation(s)}>Edit</button>
                {s.active && <button className={small} onClick={() => setTankFor(s.id)}>+ Tank</button>}
                <button className={small} disabled={busy} onClick={() => confirm(s.active ? `Close ${s.name}? It disappears from the POS and lists; its history stays.` : `Reopen ${s.name}?`) && run(() => api(`/stations/${s.id}`, { method: "PATCH", body: { active: !s.active } }), s.active ? "Station closed" : "Station reopened").then(reload)}>{s.active ? "Close" : "Reopen"}</button>
                {!s.tanks.length && <button className={danger} disabled={busy} onClick={() => confirm(`Delete ${s.name}?`) && run(() => api(`/stations/${s.id}`, { method: "DELETE" }), "Station deleted").then(reload)}>Delete</button>}
              </div>
            </div>
            <div className="mt-2 space-y-1.5">
              {s.tanks.map((t: any) => {
                const meters = s.nozzles.filter((n: any) => n.tank_id === t.id);
                return (
                  <div key={t.id} className={`flex flex-wrap items-center gap-2 rounded-lg bg-slate-50 px-2 py-1.5 text-xs ${t.active ? "" : "opacity-60"}`}>
                    <span className="min-w-0 flex-1"><b>{t.name}</b> · {PRODUCTS[t.product]} · {num(t.capacity_l)} L · {num(t.current_l)} L now · reorder at {t.reorder_pct}% · {meters.length} meter{meters.length === 1 ? "" : "s"}{!t.active && <span className="ml-1 font-semibold text-slate-500">RETIRED</span>}</span>
                    <span className="flex flex-wrap gap-1">
                      <button className={small} onClick={() => setEditTank(t)}>Edit</button>
                      {t.active && <button className={small} onClick={() => setMeterFor(t)}>+ Meter</button>}
                      <button className={small} disabled={busy} onClick={() => confirm(t.active ? `Retire ${t.name}? It must be empty. Its meters are retired too.` : `Bring ${t.name} back?`) && run(() => api(`/tanks/${t.id}`, { method: "PATCH", body: { active: !t.active } }), t.active ? "Tank retired" : "Tank restored").then(reload)}>{t.active ? "Retire" : "Restore"}</button>
                      <button className={danger} disabled={busy} onClick={() => confirm(`Delete ${t.name}? Only possible if it was never used.`) && run(() => api(`/tanks/${t.id}`, { method: "DELETE" }), "Tank deleted").then(reload)}>Delete</button>
                    </span>
                  </div>
                );
              })}
              {!s.tanks.length && <span className="text-xs text-amber-700">No tanks yet</span>}
            </div>
            {s.nozzles.length > 0 && <Meters nozzles={s.nozzles} onSaved={reload} />}
          </div>
        ))}
      </div>
      {addStation && <StationForm onClose={() => setAddStation(false)} onSaved={() => { setAddStation(false); reload(); }} />}
      {editStation && <StationForm edit={editStation} onClose={() => setEditStation(null)} onSaved={() => { setEditStation(null); reload(); }} />}
      {tankFor && <TankForm stations={data} stationId={tankFor} onClose={() => setTankFor(null)} onSaved={() => { setTankFor(null); reload(); }} />}
      {editTank && <TankForm stations={data} edit={editTank} onClose={() => setEditTank(null)} onSaved={() => { setEditTank(null); reload(); }} />}
      {meterFor && <AddMeter tank={meterFor} onClose={() => setMeterFor(null)} onSaved={() => { setMeterFor(null); reload(); }} />}
    </div>
  );
}

/** A new dispenser meter on an existing tank, with the totalizer reading showing on it today. */
function AddMeter({ tank, onClose, onSaved }: { tank: any; onClose: () => void; onSaved: () => void }) {
  const [f, setF] = useState({ label: "", totalizer: "" });
  const { busy, run } = useAction();
  return (
    <Modal open onClose={onClose} title={`New meter on ${tank.name}`}>
      <form className="space-y-3" onSubmit={async (e) => { e.preventDefault(); if (await run(() => api(`/tanks/${tank.id}/nozzles`, { body: { label: f.label || undefined, totalizer: Number(f.totalizer) || 0 } }), "Meter added")) onSaved(); }}>
        <Field label="Name on the dispenser"><input className="input" placeholder={`e.g. Machine 3 left (${PRODUCTS[tank.product]})`} maxLength={30} value={f.label} onChange={(e) => setF({ ...f, label: e.target.value })} /></Field>
        <Field label="Totalizer reading now"><input className="input tabular-nums" type="number" min={0} step="0.01" value={f.totalizer} onChange={(e) => setF({ ...f, totalizer: e.target.value })} /></Field>
        <p className="text-xs text-slate-500">It gets the next meter number and joins the next shift's handover sheet.</p>
        <div className="flex justify-end gap-2"><button type="button" className="btn-secondary" onClick={onClose}>Cancel</button><button className="btn-primary" disabled={busy}>Add meter</button></div>
      </form>
    </Modal>
  );
}

/** Meters of a station with their numbers (No.1, No.2 …); number and name can be changed, a dead meter retired or deleted. */
function Meters({ nozzles, onSaved }: { nozzles: any[]; onSaved: () => void }) {
  const [edit, setEdit] = useState<any | null>(null);
  const { busy, run } = useAction();
  return (
    <div className="mt-2">
      <div className="mb-1 text-xs font-medium text-slate-500">Meters (numbers show on shifts, reports and alerts — tap to change)</div>
      <div className="flex flex-wrap gap-2">
        {nozzles.map((n) => (
          <button key={n.id} type="button" onClick={() => setEdit({ id: n.id, meter_no: String(n.meter_no ?? ""), label: n.label, active: Boolean(n.active) })}
            className={`flex items-center gap-2 rounded-lg border border-slate-200 px-2 py-1 text-xs hover:bg-slate-50 ${n.active ? "" : "line-through opacity-50"}`}>
            <span className="flex h-6 w-6 items-center justify-center rounded-md bg-slate-800 font-bold text-white">{n.meter_no}</span>{n.label} · {PRODUCTS[n.product]}
          </button>
        ))}
      </div>
      {edit && (
        <form className="mt-2 flex flex-wrap items-end gap-2 rounded-lg bg-slate-50 p-2" onSubmit={async (e) => {
          e.preventDefault();
          if (await run(() => api(`/nozzles/${edit.id}`, { method: "PATCH", body: { meter_no: Number(edit.meter_no), label: edit.label } }), "Meter saved")) { setEdit(null); onSaved(); }
        }}>
          <Field label="Meter No."><input className="input w-20" type="number" min={1} max={99} required value={edit.meter_no} onChange={(e) => setEdit({ ...edit, meter_no: e.target.value })} /></Field>
          <Field label="Name on the dispenser"><input className="input w-40" required maxLength={30} value={edit.label} onChange={(e) => setEdit({ ...edit, label: e.target.value })} /></Field>
          <button className="btn-primary" disabled={busy}>Save</button><button type="button" className="btn-secondary" onClick={() => setEdit(null)}>Cancel</button>
          <button type="button" className="btn-secondary" disabled={busy} onClick={() => run(() => api(`/nozzles/${edit.id}`, { method: "PATCH", body: { active: !edit.active } }), edit.active ? "Meter retired" : "Meter restored").then(() => { setEdit(null); onSaved(); })}>{edit.active ? "Retire" : "Restore"}</button>
          <button type="button" className="btn-secondary !text-rose-700" disabled={busy} onClick={() => confirm("Delete this meter? Only possible if it never ran a shift.") && run(() => api(`/nozzles/${edit.id}`, { method: "DELETE" }), "Meter deleted").then(() => { setEdit(null); onSaved(); })}>Delete</button>
          <span className="w-full text-xs text-slate-500">If another meter already has this number, the two swap numbers. A retired meter leaves the handover sheet but keeps its history.</span>
        </form>
      )}
    </div>
  );
}

/** Operating limits that used to be fixed in the code. */
function Limits({ l, onSaved }: { l?: Record<string, any>; onSaved: () => void }) {
  const [f, setF] = useState({ test_limit_l: String(l?.test_limit_l ?? 10), shortage_min: String(l?.shortage_min ?? 100), utility_alert_pct: String(l?.utility_alert_pct ?? 15), shortage_tolerance_pct: String(l?.shortage_tolerance_pct ?? 0.2), shift_hours: String(l?.shift_hours ?? 24), khata_discount_max: String(l?.khata_discount_max ?? 500), pin_admin: Boolean(l?.pin_admin) });
  const { busy, run } = useAction();
  return (
    <form className="card grid grid-cols-1 gap-3 p-4 sm:grid-cols-2" onSubmit={(e) => { e.preventDefault(); run(() => api("/settings", { method: "PUT", body: { limits: { test_limit_l: Number(f.test_limit_l), shortage_min: Number(f.shortage_min), utility_alert_pct: Number(f.utility_alert_pct), shortage_tolerance_pct: Number(f.shortage_tolerance_pct), shift_hours: Number(f.shift_hours), khata_discount_max: Number(f.khata_discount_max), pin_admin: f.pin_admin } } }), "Limits saved").then(onSaved); }}>
      <h2 className="font-semibold sm:col-span-2">Limits & tolerances</h2>
      <div className="sm:col-span-2">
        <span className="label">Shift length — reminder to close fires after this. Set it to how long one salesman's shift runs.</span>
        <div className="mt-1 flex flex-wrap items-center gap-2">
          <input className="input w-28" type="number" min={1} max={72} value={f.shift_hours} onChange={(e) => setF({ ...f, shift_hours: e.target.value })} />
          <span className="text-sm text-slate-500">hours</span>
          {[["1 shift · 24h", 24], ["2 shifts · 12h", 12], ["3 shifts · 8h", 8]].map(([lbl, h]) => (
            <button key={h as number} type="button" onClick={() => setF({ ...f, shift_hours: String(h) })}
              className={`rounded-lg px-3 py-1.5 text-sm ring-1 ${Number(f.shift_hours) === h ? "bg-brand-50 font-semibold text-brand-800 ring-brand-500" : "bg-white text-slate-600 ring-slate-200 hover:bg-slate-50"}`}>{lbl as string}</button>
          ))}
        </div>
        <p className="mt-1 text-xs text-slate-500">Ek 24-ghante shift ho to 24; din-raat 2 shift ho to 12; 3 shift ho to 8. Har salesman ki shift alag track hoti hai, chahe ek ho ya kai — bas har bande ki duty time Staff page pe set kar dein.</p>
      </div>
      <label className="block"><span className="label">Test litres allowed per shift (returned to the tank)</span><input className="input" type="number" min={0} max={500} value={f.test_limit_l} onChange={(e) => setF({ ...f, test_limit_l: e.target.value })} /></label>
      <label className="block"><span className="label">Cash shortage below this (Rs) is ignored, not charged to the salesman</span><input className="input" type="number" min={0} value={f.shortage_min} onChange={(e) => setF({ ...f, shortage_min: e.target.value })} /></label>
      <label className="block"><span className="label">Tanker transit loss allowed (% of invoice) before a claim</span><input className="input" type="number" min={0} max={2} step="0.05" value={f.shortage_tolerance_pct} onChange={(e) => setF({ ...f, shortage_tolerance_pct: e.target.value })} /></label>
      <label className="block"><span className="label">Utility bill jump that raises an alert (% over last month)</span><input className="input" type="number" min={0} max={500} value={f.utility_alert_pct} onChange={(e) => setF({ ...f, utility_alert_pct: e.target.value })} /></label>
      <label className="block"><span className="label">Khata discount a salesman can give per sale (Rs) — more needs the manager / CEO</span><input className="input" type="number" min={0} value={f.khata_discount_max} onChange={(e) => setF({ ...f, khata_discount_max: e.target.value })} /></label>
      <label className="flex items-center gap-2 text-sm sm:col-span-2"><input type="checkbox" checked={f.pin_admin} onChange={(e) => setF({ ...f, pin_admin: e.target.checked })} /> Let the owner (admin) also sign in with a 4-digit PIN on the pump tablet</label>
      <div className="sm:col-span-2"><button className="btn-primary" disabled={busy}>Save</button></div>
    </form>
  );
}

const SWITCHES: [string, string, string][] = [
  ["khata_receipts", "WhatsApp receipt for every khata fill", "Police stations, schools, offices and other khata accounts get litres, rate, slip and new balance after each fill."],
  ["wholesale_messages", "WhatsApp to wholesale clients", "Each supply, payment and return, and their new rate when the pump price changes."],
  ["khata_auto_block", "Put overdue khata on hold", "An account with no payment for the days set below is put on hold at the POS until it pays; the customer is told on WhatsApp."],
  ["ask_rating", "Ask customers to rate each fill (1–5)", "After a fill on a customer's account they get a WhatsApp; a bad rating asks what went wrong, opens a complaint and alerts the manager."],
  ["wa_approvals", "Approvals on WhatsApp", "A manager's price change (when two-person rule is on) or an expense above the limit is sent to the owner — reply 1 to approve, 2 to reject."],
  ["shortage_to_staff", "Put cash shortages on the salesman's account", "When a shift closes short (above the amount set in Limits below), the amount is added to the salesman's staff account to adjust from salary."],
];
function AutoSwitches({ values, review, onSaved }: { values: Record<string, boolean>; review?: string; onSaved: () => void }) {
  const { run } = useAction();
  const [url, setUrl] = useState(review ?? "");
  return (
    <div className="card divide-y divide-slate-100">
      <h2 className="p-4 pb-2 font-semibold">Automatic messages & bookkeeping</h2>
      <form className="flex flex-wrap items-end gap-2 p-4" onSubmit={(e) => { e.preventDefault(); run(() => api("/settings", { method: "PUT", body: { google_review_url: url } }), "Saved").then(onSaved); }}>
        <label className="min-w-[260px] flex-1"><span className="label">Google review link (shown on the customer's digital receipt)</span>
          <input className="input" type="url" placeholder="https://g.page/r/…/review" value={url} onChange={(e) => setUrl(e.target.value)} /></label>
        <button className="btn-secondary">Save</button>
      </form>
      {SWITCHES.map(([k, title, text]) => (
        <label key={k} className="flex cursor-pointer items-start gap-3 p-4">
          <input type="checkbox" className="mt-1 h-5 w-5" checked={values[k] !== false}
            onChange={(e) => run(() => api("/settings", { method: "PUT", body: { automation: { [k]: e.target.checked } } }), e.target.checked ? "Turned on" : "Turned off").then(onSaved)} />
          <span><span className="block font-medium">{title}</span><span className="text-sm text-slate-600">{text}</span></span>
        </label>
      ))}
    </div>
  );
}

function KhataRules({ r, onSaved }: { r?: { block_days: number; block_institutions: boolean; late_fee_pct: number }; onSaved: () => void }) {
  const [f, setF] = useState({ block_days: String(r?.block_days ?? 60), block_institutions: r?.block_institutions ?? false, late_fee_pct: String(r?.late_fee_pct ?? 0) });
  const { busy, run } = useAction();
  return (
    <form className="card grid grid-cols-1 gap-3 p-4 sm:grid-cols-3" onSubmit={(e) => { e.preventDefault(); run(() => api("/settings", { method: "PUT", body: { khata_rules: { block_days: Number(f.block_days), block_institutions: f.block_institutions, late_fee_pct: Number(f.late_fee_pct) } } }), "Khata rules saved").then(onSaved); }}>
      <h2 className="font-semibold sm:col-span-3">Khata rules</h2>
      <label className="block"><span className="label">Hold khata after no payment for (days)</span><input className="input" type="number" min={15} max={365} value={f.block_days} onChange={(e) => setF({ ...f, block_days: e.target.value })} /></label>
      <label className="block"><span className="label">Late-payment charge per month (%, 0 = off)</span><input className="input" type="number" min={0} max={5} step="0.1" value={f.late_fee_pct} onChange={(e) => setF({ ...f, late_fee_pct: e.target.value })} /></label>
      <label className="flex items-center gap-2 self-end pb-2 text-sm"><input type="checkbox" checked={f.block_institutions} onChange={(e) => setF({ ...f, block_institutions: e.target.checked })} /> Also hold police / government / school accounts</label>
      <p className="text-xs text-slate-500 sm:col-span-3">Institutions are never charged a late fee. Their bills can be tracked with PO numbers on each account's khata page.</p>
      <div className="sm:col-span-3"><button className="btn-primary" disabled={busy}>Save</button></div>
    </form>
  );
}

function Safety({ twofa, onTwofa }: { twofa?: boolean; onTwofa?: () => void }) {
  const { data, reload } = useApi<any>("/safety");
  const { run } = useAction();
  if (!data) return null;
  return (
    <div className="card space-y-3 p-4">
      <h2 className="font-semibold">Safety rules</h2>
      <label className="flex items-start gap-3"><input type="checkbox" className="mt-1 h-5 w-5" checked={data.price_approval}
        onChange={(e) => run(() => api("/safety", { method: "PUT", body: { price_approval: e.target.checked } }), "Saved").then(reload)} />
        <span><span className="block font-medium">Two-person approval for price changes</span><span className="text-sm text-slate-600">A manager's new prices wait until the admin approves them (admin gets a notification). Big expenses already need approval above the expense limit.</span></span></label>
      <label className="flex items-start gap-3"><input type="checkbox" className="mt-1 h-5 w-5" defaultChecked={twofa}
        onChange={(e) => run(() => api("/settings", { method: "PUT", body: { admin_2fa: e.target.checked } }), e.target.checked ? "Two-factor on" : "Two-factor off").then(() => onTwofa?.())} />
        <span><span className="block font-medium">Two-factor login for the owner (admin)</span><span className="text-sm text-slate-600">After the password, a 6-digit code is sent to the admin's WhatsApp number — needed to finish signing in. Make sure every admin has a WhatsApp number on the Users page, or they could be locked out.</span></span></label>
    </div>
  );
}

function Backups() {
  const { data, reload } = useApi<any>("/backups");
  const { busy, run } = useAction();
  if (!data) return null;
  return (
    <div className="card p-4">
      <div className="mb-2 flex flex-wrap items-center gap-2"><h2 className="font-semibold">Backups</h2><span className="text-xs text-slate-500">Every night at 2:30am · last 14 kept · {data.dir}</span>
        <button className="btn-secondary ml-auto" disabled={busy} onClick={() => run(() => api("/backups", { body: {} }), "Backup made").then(reload)}>Back up now</button></div>
      {data.restore_pending && <div className="mb-2 flex items-center gap-2 rounded-lg bg-amber-50 p-2 text-sm text-amber-800">A restore is ready — restart the app to finish it.
        {data.can_restart && <button className="btn-primary ml-auto !py-1" onClick={() => run(() => api("/system/restart", { body: {} }), "Restarting — page reloads in a few seconds").then(() => setTimeout(() => location.reload(), 6000))}>Restart now</button>}
        <button className={`btn-secondary !py-1 ${data.can_restart ? "" : "ml-auto"}`} onClick={() => run(() => api("/backups/restore", { method: "DELETE" }), "Restore cancelled").then(reload)}>Cancel restore</button></div>}
      <ul className="divide-y divide-slate-100 text-sm">{data.backups.map((b: any) => (
        <li key={b.name} className="flex flex-wrap items-center gap-2 py-1.5">
          <span className="flex-1 font-mono text-xs">{b.name}</span><span className="text-xs text-slate-500">{Math.round(b.bytes / 1024).toLocaleString()} KB</span>
          <a className="btn-secondary !py-1 text-xs" href={`/api/backups/${b.name}?token=${linkToken()}`}>Download</a>
          <button className="btn-secondary !py-1 text-xs" onClick={() => confirm(`Restore ${b.name}? Today's data is backed up first; the restore finishes when the app restarts.`) && run(() => api(`/backups/${b.name}/restore`, { body: {} }), (r: any) => r.message).then(reload)}>Restore</button>
        </li>
      ))}{!data.backups.length && <li className="py-2 text-slate-500">No backups yet — the first one is made tonight.</li>}</ul>
      <p className="mt-2 text-xs text-slate-500">Tip: download a backup every week and keep it on another computer or USB, or set BACKUP_DIR to a cloud-synced folder.</p>
    </div>
  );
}

const HW_STATUS: Record<string, { label: string; cls: string }> = {
  pending: { label: "Pending", cls: "bg-amber-100 text-amber-800" }, planned: { label: "Planned", cls: "bg-sky-100 text-sky-800" },
  installed: { label: "Installed", cls: "bg-indigo-100 text-indigo-800" }, live: { label: "Live ✓", cls: "bg-emerald-100 text-emerald-800" },
};
function Hardware() {
  const { data, reload } = useApi<any[]>("/hardware");
  const { run } = useAction();
  if (!data) return null;
  return (
    <div className="card p-4">
      <h2 className="mb-1 font-semibold">Hardware connections</h2>
      <p className="mb-2 text-sm text-slate-600">Mark where each piece stands. Everything in the app already works without them; marking one “Live” is just for your own record.</p>
      <ul className="space-y-2">{data.map((x) => (
        <li key={x.key} className="flex flex-wrap items-start gap-3 rounded-lg bg-slate-50 p-2 text-sm">
          <select className={`mt-0.5 rounded px-2 py-0.5 text-xs font-semibold ${HW_STATUS[x.status]?.cls ?? HW_STATUS.pending.cls}`} value={x.status}
            onChange={(e) => run(() => api(`/hardware/${x.key}`, { method: "PUT", body: { status: e.target.value } }), "Saved").then(reload)}>
            {Object.entries(HW_STATUS).map(([k, v]) => <option key={k} value={k}>{v.label}</option>)}
          </select>
          <span className="min-w-0 flex-1"><b>{x.name}</b><span className="block text-slate-600">{x.gives}</span></span>
        </li>
      ))}</ul>
    </div>
  );
}

const SENS: [string, string, string][] = [
  ["dip_var_warn", "Dip vs book stock — warn at (%)", "A dip that differs from book stock by this much raises a warning (possible leak, miscalibration or theft)."],
  ["dip_var_crit", "Dip vs book stock — serious at (%)", "A bigger difference is flagged as serious."],
  ["delivery_short_warn", "Tanker short delivery — warn at (%)", "A tanker arriving short by this much of the invoice raises a warning."],
  ["delivery_short_crit", "Tanker short delivery — serious at (%)", "A bigger shortage is flagged as serious."],
  ["cash_short_warn", "Cash short — warn at (Rs)", "A shift closing short by this much raises a warning."],
  ["cash_short_crit", "Cash short — serious at (Rs)", "A bigger shortage is flagged as serious."],
  ["sales_drop_pct", "Sales drop — alert at (%)", "Yesterday's litres falling this far below the 4-week average raises an alert."],
  ["low_stock_days", "Low stock — alert when days to reorder ≤", "A tank this many days from its reorder level raises a low-stock alert."],
];
/** How touchy the loss / fraud / low-stock alerts are. */
function AlertSensitivity({ s, onSaved }: { s?: Record<string, number>; onSaved: () => void }) {
  const [f, setF] = useState<Record<string, string>>({});
  useEffect(() => { if (s) setF(Object.fromEntries(Object.entries(s).map(([k, v]) => [k, String(v)]))); }, [s]);
  const { busy, run } = useAction();
  if (!s) return null;
  return (
    <form className="card p-4" onSubmit={(e) => { e.preventDefault(); run(() => api("/settings", { method: "PUT", body: { sensitivity: Object.fromEntries(Object.entries(f).map(([k, v]) => [k, Number(v)])) } }), "Alert sensitivity saved").then(onSaved); }}>
      <h2 className="font-semibold">Alert sensitivity</h2>
      <p className="mb-3 text-sm text-slate-600">How soon the system warns about stock loss, short deliveries, cash shortages and low stock. Lower numbers = more sensitive.</p>
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
        {SENS.map(([k, label, hint]) => (
          <label key={k} className="block"><span className="label">{label}</span>
            <input className="input" type="number" min={0} step="0.1" value={f[k] ?? ""} onChange={(e) => setF({ ...f, [k]: e.target.value })} />
            <span className="mt-0.5 block text-xs text-slate-400">{hint}</span></label>
        ))}
      </div>
      <div className="mt-3"><button className="btn-primary" disabled={busy}>Save</button></div>
    </form>
  );
}

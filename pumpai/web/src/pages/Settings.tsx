import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { CheckCircle2, XCircle } from "lucide-react";
import { api, useApi } from "../lib/api";
import { Field, Loading, PageHeader, useAction } from "../components/ui";
import { StationForm, TankForm } from "../components/QuickAdd";
import { PRODUCTS, num } from "../lib/format";

const Status = ({ ok, label }: { ok: boolean; label: string }) => (
  <span className={`inline-flex items-center gap-1 text-sm font-medium ${ok ? "text-emerald-700" : "text-slate-500"}`}>
    {ok ? <CheckCircle2 size={16} /> : <XCircle size={16} />} {label}
  </span>
);

export default function SettingsPage() {
  const { data, reload } = useApi<any>("/settings");
  const { busy, run } = useAction();
  const [f, setF] = useState({ business_name: "", owner_name: "", owner_phone: "" });
  useEffect(() => { if (data) setF({ business_name: data.business_name, owner_name: data.owner_name ?? "", owner_phone: data.owner_phone ?? "" }); }, [data]);
  if (!data) return <Loading />;
  const i = data.integrations;

  return (
    <div className="space-y-5">
      <PageHeader title="Settings" />
      <form className="card grid gap-3 p-4 sm:grid-cols-3" onSubmit={(e) => { e.preventDefault(); run(() => api("/settings", { method: "PUT", body: f }), "Saved").then(reload); }}>
        <Field label="Business name"><input className="input" value={f.business_name} onChange={(e) => setF({ ...f, business_name: e.target.value })} /></Field>
        <Field label="Owner name"><input className="input" value={f.owner_name} onChange={(e) => setF({ ...f, owner_name: e.target.value })} /></Field>
        <Field label="Owner WhatsApp (alerts & daily brief)"><input className="input" value={f.owner_phone} onChange={(e) => setF({ ...f, owner_phone: e.target.value })} /></Field>
        <div className="sm:col-span-3"><button className="btn-primary" disabled={busy}>Save</button></div>
      </form>
      <AutoSwitches values={data.automation ?? {}} onSaved={reload} />
      <div className="grid gap-5 md:grid-cols-2">
        <div className="card space-y-2 p-4">
          <div className="flex items-center justify-between"><h2 className="font-semibold">Claude AI</h2><Status ok={i.claude.connected} label={i.claude.connected ? "Connected" : "Rule engine (offline)"} /></div>
          <p className="text-sm text-slate-600">Powers the WhatsApp agent, "Ask AI", campaign writing and the daily brief. Without a key the built-in Roman Urdu rule engine and analytics still work.</p>
          <div className="rounded bg-slate-50 p-2 font-mono text-xs">ANTHROPIC_API_KEY=sk-ant-…<br />AI_MODEL={i.claude.model}<br />AI_EFFORT={i.claude.effort}</div>
        </div>
        <div className="card space-y-2 p-4">
          <div className="flex items-center justify-between"><h2 className="font-semibold">WhatsApp Cloud API</h2><Status ok={i.whatsapp.connected} label={i.whatsapp.connected ? "Live" : "Simulated"} /></div>
          <p className="text-sm text-slate-600">In Meta Business → WhatsApp → Configuration, set the webhook below and subscribe to <b>messages</b>. Create a utility template named <b>{i.whatsapp.template}</b> with one body variable for messages outside the 24-hour window.</p>
          <div className="rounded bg-slate-50 p-2 font-mono text-xs break-all">Webhook URL: {i.whatsapp.webhook_url}<br />WA_TOKEN, WA_PHONE_NUMBER_ID, WA_VERIFY_TOKEN, WA_APP_SECRET</div>
        </div>
      </div>
      <StationsSection />
      <div className="card flex items-center justify-between p-4">
        <div><h2 className="font-semibold">Team & access</h2><p className="text-sm text-slate-600">Create Admin, Manager and Salesman logins and control what each can do.</p></div>
        <Link to="/users" className="btn-secondary">Manage users →</Link>
      </div>
    </div>
  );
}

function StationsSection() {
  const { data, reload } = useApi<any[]>("/stations");
  const [addStation, setAddStation] = useState(false);
  const [tankFor, setTankFor] = useState<number | null>(null);
  if (!data) return <Loading />;
  return (
    <div className="card p-4">
      <div className="mb-3 flex items-center justify-between"><h2 className="font-semibold">Stations & tanks</h2><button className="btn-secondary" onClick={() => setAddStation(true)}>+ Add station</button></div>
      <div className="space-y-3">
        {data.map((s) => (
          <div key={s.id} className="rounded-lg border border-slate-200 p-3">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <div><div className="font-medium">{s.name}</div><div className="text-xs text-slate-500">{[s.omc, s.city, s.address, s.timings].filter(Boolean).join(" · ")}</div></div>
              <button className="btn-secondary !px-2 !py-1 text-xs" onClick={() => setTankFor(s.id)}>+ Add tank</button>
            </div>
            <div className="mt-2 flex flex-wrap gap-2">
              {s.tanks.map((t: any) => <span key={t.id} className="rounded-lg bg-slate-50 px-2 py-1 text-xs">{t.name} · {PRODUCTS[t.product]} · {num(t.capacity_l)} L · {s.nozzles.filter((n: any) => n.tank_id === t.id).length} nozzles</span>)}
              {!s.tanks.length && <span className="text-xs text-amber-700">No tanks yet</span>}
            </div>
          </div>
        ))}
      </div>
      {addStation && <StationForm onClose={() => setAddStation(false)} onSaved={() => { setAddStation(false); reload(); }} />}
      {tankFor && <TankForm stations={data} stationId={tankFor} onClose={() => setTankFor(null)} onSaved={() => { setTankFor(null); reload(); }} />}
    </div>
  );
}

const SWITCHES: [string, string, string][] = [
  ["khata_receipts", "WhatsApp receipt for every khata fill", "Police stations, schools, offices and other khata accounts get litres, rate, slip and new balance after each fill."],
  ["wholesale_messages", "WhatsApp to wholesale clients", "Each supply, payment and return, and their new rate when the pump price changes."],
  ["shortage_to_staff", "Put cash shortages on the salesman's account", "When a shift closes short (Rs 100 or more), the amount is added to the salesman's staff account to adjust from salary."],
];
function AutoSwitches({ values, onSaved }: { values: Record<string, boolean>; onSaved: () => void }) {
  const { run } = useAction();
  return (
    <div className="card divide-y divide-slate-100">
      <h2 className="p-4 pb-2 font-semibold">Automatic messages & bookkeeping</h2>
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

import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { CheckCircle2, XCircle } from "lucide-react";
import { api, useApi } from "../lib/api";
import { Field, Loading, PageHeader, useAction } from "../components/ui";

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
      <div className="card flex items-center justify-between p-4">
        <div><h2 className="font-semibold">Team & access</h2><p className="text-sm text-slate-600">Create Admin, Manager and Salesman logins and control what each can do.</p></div>
        <Link to="/users" className="btn-secondary">Manage users →</Link>
      </div>
    </div>
  );
}

import { useEffect, useState } from "react";
import { CheckCircle2, XCircle, Upload, KeyRound, Info } from "lucide-react";
import { api, useApi } from "../lib/api";
import { Field, Loading, useAction } from "./ui";
import { squareLogo } from "../pages/Setup";
import { applyBrand, loadBranding } from "../lib/brand";

const COLORS = ["#059669", "#0f766e", "#2563eb", "#1d4ed8", "#7c3aed", "#db2777", "#dc2626", "#ea580c", "#ca8a04", "#334155"];
const OMCS = ["PSO", "Shell", "TotalEnergies", "Attock (APL)", "GO", "Hascol", "Byco / Cnergyico", "Puma", "Other"];

/** Name, logo, colour, contacts and tax numbers — shown on the app, receipts, bills, TV board and salary slips. */
export function BusinessProfile() {
  const { data, reload } = useApi<any>("/business");
  const { busy, run } = useAction();
  const [f, setF] = useState<any>(null);
  useEffect(() => { if (data) setF({ ...data, logo: null }); }, [data]);
  if (!f) return <Loading />;
  const set = (k: string) => (e: any) => setF({ ...f, [k]: e.target.value });
  const save = async () => {
    const body: any = Object.fromEntries(["name", "owner_name", "owner_phone", "biz_phone", "biz_email", "biz_address", "biz_city", "website", "ntn", "strn", "brand_color", "receipt_footer", "omc"]
      .filter((k) => f[k] != null && !(k === "biz_email" && !f[k])).map((k) => [k, f[k]]));
    if (f.logo) body.logo = f.logo;
    if (!/^#[0-9a-f]{6}$/i.test(body.brand_color ?? "")) delete body.brand_color;
    if (await run(() => api("/business", { method: "PUT", body }), "Business profile saved")) { reload(); loadBranding(); }
  };
  return (
    <div className="card space-y-3 p-4">
      <h2 className="font-semibold">Business profile & branding</h2>
      <div className="flex flex-wrap items-center gap-4">
        <label className="flex h-24 w-24 cursor-pointer flex-col items-center justify-center rounded-xl border-2 border-dashed border-slate-300 text-xs text-slate-500 hover:border-brand-500">
          {f.logo || f.logo_url ? <img src={f.logo ?? f.logo_url} alt="Logo" className="h-full w-full rounded-xl object-contain" /> : <><Upload size={20} />Logo</>}
          <input type="file" accept="image/*" className="hidden" onChange={async (e) => { const file = e.target.files?.[0]; if (file) setF({ ...f, logo: await squareLogo(file) }); }} />
        </label>
        <div className="space-y-1">
          <div className="text-sm text-slate-600">Brand colour</div>
          <div className="flex flex-wrap gap-1.5">{COLORS.map((c) => <button key={c} type="button" aria-label={`Colour ${c}`} onClick={() => { setF({ ...f, brand_color: c }); applyBrand(c); }} className={`h-7 w-7 rounded-full ring-offset-2 ${f.brand_color === c ? "ring-2 ring-slate-900" : ""}`} style={{ background: c }} />)}
            <input type="color" aria-label="Other colour" className="h-7 w-9 cursor-pointer rounded" value={f.brand_color || "#059669"} onChange={(e) => { setF({ ...f, brand_color: e.target.value }); applyBrand(e.target.value); }} /></div>
        </div>
      </div>
      <div className="grid gap-3 sm:grid-cols-3">
        <Field label="Business name"><input className="input" value={f.name ?? ""} onChange={set("name")} /></Field>
        <Field label="Owner name"><input className="input" value={f.owner_name ?? ""} onChange={set("owner_name")} /></Field>
        <Field label="Owner WhatsApp (alerts, approvals, daily brief)"><input className="input" value={f.owner_phone ?? ""} onChange={set("owner_phone")} /></Field>
        <Field label="Office / pump phone"><input className="input" value={f.biz_phone ?? ""} onChange={set("biz_phone")} /></Field>
        <Field label="Email"><input className="input" type="email" value={f.biz_email ?? ""} onChange={set("biz_email")} /></Field>
        <Field label="Website / Facebook"><input className="input" value={f.website ?? ""} onChange={set("website")} /></Field>
        <Field label="Address"><input className="input" value={f.biz_address ?? ""} onChange={set("biz_address")} /></Field>
        <Field label="City"><input className="input" value={f.biz_city ?? ""} onChange={set("biz_city")} /></Field>
        <Field label="Oil company"><select className="input" value={f.omc ?? ""} onChange={set("omc")}><option value="">—</option>{OMCS.map((o) => <option key={o}>{o}</option>)}</select></Field>
        <Field label="NTN"><input className="input" value={f.ntn ?? ""} onChange={set("ntn")} /></Field>
        <Field label="STRN"><input className="input" value={f.strn ?? ""} onChange={set("strn")} /></Field>
        <Field label="Receipt bottom line"><input className="input" value={f.receipt_footer ?? ""} onChange={set("receipt_footer")} /></Field>
      </div>
      <button className="btn-primary" disabled={busy} onClick={save}>Save profile</button>
    </div>
  );
}

const Status = ({ ok, label }: { ok: boolean; label: string }) => (
  <span className={`inline-flex items-center gap-1 text-sm font-medium ${ok ? "text-emerald-700" : "text-slate-500"}`}>{ok ? <CheckCircle2 size={16} /> : <XCircle size={16} />} {label}</span>
);

/** Claude key, WhatsApp Cloud API and the public address — set here, no server files to edit. */
export function Integrations({ ai, wa }: { ai: boolean; wa: boolean }) {
  const { data, reload } = useApi<any>("/integrations");
  const { busy, run } = useAction();
  const [v, setV] = useState<Record<string, string> | null>(null);
  useEffect(() => { if (data) setV(data.values); }, [data]);
  if (!v || !data) return <Loading />;
  const inp = (k: string, label: string, ph = "", secret = false) => (
    <Field label={label}><input className="input font-mono text-xs" type={secret ? "password" : "text"} autoComplete="off" placeholder={ph} value={v[k] ?? ""} onFocus={(e) => secret && e.target.value.startsWith("•") && setV({ ...v, [k]: "" })} onChange={(e) => setV({ ...v, [k]: e.target.value })} /></Field>
  );
  const save = () => run(() => api("/integrations", { method: "PUT", body: v }), "Saved — working now (no restart needed)").then(() => { reload(); });
  return (
    <div className="grid gap-5 md:grid-cols-2">
      <div className="card space-y-2 p-4">
        <div className="flex items-center justify-between"><h2 className="flex items-center gap-2 font-semibold"><KeyRound size={16} /> Claude AI</h2><Status ok={ai} label={ai ? "Connected" : "Rule engine (offline)"} /></div>
        <p className="text-sm text-slate-600">WhatsApp agent, Ask AI, photo reading, voice sales, coaching and campaign writing. Without a key the built-in rules still work.</p>
        {inp("anthropic_key", "API key", "sk-ant-…", true)}
        {inp("ai_model", "Model", "claude-opus-5-5")}
        <button className="btn-primary" disabled={busy} onClick={save}>Save</button>
      </div>
      <div className="card space-y-2 p-4">
        <div className="flex items-center justify-between"><h2 className="font-semibold">WhatsApp Cloud API</h2><Status ok={wa} label={wa ? "Live" : "Simulated"} /></div>
        <p className="text-sm text-slate-600">In Meta Business → WhatsApp → Configuration set the webhook <span className="break-all font-mono text-xs">{data.webhook_url}</span> with the verify token below, subscribe to <b>messages</b>, and create a utility template with one body variable.</p>
        <div className="grid gap-2 sm:grid-cols-2">
          {inp("wa_token", "Access token", "EAAG…", true)}
          {inp("wa_phone_number_id", "Phone number ID")}
          {inp("wa_verify_token", "Verify token")}
          {inp("wa_app_secret", "App secret", "", true)}
          {inp("wa_template", "Template name")}
          {inp("public_url", "Public address of this app", "https://pump.example.pk")}
        </div>
        <button className="btn-primary" disabled={busy} onClick={save}>Save</button>
      </div>
    </div>
  );
}

/** Version and who to call for support (the seller / installer). */
export function About() {
  const { data } = useApi<any>("/integrations");
  if (!data) return null;
  const vnd = data.vendor;
  return (
    <div className="card flex flex-wrap items-center gap-3 p-4 text-sm">
      <Info size={18} className="text-brand-600" />
      <span className="flex-1">PumpAI version <b>{data.version}</b> · installed on your own server · your data stays with you</span>
      {vnd?.name && <span>Support: <b>{vnd.name}</b>{vnd.phone ? <> · <a className="text-brand-700 underline" href={`https://wa.me/${vnd.phone.replace(/\D/g, "")}`}>{vnd.phone}</a></> : null}{vnd.email ? ` · ${vnd.email}` : ""}</span>}
    </div>
  );
}

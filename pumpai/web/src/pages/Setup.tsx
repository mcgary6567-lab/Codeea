import { useEffect, useState, type ReactNode } from "react";
import { SocialFields } from "../components/BusinessSettings";
import { Navigate, useNavigate } from "react-router-dom";
import { Check, ChevronLeft, ChevronRight, Plus, Trash2, Upload, Building2, UserCog, Fuel, Tag, ClipboardCheck } from "lucide-react";
import { api } from "../lib/api";
import { useAuth } from "../App";
import { ErrorBox, Loading } from "../components/ui";
import { applyBrand, loadBranding, useBranding } from "../lib/brand";

const FUELS: Record<string, string> = { PMG: "Petrol", HOBC: "Hi-Octane", HSD: "Diesel" };
const OMCS = ["PSO", "Shell", "TotalEnergies", "Attock (APL)", "GO", "Hascol", "Byco / Cnergyico", "Puma", "Other"];
const COLORS = ["#059669", "#0f766e", "#2563eb", "#1d4ed8", "#7c3aed", "#db2777", "#dc2626", "#ea580c", "#ca8a04", "#334155"];
type Tank = { name: string; product: string; capacity_l: string; current_l: string; nozzles: string };
type Station = { name: string; city: string; address: string; timings: string; tanks: Tank[] };
const newTank = (product = "PMG", n = 1): Tank => ({ name: `Tank-${n} ${FUELS[product]}`, product, capacity_l: "", current_l: "", nozzles: "2" });

/** Logo → 512×512 JPEG on white (works on receipts, PDF slips and as the app icon). */
async function squareLogo(file: File): Promise<string> {
  const url = URL.createObjectURL(file);
  try {
    const img = await new Promise<HTMLImageElement>((res, rej) => { const i = new Image(); i.onload = () => res(i); i.onerror = rej; i.src = url; });
    const c = document.createElement("canvas");
    c.width = c.height = 512;
    const g = c.getContext("2d")!;
    g.fillStyle = "#fff"; g.fillRect(0, 0, 512, 512);
    const k = Math.min(460 / img.width, 460 / img.height);
    g.drawImage(img, (512 - img.width * k) / 2, (512 - img.height * k) / 2, img.width * k, img.height * k);
    return c.toDataURL("image/jpeg", 0.9);
  } finally { URL.revokeObjectURL(url); }
}
export { squareLogo };

const STEPS = [
  { label: "Business", icon: Building2 }, { label: "Owner login", icon: UserCog }, { label: "Stations & tanks", icon: Fuel },
  { label: "Prices", icon: Tag }, { label: "Check & finish", icon: ClipboardCheck },
];

/** First-run setup: a new pump owner fills in their business, login, stations, tanks and prices. */
export default function Setup() {
  const brand = useBranding();
  const { user, login } = useAuth();
  const nav = useNavigate();
  const [status, setStatus] = useState<any>(null);
  const [step, setStep] = useState(0);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [code, setCode] = useState("");
  const [biz, setBiz] = useState({ name: "", owner_name: "", owner_phone: "", biz_phone: "", biz_email: "", biz_address: "", biz_city: "", omc: "PSO", ntn: "", strn: "", website: "", brand_color: "#059669", receipt_footer: "", logo: null as string | null,
    facebook: "", instagram: "", whatsapp: "", tiktok: "", youtube: "", twitter: "" });
  const [admin, setAdmin] = useState({ name: "", email: "", password: "", confirm: "", pin: "", phone: "" });
  const [stations, setStations] = useState<Station[]>([{ name: "", city: "", address: "", timings: "24 hours", tanks: [newTank("PMG", 1), newTank("HOBC", 2), newTank("HSD", 3)] }]);
  const [prices, setPrices] = useState<Record<string, string>>({ PMG: "", HOBC: "", HSD: "" });
  useEffect(() => { api("/setup/status").then(setStatus).catch((e) => setErr(e.message)); }, []);
  useEffect(() => { applyBrand(biz.brand_color); }, [biz.brand_color]);
  if (user) return <Navigate to="/" replace />;
  if (!status) return <Loading />;
  if (!status.needed) return <Navigate to="/login" replace />;

  const used = [...new Set(stations.flatMap((s) => s.tanks.map((t) => t.product)))];
  const problem = (): string | null => {
    if (step === 0) {
      if (status.needs_code && !code.trim()) return "Enter the setup code given by the installer";
      if (biz.name.trim().length < 2) return "Enter the business name";
      if (biz.owner_name.trim().length < 2) return "Enter the owner's name";
      if (biz.owner_phone.replace(/\D/g, "").length < 10) return "Enter the owner's WhatsApp number";
    }
    if (step === 1) {
      if (admin.name.trim().length < 2) return "Enter your name";
      if (!/^\S+@\S+\.\S+$/.test(admin.email)) return "Enter a valid email";
      if (admin.password.length < 8) return "Password must be at least 8 characters";
      if (admin.password !== admin.confirm) return "The two passwords are not the same";
      if (admin.pin && !/^\d{4}$/.test(admin.pin)) return "PIN is 4 digits";
    }
    if (step === 2) for (const s of stations) {
      if (s.name.trim().length < 2) return "Give every station a name";
      if (!s.tanks.length) return `${s.name}: add at least one tank`;
      for (const t of s.tanks) {
        if (!(Number(t.capacity_l) > 0)) return `${s.name} ${t.name}: enter the tank capacity`;
        if (Number(t.current_l) > Number(t.capacity_l)) return `${s.name} ${t.name}: stock is more than capacity`;
      }
    }
    if (step === 3) for (const p of used) if (!(Number(prices[p]) > 0)) return `Enter today's ${FUELS[p]} price`;
    return null;
  };
  const next = () => { const p = problem(); setErr(p); if (!p) setStep(step + 1); };
  const finish = async () => {
    setBusy(true); setErr(null);
    try {
      const { confirm: _c, ...a } = admin;
      const r = await api("/setup", { body: {
        code: code.trim() || undefined,
        business: Object.fromEntries(Object.entries(biz).filter(([, v]) => v !== "" && v !== null)),
        admin: { ...a, pin: a.pin || undefined, phone: a.phone || undefined },
        stations: stations.map((s) => ({ name: s.name, city: s.city || undefined, address: s.address || undefined, timings: s.timings || undefined,
          tanks: s.tanks.map((t) => ({ name: t.name, product: t.product, capacity_l: Number(t.capacity_l), current_l: Number(t.current_l) || 0, nozzles: Number(t.nozzles) || 0 })) })),
        prices: Object.fromEntries(used.map((p) => [p, Number(prices[p])])),
      } });
      await login(r.token);
      await loadBranding();
      nav("/");
    } catch (e: any) { setErr(e.message); } finally { setBusy(false); }
  };
  const setStation = (i: number, patch: Partial<Station>) => setStations(stations.map((s, j) => (j === i ? { ...s, ...patch } : s)));
  const setTank = (i: number, k: number, patch: Partial<Tank>) => setStation(i, { tanks: stations[i].tanks.map((t, j) => (j === k ? { ...t, ...patch } : t)) });

  return (
    <div className="min-h-screen bg-slate-50">
      <div className="bg-gradient-to-br from-brand-900 via-brand-700 to-brand-500 px-4 pb-16 pt-8 text-white">
        <div className="mx-auto flex max-w-3xl items-center gap-3">
          {biz.logo ? <img src={biz.logo} alt="" className="h-14 w-14 rounded-xl bg-white object-contain p-1" /> : <div className="text-4xl">⛽</div>}
          <div><h1 className="text-2xl font-bold">{biz.name || "Set up your pump"}</h1><p className="text-sm text-white/80">PumpAI {status.version}{brand?.vendor.name ? ` · installed by ${brand.vendor.name}` : ""} — takes about 5 minutes</p></div>
        </div>
      </div>
      <div className="mx-auto -mt-10 max-w-3xl px-4 pb-10">
        <ol className="mb-4 flex gap-1 overflow-x-auto rounded-xl bg-white p-2 shadow-sm ring-1 ring-slate-200">
          {STEPS.map((s, i) => (
            <li key={s.label} className={`flex flex-1 items-center gap-2 whitespace-nowrap rounded-lg px-3 py-2 text-sm ${i === step ? "bg-brand-600 text-white" : i < step ? "text-brand-700" : "text-slate-400"}`}>
              {i < step ? <Check size={16} /> : <s.icon size={16} />}<span className="hidden sm:inline">{s.label}</span><span className="sm:hidden">{i + 1}</span>
            </li>
          ))}
        </ol>
        <div className="card space-y-4 p-5">
          {step === 0 && <>
            {status.needs_code && <F label="Setup code (from the installer)"><input className="input font-mono uppercase" value={code} onChange={(e) => setCode(e.target.value)} /></F>}
            <div className="flex flex-wrap items-center gap-4">
              <label className="flex h-24 w-24 cursor-pointer flex-col items-center justify-center rounded-xl border-2 border-dashed border-slate-300 bg-white text-xs text-slate-500 hover:border-brand-500">
                {biz.logo ? <img src={biz.logo} alt="Logo" className="h-full w-full rounded-xl object-contain" /> : <><Upload size={20} />Logo</>}
                <input type="file" accept="image/*" className="hidden" onChange={async (e) => { const f = e.target.files?.[0]; if (f) setBiz({ ...biz, logo: await squareLogo(f) }); }} />
              </label>
              <div className="min-w-0 flex-1 space-y-2">
                <F label="Business name *"><input className="input" placeholder="e.g. Al-Madina Petroleum" value={biz.name} onChange={(e) => setBiz({ ...biz, name: e.target.value })} /></F>
                <div className="flex flex-wrap gap-1.5">{COLORS.map((c) => <button key={c} type="button" aria-label={`Colour ${c}`} onClick={() => setBiz({ ...biz, brand_color: c })} className={`h-7 w-7 rounded-full ring-offset-2 ${biz.brand_color === c ? "ring-2 ring-slate-900" : ""}`} style={{ background: c }} />)}
                  <input type="color" aria-label="Other colour" className="h-7 w-9 cursor-pointer rounded" value={biz.brand_color} onChange={(e) => setBiz({ ...biz, brand_color: e.target.value })} /></div>
              </div>
            </div>
            <Grid>
              <F label="Owner name *"><input className="input" value={biz.owner_name} onChange={(e) => setBiz({ ...biz, owner_name: e.target.value })} /></F>
              <F label="Owner WhatsApp *"><input className="input" placeholder="03xx xxxxxxx" value={biz.owner_phone} onChange={(e) => setBiz({ ...biz, owner_phone: e.target.value })} /></F>
              <F label="Office / pump phone"><input className="input" value={biz.biz_phone} onChange={(e) => setBiz({ ...biz, biz_phone: e.target.value })} /></F>
              <F label="Email"><input className="input" type="email" value={biz.biz_email} onChange={(e) => setBiz({ ...biz, biz_email: e.target.value })} /></F>
              <F label="Address"><input className="input" value={biz.biz_address} onChange={(e) => setBiz({ ...biz, biz_address: e.target.value })} /></F>
              <F label="City"><input className="input" value={biz.biz_city} onChange={(e) => setBiz({ ...biz, biz_city: e.target.value })} /></F>
              <F label="Oil company (OMC)"><select className="input" value={biz.omc} onChange={(e) => setBiz({ ...biz, omc: e.target.value })}>{OMCS.map((o) => <option key={o}>{o}</option>)}</select></F>
              <F label="Website"><input className="input" value={biz.website} onChange={(e) => setBiz({ ...biz, website: e.target.value })} /></F>
              <F label="NTN"><input className="input" value={biz.ntn} onChange={(e) => setBiz({ ...biz, ntn: e.target.value })} /></F>
              <F label="STRN"><input className="input" value={biz.strn} onChange={(e) => setBiz({ ...biz, strn: e.target.value })} /></F>
            </Grid>
            <F label="Line printed at the bottom of receipts"><input className="input" placeholder="e.g. Shukriya! Phir tashreef layein" value={biz.receipt_footer} onChange={(e) => setBiz({ ...biz, receipt_footer: e.target.value })} /></F>
            <div className="sm:col-span-2"><SocialFields value={biz} onChange={(k, v) => setBiz({ ...biz, [k]: v })} /></div>
          </>}
          {step === 1 && <>
            <p className="text-sm text-slate-600">This is the owner's (Admin / CEO) login with full access. Managers, salesmen and others are added later from Users & Roles.</p>
            <Grid>
              <F label="Your name *"><input className="input" value={admin.name} onChange={(e) => setAdmin({ ...admin, name: e.target.value })} /></F>
              <F label="Email (to sign in) *"><input className="input" type="email" autoComplete="username" value={admin.email} onChange={(e) => setAdmin({ ...admin, email: e.target.value })} /></F>
              <F label="Password * (8+ characters)"><input className="input" type="password" autoComplete="new-password" value={admin.password} onChange={(e) => setAdmin({ ...admin, password: e.target.value })} /></F>
              <F label="Password again *"><input className="input" type="password" autoComplete="new-password" value={admin.confirm} onChange={(e) => setAdmin({ ...admin, confirm: e.target.value })} /></F>
              <F label="4-digit PIN for the pump tablet (optional)"><input className="input" inputMode="numeric" maxLength={4} value={admin.pin} onChange={(e) => setAdmin({ ...admin, pin: e.target.value.replace(/\D/g, "") })} /></F>
              <F label="Your WhatsApp (for alerts and approvals)"><input className="input" placeholder={biz.owner_phone} value={admin.phone} onChange={(e) => setAdmin({ ...admin, phone: e.target.value })} /></F>
            </Grid>
          </>}
          {step === 2 && <>
            {stations.map((s, i) => (
              <div key={i} className="rounded-xl border border-slate-200 p-3">
                <div className="mb-2 flex items-center gap-2"><Fuel size={16} className="text-brand-600" /><b className="flex-1">Station {i + 1}</b>
                  {stations.length > 1 && <button className="text-slate-400 hover:text-red-600" aria-label="Remove station" onClick={() => setStations(stations.filter((_, j) => j !== i))}><Trash2 size={16} /></button>}</div>
                <Grid>
                  <F label="Station name *"><input className="input" placeholder={`${biz.name || "Pump"} — main`} value={s.name} onChange={(e) => setStation(i, { name: e.target.value })} /></F>
                  <F label="City"><input className="input" placeholder={biz.biz_city} value={s.city} onChange={(e) => setStation(i, { city: e.target.value })} /></F>
                  <F label="Address"><input className="input" value={s.address} onChange={(e) => setStation(i, { address: e.target.value })} /></F>
                  <F label="Timings"><input className="input" value={s.timings} onChange={(e) => setStation(i, { timings: e.target.value })} /></F>
                </Grid>
                <div className="mt-3 overflow-x-auto">
                  <table className="w-full min-w-[560px] text-sm">
                    <thead className="text-left text-xs text-slate-500"><tr><th className="pb-1">Tank</th><th>Fuel</th><th>Capacity (L)</th><th>Stock now (L)</th><th>Nozzles</th><th /></tr></thead>
                    <tbody>{s.tanks.map((t, k) => (
                      <tr key={k}>
                        <td className="pr-1 py-1"><input className="input" value={t.name} onChange={(e) => setTank(i, k, { name: e.target.value })} /></td>
                        <td className="pr-1"><select className="input" value={t.product} onChange={(e) => setTank(i, k, { product: e.target.value, name: t.name.replace(FUELS[t.product], FUELS[e.target.value]) })}>{Object.entries(FUELS).map(([p, l]) => <option key={p} value={p}>{l}</option>)}</select></td>
                        <td className="pr-1"><input className="input" type="number" min={1} placeholder="30000" value={t.capacity_l} onChange={(e) => setTank(i, k, { capacity_l: e.target.value })} /></td>
                        <td className="pr-1"><input className="input" type="number" min={0} placeholder="from dip" value={t.current_l} onChange={(e) => setTank(i, k, { current_l: e.target.value })} /></td>
                        <td className="pr-1"><input className="input w-16" type="number" min={0} max={12} value={t.nozzles} onChange={(e) => setTank(i, k, { nozzles: e.target.value })} /></td>
                        <td><button className="text-slate-400 hover:text-red-600" aria-label="Remove tank" onClick={() => setStation(i, { tanks: s.tanks.filter((_, j) => j !== k) })}><Trash2 size={15} /></button></td>
                      </tr>))}</tbody>
                  </table>
                </div>
                <button className="btn-secondary mt-2 px-2 py-1 text-xs" onClick={() => setStation(i, { tanks: [...s.tanks, newTank("PMG", s.tanks.length + 1)] })}><Plus size={13} /> Add tank</button>
              </div>
            ))}
            <button className="btn-secondary" onClick={() => setStations([...stations, { name: "", city: "", address: "", timings: "24 hours", tanks: [newTank("PMG", 1), newTank("HSD", 2)] }])}><Plus size={15} /> Add another station</button>
            <p className="text-xs text-slate-500">Nozzle meter readings start at 0 — the first salesman enters the real totalizer reading when opening the first shift. Dip charts, staff, khata accounts and suppliers are added later.</p>
          </>}
          {step === 3 && <>
            <p className="text-sm text-slate-600">Today's selling price per litre (from the latest OGRA notification). Change it any time on the Prices page.</p>
            <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">{used.map((p) => (
              <F key={p} label={`${FUELS[p]} — Rs / litre *`}><input className="input py-3 text-2xl" type="number" step="0.01" min={1} value={prices[p]} onChange={(e) => setPrices({ ...prices, [p]: e.target.value })} /></F>))}</div>
          </>}
          {step === 4 && (
            <div className="space-y-3 text-sm">
              <Row k="Business">{biz.name} · {biz.owner_name} ({biz.owner_phone}){biz.biz_city ? ` · ${biz.biz_city}` : ""}{biz.omc ? ` · ${biz.omc}` : ""}</Row>
              <Row k="Owner login">{admin.name} · {admin.email}{admin.pin ? " · PIN set" : ""}</Row>
              {stations.map((s, i) => <Row key={i} k={`Station ${i + 1}`}>{s.name}: {s.tanks.map((t) => `${t.name} (${FUELS[t.product]}, ${Number(t.capacity_l).toLocaleString()} L, ${t.nozzles} nozzles)`).join(" · ")}</Row>)}
              <Row k="Prices">{used.map((p) => `${FUELS[p]} Rs ${prices[p]}`).join(" · ")}</Row>
              <p className="rounded-lg bg-brand-50 p-3 text-brand-900">Standard expense categories, daily safety checks and all automations are switched on. You can change everything later in Settings.</p>
            </div>
          )}
          {err && <ErrorBox error={err} />}
          <div className="flex justify-between gap-2 pt-2">
            <button className="btn-secondary" disabled={step === 0 || busy} onClick={() => { setErr(null); setStep(step - 1); }}><ChevronLeft size={16} /> Back</button>
            {step < STEPS.length - 1
              ? <button className="btn-primary" onClick={next}>Next <ChevronRight size={16} /></button>
              : <button className="btn-primary px-6" disabled={busy} onClick={finish}><Check size={16} /> {busy ? "Setting up…" : "Finish & open the app"}</button>}
          </div>
        </div>
      </div>
    </div>
  );
}

const F = ({ label, children }: { label: string; children: ReactNode }) => <label className="block"><span className="label">{label}</span>{children}</label>;
const Grid = ({ children }: { children: ReactNode }) => <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">{children}</div>;
const Row = ({ k, children }: { k: string; children: ReactNode }) => <div className="flex gap-3 border-b border-slate-100 pb-2"><span className="w-28 shrink-0 text-slate-500">{k}</span><span>{children}</span></div>;

import { useEffect, useState, type ReactNode } from "react";
import { SocialFields } from "../components/BusinessSettings";
import { Navigate, useNavigate } from "react-router-dom";
import { Check, ChevronLeft, ChevronRight, Plus, Trash2, Upload, Building2, UserCog, Tag, ClipboardCheck, Gauge, MapPin, Palette, Sparkles, Cylinder } from "lucide-react";
import { api } from "../lib/api";
import { useAuth } from "../App";
import { ErrorBox, Loading } from "../components/ui";
import { applyBrand, loadBranding, useBranding } from "../lib/brand";

const FUELS: Record<string, string> = { PMG: "Petrol", HOBC: "Hi-Octane", HSD: "Diesel" };
const OMCS = ["PSO", "Shell", "TotalEnergies", "Attock (APL)", "GO", "Hascol", "Byco / Cnergyico", "Puma", "Other"];
const COLORS = ["#059669", "#0f766e", "#2563eb", "#1d4ed8", "#7c3aed", "#db2777", "#dc2626", "#ea580c", "#ca8a04", "#334155"];
type Meter = { label: string; totalizer: string };
type Tank = { name: string; product: string; capacity_l: string; current_l: string; meters: Meter[] };
type Station = { name: string; city: string; address: string; timings: string; tanks: Tank[] };
const newMeters = (n: number, product: string, from = 0): Meter[] => Array.from({ length: n }, (_, i) => ({ label: `${FUELS[product]} ${from + i + 1}`, totalizer: "" }));
const newTank = (product = "PMG", n = 1): Tank => ({ name: `Tank-${n} ${FUELS[product]}`, product, capacity_l: "", current_l: "", meters: newMeters(2, product) });
const newStation = (): Station => ({ name: "", city: "", address: "", timings: "24 hours", tanks: [newTank("PMG", 1), newTank("HSD", 2)] });

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

/** One screen per thing the pump needs; the optional branding screen can be skipped. */
const STEPS = [
  { key: "welcome", label: "Start", icon: Sparkles, title: "Welcome — let's set up your pump", ur: "خوش آمدید" },
  { key: "pump", label: "Pump", icon: Building2, title: "Your pump", ur: "پمپ کی معلومات" },
  { key: "owner", label: "Owner login", icon: UserCog, title: "Owner (CEO) login", ur: "مالک کا لاگ ان" },
  { key: "stations", label: "Stations", icon: MapPin, title: "Stations", ur: "اسٹیشن" },
  { key: "tanks", label: "Tanks", icon: Cylinder, title: "Underground tanks", ur: "ٹینک" },
  { key: "meters", label: "Meters", icon: Gauge, title: "Dispenser meters (nozzles)", ur: "میٹر / نوزل" },
  { key: "prices", label: "Prices", icon: Tag, title: "Today's prices", ur: "آج کا ریٹ" },
  { key: "brand", label: "Branding", icon: Palette, title: "Logo & details (optional)", ur: "لوگو اور تفصیل" },
  { key: "review", label: "Finish", icon: ClipboardCheck, title: "Check & finish", ur: "چیک کریں" },
] as const;
const DRAFT_KEY = "pumpai_setup_draft";

const emptyBiz = { name: "", owner_name: "", owner_phone: "", biz_phone: "", biz_email: "", biz_address: "", biz_city: "", omc: "PSO", ntn: "", strn: "", website: "", brand_color: "#059669", receipt_footer: "", logo: null as string | null,
  facebook: "", instagram: "", whatsapp: "", tiktok: "", youtube: "", twitter: "" };
const emptyAdmin = { name: "", email: "", password: "", confirm: "", pin: "", phone: "" };

/** First-run setup wizard: opens by itself on a fresh installation and walks the owner through the essentials, one screen at a time. */
export default function Setup() {
  const brand = useBranding();
  const { user, login } = useAuth();
  const nav = useNavigate();
  const [status, setStatus] = useState<any>(null);
  const [step, setStep] = useState(0);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [code, setCode] = useState("");
  const [biz, setBiz] = useState(emptyBiz);
  const [admin, setAdmin] = useState(emptyAdmin);
  const [stations, setStations] = useState<Station[]>([newStation()]);
  const [prices, setPrices] = useState<Record<string, string>>({ PMG: "", HOBC: "", HSD: "" });
  const [restored, setRestored] = useState(false);

  useEffect(() => { api("/setup/status").then(setStatus).catch((e) => setErr(e.message)); }, []);
  useEffect(() => { applyBrand(biz.brand_color); }, [biz.brand_color]);
  // a half-done setup survives a refresh or a dropped connection (passwords and the logo are never stored)
  useEffect(() => {
    try {
      const d = JSON.parse(localStorage.getItem(DRAFT_KEY) || "null");
      if (d && d.v === 2) {
        setBiz({ ...emptyBiz, ...d.biz }); setAdmin({ ...emptyAdmin, ...d.admin }); if (d.stations?.length) setStations(d.stations);
        setPrices({ ...prices, ...d.prices }); setStep(Math.min(d.step ?? 0, STEPS.length - 2)); setRestored(Boolean(d.step));
      }
    } catch { /* no draft */ }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  useEffect(() => {
    try {
      const { logo: _l, ...b } = biz; const { password: _p, confirm: _c, pin: _n, ...a } = admin;
      localStorage.setItem(DRAFT_KEY, JSON.stringify({ v: 2, step, biz: b, admin: a, stations, prices }));
    } catch { /* storage blocked */ }
  }, [step, biz, admin, stations, prices]);

  if (user) return <Navigate to="/" replace />;
  if (!status) return <Loading />;
  if (!status.needed) return <Navigate to="/login" replace />;

  const used = [...new Set(stations.flatMap((s) => s.tanks.map((t) => t.product)))];
  const key = STEPS[step].key;
  const problem = (): string | null => {
    if (key === "welcome" && status.needs_code && !code.trim()) return "Enter the setup code given by the installer · سیٹ اپ کوڈ لکھیں";
    if (key === "pump") {
      if (biz.name.trim().length < 2) return "Enter the pump / business name · پمپ کا نام لکھیں";
      if (biz.owner_name.trim().length < 2) return "Enter the owner's name · مالک کا نام";
      if (biz.owner_phone.replace(/\D/g, "").length < 10) return "Enter the owner's WhatsApp number · واٹس ایپ نمبر";
    }
    if (key === "owner") {
      if (admin.name.trim().length < 2) return "Enter your name";
      if (!/^\S+@\S+\.\S+$/.test(admin.email)) return "Enter a valid email";
      if (admin.password.length < 8) return "Password must be at least 8 characters";
      if (admin.password !== admin.confirm) return "The two passwords are not the same";
      if (admin.pin && !/^\d{4}$/.test(admin.pin)) return "PIN is 4 digits";
    }
    if (key === "stations") for (const s of stations) if (s.name.trim().length < 2) return "Give every station a name · ہر اسٹیشن کا نام";
    if (key === "tanks") for (const s of stations) {
      if (!s.tanks.length) return `${s.name}: add at least one tank`;
      for (const t of s.tanks) {
        if (!(Number(t.capacity_l) > 0)) return `${s.name} · ${t.name}: enter the tank capacity · گنجائش لکھیں`;
        if (Number(t.current_l) > Number(t.capacity_l)) return `${s.name} · ${t.name}: stock is more than capacity`;
      }
    }
    if (key === "meters") for (const s of stations) {
      if (!s.tanks.some((t) => t.meters.length)) return `${s.name}: add at least one meter · کم از کم ایک میٹر`;
      for (const t of s.tanks) for (const m of t.meters) if (m.totalizer !== "" && !(Number(m.totalizer) >= 0)) return `${t.name} · ${m.label}: reading must be a number`;
    }
    if (key === "prices") for (const p of used) if (!(Number(prices[p]) > 0)) return `Enter today's ${FUELS[p]} price · ${FUELS[p]} کا ریٹ`;
    return null;
  };
  const go = (n: number) => { setErr(null); setStep(n); window.scrollTo({ top: 0, behavior: "smooth" }); };
  const next = () => { const p = problem(); setErr(p); if (!p) go(step + 1); };
  const finish = async () => {
    setBusy(true); setErr(null);
    try {
      const { confirm: _c, ...a } = admin;
      const r = await api("/setup", { body: {
        code: code.trim() || undefined,
        business: Object.fromEntries(Object.entries(biz).filter(([, v]) => v !== "" && v !== null)),
        admin: { ...a, pin: a.pin || undefined, phone: a.phone || undefined },
        stations: stations.map((s) => {
          let no = 0; // meters are numbered 1, 2, 3… across the whole station, in the order shown
          return { name: s.name, city: s.city || undefined, address: s.address || undefined, timings: s.timings || undefined,
            tanks: s.tanks.map((t) => ({ name: t.name, product: t.product, capacity_l: Number(t.capacity_l), current_l: Number(t.current_l) || 0,
              nozzles: t.meters.map((m) => ({ meter_no: ++no, label: m.label.trim() || undefined, totalizer: Number(m.totalizer) || 0 })) })) };
        }),
        prices: Object.fromEntries(used.map((p) => [p, Number(prices[p])])),
      } });
      try { localStorage.removeItem(DRAFT_KEY); } catch { /* ignore */ }
      await login(r.token);
      await loadBranding();
      nav("/");
    } catch (e: any) { setErr(e.message); } finally { setBusy(false); }
  };
  const setStation = (i: number, patch: Partial<Station>) => setStations(stations.map((s, j) => (j === i ? { ...s, ...patch } : s)));
  const setTank = (i: number, k: number, patch: Partial<Tank>) => setStation(i, { tanks: stations[i].tanks.map((t, j) => (j === k ? { ...t, ...patch } : t)) });
  const setMeter = (i: number, k: number, m: number, patch: Partial<Meter>) => setTank(i, k, { meters: stations[i].tanks[k].meters.map((x, j) => (j === m ? { ...x, ...patch } : x)) });
  const setMeterCount = (i: number, k: number, n: number) => {
    const t = stations[i].tanks[k]; n = Math.max(0, Math.min(12, n));
    setTank(i, k, { meters: n <= t.meters.length ? t.meters.slice(0, n) : [...t.meters, ...newMeters(n - t.meters.length, t.product, t.meters.length)] });
  };
  const meterCount = stations.reduce((a, s) => a + s.tanks.reduce((b, t) => b + t.meters.length, 0), 0);

  return (
    <div className="min-h-screen bg-slate-50">
      <div className="bg-gradient-to-br from-brand-900 via-brand-700 to-brand-500 px-4 pb-16 pt-6 text-white">
        <div className="mx-auto flex max-w-2xl items-center gap-3">
          {biz.logo ? <img src={biz.logo} alt="" className="h-14 w-14 rounded-xl bg-white object-contain p-1" /> : <div className="text-4xl">⛽</div>}
          <div className="min-w-0"><h1 className="truncate text-2xl font-bold">{biz.name || "Set up your pump"}</h1><p className="text-sm text-white/80">PumpAI {status.version}{brand?.vendor?.name ? ` · installed by ${brand.vendor.name}` : ""} · step {step + 1} of {STEPS.length}</p></div>
        </div>
      </div>
      <div className="mx-auto -mt-10 max-w-2xl px-4 pb-10">
        <ol className="mb-4 flex gap-1 overflow-x-auto rounded-xl bg-white p-2 shadow-sm ring-1 ring-slate-200" aria-label="Setup steps">
          {STEPS.map((s, i) => (
            <li key={s.key}>
              <button type="button" disabled={i > step} onClick={() => go(i)} title={s.label}
                className={`flex items-center gap-1.5 whitespace-nowrap rounded-lg px-2.5 py-2 text-sm ${i === step ? "bg-brand-600 text-white" : i < step ? "text-brand-700" : "text-slate-400"}`}>
                {i < step ? <Check size={16} /> : <s.icon size={16} />}<span className="hidden sm:inline">{s.label}</span>
              </button>
            </li>
          ))}
        </ol>
        <div className="card space-y-4 p-5">
          <div><h2 className="text-lg font-bold">{STEPS[step].title}</h2><p className="font-urdu text-sm text-slate-500" lang="ur" dir="rtl">{STEPS[step].ur}</p></div>
          {restored && step > 0 && <p className="rounded-lg bg-amber-50 px-3 py-2 text-xs text-amber-900">Continuing where you left off. <button className="underline" onClick={() => { try { localStorage.removeItem(DRAFT_KEY); } catch { /* ignore */ } setBiz(emptyBiz); setAdmin(emptyAdmin); setStations([newStation()]); setPrices({ PMG: "", HOBC: "", HSD: "" }); setRestored(false); go(0); }}>Start over</button></p>}

          {key === "welcome" && <>
            <p className="text-sm text-slate-600">This takes about 5 minutes. One short form per screen — you can go back any time. Keep these ready:</p>
            <ul className="grid gap-2 text-sm sm:grid-cols-2">
              {[["Pump & owner", "name, owner's WhatsApp, OMC"], ["Owner login", "email + password for the CEO"], ["Tanks", "fuel, capacity, stock now (dip)"], ["Meters", "how many nozzles, today's reading on each"], ["Prices", "today's rate per litre"], ["Logo (optional)", "for receipts and the app icon"]].map(([k, v]) => (
                <li key={k} className="flex gap-2 rounded-lg bg-slate-50 px-3 py-2"><Check size={16} className="mt-0.5 shrink-0 text-brand-600" /><span><b>{k}</b><span className="block text-xs text-slate-500">{v}</span></span></li>))}
            </ul>
            {status.needs_code && <F label="Setup code (from the installer) *"><input className="input font-mono uppercase" value={code} onChange={(e) => setCode(e.target.value)} autoFocus /></F>}
          </>}

          {key === "pump" && <>
            <F label="Pump / business name *"><input className="input py-3 text-lg" placeholder="e.g. Al-Madina Petroleum" value={biz.name} onChange={(e) => setBiz({ ...biz, name: e.target.value })} autoFocus /></F>
            <Grid>
              <F label="Owner name *"><input className="input" value={biz.owner_name} onChange={(e) => setBiz({ ...biz, owner_name: e.target.value })} /></F>
              <F label="Owner WhatsApp *"><input className="input" inputMode="tel" placeholder="03xx xxxxxxx" value={biz.owner_phone} onChange={(e) => setBiz({ ...biz, owner_phone: e.target.value })} /></F>
              <F label="City"><input className="input" value={biz.biz_city} onChange={(e) => setBiz({ ...biz, biz_city: e.target.value })} /></F>
              <F label="Oil company (OMC)"><select className="input" value={biz.omc} onChange={(e) => setBiz({ ...biz, omc: e.target.value })}>{OMCS.map((o) => <option key={o}>{o}</option>)}</select></F>
            </Grid>
            <Hint>Phone, address, NTN, logo and social pages come on a later (optional) screen.</Hint>
          </>}

          {key === "owner" && <>
            <p className="text-sm text-slate-600">This is the owner's (Admin / CEO) login with full access. Managers, salesmen and others are added later from Users & Roles.</p>
            <Grid>
              <F label="Your name *"><input className="input" value={admin.name} onChange={(e) => setAdmin({ ...admin, name: e.target.value })} autoFocus /></F>
              <F label="Email (to sign in) *"><input className="input" type="email" autoComplete="username" value={admin.email} onChange={(e) => setAdmin({ ...admin, email: e.target.value })} /></F>
              <F label="Password * (8+ characters)"><input className="input" type="password" autoComplete="new-password" value={admin.password} onChange={(e) => setAdmin({ ...admin, password: e.target.value })} /></F>
              <F label="Password again *"><input className="input" type="password" autoComplete="new-password" value={admin.confirm} onChange={(e) => setAdmin({ ...admin, confirm: e.target.value })} /></F>
              <F label="4-digit PIN for the pump tablet (optional)"><input className="input" inputMode="numeric" maxLength={4} value={admin.pin} onChange={(e) => setAdmin({ ...admin, pin: e.target.value.replace(/\D/g, "") })} /></F>
              <F label="Your WhatsApp (for alerts and approvals)"><input className="input" inputMode="tel" placeholder={biz.owner_phone} value={admin.phone} onChange={(e) => setAdmin({ ...admin, phone: e.target.value })} /></F>
            </Grid>
          </>}

          {key === "stations" && <>
            <p className="text-sm text-slate-600">Most pumps have one station. Add more only if you run several pumps under this business.</p>
            {stations.map((s, i) => (
              <div key={i} className="rounded-xl border border-slate-200 p-3">
                <div className="mb-2 flex items-center gap-2"><MapPin size={16} className="text-brand-600" /><b className="flex-1">Station {i + 1}</b>
                  {stations.length > 1 && <button className="text-slate-400 hover:text-red-600" aria-label="Remove station" onClick={() => setStations(stations.filter((_, j) => j !== i))}><Trash2 size={16} /></button>}</div>
                <Grid>
                  <F label="Station name *"><input className="input" placeholder={`${biz.name || "Pump"} — main`} value={s.name} onChange={(e) => setStation(i, { name: e.target.value })} autoFocus={i === 0} /></F>
                  <F label="City"><input className="input" placeholder={biz.biz_city} value={s.city} onChange={(e) => setStation(i, { city: e.target.value })} /></F>
                  <F label="Address"><input className="input" value={s.address} onChange={(e) => setStation(i, { address: e.target.value })} /></F>
                  <F label="Timings"><input className="input" value={s.timings} onChange={(e) => setStation(i, { timings: e.target.value })} /></F>
                </Grid>
              </div>
            ))}
            <button className="btn-secondary" onClick={() => setStations([...stations, newStation()])}><Plus size={15} /> Add another station</button>
          </>}

          {key === "tanks" && <>
            <p className="text-sm text-slate-600">One card per underground tank. Stock now = today's dip reading (you can leave it 0 and dip later).</p>
            {stations.map((s, i) => (
              <div key={i} className="space-y-2">
                {stations.length > 1 && <div className="text-sm font-semibold text-slate-700">{s.name || `Station ${i + 1}`}</div>}
                {s.tanks.map((t, k) => (
                  <div key={k} className="rounded-xl border border-slate-200 p-3">
                    <div className="mb-2 flex items-center gap-2"><Cylinder size={16} className="text-brand-600" /><input className="input flex-1 font-medium" value={t.name} onChange={(e) => setTank(i, k, { name: e.target.value })} aria-label="Tank name" />
                      <button className="text-slate-400 hover:text-red-600" aria-label="Remove tank" onClick={() => setStation(i, { tanks: s.tanks.filter((_, j) => j !== k) })}><Trash2 size={15} /></button></div>
                    <div className="grid grid-cols-3 gap-2">
                      <F label="Fuel"><select className="input" value={t.product} onChange={(e) => setTank(i, k, { product: e.target.value, name: t.name.replace(FUELS[t.product], FUELS[e.target.value]), meters: t.meters.map((m, j) => ({ ...m, label: m.label.replace(FUELS[t.product], FUELS[e.target.value]) || `${FUELS[e.target.value]} ${j + 1}` })) })}>{Object.entries(FUELS).map(([p, l]) => <option key={p} value={p}>{l}</option>)}</select></F>
                      <F label="Capacity (L) *"><input className="input" type="number" inputMode="numeric" min={1} placeholder="30000" value={t.capacity_l} onChange={(e) => setTank(i, k, { capacity_l: e.target.value })} /></F>
                      <F label="Stock now (L)"><input className="input" type="number" inputMode="numeric" min={0} placeholder="dip" value={t.current_l} onChange={(e) => setTank(i, k, { current_l: e.target.value })} /></F>
                    </div>
                  </div>
                ))}
                <button className="btn-secondary px-2 py-1 text-xs" onClick={() => setStation(i, { tanks: [...s.tanks, newTank(s.tanks.some((t) => t.product === "PMG") ? "HSD" : "PMG", s.tanks.length + 1)] })}><Plus size={13} /> Add tank</button>
              </div>
            ))}
          </>}

          {key === "meters" && <>
            <p className="text-sm text-slate-600">How many dispenser meters (nozzles) draw from each tank, and the <b>totalizer reading on each meter right now</b> — the first shift opens from these numbers. <span className="font-urdu" lang="ur" dir="rtl">ہر میٹر کی موجودہ ریڈنگ لکھیں</span></p>
            {stations.map((s, i) => {
              let no = 0;
              return (
                <div key={i} className="space-y-2">
                  {stations.length > 1 && <div className="text-sm font-semibold text-slate-700">{s.name || `Station ${i + 1}`}</div>}
                  {s.tanks.map((t, k) => (
                    <div key={k} className="rounded-xl border border-slate-200 p-3">
                      <div className="mb-2 flex flex-wrap items-center gap-2">
                        <Gauge size={16} className="text-brand-600" /><b className="flex-1">{t.name}</b>
                        <label className="flex items-center gap-2 text-sm"><span className="text-slate-600">Meters</span>
                          <span className="inline-flex overflow-hidden rounded-lg border border-slate-300">
                            <button type="button" className="px-3 py-1 text-lg hover:bg-slate-50" aria-label="Fewer meters" onClick={() => setMeterCount(i, k, t.meters.length - 1)}>−</button>
                            <input className="w-12 border-x border-slate-300 py-1 text-center text-base font-semibold outline-none" type="number" inputMode="numeric" min={0} max={12} value={t.meters.length} onChange={(e) => setMeterCount(i, k, Number(e.target.value) || 0)} aria-label="Number of meters" />
                            <button type="button" className="px-3 py-1 text-lg hover:bg-slate-50" aria-label="More meters" onClick={() => setMeterCount(i, k, t.meters.length + 1)}>+</button>
                          </span></label>
                      </div>
                      {t.meters.length ? (
                        <div className="space-y-1.5">
                          {t.meters.map((m, j) => { no++; return (
                            <div key={j} className="grid grid-cols-[auto_1fr_1.25fr] items-center gap-2">
                              <span className="w-12 rounded-lg bg-slate-100 py-2 text-center text-xs font-bold text-slate-600">No.{no}</span>
                              <input className="input" value={m.label} onChange={(e) => setMeter(i, k, j, { label: e.target.value })} placeholder="name" aria-label={`Meter ${no} name`} />
                              <input className="input tabular-nums" type="number" inputMode="decimal" min={0} step="0.01" value={m.totalizer} onChange={(e) => setMeter(i, k, j, { totalizer: e.target.value })} placeholder="reading" aria-label={`Meter ${no} reading`} />
                            </div>); })}
                        </div>
                      ) : <p className="text-xs text-slate-400">No meters on this tank.</p>}
                    </div>
                  ))}
                </div>
              );
            })}
            <Hint>{meterCount} meter{meterCount === 1 ? "" : "s"} in all. A meter's reading can't go backwards later, so copy the totalizer exactly as it shows today. Leave it blank if you'll enter it at the first shift.</Hint>
          </>}

          {key === "prices" && <>
            <p className="text-sm text-slate-600">Today's selling price per litre (from the latest OGRA notification). Change it any time on the Prices page.</p>
            <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">{used.map((p, i) => (
              <F key={p} label={`${FUELS[p]} — Rs / litre *`}><input className="input py-3 text-2xl tabular-nums" type="number" inputMode="decimal" step="0.01" min={1} value={prices[p]} onChange={(e) => setPrices({ ...prices, [p]: e.target.value })} autoFocus={i === 0} /></F>))}</div>
          </>}

          {key === "brand" && <>
            <p className="text-sm text-slate-600">All optional — these print on receipts, statements and bills and set the app's colour. You can fill them later in Settings.</p>
            <div className="flex flex-wrap items-center gap-4">
              <label className="flex h-24 w-24 cursor-pointer flex-col items-center justify-center rounded-xl border-2 border-dashed border-slate-300 bg-white text-xs text-slate-500 hover:border-brand-500">
                {biz.logo ? <img src={biz.logo} alt="Logo" className="h-full w-full rounded-xl object-contain" /> : <><Upload size={20} />Logo</>}
                <input type="file" accept="image/*" className="hidden" onChange={async (e) => { const f = e.target.files?.[0]; if (f) setBiz({ ...biz, logo: await squareLogo(f) }); }} />
              </label>
              <div className="min-w-0 flex-1">
                <span className="label">App colour</span>
                <div className="flex flex-wrap gap-1.5">{COLORS.map((c) => <button key={c} type="button" aria-label={`Colour ${c}`} onClick={() => setBiz({ ...biz, brand_color: c })} className={`h-7 w-7 rounded-full ring-offset-2 ${biz.brand_color === c ? "ring-2 ring-slate-900" : ""}`} style={{ background: c }} />)}
                  <input type="color" aria-label="Other colour" className="h-7 w-9 cursor-pointer rounded" value={biz.brand_color} onChange={(e) => setBiz({ ...biz, brand_color: e.target.value })} /></div>
              </div>
            </div>
            <Grid>
              <F label="Office / pump phone"><input className="input" inputMode="tel" value={biz.biz_phone} onChange={(e) => setBiz({ ...biz, biz_phone: e.target.value })} /></F>
              <F label="Email"><input className="input" type="email" value={biz.biz_email} onChange={(e) => setBiz({ ...biz, biz_email: e.target.value })} /></F>
              <F label="Address"><input className="input" value={biz.biz_address} onChange={(e) => setBiz({ ...biz, biz_address: e.target.value })} /></F>
              <F label="Website"><input className="input" value={biz.website} onChange={(e) => setBiz({ ...biz, website: e.target.value })} /></F>
              <F label="NTN"><input className="input" value={biz.ntn} onChange={(e) => setBiz({ ...biz, ntn: e.target.value })} /></F>
              <F label="STRN"><input className="input" value={biz.strn} onChange={(e) => setBiz({ ...biz, strn: e.target.value })} /></F>
            </Grid>
            <F label="Line printed at the bottom of receipts"><input className="input" placeholder="e.g. Shukriya! Phir tashreef layein" value={biz.receipt_footer} onChange={(e) => setBiz({ ...biz, receipt_footer: e.target.value })} /></F>
            <SocialFields value={biz} onChange={(k, v) => setBiz({ ...biz, [k]: v })} />
          </>}

          {key === "review" && (
            <div className="space-y-3 text-sm">
              <Row k="Pump" onEdit={() => go(1)}>{biz.name} · {biz.owner_name} ({biz.owner_phone}){biz.biz_city ? ` · ${biz.biz_city}` : ""}{biz.omc ? ` · ${biz.omc}` : ""}</Row>
              <Row k="Owner login" onEdit={() => go(2)}>{admin.name} · {admin.email}{admin.pin ? " · PIN set" : ""}</Row>
              {stations.map((s, i) => { let no = 0; return (
                <Row key={i} k={stations.length > 1 ? `Station ${i + 1}` : "Station"} onEdit={() => go(3)}>
                  <b>{s.name}</b>{s.city ? ` · ${s.city}` : ""}
                  <ul className="mt-1 space-y-1">{s.tanks.map((t, k) => (
                    <li key={k}><span className="font-medium">{t.name}</span> — {FUELS[t.product]}, {Number(t.capacity_l).toLocaleString()} L capacity, {Number(t.current_l || 0).toLocaleString()} L now
                      <span className="block text-xs text-slate-500">{t.meters.length ? t.meters.map((m) => { no++; return `No.${no} ${m.label}${m.totalizer ? ` @ ${Number(m.totalizer).toLocaleString()}` : ""}`; }).join(" · ") : "no meters"}</span></li>))}</ul>
                </Row>); })}
              <Row k="Prices" onEdit={() => go(6)}>{used.map((p) => `${FUELS[p]} Rs ${prices[p]}`).join(" · ")}</Row>
              <Row k="Branding" onEdit={() => go(7)}>{[biz.logo ? "logo" : null, biz.biz_phone, biz.biz_address, biz.ntn ? `NTN ${biz.ntn}` : null].filter(Boolean).join(" · ") || <span className="text-slate-400">skipped — can be added in Settings</span>}</Row>
              <p className="rounded-lg bg-brand-50 p-3 text-brand-900">Standard expense categories, daily safety checks and all automations are switched on. You can change everything later in Settings.</p>
            </div>
          )}

          {err && <ErrorBox error={err} />}
          <div className="flex items-center justify-between gap-2 pt-2">
            <button className="btn-secondary" disabled={step === 0 || busy} onClick={() => go(step - 1)}><ChevronLeft size={16} /> Back</button>
            <div className="flex gap-2">
              {key === "brand" && <button className="btn-secondary" onClick={() => go(step + 1)}>Skip</button>}
              {key === "review"
                ? <button className="btn-primary px-6 py-3 text-base" disabled={busy} onClick={finish}><Check size={18} /> {busy ? "Setting up…" : "Finish & open the app"}</button>
                : <button className="btn-primary px-6 py-3 text-base" onClick={next}>{key === "welcome" ? "Start" : "Next"} <ChevronRight size={18} /></button>}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

const F = ({ label, children }: { label: string; children: ReactNode }) => <label className="block"><span className="label">{label}</span>{children}</label>;
const Grid = ({ children }: { children: ReactNode }) => <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">{children}</div>;
const Hint = ({ children }: { children: ReactNode }) => <p className="rounded-lg bg-slate-50 px-3 py-2 text-xs text-slate-500">{children}</p>;
const Row = ({ k, children, onEdit }: { k: string; children: ReactNode; onEdit: () => void }) => (
  <div className="flex gap-3 border-b border-slate-100 pb-2"><span className="w-24 shrink-0 text-slate-500">{k}</span><span className="min-w-0 flex-1">{children}</span>
    <button type="button" className="shrink-0 text-xs font-medium text-brand-700 hover:underline" onClick={onEdit}>Edit</button></div>
);

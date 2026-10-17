import { useEffect, useState } from "react";
import { Navigate, useNavigate } from "react-router-dom";
import { Delete, Mail, ArrowLeft, Fuel, Banknote, Container, UserCog, Crown } from "lucide-react";
import { api, getDevice, setDevice } from "../lib/api";
import { useAuth } from "../App";
import { ErrorBox } from "../components/ui";
import { useBranding } from "../lib/brand";
import { InstallAppButton } from "../components/InstallApp";

const ROLE: Record<string, string> = { admin: "Admin (CEO)", manager: "Manager", salesman: "Salesman", wholesale: "Wholesale", cashier: "Cashier" };
const ROLE_TONE: Record<string, string> = { salesman: "bg-emerald-500", manager: "bg-blue-500", wholesale: "bg-violet-500", cashier: "bg-amber-500", admin: "bg-slate-700" };
/** The portals shown on the first login screen — tap one to go straight to that module's sign-in. */
const ROLES: { key: string; label: string; ur: string; icon: any }[] = [
  { key: "salesman", label: "Salesman", ur: "سیلزمین", icon: Fuel },
  { key: "cashier", label: "Cashier", ur: "کیشیئر", icon: Banknote },
  { key: "wholesale", label: "Wholesale", ur: "ہول سیل", icon: Container },
  { key: "manager", label: "Manager", ur: "مینیجر", icon: UserCog },
  { key: "admin", label: "Admin (CEO)", ur: "ایڈمن", icon: Crown },
];
const DEMO_EMAIL: Record<string, string> = { admin: "admin@pumpai.pk", manager: "manager@pumpai.pk", salesman: "salesman@pumpai.pk", wholesale: "wholesale@pumpai.pk", cashier: "cashier@pumpai.pk" };

export default function Login() {
  const { user, login } = useAuth();
  const nav = useNavigate();
  const brand = useBranding();
  const enrolled = Boolean(getDevice());
  // first the portal chooser (roles), then the chosen portal's PIN or email sign-in
  const [screen, setScreen] = useState<"roles" | "pin" | "email">("roles");
  const [role, setRole] = useState<string | null>(null);
  const [people, setPeople] = useState<any[] | null>(null);
  // on an enrolled device we already know who has a PIN, so a role tap can jump straight to their PIN
  useEffect(() => {
    if (!enrolled) return;
    api<any[]>("/auth/pin-users", { headers: { "x-device": getDevice() ?? "" } })
      .then(setPeople).catch((e) => { if (e.status === 401) setDevice(null); });
  }, [enrolled]);
  if (user) return <Navigate to="/" replace />;
  if (brand?.setup_needed) return <Navigate to="/setup" replace />;
  const done = async (r: any) => { if (r.device_token) setDevice(r.device_token); await login(r.token); nav("/"); };
  const pickRole = (rl: string) => { setRole(rl); setScreen(enrolled && (people ?? []).some((p) => p.role === rl) ? "pin" : "email"); };
  const toEmail = (rl: string | null) => { setRole(rl); setScreen("email"); };
  const roleHasPin = (rl: string | null) => enrolled && !!rl && (people ?? []).some((p) => p.role === rl);
  return (
    <div className="flex min-h-screen items-center justify-center bg-gradient-to-br from-brand-900 via-brand-700 to-brand-500 p-4">
      <div className="w-full max-w-md">
        <div className="mb-6 text-center text-white">
          {brand?.logo_url ? <img src={brand.logo_url} alt="" className="mx-auto h-20 w-20 rounded-2xl bg-white object-contain p-1.5 shadow" /> : <div className="text-5xl">⛽</div>}
          <h1 className="mt-2 text-2xl font-bold">{brand?.name ?? "PumpAI"}</h1>
          <p className="text-white/80">{brand?.name && brand.name !== "PumpAI" ? "PumpAI — forecourt management & WhatsApp CRM" : "AI WhatsApp CRM & forecourt management"}</p>
          <div className="mt-3 flex justify-center"><div className="w-56"><InstallAppButton variant="login" /></div></div>
        </div>
        {screen === "roles" && <RoleChooser people={people} enrolled={enrolled} onPick={pickRole} onEmail={() => toEmail(null)} onAll={() => { setRole(null); setScreen("pin"); }} />}
        {screen === "pin" && <PinLogin people={people} roleFilter={role} onDone={done} onEmail={() => toEmail(role)} onBack={() => setScreen("roles")} />}
        {screen === "email" && <EmailLogin demo={Boolean(brand?.demo)} role={role} onDone={done} onBack={() => setScreen("roles")} onPin={roleHasPin(role) ? () => setScreen("pin") : undefined} />}
        <p className="mt-4 text-center text-xs text-white/70"><a className="underline" href="/privacy">Privacy</a> · <a className="underline" href="/terms">Terms</a></p>
      </div>
    </div>
  );
}

/** The portal cards: tap Salesman / Cashier / Wholesale / Manager / Admin, or Email, to open that sign-in. */
function RoleChooser({ people, enrolled, onPick, onEmail, onAll }: { people: any[] | null; enrolled: boolean; onPick: (r: string) => void; onEmail: () => void; onAll: () => void }) {
  return (
    <div className="card space-y-4 p-5">
      <h2 className="text-center text-lg font-semibold">Apna portal chunein <span className="block font-urdu text-base font-normal text-slate-500">اپنا پورٹل منتخب کریں</span></h2>
      <div className="grid grid-cols-2 gap-3">
        {ROLES.map((r) => (
          <button key={r.key} onClick={() => onPick(r.key)}
            className="flex flex-col items-center gap-2 rounded-xl border-2 border-slate-200 p-4 hover:border-brand-500 hover:bg-emerald-50 active:scale-95">
            <span className={`flex h-14 w-14 items-center justify-center rounded-full text-white ${ROLE_TONE[r.key]}`}><r.icon size={26} /></span>
            <span className="font-semibold leading-tight">{r.label}</span>
            <span className="font-urdu text-sm text-slate-500">{r.ur}</span>
            {enrolled && (people ?? []).some((p) => p.role === r.key) && <span className="rounded-full bg-emerald-100 px-2 text-[11px] font-medium text-emerald-700">PIN ready</span>}
          </button>
        ))}
        <button onClick={onEmail}
          className="flex flex-col items-center justify-center gap-2 rounded-xl border-2 border-dashed border-slate-300 p-4 hover:border-brand-500 hover:bg-slate-50 active:scale-95">
          <span className="flex h-14 w-14 items-center justify-center rounded-full bg-slate-100 text-slate-600"><Mail size={24} /></span>
          <span className="font-semibold leading-tight">Email sign-in</span>
          <span className="font-urdu text-sm text-slate-500">ای میل</span>
        </button>
      </div>
      {enrolled && people && people.length > 0 && <button onClick={onAll} className="w-full text-sm text-brand-600 hover:underline">All users with a PIN · <span className="font-urdu">سب</span></button>}
    </div>
  );
}

/** Tap your name, then type your 4-digit PIN on big buttons. Filtered to the chosen portal when a role is picked. */
function PinLogin({ people, roleFilter, onDone, onEmail, onBack }: { people: any[] | null; roleFilter: string | null; onDone: (r: any) => Promise<void>; onEmail: () => void; onBack: () => void }) {
  const [who, setWho] = useState<any>(null);
  const [pin, setPin] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const list = roleFilter ? (people ?? []).filter((p) => p.role === roleFilter) : people;
  const press = async (d: string) => {
    if (busy) return;
    const next = (pin + d).slice(0, 4);
    setPin(next);
    setError(null);
    if (next.length < 4) return;
    setBusy(true);
    try { await onDone(await api("/auth/pin", { body: { user_id: who.id, pin: next }, headers: { "x-device": getDevice() ?? "" } })); }
    catch (e: any) { setError(e.message); setPin(""); }
    finally { setBusy(false); }
  };
  return (
    <div className="card space-y-4 p-5">
      {!who ? (
        <>
          <button onClick={onBack} className="flex items-center gap-1 text-sm text-slate-500 hover:text-slate-700"><ArrowLeft size={16} /> Portals · <span className="font-urdu">پورٹل</span></button>
          <h2 className="text-center text-lg font-semibold">{roleFilter ? `${ROLE[roleFilter]} — who are you?` : "Who are you?"} <span className="block font-urdu text-base font-normal text-slate-500">آپ کون ہیں؟</span></h2>
          {error && <ErrorBox error={error} />}
          {list && !list.length ? (
            <div className="space-y-3 text-center">
              <p className="text-sm text-slate-500">{roleFilter ? `${ROLE[roleFilter]} ka koi PIN is device par set nahi.` : "No one has a PIN yet."} <span className="font-urdu">ای میل سے سائن ان کریں</span></p>
              <button onClick={onEmail} className="btn-primary w-full"><Mail size={16} /> Sign in with email</button>
            </div>
          ) : (
            <div className="grid grid-cols-2 gap-3">
              {list?.map((p) => (
                <button key={p.id} onClick={() => { setWho(p); setPin(""); setError(null); }}
                  className="flex flex-col items-center gap-2 rounded-xl border-2 border-slate-200 p-4 hover:border-brand-500 hover:bg-emerald-50 active:scale-95">
                  <span className={`flex h-14 w-14 items-center justify-center rounded-full text-2xl font-bold text-white ${ROLE_TONE[p.role] ?? "bg-slate-500"}`}>{p.name[0]}</span>
                  <span className="text-center font-semibold leading-tight">{p.name}</span>
                  <span className="text-xs text-slate-500">{ROLE[p.role]}{p.station_name ? ` · ${p.station_name}` : ""}</span>
                </button>
              ))}
            </div>
          )}
        </>
      ) : (
        <>
          <button onClick={() => { setWho(null); setPin(""); setError(null); }} className="flex items-center gap-1 text-sm text-slate-500"><ArrowLeft size={16} /> Back</button>
          <div className="text-center">
            <div className="text-lg font-semibold">{who.name}</div>
            <div className="text-sm text-slate-500">Enter your PIN <span className="font-urdu">اپنا پن لکھیں</span></div>
          </div>
          <div className="flex justify-center gap-4" aria-label={`${pin.length} of 4 digits entered`}>
            {[0, 1, 2, 3].map((i) => <span key={i} className={`h-5 w-5 rounded-full border-2 ${i < pin.length ? "border-brand-600 bg-brand-600" : "border-slate-300"}`} />)}
          </div>
          {error && <ErrorBox error={error} />}
          <div className="grid grid-cols-3 gap-3">
            {["1", "2", "3", "4", "5", "6", "7", "8", "9"].map((d) => (
              <button key={d} onClick={() => press(d)} disabled={busy} className="h-16 rounded-xl bg-slate-100 text-2xl font-semibold hover:bg-slate-200 active:scale-95">{d}</button>
            ))}
            <span />
            <button onClick={() => press("0")} disabled={busy} className="h-16 rounded-xl bg-slate-100 text-2xl font-semibold hover:bg-slate-200 active:scale-95">0</button>
            <button onClick={() => setPin(pin.slice(0, -1))} aria-label="Delete last digit" className="flex h-16 items-center justify-center rounded-xl bg-slate-100 hover:bg-slate-200"><Delete /></button>
          </div>
        </>
      )}
      <button onClick={onEmail} className="flex w-full items-center justify-center gap-1.5 text-sm text-slate-500 hover:text-slate-700"><Mail size={14} /> Sign in with email instead</button>
    </div>
  );
}

function EmailLogin({ demo, role, onDone, onBack, onPin }: { demo: boolean; role: string | null; onDone: (r: any) => Promise<void>; onBack: () => void; onPin?: () => void }) {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  // the demo pump fills in a sample login for the chosen portal (admin by default); a real installation starts empty
  useEffect(() => { if (demo) { setEmail(DEMO_EMAIL[role ?? "admin"] ?? "admin@pumpai.pk"); setPassword("demo1234"); } }, [demo, role]);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [twofa, setTwofa] = useState<{ user_id: number; phone_hint?: string } | null>(null);
  const [code, setCode] = useState("");
  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const r: any = await api("/auth/login", { body: { email, password } });
      if (r.twofa) { setTwofa({ user_id: r.user_id, phone_hint: r.phone_hint }); setCode(""); }
      else await onDone(r);
    } catch (err: any) { setError(err.message); }
    finally { setBusy(false); }
  };
  const verify = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true); setError(null);
    try { await onDone(await api("/auth/verify-2fa", { body: { user_id: twofa!.user_id, code } })); }
    catch (err: any) { setError(err.message); }
    finally { setBusy(false); }
  };
  if (twofa) return (
    <form onSubmit={verify} className="card space-y-4 p-6">
      <div><h2 className="text-lg font-semibold">Verification code</h2>
        <p className="text-sm text-slate-500">Aap ke WhatsApp number {twofa.phone_hint ?? ""} par ek 6-digit code bheja gaya hai. Wo yahan likhein.</p></div>
      {error && <ErrorBox error={error} />}
      <input className="input text-center text-2xl tracking-[0.4em]" inputMode="numeric" autoFocus maxLength={6} value={code} onChange={(e) => setCode(e.target.value.replace(/\D/g, ""))} placeholder="______" />
      <button className="btn-primary w-full" disabled={busy || code.length !== 6}>{busy ? "Checking…" : "Verify & sign in"}</button>
      <button type="button" onClick={() => { setTwofa(null); setError(null); }} className="w-full text-sm text-slate-500 hover:underline">← Back</button>
    </form>
  );
  return (
    <form onSubmit={submit} className="card space-y-4 p-6">
      <div className="flex items-center justify-between">
        <button type="button" onClick={onBack} className="flex items-center gap-1 text-sm text-slate-500 hover:text-slate-700"><ArrowLeft size={16} /> Portals</button>
        {role && <span className={`rounded-full px-2.5 py-0.5 text-xs font-semibold text-white ${ROLE_TONE[role] ?? "bg-slate-600"}`}>{ROLE[role]} sign-in</span>}
      </div>
      {error && <ErrorBox error={error} />}
      <label className="block"><span className="label">Email</span><input className="input" type="email" autoComplete="username" value={email} onChange={(e) => setEmail(e.target.value)} required /></label>
      <label className="block"><span className="label">Password</span><input className="input" type="password" autoComplete="current-password" value={password} onChange={(e) => setPassword(e.target.value)} required /></label>
      <button className="btn-primary w-full" disabled={busy}>{busy ? "Signing in…" : "Sign in"}</button>
      {onPin && <button type="button" onClick={onPin} className="w-full text-sm text-brand-600 hover:underline">Quick sign in with PIN</button>}
      {demo && <div className="rounded-lg bg-slate-50 p-3 text-xs text-slate-600">
        <div className="mb-1.5 font-medium">Demo accounts (password <b>demo1234</b>, PIN in brackets)</div>
        {[["admin@pumpai.pk", "Admin (CEO) — password only"], ["manager@pumpai.pk", "Manager [2222]"], ["salesman@pumpai.pk", "Salesman [3333]"], ["wholesale@pumpai.pk", "Wholesale officer [4444]"], ["cashier@pumpai.pk", "Cashier [5555]"]].map(([e, l]) => (
          <button type="button" key={e} onClick={() => { setEmail(e); setPassword("demo1234"); }} className="flex w-full justify-between rounded px-1.5 py-1 text-left hover:bg-white">
            <span className="font-mono">{e}</span><span className="text-slate-500">{l}</span>
          </button>
        ))}
        <p className="mt-1.5 text-slate-500">After one email sign-in, this device shows the quick PIN screen.</p>
      </div>}
      {!demo && <p className="text-center text-xs text-slate-500">After one email sign-in, this device shows the quick PIN screen. Forgot your password? Ask the owner / admin.</p>}
    </form>
  );
}

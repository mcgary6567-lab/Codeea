import { useEffect, useState } from "react";
import { Navigate, useNavigate } from "react-router-dom";
import { Delete, Mail, ArrowLeft } from "lucide-react";
import { api, getDevice, setDevice } from "../lib/api";
import { useAuth } from "../App";
import { ErrorBox } from "../components/ui";
import { useBranding } from "../lib/brand";
import { InstallAppButton } from "../components/InstallApp";

const ROLE: Record<string, string> = { admin: "Admin (CEO)", manager: "Manager", salesman: "Salesman", wholesale: "Wholesale" };
const ROLE_TONE: Record<string, string> = { salesman: "bg-emerald-500", manager: "bg-blue-500", wholesale: "bg-violet-500", cashier: "bg-amber-500", admin: "bg-slate-700" };

export default function Login() {
  const { user, login } = useAuth();
  const nav = useNavigate();
  const [mode, setMode] = useState<"pin" | "email">(getDevice() ? "pin" : "email");
  const brand = useBranding();
  if (user) return <Navigate to="/" replace />;
  if (brand?.setup_needed) return <Navigate to="/setup" replace />;
  const done = async (r: any) => { if (r.device_token) setDevice(r.device_token); await login(r.token); nav("/"); };
  return (
    <div className="flex min-h-screen items-center justify-center bg-gradient-to-br from-brand-900 via-brand-700 to-brand-500 p-4">
      <div className="w-full max-w-md">
        <div className="mb-6 text-center text-white">
          {brand?.logo_url ? <img src={brand.logo_url} alt="" className="mx-auto h-20 w-20 rounded-2xl bg-white object-contain p-1.5 shadow" /> : <div className="text-5xl">⛽</div>}
          <h1 className="mt-2 text-2xl font-bold">{brand?.name ?? "PumpAI"}</h1>
          <p className="text-white/80">{brand?.name && brand.name !== "PumpAI" ? "PumpAI — forecourt management & WhatsApp CRM" : "AI WhatsApp CRM & forecourt management"}</p>
          <div className="mt-3 flex justify-center"><div className="w-56"><InstallAppButton variant="login" /></div></div>
        </div>
        {mode === "pin" ? <PinLogin onDone={done} onEmail={() => setMode("email")} /> : <EmailLogin demo={Boolean(brand?.demo)} onDone={done} onPin={getDevice() ? () => setMode("pin") : undefined} />}
        <p className="mt-4 text-center text-xs text-white/70"><a className="underline" href="/privacy">Privacy</a> · <a className="underline" href="/terms">Terms</a></p>
      </div>
    </div>
  );
}

/** Tap your name, then type your 4-digit PIN on big buttons. */
function PinLogin({ onDone, onEmail }: { onDone: (r: any) => Promise<void>; onEmail: () => void }) {
  const [people, setPeople] = useState<any[] | null>(null);
  const [who, setWho] = useState<any>(null);
  const [pin, setPin] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    api<any[]>("/auth/pin-users", { headers: { "x-device": getDevice() ?? "" } })
      .then(setPeople).catch((e) => { setError(e.message); if (e.status === 401) { setDevice(null); onEmail(); } });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
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
          <h2 className="text-center text-lg font-semibold">Who are you? <span className="font-urdu text-base font-normal text-slate-500">آپ کون ہیں؟</span></h2>
          {error && <ErrorBox error={error} />}
          <div className="grid grid-cols-2 gap-3">
            {people?.map((p) => (
              <button key={p.id} onClick={() => { setWho(p); setPin(""); setError(null); }}
                className="flex flex-col items-center gap-2 rounded-xl border-2 border-slate-200 p-4 hover:border-brand-500 hover:bg-emerald-50 active:scale-95">
                <span className={`flex h-14 w-14 items-center justify-center rounded-full text-2xl font-bold text-white ${ROLE_TONE[p.role] ?? "bg-slate-500"}`}>{p.name[0]}</span>
                <span className="text-center font-semibold leading-tight">{p.name}</span>
                <span className="text-xs text-slate-500">{ROLE[p.role]}{p.station_name ? ` · ${p.station_name}` : ""}</span>
              </button>
            ))}
          </div>
          {people && !people.length && <p className="text-center text-sm text-slate-500">No one has a PIN yet. The admin can set PINs on the Users page.</p>}
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

function EmailLogin({ demo, onDone, onPin }: { demo: boolean; onDone: (r: any) => Promise<void>; onPin?: () => void }) {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  // the demo pump fills in a sample login; a real installation starts empty
  useEffect(() => { if (demo) { setEmail((x) => x || "admin@pumpai.pk"); setPassword((x) => x || "demo1234"); } }, [demo]);
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

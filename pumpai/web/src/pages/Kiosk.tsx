import { useState } from "react";
import { Camera, CheckCircle2, LogIn, LogOut } from "lucide-react";
import { api, useApi } from "../lib/api";
import { PageHeader, Loading, Empty, useToast } from "../components/ui";
import { LiveSelfie } from "../components/LiveSelfie";

const Ur = ({ children, className = "" }: { children: React.ReactNode; className?: string }) => <span lang="ur" dir="rtl" className={`font-urdu ${className}`}>{children}</span>;
const ROLE: Record<string, string> = { salesman: "Salesman", manager: "Manager", wholesale: "Wholesale", cashier: "Cashier", staff: "Staff" };
const t = (iso?: string | null) => (iso ? new Date(iso).toLocaleTimeString("en-PK", { hour: "2-digit", minute: "2-digit" }) : "—");

/**
 * Attendance kiosk on the shared pump tablet: whoever is signed in marks attendance for any person
 * at the pump — helper, cleaner, driver, guard, salesman — with a live selfie + location, check-in
 * and check-out both. Aim the camera at the person and tap.
 */
export default function Kiosk() {
  const { data, reload } = useApi<any[]>("/attendance/kiosk", 30_000);
  const [act, setAct] = useState<null | { user: any; kind: "in" | "out" }>(null);
  const toast = useToast();
  const mark = async (p: { photo_id: number; lat: number; lng: number; accuracy: number }) => {
    if (!act) return;
    // throws on failure so the camera stays open with the message
    await api(`/attendance/kiosk/check-${act.kind}`, { body: { user_id: act.user.id, ...p } });
    const name = act.user.name, kind = act.kind;
    setAct(null);
    toast("ok", kind === "in" ? `${name} ki haaziri lag gayi · حاضری` : `${name} check-out ho gaya · چھٹی`);
    reload();
  };
  return (
    <div className="space-y-4">
      <PageHeader title="Staff attendance · حاضری" subtitle="Manager har salesman aur staff (helper, cleaner, driver, guard) ki selfie + location se haaziri lagaye — aana aur jaana dono. Cashier, wholesale aur manager khud shaamil nahi." />
      {!data ? <Loading /> : !data.length ? <Empty>Koi staff nahi. Staff page se add karein.</Empty> : (
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
          {data.map((u) => (
            <div key={u.id} className={`card p-4 ${u.checked_out ? "opacity-70" : ""}`}>
              <div className="flex items-start justify-between gap-2">
                <div className="min-w-0">
                  <div className="truncate font-semibold">{u.name}</div>
                  <div className="text-xs text-slate-500">{u.job_title || ROLE[u.role] || u.role}{u.duty_start ? ` · duty ${u.duty_start}` : ""}</div>
                </div>
                {u.checked_out ? <CheckCircle2 size={20} className="shrink-0 text-slate-400" /> : u.checked_in ? <span className="shrink-0 rounded-full bg-emerald-100 px-2 py-0.5 text-xs font-medium text-emerald-700">On duty</span> : null}
              </div>
              <div className="mt-2 text-xs text-slate-500">{u.checked_in ? `Aaya ${t(u.check_in)}` : "Abhi nahi aaya"}{u.checked_out ? ` · Gaya ${t(u.check_out)}` : ""}</div>
              <div className="mt-3">
                {!u.checked_in ? (
                  <button className="btn-primary w-full !py-3 text-base" onClick={() => setAct({ user: u, kind: "in" })}><LogIn size={18} /> Check in · <Ur>آمد</Ur></button>
                ) : !u.checked_out ? (
                  <button className="btn-secondary w-full !py-3 text-base" onClick={() => setAct({ user: u, kind: "out" })}><LogOut size={18} /> Check out · <Ur>رخصت</Ur></button>
                ) : (
                  <div className="flex items-center justify-center gap-1 rounded-xl bg-slate-50 py-3 text-sm text-slate-500"><CheckCircle2 size={16} /> Poora din darj</div>
                )}
              </div>
            </div>
          ))}
        </div>
      )}
      <p className="text-xs text-slate-500"><Camera size={13} className="mr-1 inline" /> Har check-in aur check-out par us bande ki live selfie aur location record hoti hai — baad me Staff → Attendance par dekh sakte hain.</p>
      <LiveSelfie open={!!act} title={act ? `${act.user.name} — ${act.kind === "in" ? "check in" : "check out"} · selfie + location` : ""} onClose={() => setAct(null)} onDone={mark} />
    </div>
  );
}

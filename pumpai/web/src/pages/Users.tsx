import { useEffect, useState } from "react";
import { Plus, Pencil, Trash2, Check, X, ShieldCheck, Briefcase, Fuel, Container, KeyRound, Banknote, LogOut } from "lucide-react";
import { api, useApi } from "../lib/api";
import { Badge, Field, Loading, Modal, PageHeader, useAction, useToast } from "../components/ui";
import { useAuth, ROLE_LABEL } from "../App";
import { ago } from "../lib/format";

const ROLE_INFO: Record<string, { icon: any; tone: string; text: string }> = {
  admin: { icon: ShieldCheck, tone: "violet", text: "CEO / owner. Full system access: users, settings, credit limits, all stations and reports." },
  manager: { icon: Briefcase, tone: "blue", text: "Runs daily operations: dashboard, WhatsApp inbox, customers & khata, orders, stock, prices, campaigns, alerts and automations." },
  salesman: { icon: Fuel, tone: "green", text: "Works at one station: records sales on the POS, opens/closes their own shift, looks up customers and sees prices." },
  wholesale: { icon: Container, tone: "amber", text: "Separate access to the wholesale module only: clients, fuel supplies and returns, payments received, dues and statements. Rates are set by the admin." },
  cashier: { icon: Banknote, tone: "green", text: "The cash counter: money received and paid with vouchers, cheques (received & issued), cash from the salesmen, bank deposits, cash count and day book." },
};

/** Human-readable names for the permission matrix. */
const PERM_LABEL: Record<string, string> = {
  "dashboard.view": "Dashboard & reports", "ai.ask": "Ask AI / daily brief", "sales.create": "Record sales (POS)", "sales.view": "View sales",
  "shifts.manage": "Open / close shifts", "shifts.view_all": "See all staff shifts", "customers.view": "View customers", "customers.create": "Add customers",
  "customers.edit": "Edit customers", "credit.set_limit": "Give / change khata credit limits", "khata.manage": "Khata payments & reminders",
  "whatsapp.inbox": "WhatsApp inbox", "orders.manage": "Fuel orders", "complaints.manage": "Complaints", "campaigns.manage": "WhatsApp campaigns",
  "stock.manage": "Tanks, dips & deliveries", "prices.view": "View prices", "prices.update": "Change prices", "alerts.view": "Alerts",
  "automations.manage": "AI automations", "stations.manage": "Add stations", "settings.manage": "Business settings", "users.manage": "Users & roles",
  "wholesale.view": "Wholesale: view clients & statements", "wholesale.manage": "Wholesale: supplies, returns, payments",
  "wholesale.rates": "Wholesale: set client rates & credit limits", "wholesale.void": "Wholesale: void wrong entries",
  "reports.view": "Reports (all periods)", "suppliers.manage": "Suppliers & payments to depots",
  "expenses.view": "Expenses: view & reports", "expenses.create": "Expenses: add", "expenses.approve": "Expenses: approve, budgets & categories",
  "audit.view": "Audit log (who changed what)", "shifts.expenses": "Expenses from shift cash",
  "bank.view": "Banks: see balances & statements", "bank.manage": "Banks: add accounts, cash out, transfers",
  "cashier.desk": "Cashier desk & day book", "cash.book": "Cash book: count cash, bank deposits", "cash.receive": "Cashier: receive payments (vouchers)",
  "cash.pay": "Cashier: pay suppliers, expenses, staff advances", "cheques.manage": "Cheque register: deposit, clear, bounce", "shifts.handover": "Take cash from salesmen after the shift",
};
const PERM_UR: Record<string, string> = {
  "cashier.desk": "کیشیئر ڈیسک", "cash.book": "کیش بک", "cash.receive": "رقم وصول", "cash.pay": "ادائیگی", "cheques.manage": "چیک رجسٹر", "shifts.handover": "سیلزمین سے کیش",
  "bank.view": "بینک بیلنس", "bank.manage": "بینک اندراج", "khata.manage": "کھاتہ", "expenses.create": "خرچہ", "suppliers.manage": "سپلائر",
};
/** Rights grouped by area for the one-role view (phone friendly). */
const AREAS: [string, RegExp][] = [
  ["Cashier & money · کیشیئر", /^(cashier|cash|cheques|bank|shifts\.handover|expenses|suppliers)/], ["Sales & shifts · سیل", /^(sales|shifts|prices)/],
  ["Customers & khata · گاہک", /^(customers|credit|khata|whatsapp|orders|complaints|campaigns)/], ["Wholesale · ہول سیل", /^wholesale/],
  ["Stock · سٹاک", /^stock/], ["Staff · عملہ", /^staff/], ["Owner & system · مالک", /./],
];

type U = { id: number; name: string; email: string; role: string; station_id: number | null; station_name: string | null; active: number; created_at: string; has_pin?: number; pin_locked_until?: string | null };

export default function Users() {
  const { data, reload } = useApi<any>("/users");
  const stations = useApi<any[]>("/stations");
  const { user: me } = useAuth();
  const { busy, run } = useAction();
  const signOut = (u: U) => { if (confirm(`Sign ${u.name} out of every phone and browser?`)) run(() => api(`/users/${u.id}/sign-out`, { body: {} }), `${u.name} signed out everywhere`); };
  const [editing, setEditing] = useState<Partial<U> | null>(null);
  const [pinFor, setPinFor] = useState<U | null>(null);
  if (!data || !stations.data) return <Loading />;

  const counts = data.users.reduce((a: Record<string, number>, u: U) => ({ ...a, [u.role]: (a[u.role] ?? 0) + 1 }), {});

  return (
    <div className="space-y-5">
      <PageHeader title="Users & roles" subtitle="Create staff logins and give each one the access their role needs"
        actions={<button className="btn-primary" onClick={() => setEditing({ role: "salesman", station_id: stations.data![0]?.id })}><Plus size={16} /> Add user</button>} />

      <div className="grid grid-cols-1 gap-3 md:grid-cols-2 xl:grid-cols-5">
        {data.roles.map((r: string) => {
          const I = ROLE_INFO[r].icon;
          return (
            <div key={r} className="card min-w-0 p-4">
              <div className="flex flex-wrap items-center gap-2"><I size={18} className="text-slate-600" /><span className="font-semibold">{ROLE_LABEL[r]}</span><Badge tone={ROLE_INFO[r].tone}>{counts[r] ?? 0} {(counts[r] ?? 0) === 1 ? "user" : "users"}</Badge></div>
              <p className="mt-2 text-sm text-slate-600">{ROLE_INFO[r].text}</p>
              {r !== "admin" && <a href={`#rights-${r}`} onClick={() => setTimeout(() => window.dispatchEvent(new CustomEvent("pumpai:role", { detail: r })), 0)} className="mt-1 inline-flex min-h-9 items-center text-sm font-medium text-brand-700 underline">Manage {ROLE_LABEL[r]} access →</a>}
            </div>
          );
        })}
      </div>

      {/* phone: one card per user */}
      <ul className="card divide-y divide-slate-100 sm:hidden">
        {data.users.map((u: U) => (
          <li key={u.id} className={`px-4 py-3 ${u.active ? "" : "opacity-60"}`}>
            <div className="flex items-start justify-between gap-2">
              <span className="min-w-0 font-semibold">{u.name} {u.id === me?.id && <span className="text-xs font-normal text-slate-400">(you)</span>}</span>
              <span className="shrink-0"><Badge tone={ROLE_INFO[u.role]?.tone}>{ROLE_LABEL[u.role]}</Badge></span>
            </div>
            <div className="break-all text-xs text-slate-500">{u.email}{(u as any).phone && ` · +${(u as any).phone}`}</div>
            <div className="text-xs text-slate-500">{u.station_name ?? "All stations"} · added {ago(u.created_at)}</div>
            <div className="mt-1 flex flex-wrap gap-1">{u.active ? <Badge tone="green">Active</Badge> : <Badge>Disabled</Badge>}{u.has_pin ? <Badge tone="blue">PIN set</Badge> : null}{u.pin_locked_until ? <Badge tone="red">🔒 Locked — wrong PINs</Badge> : null}</div>
            <div className="mt-2 flex flex-wrap gap-2">
              <button className="btn-secondary min-h-9 !px-3 !py-1 text-xs" onClick={() => setPinFor(u)}><KeyRound size={14} /> {u.pin_locked_until ? "Unlock / reset PIN" : "Reset PIN"}</button>
              {u.id !== me?.id && <button className="btn-secondary min-h-9 !px-3 !py-1 text-xs" onClick={() => signOut(u)}><LogOut size={14} /> Sign out everywhere</button>}
              <button className="btn-secondary min-h-9 !px-3 !py-1 text-xs" onClick={() => setEditing(u)}><Pencil size={14} /> Edit</button>
              {u.id !== me?.id && <>
                <button className="btn-secondary min-h-9 !px-3 !py-1 text-xs" disabled={busy} onClick={() => run(() => api(`/users/${u.id}`, { method: "PATCH", body: { active: !u.active } }), u.active ? `${u.name} disabled` : `${u.name} enabled`).then(reload)}>{u.active ? "Disable" : "Enable"}</button>
                <button className="btn-secondary min-h-9 !px-3 !py-1 text-red-600" aria-label="Delete" disabled={busy} onClick={() => confirm(`Delete ${u.name}? This cannot be undone.`) && run(() => api(`/users/${u.id}`, { method: "DELETE" }), "User deleted").then(reload)}><Trash2 size={14} /></button>
              </>}
            </div>
          </li>
        ))}
      </ul>
      <div className="card hidden overflow-x-auto sm:block">
        <table className="w-full">
          <thead><tr><th className="th">Name</th><th className="th">Email (login)</th><th className="th">Role</th><th className="th">Station</th><th className="th">Status</th><th className="th">Added</th><th className="th" /></tr></thead>
          <tbody>
            {data.users.map((u: U) => (
              <tr key={u.id} className={u.active ? "" : "opacity-60"}>
                <td className="td font-medium">{u.name} {u.id === me?.id && <span className="text-xs text-slate-400">(you)</span>}</td>
                <td className="td text-sm text-slate-600">{u.email}{(u as any).phone && <div className="text-xs text-slate-400">+{(u as any).phone}</div>}</td>
                <td className="td"><Badge tone={ROLE_INFO[u.role]?.tone}>{ROLE_LABEL[u.role]}</Badge></td>
                <td className="td text-sm">{u.station_name ?? <span className="text-slate-400">All stations</span>}</td>
                <td className="td">{u.active ? <Badge tone="green">Active</Badge> : <Badge>Disabled</Badge>} {u.has_pin ? <Badge tone="blue">PIN set</Badge> : null} {u.pin_locked_until ? <Badge tone="red">🔒 Locked — wrong PINs</Badge> : null}</td>
                <td className="td text-xs text-slate-500">{ago(u.created_at)}</td>
                <td className="td">
                  <div className="flex justify-end gap-1">
                    <button className="btn-secondary !px-2 !py-1 text-xs" title="Forgot PIN? Set a new one" onClick={() => setPinFor(u)}><KeyRound size={14} /> {u.pin_locked_until ? "Unlock / reset PIN" : "Reset PIN"}</button>
                    {u.id !== me?.id && <button className="btn-secondary !px-2 !py-1 text-xs" title="Lost phone / left the job: sign out of every device" onClick={() => signOut(u)}><LogOut size={14} /></button>}
                    <button className="btn-secondary !px-2 !py-1" title="Edit" onClick={() => setEditing(u)}><Pencil size={14} /></button>
                    {u.id !== me?.id && (
                      <>
                        <button className="btn-secondary !px-2 !py-1 text-xs" disabled={busy}
                          onClick={() => run(() => api(`/users/${u.id}`, { method: "PATCH", body: { active: !u.active } }), u.active ? `${u.name} disabled` : `${u.name} enabled`).then(reload)}>
                          {u.active ? "Disable" : "Enable"}
                        </button>
                        <button className="btn-secondary !px-2 !py-1 text-red-600" title="Delete" disabled={busy}
                          onClick={() => confirm(`Delete ${u.name}? This cannot be undone.`) && run(() => api(`/users/${u.id}`, { method: "DELETE" }), "User deleted").then(reload)}>
                          <Trash2 size={14} />
                        </button>
                      </>
                    )}
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <div className="card flex flex-wrap items-center gap-3 p-4">
        <ShieldCheck size={20} className="shrink-0 text-slate-600" />
        <div className="min-w-0 flex-1 text-sm"><b>Shared tablets (PIN sign-in)</b><span className="block text-slate-500">A tablet is linked when the owner or a manager signs in on it once. Lost a tablet or changed staff? Unlink them all — each needs an owner / manager sign-in again.</span></div>
        <button className="btn-secondary min-h-9" disabled={busy} onClick={() => { if (confirm("Unlink every tablet? Staff will need an owner / manager sign-in on each tablet again.")) run(() => api("/security/unlink-devices", { body: {} }), "All tablets unlinked"); }}>Unlink all tablets</button>
      </div>
      <RoleMatrix data={data} onChanged={reload} />

      {pinFor && <ResetPin user={pinFor} onClose={() => { setPinFor(null); reload(); }} />}
      {editing && <UserForm initial={editing} stations={stations.data} onClose={() => setEditing(null)} onSaved={() => { setEditing(null); reload(); }} />}
    </div>
  );
}

export function UserForm({ initial, stations, onClose, onSaved }: { initial: Partial<U>; stations: any[]; onClose: () => void; onSaved: () => void }) {
  const isNew = !initial.id;
  const [f, setF] = useState({ name: initial.name ?? "", email: initial.email ?? "", phone: (initial as any).phone ?? "", role: initial.role ?? "salesman", station_id: initial.station_id ?? "", password: "", pin: "", clearPin: false });
  const { busy, run } = useAction();
  const save = async (e: React.FormEvent) => {
    e.preventDefault();
    const body: any = { name: f.name, email: f.email, phone: f.phone || null, role: f.role, station_id: f.station_id ? Number(f.station_id) : null };
    if (f.password) body.password = f.password;
    if (f.pin) body.pin = f.pin;
    else if (f.clearPin) body.pin = null;
    const r = await run(() => isNew ? api("/users", { body }) : api(`/users/${initial.id}`, { method: "PATCH", body }), isNew ? `${f.name} can now sign in` : "User updated");
    if (r) onSaved();
  };
  return (
    <Modal open onClose={onClose} title={isNew ? "Add user" : `Edit ${initial.name}`}>
      <form onSubmit={save} className="space-y-3">
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
          <Field label="Full name"><input className="input" required value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} /></Field>
          <Field label="Email (used to sign in)"><input className="input" type="email" required value={f.email} onChange={(e) => setF({ ...f, email: e.target.value })} /></Field>
          <div className="sm:col-span-2"><Field label="WhatsApp number (price-change & shift alerts)"><input className="input" placeholder="03xx xxxxxxx" value={f.phone} onChange={(e) => setF({ ...f, phone: e.target.value })} /></Field></div>
        </div>
        <Field label="Role">
          <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
            {["admin", "manager", "salesman", "wholesale", "cashier"].map((r) => {
              const I = ROLE_INFO[r].icon;
              return (
                <button type="button" key={r} onClick={() => setF({ ...f, role: r })}
                  className={`rounded-lg border-2 p-2 text-left text-sm ${f.role === r ? "border-brand-600 bg-emerald-50" : "border-slate-200"}`}>
                  <div className="flex items-center gap-1.5 font-medium"><I size={15} />{ROLE_LABEL[r]}</div>
                </button>
              );
            })}
          </div>
        </Field>
        <p className="text-xs text-slate-500">{ROLE_INFO[f.role].text}</p>
        <Field label={f.role === "salesman" ? "Station (required)" : "Station (optional)"}>
          <select className="input" value={f.station_id} required={f.role === "salesman"} onChange={(e) => setF({ ...f, station_id: e.target.value })}>
            {f.role !== "salesman" && <option value="">All stations</option>}
            {stations.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
          </select>
        </Field>
        <Field label={isNew ? "Password (min 8 characters)" : "New password (leave blank to keep)"}>
          <input className="input" type="password" minLength={8} required={isNew} value={f.password} onChange={(e) => setF({ ...f, password: e.target.value })} autoComplete="new-password" />
        </Field>
        <Field label={initial.has_pin ? "Quick sign-in PIN (4 digits) — set, type a new one to change" : "Quick sign-in PIN (4 digits, for the pump tablet)"}>
          <div className="flex items-center gap-3">
            <input className="input w-32 text-center tracking-[0.5em]" inputMode="numeric" pattern="\d{4}" maxLength={4} placeholder="••••" value={f.pin}
              onChange={(e) => setF({ ...f, pin: e.target.value.replace(/\D/g, "").slice(0, 4), clearPin: false })} autoComplete="off" />
            {initial.has_pin ? <label className="flex items-center gap-1.5 text-xs text-slate-600"><input type="checkbox" checked={f.clearPin} onChange={(e) => setF({ ...f, clearPin: e.target.checked, pin: "" })} /> Remove PIN</label> : null}
          </div>
        </Field>
        <div className="flex justify-end gap-2"><button type="button" className="btn-secondary" onClick={onClose}>Cancel</button><button className="btn-primary" disabled={busy}>{isNew ? "Create user" : "Save"}</button></div>
      </form>
    </Modal>
  );
}

/** Forgot PIN: the admin types a new 4-digit PIN or lets the app make one, then tells the staff member. */
function ResetPin({ user, onClose }: { user: U; onClose: () => void }) {
  const [pin, setPin] = useState("");
  const [done, setDone] = useState<string | null>(null);
  const { busy, run } = useAction();
  const reset = async (body: { pin?: string }) => { const r = await run(() => api(`/users/${user.id}/reset-pin`, { body })); if (r) setDone(r.pin); };
  return (
    <Modal open onClose={onClose} title={`New PIN for ${user.name}`}>
      {done ? (
        <div className="space-y-3 text-center">
          <p className="text-slate-600">Tell {user.name} the new PIN. It works right away{user.pin_locked_until ? " and the lock is removed" : ""}.</p>
          <div className="text-5xl font-bold tracking-[0.4em] tabular-nums" aria-label={`New PIN ${done.split("").join(" ")}`}>{done}</div>
          <button className="btn-primary w-full" onClick={onClose}>Done</button>
        </div>
      ) : (
        <div className="space-y-4">
          {user.pin_locked_until && <p className="rounded-lg bg-red-50 p-2 text-sm text-red-700">Locked after too many wrong PINs. A new PIN unlocks it.</p>}
          <button className="btn-primary w-full py-3 text-base" disabled={busy} onClick={() => reset({})}><KeyRound size={16} /> Make a new PIN for me</button>
          <div className="text-center text-xs text-slate-400">or type one</div>
          <form className="flex gap-2" onSubmit={(e) => { e.preventDefault(); reset({ pin }); }}>
            <input className="input text-center text-2xl tracking-[0.5em]" inputMode="numeric" pattern="\d{4}" maxLength={4} required placeholder="••••" aria-label="New 4-digit PIN"
              value={pin} onChange={(e) => setPin(e.target.value.replace(/\D/g, "").slice(0, 4))} autoComplete="off" />
            <button className="btn-secondary" disabled={busy || pin.length !== 4}>Set</button>
          </form>
        </div>
      )}
    </Modal>
  );
}

/** What each role may do — the admin ticks / unticks a box to give or take a right. The owner's own column is fixed. */
function RoleMatrix({ data, onChanged }: { data: any; onChanged: () => void }) {
  const { refresh } = useAuth();
  const [perms, setPerms] = useState<Record<string, string[]>>(data.permissions);
  const [busyKey, setBusyKey] = useState<string | null>(null);
  // phones get one role at a time (the all-roles table is too wide for a phone screen)
  const [one, setOne] = useState<string | null>(() => (location.hash.startsWith("#rights-") ? location.hash.slice(8) : window.matchMedia?.("(max-width: 639px)").matches ? data.roles.find((r: string) => r !== "admin") ?? null : null));
  useEffect(() => {
    const pick = (e: Event) => { setOne((e as CustomEvent).detail); document.getElementById("rights")?.scrollIntoView({ behavior: "smooth" }); };
    window.addEventListener("pumpai:role", pick);
    return () => window.removeEventListener("pumpai:role", pick);
  }, []);
  const toast = useToast();
  const { run } = useAction();
  const defaults = data.defaults as Record<string, string[]>;
  const changed = Object.keys(perms).filter((p) => data.roles.some((r: string) => perms[p].includes(r) !== defaults[p].includes(r))).length;
  const flip = async (p: string, r: string) => {
    const allow = !perms[p].includes(r);
    setBusyKey(`${p}|${r}`);
    try {
      const res = await api("/roles/permissions", { method: "PUT", body: { perm: p, role: r, allowed: allow } });
      setPerms(res.permissions);
      toast("ok", `${ROLE_LABEL[r]}: ${allow ? "can now" : "can no longer"} — ${PERM_LABEL[p] ?? p}`);
      refresh?.();
    } catch (e: any) { toast("err", e.message); }
    finally { setBusyKey(null); }
  };
  return (
    <div id="rights" className="card min-w-0 overflow-x-auto">
      <div className="flex flex-wrap items-center justify-between gap-2 p-4 pb-2">
        <div><h2 className="font-semibold">What each role can do · <span lang="ur" className="font-urdu">کون کیا کر سکتا ہے</span></h2>
          <p className="text-xs text-slate-500">Tap a box to give ✓ or take away ✗ a right. Changes work at once (the person may need to refresh). The admin (owner) always has everything.</p></div>
        {changed > 0 && <button className="btn-secondary text-sm" onClick={async () => { if (!confirm("Put every role back to the standard rights?")) return; const r: any = await run(() => api("/roles/permissions/reset", { body: {} }), "Back to the standard rights"); if (r) { setPerms(r.permissions); onChanged(); refresh?.(); } }}>Reset to standard ({changed} changed)</button>}
      </div>
      <div className="flex gap-1 overflow-x-auto px-4 pb-2">
        {[null, ...data.roles.filter((r: string) => r !== "admin")].map((r: string | null) => (
          <button key={r ?? "all"} type="button" onClick={() => setOne(r)} className={`min-h-9 shrink-0 whitespace-nowrap rounded-full px-3 py-1 text-sm sm:min-h-0 ${one === r ? "bg-slate-800 text-white" : "bg-slate-100 text-slate-700"}`}>{r ? ROLE_LABEL[r] : "All roles (table)"}</button>
        ))}
      </div>
      {one && (
        <div className="space-y-4 px-4 pb-4">
          {AREAS.map(([area, re], i) => {
            const mine = Object.keys(perms).filter((p) => re.test(p) && !AREAS.slice(0, i).some(([, r2]) => r2.test(p)));
            if (!mine.length) return null;
            return (
              <div key={area}>
                <h3 className="mb-1 text-sm font-semibold text-slate-700">{area}</h3>
                <ul className="divide-y divide-slate-100 rounded-xl ring-1 ring-slate-200">
                  {mine.map((p) => {
                    const on = perms[p].includes(one);
                    const isDefault = on === defaults[p].includes(one);
                    return (
                      <li key={p}><button type="button" role="switch" aria-checked={on} disabled={busyKey === `${p}|${one}`} onClick={() => flip(p, one)} className="flex min-h-11 w-full items-center gap-3 px-3 py-2.5 text-left text-sm hover:bg-slate-50">
                        <span className="min-w-0 flex-1">{PERM_LABEL[p] ?? p}{PERM_UR[p] && <span lang="ur" className="font-urdu ml-1 text-slate-500">· {PERM_UR[p]}</span>}{!isDefault && <span className="ml-1.5 inline-block h-2 w-2 rounded-full bg-amber-500 align-middle" title="Changed from standard" />}</span>
                        <span className={`relative h-6 w-11 shrink-0 rounded-full transition ${on ? "bg-emerald-500" : "bg-slate-300"}`}><span className={`absolute top-0.5 h-5 w-5 rounded-full bg-white shadow transition ${on ? "left-[22px]" : "left-0.5"}`} /></span>
                      </button></li>
                    );
                  })}
                </ul>
              </div>
            );
          })}
        </div>
      )}
      {!one && <table className="w-full min-w-[640px]">
        <thead><tr><th className="th">Permission</th>{data.roles.map((r: string) => <th key={r} className="th text-center">{ROLE_LABEL[r]}</th>)}</tr></thead>
        <tbody>
          {Object.keys(perms).map((p) => (
            <tr key={p} className="hover:bg-slate-50">
              <td className="td text-sm">{PERM_LABEL[p] ?? p}</td>
              {data.roles.map((r: string) => {
                const on = perms[p].includes(r);
                const isDefault = on === defaults[p].includes(r);
                const locked = r === "admin";
                return (
                  <td key={r} className="td text-center">
                    <button type="button" disabled={locked || busyKey === `${p}|${r}`} onClick={() => flip(p, r)} role="switch" aria-checked={on}
                      aria-label={`${ROLE_LABEL[r]} — ${PERM_LABEL[p] ?? p}`} title={locked ? "The owner always has every right" : isDefault ? "Standard" : "Changed from standard"}
                      className={`relative mx-auto flex h-8 w-8 items-center justify-center rounded-lg border-2 transition ${on ? "border-emerald-500 bg-emerald-50 text-emerald-700" : "border-slate-200 bg-white text-slate-300"} ${locked ? "cursor-not-allowed opacity-60" : "hover:border-brand-600 active:scale-95"}`}>
                      {on ? <Check size={18} /> : <X size={16} />}
                      {!isDefault && <span className="absolute -right-1 -top-1 h-2.5 w-2.5 rounded-full bg-amber-500" />}
                    </button>
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>}
      <p className="p-4 pt-2 text-xs text-slate-500"><span className="mr-1 inline-block h-2.5 w-2.5 rounded-full bg-amber-500 align-middle" /> = changed from the standard. Salesmen only see their own station's sales and their own shifts.</p>
    </div>
  );
}

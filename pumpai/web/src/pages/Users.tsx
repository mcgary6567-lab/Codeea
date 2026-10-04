import { useState } from "react";
import { Plus, Pencil, Trash2, Check, X, ShieldCheck, Briefcase, Fuel, Container, KeyRound } from "lucide-react";
import { api, useApi } from "../lib/api";
import { Badge, Field, Loading, Modal, PageHeader, useAction } from "../components/ui";
import { useAuth, ROLE_LABEL } from "../App";
import { ago } from "../lib/format";

const ROLE_INFO: Record<string, { icon: any; tone: string; text: string }> = {
  admin: { icon: ShieldCheck, tone: "violet", text: "CEO / owner. Full system access: users, settings, credit limits, all stations and reports." },
  manager: { icon: Briefcase, tone: "blue", text: "Runs daily operations: dashboard, WhatsApp inbox, customers & khata, orders, stock, prices, campaigns, alerts and automations." },
  salesman: { icon: Fuel, tone: "green", text: "Works at one station: records sales on the POS, opens/closes their own shift, looks up customers and sees prices." },
  wholesale: { icon: Container, tone: "amber", text: "Separate access to the wholesale module only: clients, fuel supplies and returns, payments received, dues and statements. Rates are set by the admin." },
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
};

type U = { id: number; name: string; email: string; role: string; station_id: number | null; station_name: string | null; active: number; created_at: string; has_pin?: number; pin_locked_until?: string | null };

export default function Users() {
  const { data, reload } = useApi<any>("/users");
  const stations = useApi<any[]>("/stations");
  const { user: me } = useAuth();
  const { busy, run } = useAction();
  const [editing, setEditing] = useState<Partial<U> | null>(null);
  const [pinFor, setPinFor] = useState<U | null>(null);
  if (!data || !stations.data) return <Loading />;

  const counts = data.users.reduce((a: Record<string, number>, u: U) => ({ ...a, [u.role]: (a[u.role] ?? 0) + 1 }), {});

  return (
    <div className="space-y-5">
      <PageHeader title="Users & roles" subtitle="Create staff logins and give each one the access their role needs"
        actions={<button className="btn-primary" onClick={() => setEditing({ role: "salesman", station_id: stations.data![0]?.id })}><Plus size={16} /> Add user</button>} />

      <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-4">
        {data.roles.map((r: string) => {
          const I = ROLE_INFO[r].icon;
          return (
            <div key={r} className="card p-4">
              <div className="flex items-center gap-2"><I size={18} className="text-slate-600" /><span className="font-semibold">{ROLE_LABEL[r]}</span><Badge tone={ROLE_INFO[r].tone}>{counts[r] ?? 0} {(counts[r] ?? 0) === 1 ? "user" : "users"}</Badge></div>
              <p className="mt-2 text-sm text-slate-600">{ROLE_INFO[r].text}</p>
            </div>
          );
        })}
      </div>

      <div className="card overflow-x-auto">
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

      <div className="card overflow-x-auto">
        <h2 className="p-4 pb-2 font-semibold">What each role can do</h2>
        <table className="w-full">
          <thead><tr><th className="th">Permission</th>{data.roles.map((r: string) => <th key={r} className="th text-center">{ROLE_LABEL[r]}</th>)}</tr></thead>
          <tbody>
            {Object.entries(data.permissions as Record<string, string[]>).map(([p, roles]) => (
              <tr key={p}>
                <td className="td text-sm">{PERM_LABEL[p] ?? p}</td>
                {data.roles.map((r: string) => (
                  <td key={r} className="td text-center">
                    {roles.includes(r) ? <Check size={16} className="mx-auto text-emerald-600" aria-label="Allowed" /> : <X size={16} className="mx-auto text-slate-300" aria-label="Not allowed" />}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
        <p className="p-4 pt-2 text-xs text-slate-500">Salesmen only see their own station's sales and their own shifts.</p>
      </div>

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
        <div className="grid gap-3 sm:grid-cols-2">
          <Field label="Full name"><input className="input" required value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} /></Field>
          <Field label="Email (used to sign in)"><input className="input" type="email" required value={f.email} onChange={(e) => setF({ ...f, email: e.target.value })} /></Field>
          <div className="sm:col-span-2"><Field label="WhatsApp number (price-change & shift alerts)"><input className="input" placeholder="03xx xxxxxxx" value={f.phone} onChange={(e) => setF({ ...f, phone: e.target.value })} /></Field></div>
        </div>
        <Field label="Role">
          <div className="grid gap-2 sm:grid-cols-2">
            {["admin", "manager", "salesman", "wholesale"].map((r) => {
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
        <Field label={isNew ? "Password (min 6 characters)" : "New password (leave blank to keep)"}>
          <input className="input" type="password" minLength={6} required={isNew} value={f.password} onChange={(e) => setF({ ...f, password: e.target.value })} autoComplete="new-password" />
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

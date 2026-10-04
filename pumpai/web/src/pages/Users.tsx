import { useState } from "react";
import { Plus, Pencil, Trash2, Check, X, ShieldCheck, Briefcase, Fuel, Container } from "lucide-react";
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
  "expenses.view": "Expenses: view & reports", "expenses.create": "Expenses: add", "expenses.approve": "Expenses: approve, budgets & categories",
};

type U = { id: number; name: string; email: string; role: string; station_id: number | null; station_name: string | null; active: number; created_at: string };

export default function Users() {
  const { data, reload } = useApi<any>("/users");
  const stations = useApi<any[]>("/stations");
  const { user: me } = useAuth();
  const { busy, run } = useAction();
  const [editing, setEditing] = useState<Partial<U> | null>(null);
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
                <td className="td text-sm text-slate-600">{u.email}</td>
                <td className="td"><Badge tone={ROLE_INFO[u.role]?.tone}>{ROLE_LABEL[u.role]}</Badge></td>
                <td className="td text-sm">{u.station_name ?? <span className="text-slate-400">All stations</span>}</td>
                <td className="td">{u.active ? <Badge tone="green">Active</Badge> : <Badge>Disabled</Badge>}</td>
                <td className="td text-xs text-slate-500">{ago(u.created_at)}</td>
                <td className="td">
                  <div className="flex justify-end gap-1">
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

      {editing && <UserForm initial={editing} stations={stations.data} onClose={() => setEditing(null)} onSaved={() => { setEditing(null); reload(); }} />}
    </div>
  );
}

function UserForm({ initial, stations, onClose, onSaved }: { initial: Partial<U>; stations: any[]; onClose: () => void; onSaved: () => void }) {
  const isNew = !initial.id;
  const [f, setF] = useState({ name: initial.name ?? "", email: initial.email ?? "", role: initial.role ?? "salesman", station_id: initial.station_id ?? "", password: "" });
  const { busy, run } = useAction();
  const save = async (e: React.FormEvent) => {
    e.preventDefault();
    const body: any = { name: f.name, email: f.email, role: f.role, station_id: f.station_id ? Number(f.station_id) : null };
    if (f.password) body.password = f.password;
    const r = await run(() => isNew ? api("/users", { body }) : api(`/users/${initial.id}`, { method: "PATCH", body }), isNew ? `${f.name} can now sign in` : "User updated");
    if (r) onSaved();
  };
  return (
    <Modal open onClose={onClose} title={isNew ? "Add user" : `Edit ${initial.name}`}>
      <form onSubmit={save} className="space-y-3">
        <div className="grid gap-3 sm:grid-cols-2">
          <Field label="Full name"><input className="input" required value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} /></Field>
          <Field label="Email (used to sign in)"><input className="input" type="email" required value={f.email} onChange={(e) => setF({ ...f, email: e.target.value })} /></Field>
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
        <div className="flex justify-end gap-2"><button type="button" className="btn-secondary" onClick={onClose}>Cancel</button><button className="btn-primary" disabled={busy}>{isNew ? "Create user" : "Save"}</button></div>
      </form>
    </Modal>
  );
}

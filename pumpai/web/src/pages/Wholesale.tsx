import { useEffect, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { Plus, Search, Truck, Wallet, Undo2, SlidersHorizontal, Download, Printer, Ban, ArrowLeft, Pencil } from "lucide-react";
import { api, getToken, useApi } from "../lib/api";
import { Badge, Empty, ErrorBox, Field, Loading, Modal, PageHeader, Stat, useAction } from "../components/ui";
import { PRODUCTS, ago, d, dt, num, phone, pkr, pkrShort } from "../lib/format";
import { useAuth } from "../App";

const TYPE: Record<string, { label: string; tone: string }> = {
  supply: { label: "Supply", tone: "blue" }, return: { label: "Return", tone: "amber" },
  payment: { label: "Payment", tone: "green" }, adjustment: { label: "Adjustment", tone: "violet" },
};

export default function Wholesale() {
  const { id } = useParams();
  return id ? <ClientDetail id={id} /> : <ClientList />;
}

/* ---------------- List ---------------- */
function ClientList() {
  const nav = useNavigate();
  const { can } = useAuth();
  const [q, setQ] = useState("");
  const summary = useApi<any>("/wholesale/summary");
  const list = useApi<any[]>(`/wholesale/clients?q=${encodeURIComponent(q)}`);
  const [adding, setAdding] = useState(false);
  const s = summary.data;

  return (
    <div className="space-y-5">
      <PageHeader title="Wholesale supply" subtitle="Bulk fuel to dealers and businesses — each client has their own rate card and running account"
        actions={can("wholesale.manage") && <button className="btn-primary" onClick={() => setAdding(true)}><Plus size={16} /> Add client</button>} />
      {s && (
        <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
          <Stat label="Total due (all clients)" value={pkrShort(s.total_due)} tone="amber" icon={<Wallet size={16} />} hint={`${s.clients} clients`} />
          <Stat label="Supplied this month" value={`${num(s.month.supplied_l)} L`} tone="blue" icon={<Truck size={16} />} hint={`Billed ${pkrShort(s.month.billed)} · returned ${num(s.month.returned_l)} L`} />
          <Stat label="Received this month" value={pkrShort(s.month.received)} tone="green" hint={`Today ${pkr(s.today.received)}`} />
          <Stat label="Supplied today" value={`${num(s.today.supplied_l)} L`} hint={`This month: ${s.by_product_month.map((p: any) => `${PRODUCTS[p.product]} ${num(p.litres)} L`).join(" · ") || "—"}`} />
        </div>
      )}
      <div className="card">
        <div className="border-b border-slate-200 p-3"><div className="relative max-w-sm"><Search size={15} className="absolute left-2.5 top-2.5 text-slate-400" /><input className="input pl-8" placeholder="Search client" value={q} onChange={(e) => setQ(e.target.value)} /></div></div>
        {list.error && <div className="p-3"><ErrorBox error={list.error} /></div>}
        <div className="overflow-x-auto">
          <table className="w-full">
            <thead><tr><th className="th">Client</th><th className="th">Rates (per litre)</th><th className="th text-right">This month</th><th className="th text-right">Due</th><th className="th">Limit used</th><th className="th">Last supply</th><th className="th">Last payment</th></tr></thead>
            <tbody>
              {(list.data ?? []).map((c) => {
                const used = c.credit_limit ? Math.min(100, (c.due / c.credit_limit) * 100) : 0;
                return (
                  <tr key={c.id} className={`cursor-pointer hover:bg-slate-50 ${c.active ? "" : "opacity-50"}`} onClick={() => nav(`/wholesale/${c.id}`)}>
                    <td className="td"><div className="font-medium">{c.name}</div><div className="text-xs text-slate-500">{c.business_name ?? ""}{c.phone && ` · ${phone(c.phone)}`}</div></td>
                    <td className="td text-xs">{Object.entries(c.rates).map(([p, r]: any) => <div key={p}>{PRODUCTS[p]}: <b>Rs {r.toFixed(2)}</b></div>)}{!Object.keys(c.rates).length && <span className="text-amber-600">No rate set</span>}</td>
                    <td className="td text-right tabular-nums">{num(c.month_l)} L</td>
                    <td className={`td text-right font-semibold tabular-nums ${c.due > 0 ? "" : "text-emerald-600"}`}>{pkr(c.due)}</td>
                    <td className="td">{c.credit_limit ? <><div className="h-1.5 w-24 rounded-full bg-slate-100"><div className="h-full rounded-full" style={{ width: `${used}%`, background: used >= 90 ? "#e34948" : "#2a78d6" }} /></div><span className="text-xs text-slate-500">{Math.round(used)}% of {pkrShort(c.credit_limit)}</span></> : <span className="text-xs text-slate-400">No limit</span>}</td>
                    <td className="td text-xs text-slate-500">{ago(c.last_supply)}</td>
                    <td className="td text-xs text-slate-500">{ago(c.last_payment)}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
          {list.data && !list.data.length && <Empty>No wholesale clients yet</Empty>}
        </div>
      </div>
      {s && (
        <div className="card">
          <h2 className="p-4 pb-2 font-semibold">Recent wholesale entries</h2>
          <LedgerTable rows={s.recent} showClient />
        </div>
      )}
      {adding && <ClientForm onClose={() => setAdding(false)} onSaved={(c) => { setAdding(false); nav(`/wholesale/${c.id}`); }} />}
    </div>
  );
}

function ClientForm({ initial, onClose, onSaved }: { initial?: any; onClose: () => void; onSaved: (c: any) => void }) {
  const { can } = useAuth();
  const admin = can("wholesale.rates");
  const [f, setF] = useState<any>({
    name: initial?.name ?? "", business_name: initial?.business_name ?? "", phone: initial?.phone ?? "", city: initial?.city ?? "", address: initial?.address ?? "",
    credit_limit: initial?.credit_limit ?? 0, opening_balance: initial?.opening_balance ?? 0, notes: initial?.notes ?? "",
    PMG: initial?.rates?.PMG ?? "", HOBC: initial?.rates?.HOBC ?? "", HSD: initial?.rates?.HSD ?? "",
  });
  const { busy, run } = useAction();
  const save = async (e: React.FormEvent) => {
    e.preventDefault();
    const body: any = { name: f.name, business_name: f.business_name || null, phone: f.phone || null, city: f.city || null, address: f.address || null, notes: f.notes || null };
    if (admin) {
      body.credit_limit = Number(f.credit_limit) || 0;
      body.opening_balance = Number(f.opening_balance) || 0;
      const rates = Object.fromEntries(["PMG", "HOBC", "HSD"].filter((p) => Number(f[p]) > 0).map((p) => [p, Number(f[p])]));
      if (Object.keys(rates).length) body.rates = rates;
    }
    const r = await run(() => initial ? api(`/wholesale/clients/${initial.id}`, { method: "PATCH", body }) : api("/wholesale/clients", { body }), "Client saved");
    if (r) onSaved(r);
  };
  return (
    <Modal open onClose={onClose} title={initial ? `Edit ${initial.name}` : "Add wholesale client"} wide>
      <form onSubmit={save} className="space-y-4">
        <div className="grid gap-3 sm:grid-cols-3">
          <Field label="Contact / client name"><input className="input" required value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} /></Field>
          <Field label="Business name"><input className="input" value={f.business_name} onChange={(e) => setF({ ...f, business_name: e.target.value })} /></Field>
          <Field label="WhatsApp / phone"><input className="input" placeholder="03xx xxxxxxx" value={f.phone} onChange={(e) => setF({ ...f, phone: e.target.value })} /></Field>
          <Field label="City"><input className="input" value={f.city} onChange={(e) => setF({ ...f, city: e.target.value })} /></Field>
          <div className="sm:col-span-2"><Field label="Address"><input className="input" value={f.address} onChange={(e) => setF({ ...f, address: e.target.value })} /></Field></div>
        </div>
        {admin ? (
          <div className="rounded-lg border border-violet-200 bg-violet-50/50 p-3">
            <div className="mb-2 text-sm font-medium">Rate card & credit (admin only)</div>
            <div className="grid gap-3 sm:grid-cols-5">
              {["PMG", "HOBC", "HSD"].map((p) => (
                <Field key={p} label={`${PRODUCTS[p]} rate (Rs/L)`}><input className="input" type="number" step="0.01" min={0} placeholder="not supplied" value={f[p]} onChange={(e) => setF({ ...f, [p]: e.target.value })} /></Field>
              ))}
              <Field label="Credit limit (Rs, 0 = none)"><input className="input" type="number" min={0} value={f.credit_limit} onChange={(e) => setF({ ...f, credit_limit: e.target.value })} /></Field>
              <Field label="Opening balance (Rs due)"><input className="input" type="number" value={f.opening_balance} onChange={(e) => setF({ ...f, opening_balance: e.target.value })} /></Field>
            </div>
          </div>
        ) : <p className="text-xs text-slate-500">Rates, credit limit and opening balance are set by the admin.</p>}
        <Field label="Notes"><input className="input" value={f.notes} onChange={(e) => setF({ ...f, notes: e.target.value })} /></Field>
        <div className="flex justify-end gap-2"><button type="button" className="btn-secondary" onClick={onClose}>Cancel</button><button className="btn-primary" disabled={busy}>Save</button></div>
      </form>
    </Modal>
  );
}

/* ---------------- Detail ---------------- */
function ClientDetail({ id }: { id: string }) {
  const { can } = useAuth();
  const client = useApi<any>(`/wholesale/clients/${id}`);
  const [range, setRange] = useState({ from: "", to: "" });
  const qs = new URLSearchParams(Object.entries(range).filter(([, v]) => v)).toString();
  const stmt = useApi<any>(`/wholesale/clients/${id}/statement${qs ? `?${qs}` : ""}`);
  const [action, setAction] = useState<null | "supply" | "return" | "payment" | "adjustment" | "rates" | "edit">(null);
  const refresh = () => { client.reload(); stmt.reload(); };

  if (client.error) return <ErrorBox error={client.error} />;
  if (!client.data) return <Loading />;
  const c = client.data;
  const s = c.summary;
  const m = c.month;
  const csvUrl = `/api/wholesale/clients/${id}/statement.csv?${qs ? qs + "&" : ""}token=${encodeURIComponent(getToken() ?? "")}`;

  return (
    <div className="space-y-5">
      <div className="print:hidden"><Link to="/wholesale" className="inline-flex items-center gap-1 text-sm text-brand-600 hover:underline"><ArrowLeft size={14} /> All clients</Link></div>
      <PageHeader title={c.name} subtitle={[c.business_name, c.city, c.phone && phone(c.phone)].filter(Boolean).join(" · ")}
        actions={<div className="flex flex-wrap gap-2 print:hidden">
          {can("wholesale.manage") && <>
            <button className="btn-primary" onClick={() => setAction("supply")} disabled={!c.active}><Truck size={15} /> New supply</button>
            <button className="btn-secondary" onClick={() => setAction("payment")}><Wallet size={15} /> Receive payment</button>
            <button className="btn-secondary" onClick={() => setAction("return")}><Undo2 size={15} /> Fuel return</button>
          </>}
          {can("wholesale.rates") && <button className="btn-secondary" onClick={() => setAction("adjustment")}><SlidersHorizontal size={15} /> Adjustment</button>}
          {can("wholesale.manage") && <button className="btn-secondary" onClick={() => setAction("edit")}><Pencil size={15} /> Edit</button>}
        </div>} />

      <div className="grid grid-cols-2 gap-3 lg:grid-cols-5">
        <Stat label="Current due" value={pkr(s.due)} tone={s.due > 0 ? "amber" : "green"} hint={c.credit_limit ? `Limit ${pkr(c.credit_limit)} · ${Math.round((s.due / c.credit_limit) * 100)}% used` : "No credit limit"} />
        <Stat label="Total billed" value={pkrShort(s.billed)} hint={`${s.supplies} supplies · opening ${pkr(c.opening_balance)}`} />
        <Stat label="Total received" value={pkrShort(s.received)} tone="green" hint={`Last payment ${ago(s.last_payment)}`} />
        <Stat label="Returns credited" value={pkrShort(s.returned)} hint={s.adjustments ? `Adjustments ${pkr(s.adjustments)}` : undefined} />
        <Stat label="This month" value={`${num(m.by_product.reduce((a: number, p: any) => a + p.supplied_l, 0))} L`} hint={`Billed ${pkrShort(m.billed)} · received ${pkrShort(m.received)}`} />
      </div>

      <div className="grid gap-5 lg:grid-cols-3">
        <div className="card p-4">
          <div className="mb-2 flex items-center justify-between"><h2 className="font-semibold">Rate card</h2>{can("wholesale.rates") && <button className="text-xs text-brand-600 hover:underline print:hidden" onClick={() => setAction("rates")}>Change rates</button>}</div>
          {Object.keys(PRODUCTS).map((p) => (
            <div key={p} className="flex justify-between border-b border-slate-100 py-1.5 text-sm"><span>{PRODUCTS[p]}</span><span className="font-semibold tabular-nums">{c.rates[p] ? `Rs ${c.rates[p].toFixed(2)} / L` : <span className="font-normal text-slate-400">not supplied</span>}</span></div>
          ))}
          {c.rate_history.length > 0 && <details className="mt-2 text-xs text-slate-500"><summary className="cursor-pointer">Rate history</summary>
            <ul className="mt-1 space-y-0.5">{c.rate_history.map((h: any) => <li key={h.id}>{d(h.created_at)} · {PRODUCTS[h.product]}: {h.old_rate ? `Rs ${h.old_rate} → ` : ""}Rs {h.new_rate} ({h.changed_by})</li>)}</ul></details>}
        </div>
        <div className="card p-4 lg:col-span-2">
          <h2 className="mb-2 font-semibold">Fuel account (all time)</h2>
          <table className="w-full">
            <thead><tr><th className="th">Product</th><th className="th text-right">Supplied (out)</th><th className="th text-right">Returned (in)</th><th className="th text-right">Net litres</th><th className="th text-right">Net amount</th></tr></thead>
            <tbody>{s.by_product.map((p: any) => (
              <tr key={p.product}><td className="td">{PRODUCTS[p.product]}</td><td className="td text-right tabular-nums">{num(p.supplied_l)} L</td><td className="td text-right tabular-nums">{num(p.returned_l)} L</td>
                <td className="td text-right font-medium tabular-nums">{num(p.supplied_l - p.returned_l)} L</td><td className="td text-right tabular-nums">{pkr(p.net_amount)}</td></tr>
            ))}</tbody>
          </table>
          {!s.by_product.length && <Empty>No fuel supplied yet</Empty>}
        </div>
      </div>

      <div className="card">
        <div className="flex flex-wrap items-end gap-3 p-4 print:hidden">
          <h2 className="mr-auto font-semibold">Account statement</h2>
          <Field label="From"><input className="input" type="date" value={range.from} onChange={(e) => setRange({ ...range, from: e.target.value })} /></Field>
          <Field label="To"><input className="input" type="date" value={range.to} onChange={(e) => setRange({ ...range, to: e.target.value })} /></Field>
          <a className="btn-secondary" href={csvUrl}><Download size={15} /> Excel / CSV</a>
          <button className="btn-secondary" onClick={() => window.print()}><Printer size={15} /> Print</button>
        </div>
        <h2 className="hidden px-4 pt-4 font-semibold print:block">Account statement {range.from && `from ${range.from}`} {range.to && `to ${range.to}`}</h2>
        {stmt.data ? (
          <>
            <div className="flex justify-between bg-slate-50 px-4 py-2 text-sm"><span>Opening balance</span><span className="font-medium tabular-nums">{pkr(stmt.data.opening_balance)}</span></div>
            <LedgerTable rows={stmt.data.lines} running onVoid={can("wholesale.void") ? async (row) => {
              const reason = prompt(`Void ${row.type} #${row.id} of ${pkr(row.amount)}? Enter a reason:`);
              if (reason) { await api(`/wholesale/txns/${row.id}/void`, { body: { reason } }).catch((e) => alert(e.message)); refresh(); }
            } : undefined} />
            <div className="flex justify-between bg-slate-50 px-4 py-2 text-sm font-semibold"><span>Closing balance (due)</span><span className="tabular-nums">{pkr(stmt.data.closing_balance)}</span></div>
          </>
        ) : <Loading />}
      </div>

      {(action === "supply" || action === "return") && <FuelEntry kind={action} client={c} onClose={() => setAction(null)} onDone={() => { setAction(null); refresh(); }} />}
      {action === "payment" && <PaymentEntry client={c} onClose={() => setAction(null)} onDone={() => { setAction(null); refresh(); }} />}
      {action === "adjustment" && <AdjustmentEntry client={c} onClose={() => setAction(null)} onDone={() => { setAction(null); refresh(); }} />}
      {action === "rates" && <RatesEditor client={c} onClose={() => setAction(null)} onDone={() => { setAction(null); refresh(); }} />}
      {action === "edit" && <ClientForm initial={c} onClose={() => setAction(null)} onSaved={() => { setAction(null); refresh(); }} />}
    </div>
  );
}

function LedgerTable({ rows, running, showClient, onVoid }: { rows: any[]; running?: boolean; showClient?: boolean; onVoid?: (row: any) => void }) {
  if (!rows.length) return <Empty>No entries</Empty>;
  return (
    <div className="overflow-x-auto">
      <table className="w-full">
        <thead><tr>
          <th className="th">Date</th>{showClient && <th className="th">Client</th>}<th className="th">Entry</th><th className="th">Details</th>
          <th className="th text-right">Debit (billed)</th><th className="th text-right">Credit (paid / returned)</th>{running && <th className="th text-right">Balance</th>}<th className="th print:hidden" />
        </tr></thead>
        <tbody>
          {rows.map((r) => {
            const debit = running ? r.debit : r.type === "supply" || (r.type === "adjustment" && r.amount > 0) ? Math.abs(r.amount) : 0;
            const credit = running ? r.credit : debit ? 0 : Math.abs(r.amount);
            return (
              <tr key={r.id} className={r.voided ? "text-slate-400 line-through" : ""}>
                <td className="td text-xs">{dt(r.txn_date)}</td>
                {showClient && <td className="td text-sm"><Link to={`/wholesale/${r.client_id}`} className="hover:underline">{r.client_name}</Link></td>}
                <td className="td"><Badge tone={TYPE[r.type].tone}>{TYPE[r.type].label}</Badge> {r.voided ? <Badge tone="red">VOID</Badge> : null}</td>
                <td className="td text-xs">
                  {r.product && <div>{num(r.litres, 2)} L {PRODUCTS[r.product]} @ Rs {r.rate}{r.station_name ? ` · ${r.station_name.replace("Al-Madina ", "")}` : ""}</div>}
                  {r.method && <div>{r.method}</div>}
                  <div className="text-slate-500">{[r.vehicle_no, r.ref, r.note, r.voided && r.void_reason].filter(Boolean).join(" · ")}</div>
                </td>
                <td className="td text-right tabular-nums">{debit ? pkr(debit) : ""}</td>
                <td className="td text-right tabular-nums text-emerald-700">{credit ? pkr(credit) : ""}</td>
                {running && <td className="td text-right font-medium tabular-nums">{pkr(r.balance)}</td>}
                <td className="td print:hidden">{onVoid && !r.voided && <button className="text-slate-400 hover:text-red-600" title="Void entry" onClick={() => onVoid(r)}><Ban size={14} /></button>}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function FuelEntry({ kind, client, onClose, onDone }: { kind: "supply" | "return"; client: any; onClose: () => void; onDone: () => void }) {
  const { can } = useAuth();
  const stations = useApi<any[]>("/stations");
  const products = Object.keys(client.rates).length ? Object.keys(client.rates) : Object.keys(PRODUCTS);
  const [f, setF] = useState<any>({ station_id: "", product: products[0], litres: "", rate: "", vehicle_no: "", ref: "", note: "", txn_date: new Date().toISOString().slice(0, 10), override_limit: false });
  useEffect(() => { if (stations.data && !f.station_id) setF((x: any) => ({ ...x, station_id: stations.data![0].id })); }, [stations.data]);
  const { busy, run } = useAction();
  const station = stations.data?.find((s) => s.id === Number(f.station_id));
  const tank = station?.tanks.filter((t: any) => t.product === f.product).sort((a: any, b: any) => b.current_l - a.current_l)[0];
  const rate = Number(f.rate) || client.rates[f.product] || 0;
  const amount = Number(f.litres) * rate;
  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    const body: any = { station_id: Number(f.station_id), product: f.product, litres: Number(f.litres), vehicle_no: f.vehicle_no || null, ref: f.ref || null, note: f.note || null, txn_date: f.txn_date };
    if (f.rate && can("wholesale.rates")) body.rate = Number(f.rate);
    if (f.override_limit) body.override_limit = true;
    if (await run(() => api(`/wholesale/clients/${client.id}/${kind}`, { body }), (r: any) => `${kind === "supply" ? "Supply" : "Return"} saved. Due now ${pkr(r.due_after)}`)) onDone();
  };
  return (
    <Modal open onClose={onClose} title={kind === "supply" ? `Supply fuel to ${client.name}` : `Fuel returned by ${client.name}`}>
      <form onSubmit={submit} className="space-y-3">
        <div className="grid gap-3 sm:grid-cols-2">
          <Field label={kind === "supply" ? "From station" : "Into station"}><select className="input" value={f.station_id} onChange={(e) => setF({ ...f, station_id: e.target.value })}>{(stations.data ?? []).map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}</select></Field>
          <Field label="Product"><select className="input" value={f.product} onChange={(e) => setF({ ...f, product: e.target.value })}>{products.map((p) => <option key={p} value={p}>{PRODUCTS[p]}</option>)}</select></Field>
          <Field label="Litres"><input className="input" type="number" step="0.01" min={1} required value={f.litres} onChange={(e) => setF({ ...f, litres: e.target.value })} /></Field>
          <Field label={can("wholesale.rates") ? "Rate (Rs/L) — blank = rate card" : "Rate (Rs/L)"}>
            <input className="input" type="number" step="0.01" disabled={!can("wholesale.rates")} placeholder={client.rates[f.product] ? String(client.rates[f.product]) : kind === "return" ? "last supply rate" : "no rate set"} value={f.rate} onChange={(e) => setF({ ...f, rate: e.target.value })} />
          </Field>
          <Field label="Date"><input className="input" type="date" value={f.txn_date} onChange={(e) => setF({ ...f, txn_date: e.target.value })} /></Field>
          <Field label="Tanker / vehicle no."><input className="input" value={f.vehicle_no} onChange={(e) => setF({ ...f, vehicle_no: e.target.value })} /></Field>
          <Field label="Delivery note / ref no."><input className="input" value={f.ref} onChange={(e) => setF({ ...f, ref: e.target.value })} /></Field>
          <Field label="Note"><input className="input" value={f.note} onChange={(e) => setF({ ...f, note: e.target.value })} /></Field>
        </div>
        {tank && <p className="text-xs text-slate-500">{tank.name}: {num(tank.current_l)} L in stock {kind === "return" && `· space ${num(tank.capacity_l - tank.current_l)} L`}</p>}
        {Number(f.litres) > 0 && rate > 0 && <div className="rounded-lg bg-slate-50 p-2 text-sm">{num(Number(f.litres), 2)} L × Rs {rate} = <b>{pkr(amount)}</b> · due after: <b>{pkr(client.summary.due + (kind === "supply" ? amount : -amount))}</b></div>}
        {kind === "supply" && can("wholesale.rates") && client.credit_limit > 0 && <label className="flex items-center gap-2 text-xs"><input type="checkbox" checked={f.override_limit} onChange={(e) => setF({ ...f, override_limit: e.target.checked })} /> Allow even if it crosses the credit limit (admin)</label>}
        <div className="flex justify-end gap-2"><button type="button" className="btn-secondary" onClick={onClose}>Cancel</button><button className="btn-primary" disabled={busy}>Save {kind}</button></div>
      </form>
    </Modal>
  );
}

function PaymentEntry({ client, onClose, onDone }: { client: any; onClose: () => void; onDone: () => void }) {
  const [f, setF] = useState({ amount: "", method: "Bank transfer", ref: "", note: "", txn_date: new Date().toISOString().slice(0, 10) });
  const { busy, run } = useAction();
  return (
    <Modal open onClose={onClose} title={`Receive payment — ${client.name}`}>
      <form className="space-y-3" onSubmit={async (e) => {
        e.preventDefault();
        if (await run(() => api(`/wholesale/clients/${client.id}/payment`, { body: { ...f, amount: Number(f.amount), ref: f.ref || null, note: f.note || null } }), (r: any) => `Payment saved. Due now ${pkr(r.due_after)}`)) onDone();
      }}>
        <p className="text-sm text-slate-600">Current due: <b>{pkr(client.summary.due)}</b></p>
        <div className="grid gap-3 sm:grid-cols-2">
          <Field label="Amount (Rs)"><input className="input" type="number" min={1} required value={f.amount} onChange={(e) => setF({ ...f, amount: e.target.value })} /></Field>
          <Field label="Method"><select className="input" value={f.method} onChange={(e) => setF({ ...f, method: e.target.value })}>{["Bank transfer", "Cash", "Cheque", "Raast", "JazzCash", "Easypaisa", "Online"].map((m) => <option key={m}>{m}</option>)}</select></Field>
          <Field label="Cheque / transaction ref"><input className="input" value={f.ref} onChange={(e) => setF({ ...f, ref: e.target.value })} /></Field>
          <Field label="Date"><input className="input" type="date" value={f.txn_date} onChange={(e) => setF({ ...f, txn_date: e.target.value })} /></Field>
        </div>
        <Field label="Note"><input className="input" value={f.note} onChange={(e) => setF({ ...f, note: e.target.value })} /></Field>
        <div className="flex justify-end gap-2"><button type="button" className="btn-secondary" onClick={onClose}>Cancel</button><button className="btn-primary" disabled={busy}>Save payment</button></div>
      </form>
    </Modal>
  );
}

function AdjustmentEntry({ client, onClose, onDone }: { client: any; onClose: () => void; onDone: () => void }) {
  const [f, setF] = useState({ dir: "-1", amount: "", note: "" });
  const { busy, run } = useAction();
  return (
    <Modal open onClose={onClose} title={`Adjustment — ${client.name}`}>
      <form className="space-y-3" onSubmit={async (e) => {
        e.preventDefault();
        if (await run(() => api(`/wholesale/clients/${client.id}/adjustment`, { body: { amount: Number(f.dir) * Number(f.amount), note: f.note } }), (r: any) => `Adjustment saved. Due now ${pkr(r.due_after)}`)) onDone();
      }}>
        <Field label="Type"><select className="input" value={f.dir} onChange={(e) => setF({ ...f, dir: e.target.value })}><option value="-1">Reduce due (discount, write-off, correction)</option><option value="1">Increase due (charges, freight, correction)</option></select></Field>
        <Field label="Amount (Rs)"><input className="input" type="number" min={1} required value={f.amount} onChange={(e) => setF({ ...f, amount: e.target.value })} /></Field>
        <Field label="Reason (required)"><input className="input" required minLength={3} value={f.note} onChange={(e) => setF({ ...f, note: e.target.value })} /></Field>
        <div className="flex justify-end gap-2"><button type="button" className="btn-secondary" onClick={onClose}>Cancel</button><button className="btn-primary" disabled={busy}>Save</button></div>
      </form>
    </Modal>
  );
}

function RatesEditor({ client, onClose, onDone }: { client: any; onClose: () => void; onDone: () => void }) {
  const [r, setR] = useState<Record<string, string>>(Object.fromEntries(Object.keys(PRODUCTS).map((p) => [p, client.rates[p] ? String(client.rates[p]) : ""])));
  const prices = useApi<any>("/prices");
  const { busy, run } = useAction();
  return (
    <Modal open onClose={onClose} title={`Rate card — ${client.name}`}>
      <form className="space-y-3" onSubmit={async (e) => {
        e.preventDefault();
        const rates = Object.fromEntries(Object.entries(r).filter(([, v]) => Number(v) > 0).map(([k, v]) => [k, Number(v)]));
        if (await run(() => api(`/wholesale/clients/${client.id}/rates`, { method: "PUT", body: { rates } }), "Rates updated")) onDone();
      }}>
        {Object.keys(PRODUCTS).map((p) => (
          <label key={p} className="flex items-center justify-between gap-3">
            <span className="text-sm">{PRODUCTS[p]} <span className="text-xs text-slate-500">(retail Rs {prices.data?.current?.[p]?.price ?? "—"})</span></span>
            <input className="input w-36 text-right" type="number" step="0.01" min={0} placeholder="not supplied" value={r[p]} onChange={(e) => setR({ ...r, [p]: e.target.value })} />
          </label>
        ))}
        <p className="text-xs text-slate-500">New rates apply to future supplies only; past entries keep their rate.</p>
        <div className="flex justify-end gap-2"><button type="button" className="btn-secondary" onClick={onClose}>Cancel</button><button className="btn-primary" disabled={busy}>Save rates</button></div>
      </form>
    </Modal>
  );
}

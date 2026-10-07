import { useEffect, useState } from "react";
import { Link, useNavigate, useParams, useSearchParams } from "react-router-dom";
import { Plus, Search, Truck, Wallet, Undo2, SlidersHorizontal, Download, Printer, Ban, ArrowLeft, Pencil, Send, Banknote } from "lucide-react";
import { api, linkToken, useApi } from "../lib/api";
import { Badge, Empty, ErrorBox, Field, Loading, Modal, PageHeader, Stat, useAction } from "../components/ui";
import { PRODUCTS, ago, d, dt, num, phone, pkr, pkrShort } from "../lib/format";
import { useAuth } from "../App";
import { WholesaleDashboard } from "../components/WholesaleDashboard";
import { ProofPhotos, ProofThumbs } from "../components/Capture";
import { AccountPicker } from "../components/BankParts";
import { PortalCard } from "../components/PortalCard";
import { FleetPicker, FleetTab, TripForm, TripSheet, TripsTab, fleetBody } from "../components/WholesaleFleet";
import { ChequeForm, ClientDeskCard, CollectTab, OrderForm, OrdersTab, PromiseForm } from "../components/WholesaleDesk";
import { WholesaleVoice } from "../components/WholesaleVoice";
const Ur = ({ children, className = "" }: { children: React.ReactNode; className?: string }) => <span lang="ur" dir="rtl" className={`font-urdu ${className}`}>{children}</span>;

const TYPE: Record<string, { label: string; tone: string }> = {
  supply: { label: "Supply", tone: "blue" }, return: { label: "Return", tone: "amber" },
  payment: { label: "Payment", tone: "green" }, adjustment: { label: "Adjustment", tone: "violet" }, carriage: { label: "Carriage / kiraya", tone: "violet" },
  fuel_note: { label: "Fuel payment", tone: "slate" },
} as const;
const fuelLabel = (r: any) => r.fuel_mode === "direct"
  ? `Paid depot direct — ${r.depot_name ?? "depot"}`
  : `Through us → ${r.depot_name ?? "depot"}${r.fuel_status === "held" ? " (forwarding pending)" : ""}`;

export default function Wholesale() {
  const { id } = useParams();
  return id ? <ClientDetail id={id} /> : <ClientList />;
}

/* ---------------- List ---------------- */
function ClientList() {
  const nav = useNavigate();
  const { can } = useAuth();
  const summary = useApi<any>("/wholesale/summary");
  const [params, setParams] = useSearchParams();
  const tab = (params.get("tab") ?? "dashboard") as "dashboard" | "clients" | "orders" | "collect" | "trips" | "fleet";
  const action = params.get("do"); // add-client | trip | rate | tanker | driver (from the menu)
  const setTab = (t: string) => setParams(t === "dashboard" ? {} : { tab: t });
  const clearAction = () => { const p = new URLSearchParams(params); p.delete("do"); setParams(p, { replace: true }); };
  const [sheet, setSheet] = useState<any | null>(null); // the trip just saved
  const [tripsKey, setTripsKey] = useState(0);
  const s = summary.data;

  return (
    <div className="space-y-5">
      <PageHeader title="Wholesale supply · ہول سیل سپلائی" subtitle="Bulk fuel to dealers and businesses — each client has their own rate card and running account" />
      <div className="flex gap-1 overflow-x-auto border-b border-slate-200">
        {([["dashboard", "Dashboard", "ڈیش بورڈ"], ["clients", "Clients", "کلائنٹس"], ["orders", "Order book", "آرڈر بک"], ["collect", "Recovery", "وصولی"], ["trips", "Tanker trips", "ٹینکر ٹرپ"], ["fleet", "Tankers & drivers", "ٹینکر اور ڈرائیور"]] as const).map(([k, l, u]) => (
          <button key={k} onClick={() => setTab(k)} className={`whitespace-nowrap border-b-2 px-3 py-1.5 text-center text-sm leading-tight ${tab === k ? "border-brand-600 font-medium text-brand-700" : "border-transparent text-slate-600"}`}>{l}<Ur className="block text-xs">{u}</Ur></button>
        ))}
      </div>
      {tab === "trips" && <TripsTab key={tripsKey} onNew={can("wholesale.manage") ? () => setParams({ tab: "trips", do: "trip" }) : undefined} />}
      {tab === "fleet" && <FleetTab key={action ?? "fleet"} start={action === "tanker" || action === "driver" ? action : null} />}
      {tab === "orders" && <OrdersTab onTrip={() => setParams({ tab: "orders", do: "trip" })} />}
      {tab === "collect" && <CollectTab />}
      {tab === "clients" && <ClientsTable onAdd={() => setParams({ tab: "clients", do: "add-client" })} />}
      {tab === "dashboard" && <>
        <WholesaleDashboard key={tripsKey} onTrip={() => setParams({ do: "trip" })} onAddClient={() => setParams({ do: "add-client" })} onFleet={() => setTab("fleet")} onTab={setTab} />
        {s && (
          <div className="card">
            <h2 className="p-4 pb-2 font-semibold">Recent wholesale entries</h2>
            <LedgerTable rows={s.recent} showClient />
          </div>
        )}
      </>}
      {action === "trip" && <TripForm onClose={clearAction} onDone={(t) => { clearAction(); summary.reload(); setTripsKey((k) => k + 1); setSheet(t); }} />}
      {action === "rate" && <ClientPicker title="Change rate — which client?" onClose={clearAction} onPick={(c) => nav(`/wholesale/${c.id}?do=rates`)} />}
      {sheet && <TripSheet id={sheet.id} initial={sheet} onClose={() => setSheet(null)} />}
      {action === "add-client" && <ClientForm onClose={clearAction} onSaved={(c) => nav(`/wholesale/${c.id}`)} />}
    </div>
  );
}

/** All clients with their rates, due and limit — the plain list. */
function ClientsTable({ onAdd }: { onAdd: () => void }) {
  const nav = useNavigate();
  const { can } = useAuth();
  const [q, setQ] = useState("");
  const list = useApi<any[]>(`/wholesale/clients?q=${encodeURIComponent(q)}`);
  return (
    <div className="card">
      <div className="flex flex-wrap items-center justify-between gap-2 border-b border-slate-200 p-3">
        <div className="relative w-full max-w-sm"><Search size={15} className="absolute left-2.5 top-2.5 text-slate-400" /><input className="input pl-8" placeholder="Search client" value={q} onChange={(e) => setQ(e.target.value)} /></div>
        {can("wholesale.manage") && <button className="btn-primary" onClick={onAdd}><Plus size={16} /> Add client</button>}
      </div>
      {list.error && <div className="p-3"><ErrorBox error={list.error} /></div>}
      <ul className="divide-y divide-slate-100 sm:hidden">
        {(list.data ?? []).map((c) => {
          const used = c.credit_limit ? Math.min(100, (c.due / c.credit_limit) * 100) : 0;
          return (
            <li key={c.id} className={`cursor-pointer space-y-1.5 p-3 active:bg-slate-50 ${c.active ? "" : "opacity-50"}`} onClick={() => nav(`/wholesale/${c.id}`)}>
              <div className="flex items-start justify-between gap-2">
                <div className="min-w-0"><div className="break-words font-medium">{c.name}</div><div className="break-words text-xs text-slate-500">{c.business_name ?? ""}{c.phone && ` · ${phone(c.phone)}`}</div></div>
                <div className="shrink-0 text-right"><div className={`font-semibold tabular-nums ${c.due > 0 ? "" : "text-emerald-600"}`}>{pkr(c.due)}</div><div className="text-[11px] text-slate-500">Due · <span lang="ur" className="font-urdu">بقایا</span></div></div>
              </div>
              <div className="flex flex-wrap gap-x-3 gap-y-0.5 text-xs">{Object.entries(c.rate_card ?? {}).map(([p, r]: any) => <span key={p}>{PRODUCTS[p]}: <b>Rs {r.rate?.toFixed(2) ?? "—"}</b></span>)}{!Object.keys(c.rates).length && <span className="text-amber-600">No rate set · <span lang="ur" className="font-urdu">ریٹ نہیں</span></span>}</div>
              {c.credit_limit > 0 && <div className="flex items-center gap-2"><div className="h-1.5 flex-1 rounded-full bg-slate-100"><div className="h-full rounded-full" style={{ width: `${used}%`, background: used >= 90 ? "#e34948" : "#2a78d6" }} /></div><span className="shrink-0 text-[11px] text-slate-500">{Math.round(used)}% of {pkrShort(c.credit_limit)}</span></div>}
              <div className="flex justify-between text-[11px] text-slate-500"><span>{num(c.month_l)} L this month</span><span>Supply {ago(c.last_supply)} · Paid {ago(c.last_payment)}</span></div>
            </li>
          );
        })}
      </ul>
      <div className="hidden overflow-x-auto sm:block">
        <table className="w-full">
          <thead><tr><th className="th">Client</th><th className="th">Rates (per litre)</th><th className="th text-right">This month</th><th className="th text-right">Due</th><th className="th">Limit used</th><th className="th">Last supply</th><th className="th">Last payment</th></tr></thead>
          <tbody>
            {(list.data ?? []).map((c) => {
              const used = c.credit_limit ? Math.min(100, (c.due / c.credit_limit) * 100) : 0;
              return (
                <tr key={c.id} className={`cursor-pointer hover:bg-slate-50 ${c.active ? "" : "opacity-50"}`} onClick={() => nav(`/wholesale/${c.id}`)}>
                  <td className="td"><div className="font-medium">{c.name}</div><div className="text-xs text-slate-500">{c.business_name ?? ""}{c.phone && ` · ${phone(c.phone)}`}</div></td>
                  <td className="td text-xs">{Object.entries(c.rate_card ?? {}).map(([p, r]: any) => <div key={p}>{PRODUCTS[p]}: <b>Rs {r.rate?.toFixed(2) ?? "—"}</b> <span className={`whitespace-nowrap ${r.mode === "discount" ? "text-violet-700" : "text-slate-400"}`}>{r.label}</span></div>)}{!Object.keys(c.rates).length && <span className="text-amber-600">No rate set</span>}</td>
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
  );
}

function ClientPicker({ title, onClose, onPick }: { title: string; onClose: () => void; onPick: (c: any) => void }) {
  const [q, setQ] = useState("");
  const list = useApi<any[]>(`/wholesale/clients?q=${encodeURIComponent(q)}`);
  return (
    <Modal open onClose={onClose} title={title}>
      <input className="input mb-2" autoFocus placeholder="Search client" value={q} onChange={(e) => setQ(e.target.value)} />
      <div className="max-h-[60vh] space-y-1 overflow-y-auto">
        {(list.data ?? []).filter((c) => c.active).map((c) => (
          <button key={c.id} className="flex w-full items-center justify-between rounded-lg px-3 py-2 text-left hover:bg-slate-50" onClick={() => onPick(c)}>
            <span><span className="font-medium">{c.name}</span> <span className="text-xs text-slate-500">{c.city}</span></span>
            <span className="text-xs text-slate-600">{Object.entries(c.rates ?? {}).map(([p, r]: any) => `${PRODUCTS[p]} ${r}`).join(" · ")}</span>
          </button>
        ))}
      </div>
    </Modal>
  );
}

export function ClientForm({ initial, onClose, onSaved }: { initial?: any; onClose: () => void; onSaved: (c: any) => void }) {
  const { can } = useAuth();
  const admin = can("wholesale.rates");
  const [f, setF] = useState<any>({
    name: initial?.name ?? "", business_name: initial?.business_name ?? "", phone: initial?.phone ?? "", city: initial?.city ?? "", address: initial?.address ?? "",
    credit_limit: initial?.credit_limit ?? 0, opening_balance: initial?.opening_balance ?? 0, notes: initial?.notes ?? "",
  });
  const [rc, setRc] = useState<RateDraft>(() => draftFrom(initial?.rate_card));
  const prices = useApi<any>(admin ? "/prices" : null);
  const { busy, run } = useAction();
  const save = async (e: React.FormEvent) => {
    e.preventDefault();
    const body: any = { name: f.name, business_name: f.business_name || null, phone: f.phone || null, city: f.city || null, address: f.address || null, notes: f.notes || null };
    if (admin) {
      body.credit_limit = Number(f.credit_limit) || 0;
      body.opening_balance = Number(f.opening_balance) || 0;
      const rates = draftToBody(rc);
      if (Object.keys(rates).length) body.rates = rates;
    }
    const r = await run(() => initial ? api(`/wholesale/clients/${initial.id}`, { method: "PATCH", body }) : api("/wholesale/clients", { body }), "Client saved");
    if (r) onSaved(r);
  };
  return (
    <Modal open onClose={onClose} title={initial ? `Edit ${initial.name}` : "Add wholesale client"} wide>
      <form onSubmit={save} className="space-y-4">
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
          <Field label="Contact / client name"><input className="input" required value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} /></Field>
          <Field label="Business name"><input className="input" value={f.business_name} onChange={(e) => setF({ ...f, business_name: e.target.value })} /></Field>
          <Field label="WhatsApp / phone"><input className="input" placeholder="03xx xxxxxxx" value={f.phone} onChange={(e) => setF({ ...f, phone: e.target.value })} /></Field>
          <Field label="City"><input className="input" value={f.city} onChange={(e) => setF({ ...f, city: e.target.value })} /></Field>
          <div className="sm:col-span-2"><Field label="Address"><input className="input" value={f.address} onChange={(e) => setF({ ...f, address: e.target.value })} /></Field></div>
        </div>
        {admin ? (
          <div className="rounded-lg border border-violet-200 bg-violet-50/50 p-3">
            <div className="mb-2 text-sm font-medium">Rate card & credit (admin only)</div>
            <RateCardEditor draft={rc} setDraft={setRc} pump={prices.data?.current} />
            <div className="mt-3 grid grid-cols-1 gap-3 sm:grid-cols-2">
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
  const [params, setParams] = useSearchParams();
  const [action, setAction] = useState<null | "supply" | "return" | "payment" | "adjustment" | "rates" | "edit" | "order" | "promise" | "cheque" | "carriage" | "fuelpay">(() => (params.get("do") as any) || null);
  // supplying a booked order (from the order book) fills the form and closes the order
  const [orderId, setOrderId] = useState<number | null>(() => Number(params.get("order")) || null);
  const [deskKey, setDeskKey] = useState(0);
  useEffect(() => { if (params.get("do")) setParams({}, { replace: true }); }, []);
  const [sending, setSending] = useState(false);
  const { run: runMsg } = useAction();
  const refresh = () => { client.reload(); stmt.reload(); setDeskKey((k) => k + 1); };

  if (client.error) return <ErrorBox error={client.error} />;
  if (!client.data) return <Loading />;
  const c = client.data;
  const s = c.summary;
  const m = c.month;
  const csvUrl = `/api/wholesale/clients/${id}/statement.csv?${qs ? qs + "&" : ""}token=${linkToken()}`;

  return (
    <div className="space-y-5">
      <div className="print:hidden"><Link to="/wholesale" className="inline-flex min-h-9 items-center gap-1 text-sm text-brand-600 hover:underline"><ArrowLeft size={14} /> All clients</Link></div>
      <PageHeader title={c.name} subtitle={[c.business_name, c.city, c.phone && phone(c.phone)].filter(Boolean).join(" · ")}
        actions={<div className="flex flex-wrap gap-2 print:hidden">
          {can("wholesale.manage") && <>
            <button className="btn-primary" onClick={() => setAction("supply")} disabled={!c.active}><Truck size={15} /> New supply · <Ur>سپلائی</Ur></button>
            <button className="btn-secondary" onClick={() => setAction("payment")}><Wallet size={15} /> Receive payment · <Ur>رقم وصول</Ur></button>
            <button className="btn-secondary" onClick={() => setAction("return")}><Undo2 size={15} /> Fuel return · <Ur>واپسی</Ur></button>
            {can("wholesale.manage") && <button className="btn-secondary" onClick={() => setAction("carriage")} disabled={!c.active}><Truck size={15} /> Bypass (our ID) · <Ur>کرایہ</Ur></button>}
            {can("wholesale.manage") && <button className="btn-secondary" onClick={() => setAction("fuelpay")}><Banknote size={15} /> Fuel payment · <Ur>فیول</Ur></button>}
          </>}
          {can("wholesale.rates") && <button className="btn-secondary" onClick={() => setAction("adjustment")}><SlidersHorizontal size={15} /> Adjustment · <Ur>ایڈجسٹمنٹ</Ur></button>}
          {can("wholesale.manage") && c.phone && <button className="btn-secondary" disabled={sending} onClick={async () => {
            setSending(true);
            await runMsg(() => api(`/wholesale/clients/${c.id}/send-statement`, { body: { month: new Date(Date.now() + 5 * 3600_000).toISOString().slice(0, 7) } }), "This month's statement sent on WhatsApp");
            setSending(false);
          }}><Send size={15} /> WhatsApp statement · <Ur>حساب بھیجیں</Ur></button>}
          {can("wholesale.manage") && <button className="btn-secondary" onClick={() => setAction("edit")}><Pencil size={15} /> Edit · <Ur>تبدیل</Ur></button>}
        </div>} />

      {(can("wholesale.manage") || can("wholesale.view")) && <div className="print:hidden"><WholesaleVoice compact clientId={c.id} onDone={refresh} /></div>}

      <div className="grid grid-cols-2 gap-3 lg:grid-cols-5">
        <Stat label="Current due · بقایا" value={pkr(s.due)} tone={s.due > 0 ? "amber" : "green"} hint={c.credit_limit ? `Limit ${pkr(c.credit_limit)} · ${Math.round((s.due / c.credit_limit) * 100)}% used` : "No credit limit"} />
        <Stat label="Total billed · کل بل" value={pkrShort(s.billed)} hint={`${s.supplies} supplies · opening ${pkr(c.opening_balance)}`} />
        <Stat label="Total received · کل وصولی" value={pkrShort(s.received)} tone="green" hint={`Last payment ${ago(s.last_payment)}`} />
        <Stat label="Returns credited · واپسی" value={pkrShort(s.returned)} hint={s.adjustments ? `Adjustments ${pkr(s.adjustments)}` : undefined} />
        <Stat label="This month · اس مہینے" value={`${num(m.by_product.reduce((a: number, p: any) => a + p.supplied_l, 0))} L`} hint={`Billed ${pkrShort(m.billed)} · received ${pkrShort(m.received)}`} />
      </div>

      <div className="grid grid-cols-1 gap-5 lg:grid-cols-3">
        <div className="card min-w-0 p-4">
          <div className="mb-2 flex items-center justify-between"><h2 className="font-semibold">Rate card · <Ur>ریٹ</Ur></h2>{can("wholesale.rates") && <button className="-my-2 min-h-9 px-1 text-xs text-brand-600 hover:underline print:hidden" onClick={() => setAction("rates")}>Change rates</button>}</div>
          {Object.keys(PRODUCTS).map((p) => {
            const r = c.rate_card?.[p];
            return (
              <div key={p} className="border-b border-slate-100 py-2 text-sm">
                <div className="flex justify-between"><span>{PRODUCTS[p]}</span>
                  <span className="font-semibold tabular-nums">{r?.rate != null ? `Rs ${r.rate.toFixed(2)} / L` : <span className="font-normal text-slate-400">not supplied</span>}</span></div>
                {r && <div className="mt-0.5 flex flex-wrap justify-between gap-x-3 text-xs text-slate-500">
                  <span><Badge tone={r.mode === "discount" ? "violet" : "slate"}>{r.mode === "discount" ? `Pump − Rs ${r.discount.toFixed(2)}` : "Fixed rate"}</Badge>
                    {r.pump != null && <span className="ml-1">pump Rs {r.pump.toFixed(2)}</span>}</span>
                  {r.margin != null && <span className={r.margin < 0 ? "font-medium text-red-600" : "text-emerald-700"}>our margin Rs {r.margin.toFixed(2)}/L</span>}
                </div>}
              </div>
            );
          })}
          <p className="mt-2 text-xs text-slate-400">"Pump − Rs X" rates change automatically with every pump price change. Our margin = client rate − last purchase rate.</p>
          {c.rate_history.length > 0 && <details className="mt-2 text-xs text-slate-500"><summary className="cursor-pointer">Rate history</summary>
            <ul className="mt-1 space-y-0.5">{c.rate_history.map((h: any) => <li key={h.id}>{d(h.created_at)} · {PRODUCTS[h.product]}: {h.old_rate ? `Rs ${h.old_rate} → ` : ""}Rs {h.new_rate}{h.note ? ` · ${h.note}` : ""} ({h.changed_by})</li>)}</ul></details>}
        </div>
        <div className="card min-w-0 p-4 lg:col-span-2">
          <h2 className="mb-2 font-semibold">Fuel account (all time) · <Ur>تیل کا حساب</Ur></h2>
          {/* phone: one row per product */}
          <ul className="divide-y divide-slate-100 sm:hidden print:hidden">
            {s.by_product.map((p: any) => (
              <li key={p.product} className="py-2">
                <div className="flex items-start justify-between gap-2"><span className="min-w-0 font-medium">{PRODUCTS[p.product]}</span><span className="shrink-0 font-semibold tabular-nums">{pkr(p.net_amount)}</span></div>
                <div className="text-xs text-slate-500">Out {num(p.supplied_l)} L · returned {num(p.returned_l)} L · net <b className="tabular-nums text-slate-700">{num(p.supplied_l - p.returned_l)} L</b></div>
              </li>
            ))}
          </ul>
          <table className="hidden w-full sm:table print:table">
            <thead><tr><th className="th">Product</th><th className="th text-right">Supplied (out)</th><th className="th text-right">Returned (in)</th><th className="th text-right">Net litres</th><th className="th text-right">Net amount</th></tr></thead>
            <tbody>{s.by_product.map((p: any) => (
              <tr key={p.product}><td className="td">{PRODUCTS[p.product]}</td><td className="td text-right tabular-nums">{num(p.supplied_l)} L</td><td className="td text-right tabular-nums">{num(p.returned_l)} L</td>
                <td className="td text-right font-medium tabular-nums">{num(p.supplied_l - p.returned_l)} L</td><td className="td text-right tabular-nums">{pkr(p.net_amount)}</td></tr>
            ))}</tbody>
          </table>
          {!s.by_product.length && <Empty>No fuel supplied yet</Empty>}
        </div>
      </div>

      {can("wholesale.manage") && <HeldFuel clientId={c.id} refreshKey={deskKey} onChanged={refresh} />}

      <ClientDeskCard client={c} refreshKey={deskKey} onChanged={refresh} onAction={(a, oid) => { if (a === "supply-order") { setOrderId(oid ?? null); setAction("supply"); } else setAction(a); }} />

      <div className="print:hidden"><PortalCard base={`/wholesale/clients/${c.id}/portal`} name={c.name} phone={c.phone} canManage={can("wholesale.manage")} /></div>

      <div className="card">
        <div className="grid grid-cols-2 items-end gap-3 p-4 sm:flex sm:flex-wrap print:hidden">
          <h2 className="col-span-2 mr-auto font-semibold">Account statement · <Ur>کھاتہ</Ur></h2>
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

      {(action === "supply" || action === "return") && <FuelEntry kind={action} client={c} orderId={action === "supply" ? orderId : null} onClose={() => { setAction(null); setOrderId(null); }} onDone={() => { setAction(null); setOrderId(null); refresh(); }} />}
      {action === "order" && <OrderForm client={c} onClose={() => setAction(null)} onDone={() => { setAction(null); refresh(); }} />}
      {action === "promise" && <PromiseForm client={c} onClose={() => setAction(null)} onDone={() => { setAction(null); refresh(); }} />}
      {action === "cheque" && <ChequeForm client={c} onClose={() => setAction(null)} onDone={() => { setAction(null); refresh(); }} />}
      {action === "payment" && <PaymentEntry client={c} onClose={() => setAction(null)} onDone={() => { setAction(null); refresh(); }} />}
      {action === "adjustment" && <AdjustmentEntry client={c} onClose={() => setAction(null)} onDone={() => { setAction(null); refresh(); }} />}
      {action === "carriage" && <CarriageEntry client={c} onClose={() => setAction(null)} onDone={() => { setAction(null); refresh(); }} />}
      {action === "fuelpay" && <FuelPaymentEntry client={c} onClose={() => setAction(null)} onDone={() => { setAction(null); refresh(); }} />}
      {action === "rates" && <RatesEditor client={c} onClose={() => setAction(null)} onDone={() => { setAction(null); refresh(); }} />}
      {action === "edit" && <ClientForm initial={c} onClose={() => setAction(null)} onSaved={() => { setAction(null); refresh(); }} />}
    </div>
  );
}

function LedgerTable({ rows, running, showClient, onVoid }: { rows: any[]; running?: boolean; showClient?: boolean; onVoid?: (row: any) => void }) {
  if (!rows.length) return <Empty>No entries</Empty>;
  const amounts = (r: any) => {
    if (r.type === "fuel_note") return { debit: 0, credit: 0 }; // informational — never in the debit/credit columns
    const debit = running ? r.debit : r.type === "supply" || r.type === "carriage" || (r.type === "adjustment" && r.amount > 0) ? Math.abs(r.amount) : 0;
    return { debit, credit: running ? r.credit : debit ? 0 : Math.abs(r.amount) };
  };
  const extra = (r: any) => [r.vehicle_no && `🚛 ${r.vehicle_no}`, r.driver_name && `👤 ${r.driver_name}`, r.location && `📍 ${r.location}`, r.trip_id && `trip #${r.trip_id}`, r.ref, r.note, r.voided && r.void_reason].filter(Boolean).join(" · ");
  return (
    <>
    {/* phone: one line per entry */}
    <ul className="divide-y divide-slate-100 sm:hidden print:hidden">
      {rows.map((r) => {
        const { debit, credit } = amounts(r);
        return (
          <li key={r.id} className={`flex gap-3 px-4 py-2.5 ${r.voided ? "text-slate-400 line-through" : ""}`}>
            <div className="min-w-0 flex-1">
              <div className="flex flex-wrap items-center gap-1.5 text-xs text-slate-500"><Badge tone={TYPE[r.type].tone}>{TYPE[r.type].label}</Badge>{r.voided ? <Badge tone="red">VOID</Badge> : null}{dt(r.txn_date)}</div>
              {showClient && <Link to={`/wholesale/${r.client_id}`} className="flex min-h-9 items-center font-medium hover:underline">{r.client_name}</Link>}
              <div className="text-sm">{r.type === "fuel_note" ? <>Fuel <b>{pkr(r.amount)}</b> · {fuelLabel(r)}</> : r.type === "carriage" ? `Kiraya${r.litres ? ` · ${num(r.litres, 2)} L ${r.product ? PRODUCTS[r.product] : "fuel"}` : ""}` : r.product ? `${num(r.litres, 2)} L ${PRODUCTS[r.product]} @ Rs ${r.rate}` : r.method ?? ""}</div>
              {extra(r) && <div className="break-words text-xs text-slate-500">{extra(r)}</div>}
              <ProofThumbs ids={r.proof_ids} />
            </div>
            <div className="shrink-0 text-right tabular-nums">
              {debit ? <div className="font-semibold">{pkr(debit)}</div> : null}
              {credit ? <div className="font-semibold text-emerald-700">−{pkr(credit)}</div> : null}
              {running && <div className="text-xs text-slate-500">bal {pkr(r.balance)}</div>}
              {onVoid && !r.voided && <button className="-mr-2 inline-flex h-9 w-9 items-center justify-center text-slate-400 hover:text-red-600" title="Void entry" aria-label="Void entry" onClick={() => onVoid(r)}><Ban size={14} /></button>}
            </div>
          </li>
        );
      })}
    </ul>
    <div className="hidden overflow-x-auto sm:block print:block">
      <table className="w-full">
        <thead><tr>
          <th className="th">Date</th>{showClient && <th className="th">Client</th>}<th className="th">Entry</th><th className="th">Details</th>
          <th className="th text-right">Debit<span className="print:hidden"> (billed)</span></th><th className="th text-right">Credit<span className="print:hidden"> (paid / returned)</span></th>{running && <th className="th text-right">Balance</th>}<th className="th print:hidden" />
        </tr></thead>
        <tbody>
          {rows.map((r) => {
            const debit = r.type === "fuel_note" ? 0 : running ? r.debit : r.type === "supply" || r.type === "carriage" || (r.type === "adjustment" && r.amount > 0) ? Math.abs(r.amount) : 0;
            const credit = r.type === "fuel_note" ? 0 : running ? r.credit : debit ? 0 : Math.abs(r.amount);
            return (
              <tr key={r.id} className={r.voided ? "text-slate-400 line-through" : ""}>
                <td className="td text-xs"><span className="print:hidden">{dt(r.txn_date)}</span><span className="hidden whitespace-nowrap print:inline">{d(r.txn_date)}</span></td>
                {showClient && <td className="td text-sm"><Link to={`/wholesale/${r.client_id}`} className="hover:underline">{r.client_name}</Link></td>}
                <td className="td"><Badge tone={TYPE[r.type].tone}>{TYPE[r.type].label}</Badge> {r.voided ? <Badge tone="red">VOID</Badge> : null}</td>
                <td className="td text-xs">
                  {r.type === "fuel_note" ? <div>Fuel <b>{pkr(r.amount)}</b> · {fuelLabel(r)}</div>
                    : r.type === "carriage" ? <div>Kiraya{r.litres ? ` · ${num(r.litres, 2)} L ${r.product ? PRODUCTS[r.product] : "fuel"}` : ""}</div>
                    : r.product && <div>{num(r.litres, 2)} L {PRODUCTS[r.product]} @ Rs {r.rate}{r.station_name ? ` · ${r.station_name}` : ""}</div>}
                  {r.method && <div>{r.method}</div>}
                  <ProofThumbs ids={r.proof_ids} />
                  <div className="text-slate-500 print:text-[10px] print:leading-tight">{[r.vehicle_no && `🚛 ${r.vehicle_no}`, r.driver_name && `👤 ${r.driver_name}`, r.location && `📍 ${r.location}`, r.trip_id && `trip #${r.trip_id}`, r.ref, r.note, r.voided && r.void_reason].filter(Boolean).join(" · ")}</div>
                </td>
                <td className="td whitespace-nowrap text-right tabular-nums">{debit ? pkr(debit) : ""}</td>
                <td className="td whitespace-nowrap text-right tabular-nums text-emerald-700">{credit ? pkr(credit) : ""}</td>
                {running && <td className="td whitespace-nowrap text-right font-medium tabular-nums">{pkr(r.balance)}</td>}
                <td className="td print:hidden">{onVoid && !r.voided && <button className="text-slate-400 hover:text-red-600" title="Void entry" onClick={() => onVoid(r)}><Ban size={14} /></button>}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
    </>
  );
}

function FuelEntry({ kind, client, orderId, onClose, onDone }: { kind: "supply" | "return"; client: any; orderId?: number | null; onClose: () => void; onDone: () => void }) {
  const desk = useApi<any>(orderId ? `/wholesale/clients/${client.id}/desk` : null);
  const order = desk.data?.orders.find((o: any) => o.id === orderId);
  const { can } = useAuth();
  const stations = useApi<any[]>("/stations");
  const products = Object.keys(client.rates).length ? Object.keys(client.rates) : Object.keys(PRODUCTS);
  const [photos, setPhotos] = useState<number[]>([]);
  const [f, setF] = useState<any>({ station_id: "", product: products[0], litres: "", rate: "", tanker_id: "", driver_id: "", vehicle_no: "", location: client.city ?? "", ref: "", note: "", txn_date: new Date().toISOString().slice(0, 10), override_limit: false });
  useEffect(() => { if (stations.data && !f.station_id) setF((x: any) => ({ ...x, station_id: stations.data![0].id })); }, [stations.data]);
  useEffect(() => { if (order) setF((x: any) => ({ ...x, product: order.product, litres: String(order.litres), location: order.location ?? x.location, note: order.note ?? x.note })); }, [order?.id]);
  const { busy, run } = useAction();
  const station = stations.data?.find((s) => s.id === Number(f.station_id));
  const tank = station?.tanks.filter((t: any) => t.product === f.product).sort((a: any, b: any) => b.current_l - a.current_l)[0];
  const rate = Number(f.rate) || client.rates[f.product] || 0;
  const amount = Number(f.litres) * rate;
  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    const body: any = { station_id: Number(f.station_id), product: f.product, litres: Number(f.litres), ref: f.ref || null, note: f.note || null, txn_date: f.txn_date, photo_ids: photos,
      ...(kind === "supply" ? { ...fleetBody(f), location: f.location || null } : { vehicle_no: f.vehicle_no || null }) };
    if (f.rate && can("wholesale.rates")) body.rate = Number(f.rate);
    if (f.override_limit) body.override_limit = true;
    if (order) body.order_id = order.id;
    if (await run(() => api(`/wholesale/clients/${client.id}/${kind}`, { body }), (r: any) => `${kind === "supply" ? "Supply" : "Return"} saved. Due now ${pkr(r.due_after)}`)) onDone();
  };
  return (
    <Modal open onClose={onClose} title={kind === "supply" ? `Supply fuel to ${client.name} · سپلائی` : `Fuel returned by ${client.name} · واپسی`}>
      <form onSubmit={submit} className="space-y-3">
        {order && <p className="rounded-lg bg-brand-50 p-2 text-sm text-brand-800">📋 Delivering the order for <b>{order.needed_on}</b> — {num(order.litres)} L {PRODUCTS[order.product]}. Saving closes the order.</p>}
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
          <Field label={kind === "supply" ? "From station · کہاں سے" : "Into station · کہاں"}><select className="input" value={f.station_id} onChange={(e) => setF({ ...f, station_id: e.target.value })}>{(stations.data ?? []).map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}</select></Field>
          <Field label="Product · تیل"><select className="input" value={f.product} onChange={(e) => setF({ ...f, product: e.target.value })}>{products.map((p) => <option key={p} value={p}>{PRODUCTS[p]}</option>)}</select></Field>
          <Field label="Litres · لیٹر"><input className="input" type="number" step="0.01" min={1} required value={f.litres} onChange={(e) => setF({ ...f, litres: e.target.value })} /></Field>
          <Field label={can("wholesale.rates") ? "Rate (Rs/L) — blank = rate card" : "Rate (Rs/L)"}>
            <input className="input" type="number" step="0.01" disabled={!can("wholesale.rates")} placeholder={client.rates[f.product] ? String(client.rates[f.product]) : kind === "return" ? "last supply rate" : "no rate set"} value={f.rate} onChange={(e) => setF({ ...f, rate: e.target.value })} />
          </Field>
          <Field label="Date · تاریخ"><input className="input" type="date" value={f.txn_date} onChange={(e) => setF({ ...f, txn_date: e.target.value })} /></Field>
          {kind === "supply" ? <>
            <FleetPicker f={f} setF={setF} />
            <Field label="Drop location · جگہ"><input className="input" value={f.location} onChange={(e) => setF({ ...f, location: e.target.value })} /></Field>
          </> : <Field label="Tanker / vehicle no."><input className="input" value={f.vehicle_no} onChange={(e) => setF({ ...f, vehicle_no: e.target.value })} /></Field>}
          <Field label="Delivery note / ref no."><input className="input" value={f.ref} onChange={(e) => setF({ ...f, ref: e.target.value })} /></Field>
          <Field label="Note"><input className="input" value={f.note} onChange={(e) => setF({ ...f, note: e.target.value })} /></Field>
        </div>
        <ProofPhotos value={photos} onChange={setPhotos} hint={kind === "supply" ? "signed delivery note / chalan, tanker at site" : "return slip"} />
        {tank && <p className="text-xs text-slate-500">{tank.name}: {num(tank.current_l)} L in stock {kind === "return" && `· space ${num(tank.capacity_l - tank.current_l)} L`}</p>}
        {Number(f.litres) > 0 && rate > 0 && <div className="rounded-lg bg-slate-50 p-2 text-sm">{num(Number(f.litres), 2)} L × Rs {rate} = <b>{pkr(amount)}</b> · due after: <b>{pkr(client.summary.due + (kind === "supply" ? amount : -amount))}</b></div>}
        {kind === "supply" && can("wholesale.rates") && client.credit_limit > 0 && <label className="flex items-center gap-2 text-xs"><input type="checkbox" checked={f.override_limit} onChange={(e) => setF({ ...f, override_limit: e.target.checked })} /> Allow even if it crosses the credit limit (admin)</label>}
        <div className="flex justify-end gap-2"><button type="button" className="btn-secondary" onClick={onClose}>Cancel</button><button className="btn-primary" disabled={busy}>Save {kind} · محفوظ کریں</button></div>
      </form>
    </Modal>
  );
}

function PaymentEntry({ client, onClose, onDone }: { client: any; onClose: () => void; onDone: () => void }) {
  const [f, setF] = useState({ amount: "", method: "Bank transfer", ref: "", note: "", txn_date: new Date().toISOString().slice(0, 10) });
  const [photos, setPhotos] = useState<number[]>([]);
  const [account, setAccount] = useState<number | null>(null);
  const { busy, run } = useAction();
  return (
    <Modal open onClose={onClose} title={`Receive payment — ${client.name} · رقم وصول`}>
      <form className="space-y-3" onSubmit={async (e) => {
        e.preventDefault();
        if (await run(() => api(`/wholesale/clients/${client.id}/payment`, { body: { ...f, amount: Number(f.amount), ref: f.ref || null, note: f.note || null, photo_ids: photos, account_id: account } }), (r: any) => `Payment saved. Due now ${pkr(r.due_after)}`)) onDone();
      }}>
        <p className="text-sm text-slate-600">Current due: <b>{pkr(client.summary.due)}</b></p>
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
          <Field label="Amount (Rs) · رقم"><input className="input" type="number" min={1} required value={f.amount} onChange={(e) => setF({ ...f, amount: e.target.value })} /></Field>
          <Field label="Method · طریقہ"><select className="input" value={f.method} onChange={(e) => setF({ ...f, method: e.target.value })}>{["Bank transfer", "Cash", "Cheque", "Raast", "JazzCash", "Easypaisa", "Online"].map((m) => <option key={m}>{m}</option>)}</select></Field>
          <Field label="Cheque / transaction ref"><input className="input" value={f.ref} onChange={(e) => setF({ ...f, ref: e.target.value })} /></Field>
          <Field label="Date"><input className="input" type="date" value={f.txn_date} onChange={(e) => setF({ ...f, txn_date: e.target.value })} /></Field>
        </div>
        <AccountPicker method={f.method} value={account} onChange={setAccount} />
        <Field label="Note"><input className="input" value={f.note} onChange={(e) => setF({ ...f, note: e.target.value })} /></Field>
        <ProofPhotos value={photos} onChange={setPhotos} required={f.method === "Cheque"} hint={f.method === "Cheque" ? "photo of the cheque (both sides)" : f.method === "Cash" ? "cash receipt / counted notes" : "bank slip or payment screenshot"} />
        <div className="flex justify-end gap-2"><button type="button" className="btn-secondary" onClick={onClose}>Cancel</button><button className="btn-primary" disabled={busy || (f.method === "Cheque" && !photos.length)}>Save payment · محفوظ کریں</button></div>
      </form>
    </Modal>
  );
}

function AdjustmentEntry({ client, onClose, onDone }: { client: any; onClose: () => void; onDone: () => void }) {
  const [f, setF] = useState({ dir: "-1", amount: "", note: "" });
  const [photos, setPhotos] = useState<number[]>([]);
  const { busy, run } = useAction();
  return (
    <Modal open onClose={onClose} title={`Adjustment — ${client.name}`}>
      <form className="space-y-3" onSubmit={async (e) => {
        e.preventDefault();
        if (await run(() => api(`/wholesale/clients/${client.id}/adjustment`, { body: { amount: Number(f.dir) * Number(f.amount), note: f.note, photo_ids: photos } }), (r: any) => `Adjustment saved. Due now ${pkr(r.due_after)}`)) onDone();
      }}>
        <Field label="Type"><select className="input" value={f.dir} onChange={(e) => setF({ ...f, dir: e.target.value })}><option value="-1">Reduce due (discount, write-off, correction)</option><option value="1">Increase due (charges, freight, correction)</option></select></Field>
        <Field label="Amount (Rs)"><input className="input" type="number" min={1} required value={f.amount} onChange={(e) => setF({ ...f, amount: e.target.value })} /></Field>
        <Field label="Reason (required)"><input className="input" required minLength={3} value={f.note} onChange={(e) => setF({ ...f, note: e.target.value })} /></Field>
        <ProofPhotos value={photos} onChange={setPhotos} hint="freight bill, letter, agreement" />
        <div className="flex justify-end gap-2"><button type="button" className="btn-secondary" onClick={onClose}>Cancel</button><button className="btn-primary" disabled={busy}>Save</button></div>
      </form>
    </Modal>
  );
}

/** Bypass supply on OUR depot ID: the fuel is the client's (not our books), we only bill the kiraya/carriage as income. */
function CarriageEntry({ client, onClose, onDone }: { client: any; onClose: () => void; onDone: () => void }) {
  const depots = useApi<any[]>("/wholesale/depots");
  const [f, setF] = useState({ supplier_id: "", invoice_ref: "", vehicle_no: "", amount: "", note: "" });
  const [lines, setLines] = useState<{ product: string; litres: string }[]>([{ product: "HSD", litres: "" }]);
  const { busy, run } = useAction();
  const totalL = lines.reduce((a, l) => a + (Number(l.litres) || 0), 0);
  const kiraya = Number(f.amount) || 0; // fixed kiraya written on the depot invoice
  const valid = f.supplier_id && totalL > 0 && kiraya > 0;
  return (
    <Modal open onClose={onClose} title={`Bypass supply (our ID) — ${client.name}`}>
      <form className="space-y-3" onSubmit={async (e) => {
        e.preventDefault();
        const body = {
          supplier_id: Number(f.supplier_id), invoice_ref: f.invoice_ref || null, vehicle_no: f.vehicle_no || null,
          lines: lines.filter((l) => Number(l.litres) > 0).map((l) => ({ product: l.product, litres: Number(l.litres) })),
          mode: "lump", amount: Number(f.amount), note: f.note || null,
        };
        if (await run(() => api(`/wholesale/clients/${client.id}/carriage`, { body }), (r: any) => `Kiraya ${pkr(r.kiraya)} billed. Due now ${pkr(r.due)}`)) onDone();
      }}>
        <p className="rounded-lg bg-sky-50 px-3 py-2 text-xs text-sky-900">Depot humari ID par load karta hai aur hamein invoice deta hai — fuel ka paisa humari books mein nahi aata. Hum sirf <b>kiraya</b> client se charge karte hain, jo poora munafa hai. <Ur className="block">صرف کرایہ کلائنٹ کے ذمے — فیول ہمارے کھاتے میں نہیں</Ur></p>
        <Field label="Depot (our ID) *"><select className="input" required value={f.supplier_id} onChange={(e) => setF({ ...f, supplier_id: e.target.value })}>
          <option value="">— choose depot —</option>{(depots.data ?? []).map((d) => <option key={d.id} value={d.id}>{d.name}</option>)}</select></Field>
        <div className="space-y-2">
          <span className="label">Fuel lifted (for the record) · <Ur>کتنا تیل</Ur></span>
          {lines.map((l, i) => (
            <div key={i} className="flex gap-2">
              <select className="input" value={l.product} onChange={(e) => setLines(lines.map((x, j) => j === i ? { ...x, product: e.target.value } : x))}>{Object.entries(PRODUCTS).map(([k, v]) => <option key={k} value={k}>{v}</option>)}</select>
              <input className="input" type="number" min={0} placeholder="litres" value={l.litres} onChange={(e) => setLines(lines.map((x, j) => j === i ? { ...x, litres: e.target.value } : x))} />
              {lines.length > 1 && <button type="button" className="min-h-10 px-2 text-red-600" aria-label="Remove" onClick={() => setLines(lines.filter((_, j) => j !== i))}>✕</button>}
            </div>
          ))}
          <button type="button" className="text-xs font-medium text-brand-700 hover:underline" onClick={() => setLines([...lines, { product: "PMG", litres: "" }])}>+ Add fuel</button>
        </div>
        <div className="grid gap-3 sm:grid-cols-2">
          <Field label="Invoice no. (depot) · انوائس"><input className="input" value={f.invoice_ref} onChange={(e) => setF({ ...f, invoice_ref: e.target.value })} /></Field>
          <Field label="Tanker / vehicle"><input className="input" value={f.vehicle_no} onChange={(e) => setF({ ...f, vehicle_no: e.target.value })} /></Field>
        </div>
        <Field label="Kiraya (fixed — as written on the depot invoice) · کرایہ *"><input className="input" type="number" min={1} required value={f.amount} onChange={(e) => setF({ ...f, amount: e.target.value })} placeholder="e.g. 8000" /></Field>
        <Field label="Note"><input className="input" value={f.note} onChange={(e) => setF({ ...f, note: e.target.value })} /></Field>
        <div className="rounded-lg bg-slate-50 px-3 py-2 text-sm">Total {num(totalL)} L · Kiraya billed: <b className="tabular-nums">{pkr(kiraya)}</b> <span className="text-slate-500">(client ke zimme, poora munafa)</span></div>
        <div className="flex justify-end gap-2"><button type="button" className="btn-secondary" onClick={onClose}>Cancel</button><button className="btn-primary" disabled={busy || !valid}>Bill kiraya</button></div>
      </form>
    </Modal>
  );
}

/** Fuel money for a bypass supply — separate from kiraya. Client paid the depot direct (record only), or sent it to us to forward. */
function FuelPaymentEntry({ client, onClose, onDone }: { client: any; onClose: () => void; onDone: () => void }) {
  const depots = useApi<any[]>("/wholesale/depots");
  const [f, setF] = useState({ supplier_id: "", amount: "", mode: "direct", invoice_ref: "", note: "", in_method: "Bank transfer", in_ref: "", forward_now: true, fwd_method: "Bank transfer", fwd_ref: "" });
  const [inAcc, setInAcc] = useState<number | null>(null);
  const [fwdAcc, setFwdAcc] = useState<number | null>(null);
  const [photos, setPhotos] = useState<number[]>([]);
  const { busy, run } = useAction();
  const through = f.mode === "through_us";
  const valid = f.supplier_id && Number(f.amount) > 0 && (!through || inAcc);
  return (
    <Modal open onClose={onClose} title={`Fuel payment (bypass) — ${client.name}`}>
      <form className="space-y-3" onSubmit={async (e) => {
        e.preventDefault();
        const body = {
          supplier_id: Number(f.supplier_id), amount: Number(f.amount), mode: f.mode, invoice_ref: f.invoice_ref || null, note: f.note || null, photo_ids: photos,
          ...(through ? { in_method: f.in_method, in_account_id: inAcc, in_ref: f.in_ref || null, forward_now: f.forward_now, fwd_method: f.fwd_method, fwd_account_id: fwdAcc ?? inAcc, fwd_ref: f.fwd_ref || null } : {}),
        };
        if (await run(() => api(`/wholesale/clients/${client.id}/fuel-payment`, { body }), "Fuel payment saved")) onDone();
      }}>
        <p className="rounded-lg bg-slate-50 px-3 py-2 text-xs text-slate-600">Yeh depot ke fuel ka paisa hai — <b>humara kiraya nahi</b>. Client ki due par asar nahi; sirf record (aur through-us mein humare bank se guzarta hai, net zero).</p>
        <Field label="Depot *"><select className="input" required value={f.supplier_id} onChange={(e) => setF({ ...f, supplier_id: e.target.value })}>
          <option value="">— choose depot —</option>{(depots.data ?? []).map((d) => <option key={d.id} value={d.id}>{d.name}</option>)}</select></Field>
        <div className="grid gap-3 sm:grid-cols-2">
          <Field label="Fuel amount (Rs) *"><input className="input" type="number" min={1} required value={f.amount} onChange={(e) => setF({ ...f, amount: e.target.value })} /></Field>
          <Field label="Depot invoice no."><input className="input" value={f.invoice_ref} onChange={(e) => setF({ ...f, invoice_ref: e.target.value })} /></Field>
        </div>
        <fieldset>
          <legend className="label">How was it paid? · <Ur>کیسے</Ur></legend>
          <div className="grid grid-cols-1 gap-2">
            <button type="button" onClick={() => setF({ ...f, mode: "direct" })} aria-pressed={f.mode === "direct"} className={`rounded-xl px-3 py-2.5 text-left text-sm ${f.mode === "direct" ? "bg-slate-800 font-semibold text-white" : "bg-slate-100"}`}>Client paid the depot <b>direct</b> — we keep the screenshot · <Ur>کلائنٹ نے سیدھا ڈپو کو</Ur></button>
            <button type="button" onClick={() => setF({ ...f, mode: "through_us" })} aria-pressed={through} className={`rounded-xl px-3 py-2.5 text-left text-sm ${through ? "bg-slate-800 font-semibold text-white" : "bg-slate-100"}`}>Client sent it to <b>us</b> → we forward to the depot · <Ur>ہمیں بھیجا، ہم ڈپو کو</Ur></button>
          </div>
        </fieldset>
        {through && <div className="space-y-3 rounded-xl bg-sky-50 p-3">
          <AccountPicker method={f.in_method} value={inAcc} onChange={setInAcc} label="Client sent to which of our accounts? · کس اکاؤنٹ میں آیا" required />
          <Field label="Ref (client's transfer)"><input className="input" value={f.in_ref} onChange={(e) => setF({ ...f, in_ref: e.target.value })} /></Field>
          <label className="flex items-center gap-2 text-sm"><input type="checkbox" className="h-5 w-5" checked={f.forward_now} onChange={(e) => setF({ ...f, forward_now: e.target.checked })} /> Forward to the depot now · <Ur>ابھی ڈپو کو بھیج دیا</Ur></label>
          {f.forward_now && <>
            <AccountPicker method={f.fwd_method} value={fwdAcc} onChange={setFwdAcc} label="Forwarded from which account? · کس اکاؤنٹ سے" />
            <Field label="Ref (to depot)"><input className="input" value={f.fwd_ref} onChange={(e) => setF({ ...f, fwd_ref: e.target.value })} /></Field>
          </>}
          {!f.forward_now && <p className="text-xs text-amber-800">Abhi "held" dikhega — baad mein Forward kar dena. · <Ur>بعد میں فارورڈ کریں</Ur></p>}
        </div>}
        <Field label="Note"><input className="input" value={f.note} onChange={(e) => setF({ ...f, note: e.target.value })} /></Field>
        <ProofPhotos value={photos} onChange={setPhotos} hint={f.mode === "direct" ? "client's payment screenshot" : "bank receipt(s)"} />
        <div className="flex justify-end gap-2"><button type="button" className="btn-secondary" onClick={onClose}>Cancel</button><button className="btn-primary" disabled={busy || !valid}>Save</button></div>
      </form>
    </Modal>
  );
}

/** Fuel money we received from this client but have not yet forwarded to the depot. */
function HeldFuel({ clientId, refreshKey, onChanged }: { clientId: number; refreshKey: number; onChanged: () => void }) {
  const { data, reload } = useApi<any[]>(`/wholesale/fuel-held?k=${refreshKey}`);
  const { busy, run } = useAction();
  const mine = (data ?? []).filter((f) => f.client_id === clientId);
  if (!mine.length) return null;
  return (
    <div className="card border-l-4 border-l-amber-500 p-4 print:hidden">
      <h2 className="mb-2 font-semibold">⏳ Fuel money held for the depot · <Ur>ڈپو کو دینا باقی</Ur></h2>
      <ul className="divide-y divide-slate-100">
        {mine.map((f) => (
          <li key={f.id} className="flex flex-wrap items-center gap-2 py-2 text-sm">
            <span className="min-w-0 flex-1"><b className="tabular-nums">{pkr(f.amount)}</b> → {f.depot_name ?? "depot"}{f.invoice_ref ? ` · inv ${f.invoice_ref}` : ""}<span className="block text-xs text-slate-500">{dt(f.txn_date)}{f.note ? ` · ${f.note}` : ""}</span></span>
            <button className="btn-primary !py-1.5 text-sm" disabled={busy} onClick={() => run(() => api(`/wholesale/fuel-payments/${f.id}/forward`, { body: {} }), "Forwarded to the depot").then(() => { reload(); onChanged(); })}><Send size={14} /> Forward to depot</button>
          </li>
        ))}
      </ul>
    </div>
  );
}

function RatesEditor({ client, onClose, onDone }: { client: any; onClose: () => void; onDone: () => void }) {
  const [r, setR] = useState<RateDraft>(() => draftFrom(client.rate_card));
  const prices = useApi<any>("/prices");
  const { busy, run } = useAction();
  return (
    <Modal open onClose={onClose} title={`Rate card — ${client.name}`}>
      <form className="space-y-3" onSubmit={async (e) => {
        e.preventDefault();
        const rates = draftToBody(r);
        if (await run(() => api(`/wholesale/clients/${client.id}/rates`, { method: "PUT", body: { rates } }), "Rates updated")) onDone();
      }}>
        <RateCardEditor draft={r} setDraft={setR} pump={prices.data?.current} />
        <p className="text-xs text-slate-500">New rates apply to future supplies only; past entries keep their rate.</p>
        <div className="flex justify-end gap-2"><button type="button" className="btn-secondary" onClick={onClose}>Cancel</button><button className="btn-primary" disabled={busy}>Save rates</button></div>
      </form>
    </Modal>
  );
}

/* ---------------- Rate card editor: pump − Rs X (follows price changes) or a fixed rate ---------------- */
type RateMode = "discount" | "fixed" | "none";
type RateDraft = Record<string, { mode: RateMode; value: string }>;

function draftFrom(card?: Record<string, any>): RateDraft {
  return Object.fromEntries(Object.keys(PRODUCTS).map((p) => {
    const r = card?.[p];
    return [p, !r ? { mode: "none", value: "" } : r.mode === "discount" ? { mode: "discount", value: String(r.discount) } : { mode: "fixed", value: String(r.fixed ?? r.rate) }];
  }));
}

function draftToBody(d: RateDraft) {
  const out: Record<string, any> = {};
  for (const [p, r] of Object.entries(d)) {
    if (r.mode === "discount" && r.value !== "" && !isNaN(Number(r.value))) out[p] = { mode: "discount", discount: Number(r.value) };
    if (r.mode === "fixed" && Number(r.value) > 0) out[p] = { mode: "fixed", rate: Number(r.value) };
  }
  return out;
}

function RateCardEditor({ draft, setDraft, pump }: { draft: RateDraft; setDraft: (d: RateDraft) => void; pump?: Record<string, { price: number }> }) {
  const MODES: [RateMode, string][] = [["discount", "Pump − Rs"], ["fixed", "Fixed rate"], ["none", "Not supplied"]];
  return (
    <div className="space-y-2">
      {Object.keys(PRODUCTS).map((p) => {
        const r = draft[p];
        const set = (x: Partial<{ mode: RateMode; value: string }>) => setDraft({ ...draft, [p]: { ...r, ...x } });
        const price = pump?.[p]?.price;
        const eff = r.mode === "discount" && price != null && r.value !== "" ? price - Number(r.value) : r.mode === "fixed" ? Number(r.value) : null;
        return (
          <div key={p} className="rounded-lg border border-slate-200 bg-white p-2.5">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <span className="text-sm font-medium">{PRODUCTS[p]} <span className="text-xs font-normal text-slate-500">pump Rs {price?.toFixed(2) ?? "—"}</span></span>
              <div className="flex rounded-lg border border-slate-200 p-0.5" role="group" aria-label={`${PRODUCTS[p]} rate type`}>
                {MODES.map(([m, label]) => (
                  <button key={m} type="button" aria-pressed={r.mode === m} onClick={() => set({ mode: m, value: m === r.mode ? r.value : m === "fixed" && eff ? eff.toFixed(2) : m === "discount" ? "2" : "" })}
                    className={`rounded-md px-2.5 py-1 text-xs ${r.mode === m ? "bg-slate-900 text-white" : "text-slate-600 hover:bg-slate-100"}`}>{label}</button>
                ))}
              </div>
            </div>
            {r.mode !== "none" && (
              <div className="mt-2 flex flex-wrap items-center gap-2 text-sm">
                {r.mode === "discount" && <span className="text-slate-500">Pump rate −</span>}
                <input className="input w-32 text-right" type="number" step="0.01" min={r.mode === "fixed" ? 0.01 : -200} required aria-label={`${PRODUCTS[p]} ${r.mode === "discount" ? "rupees below pump rate" : "fixed rate"}`}
                  value={r.value} onChange={(e) => set({ value: e.target.value })} />
                <span className="text-slate-500">Rs/L</span>
                {eff != null && eff > 0 && <span className="ml-auto text-xs text-slate-600">= <b>Rs {eff.toFixed(2)}</b> / L today{r.mode === "discount" ? " · moves with pump price" : ""}</span>}
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}

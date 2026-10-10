import { useState } from "react";
import { api, useApi } from "../lib/api";
import { Badge, Empty, Field, Loading, PageHeader, useAction } from "../components/ui";
import { pkr, num, dt } from "../lib/format";
import { Fuel, Truck, Receipt, Ban, Search, Undo2, Factory, Container, Building2 } from "lucide-react";

type Tab = "sale" | "delivery" | "khata" | "supplier" | "wholesale" | "rent";
const TABS: { t: Tab; label: string; short: string; icon: any }[] = [
  { t: "sale", label: "Sale void", short: "Sale", icon: Fuel },
  { t: "delivery", label: "Delivery void", short: "Delivery", icon: Truck },
  { t: "khata", label: "Khata entry void", short: "Khata", icon: Receipt },
  { t: "supplier", label: "Supplier payment void", short: "Supplier", icon: Factory },
  { t: "wholesale", label: "Wholesale void", short: "Wholesale", icon: Container },
  { t: "rent", label: "Rent receipt void", short: "Rent", icon: Building2 },
];

/** Ask for a reason (min 3 chars) and POST the void; calls reload on success. */
function useVoid(reload: () => void) {
  const { busy, run } = useAction();
  const doVoid = (url: string, what: string) => {
    const reason = window.prompt(`${what}\n\nKyun theek kar rahe hain? (wajah zaroori)`);
    if (reason == null) return;
    if (reason.trim().length < 3) { window.alert("Wajah likhein (kam se kam 3 harf)."); return; }
    run(() => api(url, { body: { reason: reason.trim() } }), "Theek ho gaya — books tally").then((r) => { if (r) reload(); });
  };
  return { busy, doVoid };
}

/** CEO-only: undo a wrong sale / stock delivery / manual khata entry — everything it moved is reversed and the books stay tallied. */
export default function Corrections() {
  const [tab, setTab] = useState<Tab>("sale");
  return (
    <div>
      <PageHeader title="Corrections — galat entry theek karein · تصحیح"
        subtitle="Sirf CEO. Koi galat sale, delivery, supplier/wholesale payment, manual khata ya rent entry undo karein — poora paisa, balance aur stock ulta ho jata hai. (Thekedar/carriage ki void Carriage page par hai.) Baad me Audit → Hisaab check se 0 rupaye farq confirm karein." />
      <div className="mb-4 grid grid-cols-3 gap-1.5 sm:gap-2">
        {TABS.map((x) => (
          <button key={x.t} onClick={() => setTab(x.t)}
            className={`flex flex-col items-center justify-center gap-1 rounded-xl px-1 py-2 text-xs font-semibold sm:flex-row sm:gap-1.5 sm:px-3 sm:py-2.5 sm:text-sm ${tab === x.t ? "bg-brand-600 text-white" : "bg-slate-100 text-slate-600"}`}>
            <x.icon size={16} className="shrink-0" /> <span>{x.short}</span>
          </button>
        ))}
      </div>
      {tab === "sale" && <SaleVoid />}
      {tab === "delivery" && <DeliveryVoid />}
      {tab === "khata" && <KhataVoid />}
      {tab === "supplier" && <SupplierVoid />}
      {tab === "wholesale" && <WholesaleVoid />}
      {tab === "rent" && <RentVoid />}
    </div>
  );
}

/** A simple recent-list of txns, each with a reason-prompted Void (or a note when it belongs to another undo). */
function TxnList({ url, empty, icon: Icon, row, label, elsewhere }: {
  url: string; empty: string; icon: any;
  row: (x: any) => { title: string; sub: string; id: number; voidable: boolean; owner?: string | null };
  label: (x: any) => string; elsewhere: Record<string, string>;
}) {
  const { data, reload } = useApi<any>(url);
  const { busy, doVoid } = useVoid(reload);
  const key = Object.keys(data ?? {}).find((k) => Array.isArray((data ?? {})[k])) ?? "";
  const items: any[] = (data ?? {})[key] ?? [];
  const voidUrl = url.split("?")[0].replace(/s$/, ""); // /corrections/supplier-txns → /corrections/supplier-txn
  return !data ? <Loading /> : !items.length ? <Empty><Icon className="mx-auto mb-1 text-slate-300" />{empty}</Empty> : (
    <div className="mt-1 card divide-y divide-slate-100">
      {items.map((x) => { const r = row(x); return (
        <div key={r.id} className="flex items-center gap-3 px-3 py-2.5">
          <div className="min-w-0 flex-1"><div className="truncate font-medium">{r.title}</div><div className="truncate text-xs text-slate-500">{r.sub}</div></div>
          {r.voidable
            ? <button disabled={busy} onClick={() => doVoid(`${voidUrl}/${r.id}/void`, label(x))} className="btn-danger shrink-0"><Ban size={15} /> Void</button>
            : <span className="shrink-0 text-right text-[11px] text-slate-400">{(r.owner && elsewhere[r.owner]) || "yahan nahi"}</span>}
        </div>
      ); })}
    </div>
  );
}

function SupplierVoid() {
  return <TxnList url="/corrections/supplier-txns" empty="Koi supplier entry nahi" icon={Factory}
    elsewhere={{ delivery: "Delivery void se", cashier: "Cashier void se" }}
    row={(x) => ({ id: x.id, voidable: x.voidable, owner: x.owner,
      title: `${x.type === "payment" ? "Payment" : x.type === "purchase" ? "Purchase" : "Adjustment"} · ${pkr(x.amount)}`,
      sub: [x.supplier, x.method, x.product, x.ref, dt(x.created_at ?? x.txn_date)].filter(Boolean).join(" · ") })}
    label={(x) => `Supplier ${x.type} void: ${x.supplier} — ${pkr(x.amount)}`} />;
}

function WholesaleVoid() {
  return <TxnList url="/corrections/wholesale-txns" empty="Koi wholesale entry nahi" icon={Container}
    elsewhere={{ trip: "Trip (wholesale page)", cashier: "Cashier void se" }}
    row={(x) => ({ id: x.id, voidable: x.voidable, owner: x.owner,
      title: `${x.type} · ${x.litres ? `${num(x.litres, 2)}L · ` : ""}${pkr(x.amount)}`,
      sub: [x.client, x.product, x.method, x.ref, dt(x.txn_date)].filter(Boolean).join(" · ") })}
    label={(x) => `Wholesale ${x.type} void: ${x.client} — ${pkr(x.amount)}`} />;
}

function RentVoid() {
  return <TxnList url="/corrections/rent" empty="Koi rent receipt nahi" icon={Building2} elsewhere={{}}
    row={(p) => ({ id: p.id, voidable: true, title: `Rent · ${pkr(p.amount)} · ${p.for_month}`,
      sub: [p.unit, p.tenant_name, p.method, dt(p.created_at)].filter(Boolean).join(" · ") })}
    label={(p) => `Rent void: ${p.unit} — ${pkr(p.amount)} (${p.for_month})`} />;
}

function SaleVoid() {
  const [q, setQ] = useState("");
  const { data, reload } = useApi<any>(`/corrections/sales?q=${encodeURIComponent(q)}`);
  const { busy, doVoid } = useVoid(reload);
  return (
    <div>
      <Field label="Dhoondein — customer, gaari ya parchi no.">
        <div className="relative"><Search size={15} className="absolute left-2.5 top-2.5 text-slate-400" />
          <input className="input w-full pl-8" placeholder="Naam / vehicle / slip…" value={q} onChange={(e) => setQ(e.target.value)} /></div>
      </Field>
      {!data ? <Loading /> : !data.sales.length ? <Empty><Fuel className="mx-auto mb-1 text-slate-300" />Koi sale nahi mili</Empty> : (
        <div className="mt-3 card divide-y divide-slate-100">
          {data.sales.map((s: any) => (
            <div key={s.id} className="flex items-center gap-3 px-3 py-2.5">
              <div className="min-w-0 flex-1">
                <div className="truncate font-medium">{s.product} · {num(s.litres, 2)}L · {pkr(s.amount)}
                  <Badge tone={s.payment_method === "cash" ? "green" : s.payment_method === "khata" ? "amber" : "blue"}>{s.payment_method}</Badge>
                  {s.pending ? <Badge tone="amber">card-hold</Badge> : null}{s.at_close ? <Badge tone="slate">close</Badge> : null}</div>
                <div className="truncate text-xs text-slate-500">{[s.customer, s.vehicle_no, s.slip_no && `#${s.slip_no}`, s.station, s.by_name].filter(Boolean).join(" · ")} · {dt(s.created_at)}</div>
              </div>
              <button disabled={busy} onClick={() => doVoid(`/corrections/sale/${s.id}/void`, `Sale void: ${s.product} ${num(s.litres, 2)}L — ${pkr(s.amount)} (${s.payment_method})`)}
                className="btn-danger shrink-0"><Ban size={15} /> Void</button>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function DeliveryVoid() {
  const { data, reload } = useApi<any>("/corrections/deliveries");
  const { busy, doVoid } = useVoid(reload);
  return (
    <div>
      {!data ? <Loading /> : !data.deliveries.length ? <Empty><Truck className="mx-auto mb-1 text-slate-300" />Koi delivery nahi</Empty> : (
        <div className="mt-1 card divide-y divide-slate-100">
          {data.deliveries.map((d: any) => (
            <div key={d.id} className="flex items-center gap-3 px-3 py-2.5">
              <div className="min-w-0 flex-1">
                <div className="truncate font-medium">{d.product} · {num(d.received_l)}L · {pkr(d.amount)}
                  {d.claim_status && <Badge tone={d.claim_status === "open" ? "amber" : "red"}>claim: {d.claim_status}</Badge>}</div>
                <div className="truncate text-xs text-slate-500">{[d.supplier, d.tanker_no, d.tank, `rate ${pkr(d.purchase_rate)}`].filter(Boolean).join(" · ")} · {dt(d.created_at)}</div>
              </div>
              {d.voidable
                ? <button disabled={busy} onClick={() => doVoid(`/corrections/delivery/${d.id}/void`, `Delivery void: ${d.supplier} ${num(d.received_l)}L — ${pkr(d.amount)}`)} className="btn-danger shrink-0"><Ban size={15} /> Void</button>
                : <span className="shrink-0 text-xs text-slate-400">claim pehle handle karein</span>}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function KhataVoid() {
  const [q, setQ] = useState("");
  const [cust, setCust] = useState<{ id: number; name: string } | null>(null);
  const { data: parties } = useApi<any>(q.trim() ? `/cashier/parties?kind=khata` : null);
  const matches = ((parties?.khata ?? []) as any[]).filter((c) => c.name.toLowerCase().includes(q.trim().toLowerCase())).slice(0, 8);
  const { data, reload } = useApi<any>(cust ? `/corrections/khata?customer_id=${cust.id}` : null);
  const { busy, doVoid } = useVoid(reload);
  return (
    <div>
      <Field label="Customer dhoondein">
        <div className="relative"><Search size={15} className="absolute left-2.5 top-2.5 text-slate-400" />
          <input className="input w-full pl-8" placeholder="Khata customer ka naam…" value={q} onChange={(e) => { setQ(e.target.value); setCust(null); }} /></div>
      </Field>
      {q.trim() && !cust && (
        <div className="mt-1 card divide-y divide-slate-100">
          {matches.length ? matches.map((c) => (
            <button key={c.id} onClick={() => { setCust({ id: c.id, name: c.name }); setQ(c.name); }} className="flex w-full items-center justify-between px-3 py-2 text-left hover:bg-slate-50">
              <span className="font-medium">{c.name}</span><span className="text-xs text-slate-500">{pkr(c.balance)}</span>
            </button>
          )) : <div className="px-3 py-2 text-sm text-slate-400">Koi customer nahi mila</div>}
        </div>
      )}
      {cust && !data && <Loading />}
      {cust && data && (
        <>
          <div className="my-3 text-sm text-slate-600">Khata balance abhi: <b>{pkr(data.customer.balance)}</b></div>
          {!data.entries.length ? <Empty><Receipt className="mx-auto mb-1 text-slate-300" />Koi khata entry nahi</Empty> : (
            <div className="card divide-y divide-slate-100">
              {data.entries.map((e: any) => (
                <div key={e.id} className="flex items-center gap-3 px-3 py-2.5">
                  <div className="min-w-0 flex-1">
                    <div className="font-medium">{e.type === "debit" ? "Charge" : "Payment"} · {pkr(e.amount)}</div>
                    <div className="truncate text-xs text-slate-500">{[e.note, e.ref].filter(Boolean).join(" · ")} · {dt(e.created_at)}</div>
                  </div>
                  {e.voidable
                    ? <button disabled={busy} onClick={() => doVoid(`/corrections/khata/${e.id}/void`, `Khata ${e.type === "debit" ? "charge" : "payment"} void: ${pkr(e.amount)}`)} className="btn-danger shrink-0"><Ban size={15} /> Void</button>
                    : <span className="shrink-0 text-right text-[11px] text-slate-400">{e.owner === "sale" ? "Sale void se" : e.owner === "other" ? "Other entries se" : "Cashier void se"}</span>}
                </div>
              ))}
            </div>
          )}
        </>
      )}
      {!q.trim() && !cust && <div className="mt-6 flex flex-col items-center gap-1 text-slate-400"><Undo2 size={22} /><span className="text-sm">Customer chunein, phir galat charge ya payment void karein.</span></div>}
    </div>
  );
}

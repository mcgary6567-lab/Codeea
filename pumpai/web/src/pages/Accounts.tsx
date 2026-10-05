import { useEffect, useState } from "react";
import { Truck, Scale, Percent, Landmark, BookOpenCheck, Download, Upload, Send } from "lucide-react";
import { api, getToken, useApi } from "../lib/api";
import { Badge, Empty, Field, Loading, Modal, PageHeader, Stat, useAction, ErrorBox } from "../components/ui";
import { d as day, dt, num, pkr, PRODUCTS } from "../lib/format";
import { useAuth } from "../App";
import { ALL_PK_BANKS } from "../lib/banks";
import { ProofPhotos, ProofThumbs } from "../components/Capture";

const TABS = [
  { key: "claims", label: "Tanker claims", icon: Truck, perm: "suppliers.manage" },
  { key: "compare", label: "Depot comparison", icon: Scale, perm: "suppliers.manage" },
  { key: "tax", label: "Tax", icon: Percent, perm: "reports.view" },
  { key: "bank", label: "Bank reconciliation", icon: Landmark, perm: "expenses.approve" },
  { key: "ledger", label: "Ledger export", icon: BookOpenCheck, perm: "reports.view" },
] as const;
const thisMonth = () => new Date(Date.now() + 5 * 3600_000).toISOString().slice(0, 7);
const today = () => new Date(Date.now() + 5 * 3600_000).toISOString().slice(0, 10);
const dl = (path: string) => `/api${path}${path.includes("?") ? "&" : "?"}token=${encodeURIComponent(getToken() ?? "")}`;

/** Money side of the pump: tanker shortage claims, which depot is cheapest, tax, bank statement matching and the accountant's export. */
export default function Accounts() {
  const { can } = useAuth();
  const tabs = TABS.filter((t) => can(t.perm));
  const [tab, setTab] = useState<string>(() => tabs.find((t) => `#${t.key}` === location.hash)?.key ?? tabs[0]?.key);
  return (
    <div className="space-y-5">
      <PageHeader title="Accounts & tax" subtitle="Tanker claims, depot comparison, sales tax and withholding, bank statement matching and export for the accountant" />
      <div className="flex gap-1 overflow-x-auto border-b border-slate-200">
        {tabs.map((t) => (
          <button key={t.key} onClick={() => { setTab(t.key); history.replaceState(null, "", `#${t.key}`); }}
            className={`-mb-px flex items-center gap-2 whitespace-nowrap border-b-2 px-4 py-2 text-sm font-medium ${tab === t.key ? "border-brand-600 text-brand-700" : "border-transparent text-slate-500"}`}><t.icon size={15} />{t.label}</button>
        ))}
      </div>
      {tab === "claims" && <Claims />}
      {tab === "compare" && <Compare />}
      {tab === "tax" && <Tax />}
      {tab === "bank" && <Bank />}
      {tab === "ledger" && <Ledger />}
    </div>
  );
}

const STATUS_TONE: Record<string, string> = { open: "amber", claimed: "blue", partly: "violet", recovered: "green", written_off: "slate" };

function Claims() {
  const [status, setStatus] = useState("");
  const [settle, setSettle] = useState<any | null>(null);
  const { data, reload } = useApi<any>(`/claims${status ? `?status=${status}` : ""}`);
  const { busy, run } = useAction();
  if (!data) return <Loading />;
  const s = data.summary;
  return (
    <>
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <Stat label="Not claimed yet" value={pkr(s.not_claimed)} tone="amber" hint="Send these to the depot" />
        <Stat label="Claimed, waiting" value={pkr(s.claimed)} tone="blue" />
        <Stat label="Recovered" value={pkr(s.recovered)} tone="green" hint="Credit notes from the depot" />
        <Stat label="Written off" value={pkr(s.written_off)} />
      </div>
      <div className="card p-3 text-sm text-slate-600">Every tanker that arrives short by more than <b>{data.tolerance_pct}%</b> (allowed transit loss) becomes a claim of the extra litres × purchase rate. The allowed loss can be changed by the admin.</div>
      <div className="grid grid-cols-1 gap-5 2xl:grid-cols-[1fr_300px]">
        <div className="card overflow-x-auto">
          <div className="flex gap-1 p-2 text-sm">{["", "open", "claimed", "partly", "recovered", "written_off"].map((x) => <button key={x} onClick={() => setStatus(x)} className={`rounded-full px-3 py-1 ${status === x ? "bg-brand-600 text-white" : "bg-slate-100"}`}>{x ? x.replace("_", " ") : "All"}</button>)}</div>
          <table className="w-full min-w-[720px] text-sm">
            <thead className="bg-slate-50 text-left text-xs uppercase text-slate-500"><tr><th className="px-3 py-2">Tanker</th><th>Depot</th><th className="text-right">Invoice / received</th><th className="text-right">Claim</th><th>Status</th><th /></tr></thead>
            <tbody className="divide-y divide-slate-100">
              {data.claims.map((c: any) => (
                <tr key={c.id}>
                  <td className="px-3 py-2"><b>{c.tanker_no ?? "—"}</b><span className="block text-xs text-slate-500">{day(c.delivered_at)} · {c.station} {c.tank}</span></td>
                  <td>{c.supplier_name ?? "—"}</td>
                  <td className="text-right tabular-nums">{num(c.invoice_l)} / {num(c.received_l)} L<span className="block text-xs text-red-600">{c.shortage_pct}% short</span></td>
                  <td className="text-right tabular-nums"><b>{pkr(c.amount)}</b><span className="block text-xs text-slate-500">{c.litres} L × {c.rate}{c.recovered ? ` · got ${pkr(c.recovered)}` : ""}</span></td>
                  <td><Badge tone={STATUS_TONE[c.status]}>{c.status.replace("_", " ")}</Badge>{c.claim_ref && <span className="block text-xs text-slate-500">{c.claim_ref}</span>}<ProofThumbs ids={c.proof_ids} /></td>
                  <td className="whitespace-nowrap px-2 text-right">
                    {c.status === "open" && <button className="btn-primary px-2 py-1 text-xs" disabled={busy} onClick={() => run(() => api(`/claims/${c.id}/claim`, { body: { claim_ref: prompt("Claim / letter number (optional)") || null } }), (r: any) => r.sent ? "Claim sent to the depot on WhatsApp" : "Marked as claimed").then(reload)}><Send size={12} /> Claim</button>}
                    {["claimed", "partly"].includes(c.status) && <>
                      <button className="btn-secondary px-2 py-1 text-xs" disabled={busy} onClick={() => setSettle(c)}>Got credit</button>
                      <button className="ml-1 text-xs text-slate-500 underline" onClick={() => confirm("Write this claim off?") && run(() => api(`/claims/${c.id}/settle`, { body: { action: "written_off" } }), "Written off").then(reload)}>Write off</button>
                    </>}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          {!data.claims.length && <Empty>No claims</Empty>}
        </div>
        <div className="card p-4">
          <h3 className="mb-2 font-semibold">By depot</h3>
          <ul className="space-y-2 text-sm">{data.by_supplier.map((x: any) => (
            <li key={x.supplier}><div className="flex justify-between"><b>{x.supplier}</b><span className="tabular-nums">{pkr(x.amount)}</span></div>
              <div className="text-xs text-slate-500">{x.n} tankers · {num(x.litres)} L short · recovered {pkr(x.recovered)}</div></li>))}</ul>
        </div>
      </div>
      {settle && <SettleClaim c={settle} onClose={() => setSettle(null)} onDone={() => { setSettle(null); reload(); }} />}
    </>
  );
}

/** Credit note from the depot for a tanker shortage, with a photo of the credit note / letter. */
function SettleClaim({ c, onClose, onDone }: { c: any; onClose: () => void; onDone: () => void }) {
  const left = Math.round(c.amount - c.recovered);
  const [amount, setAmount] = useState(String(left));
  const [photos, setPhotos] = useState<number[]>([]);
  const { busy, run } = useAction();
  return (
    <Modal open onClose={onClose} title={`Credit received — ${c.tanker_no ?? "tanker"}`}>
      <form className="space-y-3" onSubmit={async (e) => {
        e.preventDefault();
        if (await run(() => api(`/claims/${c.id}/settle`, { body: { action: "recovered", amount: Number(amount), photo_ids: photos } }), "Credit note recorded — payable reduced")) onDone();
      }}>
        <Field label={`Amount recovered (up to ${pkr(left)})`}><input className="input text-lg" type="number" min={1} max={left} required value={amount} onChange={(e) => setAmount(e.target.value)} /></Field>
        <ProofPhotos value={photos} onChange={setPhotos} hint="credit note / depot letter" />
        <div className="flex justify-end gap-2"><button type="button" className="btn-secondary" onClick={onClose}>Cancel</button><button className="btn-primary" disabled={busy}>Save</button></div>
      </form>
    </Modal>
  );
}

function Compare() {
  const [days, setDays] = useState(90);
  const { data } = useApi<any>(`/suppliers-compare?days=${days}`);
  if (!data) return <Loading />;
  const products = [...new Set<string>(data.rows.map((r: any) => r.product))];
  return (
    <>
      <div className="flex gap-2 text-sm">{[30, 90, 180].map((x) => <button key={x} onClick={() => setDays(x)} className={`rounded-full px-3 py-1 ${days === x ? "bg-brand-600 text-white" : "bg-slate-100"}`}>{x} days</button>)}</div>
      {products.map((p) => {
        const rows = data.rows.filter((r: any) => r.product === p).sort((a: any, b: any) => a.landed_per_l - b.landed_per_l);
        const best = data.best[p];
        return (
          <div key={p} className="card overflow-x-auto p-4">
            <div className="mb-2 flex flex-wrap items-center gap-2"><h3 className="flex-1 font-semibold">{PRODUCTS[p] ?? p}</h3>
              {best && <Badge tone="green">Cheapest: {best.supplier} · saves Rs {best.saving_per_l}/L</Badge>}</div>
            <table className="w-full min-w-[640px] text-sm">
              <thead className="text-left text-xs uppercase text-slate-500"><tr><th className="py-1">Depot</th><th className="text-right">Tankers</th><th className="text-right">Litres</th><th className="text-right">Rate /L</th><th className="text-right">Freight /L</th><th className="text-right">Shortage</th><th className="text-right">Landed cost /L</th></tr></thead>
              <tbody className="divide-y divide-slate-100">{rows.map((r: any, i: number) => (
                <tr key={r.supplier_id} className={i === 0 && rows.length > 1 ? "bg-emerald-50" : ""}>
                  <td className="py-1.5 font-medium">{r.supplier}</td><td className="text-right tabular-nums">{r.tankers}</td><td className="text-right tabular-nums">{num(r.litres)}</td>
                  <td className="text-right tabular-nums">{r.avg_rate.toFixed(2)}</td><td className="text-right tabular-nums">{r.freight_per_l.toFixed(2)}</td>
                  <td className="text-right tabular-nums">{r.shortage_pct}% <span className="text-xs text-slate-500">(+{r.shortage_cost_per_l.toFixed(2)})</span></td>
                  <td className="text-right font-bold tabular-nums">{r.landed_per_l.toFixed(2)}</td>
                </tr>))}</tbody>
            </table>
          </div>
        );
      })}
      {!products.length && <Empty>No tankers with a depot and rate in this period</Empty>}
      <p className="text-xs text-slate-500">Landed cost = (amount paid − shortage recovered + freight) ÷ litres that reached the tank. Enter the freight on each tanker delivery to include it.</p>
    </>
  );
}

function Tax() {
  const [month, setMonth] = useState(thisMonth());
  const { data, reload } = useApi<any>(`/tax/report?month=${month}`);
  const { can } = useAuth();
  const { busy, run } = useAction();
  const [f, setF] = useState<any>(null);
  const [w, setW] = useState({ payee: "", gross: "", rate: "", section: "" });
  const [sel, setSel] = useState<number[]>([]);
  useEffect(() => { if (data && !f) setF({ ...data.settings, exempt: data.settings.exempt.join(",") }); }, [data]);
  if (!data) return <Loading />;
  return (
    <>
      <div className="flex flex-wrap items-end gap-3">
        <Field label="Month"><input type="month" className="input" value={month} onChange={(e) => setMonth(e.target.value || thisMonth())} /></Field>
        <a className="btn-secondary" href={dl(`/tax/report.csv?month=${month}`)}><Download size={15} /> Excel / CSV</a>
      </div>
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <Stat label="Shop sales" value={pkr(data.shop_total.sales)} hint={`Taxable value ${pkr(data.shop_total.value)}`} />
        <Stat label={`Sales tax (GST ${data.settings.gst_pct}%)`} value={pkr(data.shop_total.tax)} tone="blue" hint="On shop / lubricant sales" />
        <Stat label="Tax withheld this month" value={pkr(data.wht_total.amount)} tone="amber" hint={`${pkr(data.wht_total.pending)} not deposited yet`} />
        <Stat label="All withholding to deposit" value={pkr(data.wht_pending_all.v)} tone={data.wht_pending_all.v ? "red" : "slate"} hint={`${data.wht_pending_all.n} entries without a CPR`} />
      </div>
      <div className="grid grid-cols-1 gap-5 lg:grid-cols-2">
        <div className="card p-4">
          <h3 className="mb-2 font-semibold">Sales tax on shop sales</h3>
          <table className="w-full text-sm"><thead className="text-left text-xs uppercase text-slate-500"><tr><th className="py-1">Category</th><th className="text-right">Sales</th><th className="text-right">Value</th><th className="text-right">Tax</th></tr></thead>
            <tbody className="divide-y divide-slate-100">{data.shop.map((x: any) => (
              <tr key={x.category}><td className="py-1.5 capitalize">{x.category}{x.exempt && <span className="ml-1 text-xs text-slate-500">(exempt)</span>}</td><td className="text-right tabular-nums">{pkr(x.sales)}</td><td className="text-right tabular-nums">{pkr(x.value)}</td><td className="text-right tabular-nums">{pkr(x.tax)}</td></tr>))}</tbody></table>
          <h3 className="mb-1 mt-4 font-semibold">Fuel</h3>
          <ul className="text-sm">{data.fuel.map((x: any) => <li key={x.product} className="flex justify-between py-0.5"><span>{x.name} · {num(x.litres)} L</span><span className="tabular-nums">{pkr(x.sales)}{x.tax ? ` (tax ${pkr(x.tax)})` : ""}</span></li>)}</ul>
          <p className="mt-1 text-xs text-slate-500">Fuel is sold at the government price; set a fuel GST % only if your accountant asks for it to be shown.</p>
        </div>
        <div className="card p-4">
          <h3 className="mb-2 font-semibold">Withholding tax</h3>
          <ul className="divide-y divide-slate-100 text-sm">
            {data.withholding.map((x: any) => (
              <li key={x.id} className="flex items-center gap-2 py-1.5">
                {!x.cpr_no && <input type="checkbox" aria-label="Select" checked={sel.includes(x.id)} onChange={(e) => setSel(e.target.checked ? [...sel, x.id] : sel.filter((i) => i !== x.id))} />}
                <span className="flex-1">{x.payee}<span className="block text-xs text-slate-500">{day(x.txn_date)} · {x.section ?? ""} · on {pkr(x.gross)}{x.rate ? ` @ ${x.rate}%` : ""}</span></span>
                <span className="tabular-nums font-semibold">{pkr(x.amount)}</span>
                {x.cpr_no ? <Badge tone="green">CPR {x.cpr_no}</Badge> : <Badge tone="amber">to deposit</Badge>}
              </li>
            ))}
          </ul>
          {!data.withholding.length && <Empty>No tax withheld this month</Empty>}
          {sel.length > 0 && <button className="btn-primary mt-2" disabled={busy} onClick={() => { const cpr = prompt("CPR number from the FBR payment"); if (cpr) run(() => api("/tax/withholding/deposit", { body: { ids: sel, cpr_no: cpr } }), "Marked as deposited").then(() => { setSel([]); reload(); }); }}>Deposited — add CPR ({sel.length})</button>}
          <form className="mt-3 grid grid-cols-2 gap-2 rounded-lg bg-slate-50 p-3 text-sm" onSubmit={async (e) => {
            e.preventDefault();
            if (await run(() => api("/tax/withholding", { body: { payee: w.payee, gross: Number(w.gross), rate: Number(w.rate) || null, section: w.section || null } }), "Withholding recorded")) { setW({ payee: "", gross: "", rate: "", section: "" }); reload(); }
          }}>
            <div className="col-span-2 text-xs text-slate-600">Other withholding (rent, services). Supplier payments: enter "Tax withheld" when paying the supplier.</div>
            <input className="input" placeholder="Paid to" required value={w.payee} onChange={(e) => setW({ ...w, payee: e.target.value })} />
            <input className="input" type="number" min={1} placeholder="Gross amount" required value={w.gross} onChange={(e) => setW({ ...w, gross: e.target.value })} />
            <input className="input" type="number" min={0} step={0.5} placeholder="Rate %" required value={w.rate} onChange={(e) => setW({ ...w, rate: e.target.value })} />
            <input className="input" placeholder="Section e.g. 155" value={w.section} onChange={(e) => setW({ ...w, section: e.target.value })} />
            <button className="btn-secondary col-span-2" disabled={busy}>Add</button>
          </form>
        </div>
      </div>
      {can("settings.manage") && f && (
        <form className="card grid grid-cols-1 gap-3 p-4 sm:grid-cols-4" onSubmit={async (e) => {
          e.preventDefault();
          if (await run(() => api("/tax/settings", { method: "PUT", body: { gst_pct: Number(f.gst_pct), prices_include_tax: Boolean(f.prices_include_tax), fuel_gst_pct: Number(f.fuel_gst_pct), ntn: f.ntn, strn: f.strn, wht_section: f.wht_section,
            exempt: String(f.exempt).split(",").map((x) => x.trim()).filter(Boolean) } }), "Tax settings saved")) reload();
        }}>
          <h3 className="font-semibold sm:col-span-4">Tax settings</h3>
          <Field label="NTN"><input className="input" value={f.ntn} onChange={(e) => setF({ ...f, ntn: e.target.value })} /></Field>
          <Field label="STRN"><input className="input" value={f.strn} onChange={(e) => setF({ ...f, strn: e.target.value })} /></Field>
          <Field label="Shop GST %"><input className="input" type="number" min={0} max={30} value={f.gst_pct} onChange={(e) => setF({ ...f, gst_pct: e.target.value })} /></Field>
          <Field label="Fuel GST % (usually 0)"><input className="input" type="number" min={0} max={30} value={f.fuel_gst_pct} onChange={(e) => setF({ ...f, fuel_gst_pct: e.target.value })} /></Field>
          <label className="flex items-center gap-2 text-sm sm:col-span-2"><input type="checkbox" checked={f.prices_include_tax} onChange={(e) => setF({ ...f, prices_include_tax: e.target.checked })} /> Shop prices already include GST</label>
          <Field label="Exempt categories (comma)"><input className="input" placeholder="tuck, service" value={f.exempt} onChange={(e) => setF({ ...f, exempt: e.target.value })} /></Field>
          <Field label="Default WHT section"><input className="input" value={f.wht_section} onChange={(e) => setF({ ...f, wht_section: e.target.value })} /></Field>
          <div className="sm:col-span-4"><button className="btn-primary" disabled={busy}>Save tax settings</button> <span className="text-xs text-slate-500">NTN / STRN and the tax line are printed on every shop receipt.</span></div>
        </form>
      )}
    </>
  );
}

function Bank() {
  const [csv, setCsv] = useState("");
  const [bankName, setBankName] = useState("");
  const [res, setRes] = useState<any>(null);
  const [err, setErr] = useState<string | null>(null);
  const history = useApi<any[]>("/bank/reconciliations");
  const { busy, run } = useAction();
  const go = async () => { setErr(null); try { setRes(await api("/bank/reconcile", { body: { csv, bank: bankName || undefined } })); history.reload(); } catch (e: any) { setErr(e.message); } };
  return (
    <>
      <div className="card space-y-3 p-4">
        <p className="text-sm text-slate-600">Download the statement from internet banking as <b>CSV / Excel (save as CSV)</b> and upload it. Deposits, cheques and payments are matched with the books by amount and date.</p>
        <div className="flex flex-wrap items-end gap-2">
          <label className="btn-secondary cursor-pointer"><Upload size={15} /> Choose statement file<input type="file" accept=".csv,text/csv" className="hidden" onChange={async (e) => { const f = e.target.files?.[0]; if (f) setCsv(await f.text()); }} /></label>
          <Field label="Bank (to match deposits)"><input className="input" list="pk-banks" placeholder="e.g. HBL" value={bankName} onChange={(e) => setBankName(e.target.value)} />
            <datalist id="pk-banks">{ALL_PK_BANKS.map((b) => <option key={b} value={b} />)}</datalist></Field>
          <button className="btn-primary" disabled={!csv || busy} onClick={go}>Match with books</button>
          {csv && <span className="text-xs text-slate-500">{csv.split("\n").length} lines loaded</span>}
        </div>
        {err && <ErrorBox error={err} />}
      </div>
      {res && <>
        <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
          <Stat label="Statement lines" value={res.statement_lines} hint={`${day(res.period.from)} – ${day(res.period.to)}`} />
          <Stat label="Matched" value={`${res.matched} / ${res.statement_lines}`} tone="green" />
          <Stat label="In bank, not in books" value={pkr(res.bank_only_total.in + res.bank_only_total.out)} tone="amber" hint={`${res.bank_only.length} lines`} />
          <Stat label="In books, not in bank yet" value={pkr(res.books_only_total.in + res.books_only_total.out)} tone="blue" hint={`${res.books_only.length} entries`} />
        </div>
        <div className="grid grid-cols-1 gap-5 lg:grid-cols-2">
          <div className="card p-4">
            <h3 className="mb-2 font-semibold">In the bank but not in the books</h3>
            <ul className="divide-y divide-slate-100 text-sm">{res.bank_only.map((l: any, i: number) => (
              <li key={i} className="flex items-center gap-2 py-1.5"><span className="flex-1">{l.text}<span className="block text-xs text-slate-500">{day(l.at)} · {l.hint}</span></span>
                <span className={`tabular-nums font-semibold ${l.debit ? "text-red-600" : "text-emerald-700"}`}>{l.debit ? `−${pkr(l.debit)}` : `+${pkr(l.credit)}`}</span>
                {l.debit > 0 && /charges/i.test(l.hint) && <button className="btn-secondary px-2 py-1 text-xs" disabled={busy} onClick={() => run(() => api("/expenses", { body: { category: "Bank charges", amount: l.debit, method: "bank", paid_to: "Bank", note: l.text, expense_date: l.at.slice(0, 10) } }), "Added to expenses")}>Add expense</button>}
              </li>))}</ul>
            {!res.bank_only.length && <Empty>Everything in the statement is in the books ✓</Empty>}
          </div>
          <div className="card p-4">
            <h3 className="mb-2 font-semibold">In the books but not in the bank yet</h3>
            <ul className="divide-y divide-slate-100 text-sm">{res.books_only.map((e: any) => (
              <li key={e.key} className="flex items-center gap-2 py-1.5"><span className="flex-1">{e.what}<span className="block text-xs text-slate-500">{dt(e.at)}{e.ref ? ` · ${e.ref}` : ""} · {e.side === "out" ? "cheque / payment not cleared" : "deposit in transit"}</span></span>
                <span className={`tabular-nums font-semibold ${e.side === "out" ? "text-red-600" : "text-emerald-700"}`}>{e.side === "out" ? "−" : "+"}{pkr(e.amount)}</span></li>))}</ul>
            {!res.books_only.length && <Empty>Nothing pending</Empty>}
          </div>
        </div>
        <details className="card p-4 text-sm"><summary className="cursor-pointer font-semibold">Matched ({res.matched})</summary>
          <ul className="mt-2 divide-y divide-slate-100">{res.matched_lines.map((m: any, i: number) => <li key={i} className="flex justify-between py-1"><span>{day(m.line.at)} · {m.entry.what}</span><span className="tabular-nums">{pkr(m.line.credit || m.line.debit)}</span></li>)}</ul></details>
      </>}
      {history.data && history.data.length > 0 && <div className="card p-4 text-sm"><h3 className="mb-1 font-semibold">Past reconciliations</h3>
        {history.data.map((h: any) => <div key={h.id} className="flex justify-between border-b border-slate-100 py-1"><span>{dt(h.created_at)} · {h.bank ?? "All banks"} · {day(h.period_from)}–{day(h.period_to)}</span><span>{h.matched}/{h.lines} matched{h.statement_closing != null ? ` · closing ${pkr(h.statement_closing)}` : ""}</span></div>)}</div>}
    </>
  );
}

function Ledger() {
  const [r, setR] = useState({ from: `${thisMonth()}-01`, to: today() });
  const { data, error } = useApi<any>(`/ledger?from=${r.from}&to=${r.to}`);
  const q = `from=${r.from}&to=${r.to}`;
  return (
    <>
      <div className="card flex flex-wrap items-end gap-3 p-4">
        <Field label="From"><input type="date" className="input" value={r.from} onChange={(e) => setR({ ...r, from: e.target.value })} /></Field>
        <Field label="To"><input type="date" className="input" value={r.to} onChange={(e) => setR({ ...r, to: e.target.value })} /></Field>
        <a className="btn-primary" href={dl(`/ledger.csv?${q}`)}><Download size={15} /> Excel / QuickBooks (CSV)</a>
        <a className="btn-secondary" href={dl(`/ledger/tally.xml?${q}`)}><Download size={15} /> Tally (XML)</a>
        <p className="w-full text-xs text-slate-500">Double-entry journal: one voucher per day for fuel and shop sales, and one per payment, purchase, expense, deposit, coupon, wallet and staff entry. Up to 3 months at a time.</p>
      </div>
      {error && <ErrorBox error={error} />}
      {!data ? !error && <Loading /> : (
        <div className="grid grid-cols-1 gap-5 lg:grid-cols-[1fr_1.2fr]">
          <div className="card overflow-x-auto p-4">
            <h3 className="mb-2 font-semibold">Trial balance</h3>
            <table className="w-full text-sm"><thead className="text-left text-xs uppercase text-slate-500"><tr><th className="py-1">Account</th><th className="text-right">Debit</th><th className="text-right">Credit</th></tr></thead>
              <tbody className="divide-y divide-slate-100">{data.trial_balance.map((a: any) => <tr key={a.account}><td className="py-1">{a.account}</td><td className="text-right tabular-nums">{a.debit ? num(a.debit) : ""}</td><td className="text-right tabular-nums">{a.credit ? num(a.credit) : ""}</td></tr>)}</tbody>
              <tfoot><tr className="border-t-2 border-slate-800 font-bold"><td className="py-1">Total</td><td className="text-right tabular-nums">{num(data.totals.debit)}</td><td className="text-right tabular-nums">{num(data.totals.credit)}</td></tr></tfoot></table>
          </div>
          <div className="card p-4">
            <h3 className="mb-2 font-semibold">Latest vouchers <span className="text-sm font-normal text-slate-500">({data.voucher_count} in period)</span></h3>
            <ul className="max-h-[560px] divide-y divide-slate-100 overflow-y-auto text-sm">
              {[...data.vouchers].reverse().slice(0, 60).map((v: any) => (
                <li key={v.no} className="py-2"><div className="flex justify-between"><span><b>{v.no}</b> · {v.type}</span><span className="text-xs text-slate-500">{v.date}</span></div>
                  <div className="text-xs text-slate-600">{v.narration}</div>
                  {v.lines.map((l: any) => <div key={l.account} className="flex justify-between text-xs"><span className={l.credit ? "pl-4" : ""}>{l.account}</span><span className="tabular-nums">{l.debit ? `Dr ${num(l.debit)}` : `Cr ${num(l.credit)}`}</span></div>)}
                </li>
              ))}
            </ul>
          </div>
        </div>
      )}
    </>
  );
}

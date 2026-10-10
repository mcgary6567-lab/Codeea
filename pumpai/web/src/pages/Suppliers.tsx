import { useState } from "react";
import { Plus, Printer, Wallet, Building2, Pencil, Download } from "lucide-react";
import { VoucherSlip } from "./Cashier";
import { api, linkToken, useApi } from "../lib/api";
import { ProofPhotos, ProofThumbs } from "../components/Capture";
import { AccountPicker } from "../components/BankParts";
import { Badge, Empty, Field, Loading, Modal, PageHeader, PhoneInput, Stat, useAction } from "../components/ui";
import { Ur } from "../components/VoiceShell";
import { PRODUCTS, ago, d, dt, num, phone, pkr, pkrShort } from "../lib/format";

const OIL_COMPANIES = ["PSO", "Shell", "Total PARCO", "Attock (APL)", "GO", "Hascol", "Byco / Puma", "Be Energy", "Askar", "Other"];
/** "Shell — Kamran", or just the name when no company is set. */
const label = (s: any) => [s.company, s.name].filter(Boolean).join(" — ");

export default function Suppliers() {
  const { data, reload } = useApi<any[]>("/suppliers");
  const depots = useApi<any[]>("/depots");
  const [open, setOpen] = useState<number | null>(null);
  const [adding, setAdding] = useState(false);
  const [addDepot, setAddDepot] = useState(false);
  const [editDepot, setEditDepot] = useState<any>(null);
  if (!data) return <Loading />;
  const total = data.reduce((a, s) => a + Math.max(0, s.owed), 0);
  // group the contacts under their depot; standalone ones go last under "No depot"
  const byDepot = new Map<string, { depot: any; rows: any[] }>();
  for (const s of data) {
    const key = s.depot_id ? `d${s.depot_id}` : "none";
    if (!byDepot.has(key)) byDepot.set(key, { depot: s.depot_id ? { id: s.depot_id, name: s.depot_name, address: s.depot_address, city: s.depot_city } : null, rows: [] });
    byDepot.get(key)!.rows.push(s);
  }
  const groups = [...byDepot.values()].sort((a, b) => (a.depot ? 0 : 1) - (b.depot ? 0 : 1) || (a.depot?.name ?? "").localeCompare(b.depot?.name ?? ""));

  return (
    <div className="space-y-5">
      <PageHeader title="Suppliers & depots" subtitle="Har depot ke andar company aur banda — order aur payment sahi aadmi ko. Tanker deliveries bhi yahan khud-ba-khud aati hain."
        actions={<div className="flex gap-2">
          <button className="btn-secondary" onClick={() => setAddDepot(true)}><Building2 size={16} /> Add depot</button>
          <button className="btn-primary" onClick={() => setAdding(true)}><Plus size={16} /> Add contact</button>
        </div>} />
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <Stat label="We owe suppliers" value={pkrShort(total)} tone="red" />
        <Stat label="Depots" value={depots.data?.length ?? "—"} />
        <Stat label="Contacts" value={data.length} />
        <Stat label="Bought this month" value={`${num(data.reduce((a, s) => a + s.month_l, 0))} L`} />
      </div>

      {groups.map(({ depot, rows }) => (
        <div key={depot?.id ?? "none"} className="card overflow-hidden">
          <div className="flex items-start justify-between gap-2 border-b border-slate-100 bg-slate-50 px-4 py-2.5">
            <div className="min-w-0">
              <h2 className="flex items-center gap-1.5 font-semibold">{depot ? <Building2 size={15} className="shrink-0 text-slate-500" /> : null}{depot?.name ?? "No depot (standalone)"}</h2>
              {depot && <div className="text-xs text-slate-500">{[depot.address, depot.city].filter(Boolean).join(" · ") || "No address"}</div>}
            </div>
            <div className="shrink-0 text-right">
              <div className="text-sm font-semibold tabular-nums">{pkr(rows.reduce((a, s) => a + Math.max(0, s.owed), 0))}</div>
              <div className="text-[11px] text-slate-500">we owe</div>
              {depot && <button className="btn-secondary mt-1 !px-2 !py-0.5 text-xs" onClick={() => setEditDepot(depot)}><Pencil size={11} /> Edit</button>}
            </div>
          </div>
          <ul className="divide-y divide-slate-100">
            {rows.map((s) => (
              <li key={s.id} className={`flex cursor-pointer items-start justify-between gap-2 px-4 py-3 active:bg-slate-50 hover:bg-slate-50 ${s.active ? "" : "opacity-50"}`} onClick={() => setOpen(s.id)}>
                <span className="min-w-0">
                  <span className="block font-medium">{s.company ? <><span className="text-slate-900">{s.company}</span> <span className="text-slate-400">—</span> {s.name}</> : s.name}</span>
                  <span className="text-xs text-slate-500">{[s.phone ? phone(s.phone) : "", s.month_l ? `${num(s.month_l)} L this month` : "", s.last_purchase ? `bought ${ago(s.last_purchase)}` : ""].filter(Boolean).join(" · ")}</span>
                </span>
                <span className="shrink-0 text-right"><span className="block font-semibold tabular-nums">{pkr(s.owed)}</span><span className="text-[11px] text-slate-500">we owe · <Ur>باقی</Ur></span></span>
              </li>
            ))}
          </ul>
        </div>
      ))}
      {!data.length && <div className="card"><Empty>No suppliers yet. Add a depot, then add the company men inside it.</Empty></div>}

      {open && <SupplierDetail id={open} onClose={() => setOpen(null)} onChanged={reload} depots={depots.data ?? []} onDepots={depots.reload} />}
      {adding && <SupplierForm depots={depots.data ?? []} onClose={() => setAdding(false)} onSaved={() => { setAdding(false); reload(); }} onDepots={depots.reload} />}
      {addDepot && <DepotForm onClose={() => setAddDepot(false)} onSaved={() => { setAddDepot(false); depots.reload(); }} />}
      {editDepot && <DepotForm initial={editDepot} onClose={() => setEditDepot(null)} onSaved={() => { setEditDepot(null); depots.reload(); reload(); }} />}
    </div>
  );
}

function DepotForm({ initial, onClose, onSaved }: { initial?: any; onClose: () => void; onSaved: () => void }) {
  const [f, setF] = useState({ name: initial?.name ?? "", address: initial?.address ?? "", city: initial?.city ?? "", phone: initial?.phone ?? "", notes: initial?.notes ?? "" });
  const { busy, run } = useAction();
  const save = async (e: any) => {
    e.preventDefault();
    const body = { name: f.name, address: f.address || null, city: f.city || null, phone: f.phone || null, notes: f.notes || null };
    const ok = initial ? await run(() => api(`/depots/${initial.id}`, { method: "PATCH", body }), "Depot saved") : await run(() => api("/depots", { body }), "Depot added");
    if (ok) onSaved();
  };
  return (
    <Modal open onClose={onClose} title={initial ? "Edit depot" : "Add depot"}>
      <form className="space-y-3" onSubmit={save}>
        <Field label="Depot name (e.g. Kotadu Depot)"><input className="input" required value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} /></Field>
        <div className="grid gap-3 sm:grid-cols-2">
          <Field label="Address"><input className="input" value={f.address} onChange={(e) => setF({ ...f, address: e.target.value })} /></Field>
          <Field label="City"><input className="input" value={f.city} onChange={(e) => setF({ ...f, city: e.target.value })} /></Field>
        </div>
        <Field label="Depot phone (optional)"><PhoneInput value={f.phone} onChange={(v) => setF({ ...f, phone: v })} /></Field>
        <Field label="Notes (optional)"><input className="input" value={f.notes} onChange={(e) => setF({ ...f, notes: e.target.value })} /></Field>
        <p className="text-xs text-slate-500">Depot sirf header hai — andar company aur banda (jaise Shell — Kamran) alag add karein, khata unhi par chalega.</p>
        <div className="flex justify-end gap-2"><button type="button" className="btn-secondary" onClick={onClose}>Cancel</button><button className="btn-primary" disabled={busy || !f.name}>Save</button></div>
      </form>
    </Modal>
  );
}

/** Add or edit a supplier contact (company + person) inside a depot. */
function SupplierForm({ initial, depots, onClose, onSaved, onDepots }: { initial?: any; depots: any[]; onClose: () => void; onSaved: () => void; onDepots: () => void }) {
  const [f, setF] = useState({
    name: initial?.name ?? "", company: initial?.company ?? "", companyOther: "", depot_id: initial?.depot_id ? String(initial.depot_id) : "",
    phone: initial?.phone ?? "", opening_balance: String(initial?.opening_balance ?? "0"), notes: initial?.notes ?? "",
  });
  const [newDepot, setNewDepot] = useState(false);
  const { busy, run } = useAction();
  const knownCompany = OIL_COMPANIES.includes(f.company);
  const save = async (e: any) => {
    e.preventDefault();
    const company = f.company === "Other" ? (f.companyOther || null) : (f.company || null);
    const base = { name: f.name, company, depot_id: f.depot_id ? Number(f.depot_id) : null, phone: f.phone || null, notes: f.notes || null };
    const ok = initial
      ? await run(() => api(`/suppliers/${initial.id}`, { method: "PATCH", body: base }), "Saved")
      : await run(() => api("/suppliers", { body: { ...base, opening_balance: Number(f.opening_balance) || 0 } }), "Contact added");
    if (ok) onSaved();
  };
  return (
    <Modal open onClose={onClose} title={initial ? "Edit contact" : "Add supplier contact"}>
      {newDepot && <DepotForm onClose={() => setNewDepot(false)} onSaved={() => { setNewDepot(false); onDepots(); }} />}
      <form className="space-y-3" onSubmit={save}>
        <Field label="Depot">
          <div className="flex gap-2">
            <select className="input" value={f.depot_id} onChange={(e) => setF({ ...f, depot_id: e.target.value })}>
              <option value="">— No depot —</option>
              {depots.map((d) => <option key={d.id} value={d.id}>{d.name}</option>)}
            </select>
            <button type="button" className="btn-secondary whitespace-nowrap !px-3" onClick={() => setNewDepot(true)}><Plus size={14} /> New</button>
          </div>
        </Field>
        <div className="grid gap-3 sm:grid-cols-2">
          <Field label="Company">
            <select className="input" value={knownCompany || f.company === "" ? f.company : "Other"} onChange={(e) => setF({ ...f, company: e.target.value })}>
              <option value="">— none —</option>
              {OIL_COMPANIES.map((c) => <option key={c}>{c}</option>)}
            </select>
          </Field>
          <Field label="Person's name (e.g. Kamran)"><input className="input" required value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} /></Field>
        </div>
        {(f.company === "Other" || (!knownCompany && f.company !== "")) && <Field label="Write the company"><input className="input" value={f.company === "Other" ? f.companyOther : f.company} onChange={(e) => setF({ ...f, company: "Other", companyOther: e.target.value })} /></Field>}
        <Field label="Phone"><PhoneInput value={f.phone} onChange={(v) => setF({ ...f, phone: v })} /></Field>
        {!initial && <Field label="Opening balance we owe (Rs)"><input className="input" type="number" value={f.opening_balance} onChange={(e) => setF({ ...f, opening_balance: e.target.value })} /></Field>}
        <Field label="Notes (optional)"><input className="input" value={f.notes} onChange={(e) => setF({ ...f, notes: e.target.value })} /></Field>
        <div className="flex items-center justify-end gap-2">
          {initial && <button type="button" className="btn-secondary mr-auto !text-amber-700" disabled={busy} onClick={() => run(() => api(`/suppliers/${initial.id}`, { method: "PATCH", body: { active: !initial.active } }), initial.active ? "Deactivated" : "Reactivated").then(onSaved)}>{initial.active ? "Deactivate" : "Reactivate"}</button>}
          <button type="button" className="btn-secondary" onClick={onClose}>Cancel</button><button className="btn-primary" disabled={busy || !f.name}>Save</button>
        </div>
      </form>
    </Modal>
  );
}

function SupplierDetail({ id, onClose, onChanged, depots, onDepots }: { id: number; onClose: () => void; onChanged: () => void; depots: any[]; onDepots: () => void }) {
  const { data: s, reload } = useApi<any>(`/suppliers/${id}`);
  const [pay, setPay] = useState({ amount: "", method: "Bank transfer", ref: "", wht: "" });
  const [photos, setPhotos] = useState<number[]>([]);
  const [account, setAccount] = useState<number | null>(null);
  const [slip, setSlip] = useState<any>(null);
  const [edit, setEdit] = useState(false);
  const { busy, run } = useAction();
  const title = s ? label(s) : "Supplier";
  // the cashier's two-copy payment voucher, for a payment made from this page
  const voucherOf = (t: any, owedAfter: number | null) => ({
    prepared: true, party_type: "supplier", balance_after: owedAfter, account: t.account_name,
    voucher: { no: `SP-${String(t.id).padStart(5, "0")}`, direction: "out", amount: t.amount, party_name: label(s), method: t.method, ref: t.ref,
      note: [s.depot_name ? `Depot: ${s.depot_name}` : "", t.note, t.withholding ? `Income tax withheld ${pkr(t.withholding)} (paid to FBR)` : ""].filter(Boolean).join(" · ") || null, created_by: t.created_by, created_at: t.created_at },
  });
  const printable = (t: any) => t.type === "payment" && t.method !== "WHT";
  return (
    <Modal open onClose={onClose} title={title} wide>
      {slip && <VoucherSlip r={slip} onClose={() => setSlip(null)} />}
      {edit && s && <SupplierForm initial={s} depots={depots} onClose={() => setEdit(false)} onSaved={() => { setEdit(false); reload(); onChanged(); }} onDepots={onDepots} />}
      {!s ? <Loading /> : (
        <div className="space-y-4">
          {/* on paper: the supplier's account, oldest entry first, like a bank statement */}
          <div className="own-title hidden print:block">
            <div className="flex items-end justify-between border-b border-slate-300 pb-2">
              <div><div className="text-lg font-bold">{label(s)}</div><div className="text-xs text-slate-600">{[s.depot_name ? `Depot: ${s.depot_name}${s.depot_address ? `, ${s.depot_address}` : ""}` : "", s.phone ? phone(s.phone) : "", "Supplier account statement"].filter(Boolean).join(" · ")}</div></div>
              <div className="text-right text-sm">We owe: <b className="tabular-nums">{pkr(s.owed)}</b><div className="text-xs text-slate-500">as on {dt(new Date().toISOString())}</div></div>
            </div>
            <table className="mt-2 w-full">
              <thead><tr><th className="th">Date</th><th className="th">Entry</th><th className="th">Details</th><th className="th text-right">Purchased</th><th className="th text-right">Paid</th><th className="th text-right">We owe</th></tr></thead>
              <tbody>
                <tr><td className="td" colSpan={5}>Opening balance</td><td className="td whitespace-nowrap text-right tabular-nums">{pkr(s.opening_balance ?? 0)}</td></tr>
                {[...s.lines].reverse().map((t: any) => (
                  <tr key={t.id}>
                    <td className="td whitespace-nowrap">{d(t.txn_date)}</td><td className="td capitalize">{t.type}</td>
                    <td className="td">{t.product ? `${num(t.litres)} L ${PRODUCTS[t.product]} @ Rs ${t.rate}` : t.method}<span className="text-[10px] text-slate-500">{[t.ref, t.note].filter(Boolean).map((x) => ` · ${x}`).join("")}</span></td>
                    <td className="td whitespace-nowrap text-right tabular-nums">{t.debit ? pkr(t.debit) : ""}</td>
                    <td className="td whitespace-nowrap text-right tabular-nums">{t.credit ? pkr(t.credit) : ""}</td>
                    <td className="td whitespace-nowrap text-right tabular-nums">{pkr(t.balance)}</td>
                  </tr>))}
                <tr className="font-bold"><td className="td border-t-2 border-slate-800" colSpan={3}>Closing balance (we owe)</td>
                <td className="td whitespace-nowrap text-right tabular-nums">{pkr(s.lines.reduce((a: number, t: any) => a + t.debit, 0))}</td>
                <td className="td whitespace-nowrap text-right tabular-nums">{pkr(s.lines.reduce((a: number, t: any) => a + t.credit, 0))}</td>
                <td className="td whitespace-nowrap text-right tabular-nums">{pkr(s.owed)}</td></tr>
              </tbody>
            </table>
            <div className="mt-8 grid break-inside-avoid grid-cols-2 gap-10 text-center text-xs text-slate-600"><div className="border-t border-slate-500 pt-1">Prepared by</div><div className="border-t border-slate-500 pt-1">Supplier (confirmed) · <Ur>سپلائر</Ur></div></div>
          </div>
          {/* depot / company header on screen */}
          <div className="flex flex-wrap items-center gap-2 print:hidden">
            {s.depot_name && <span className="badge gap-1 bg-slate-100 text-slate-700"><Building2 size={12} /> {s.depot_name}{s.depot_address ? ` · ${s.depot_address}` : ""}</span>}
            {s.company && <Badge tone="blue">{s.company}</Badge>}
            <button type="button" className="btn-secondary ml-auto min-h-9 !py-1" onClick={() => setEdit(true)}><Pencil size={14} /> Edit contact</button>
          </div>
          <div className="flex flex-wrap items-end gap-3 print:hidden">
            <Stat label="We owe" value={pkr(s.owed)} tone="red" />
            <a className="btn-secondary min-h-10" href={`/api/suppliers/${id}/statement.csv?token=${linkToken()}`}><Download size={15} /> Excel / CSV</a>
            <button type="button" className="btn-secondary min-h-10" onClick={() => window.print()}><Printer size={15} /> Print statement · <Ur>پرنٹ</Ur></button>
            <form className="grid w-full basis-full grid-cols-2 items-end gap-2 sm:flex sm:min-w-0 sm:flex-1 sm:basis-auto sm:flex-wrap" onSubmit={async (e) => {
              e.preventDefault();
              const res: any = await run(() => api(`/suppliers/${id}/payment`, { body: { amount: Number(pay.amount), method: pay.method, ref: pay.ref || null, withholding: Number(pay.wht) || 0, photo_ids: photos, account_id: account } }), (r: any) => `Payment saved. We now owe ${pkr(r.owed)}`);
              if (res) { setPay({ ...pay, amount: "", ref: "", wht: "" }); setPhotos([]); reload(); onChanged(); if (res.payment) setSlip(voucherOf(res.payment, res.owed)); }
            }}>
              <Field label="Pay amount (Rs)"><input className="input sm:w-40" type="number" min={1} required value={pay.amount} onChange={(e) => setPay({ ...pay, amount: e.target.value })} /></Field>
              <Field label="Method"><select className="input" value={pay.method} onChange={(e) => setPay({ ...pay, method: e.target.value })}>{["Bank transfer", "Pay order", "Online (1LINK)", "Cheque", "Cash"].map((m) => <option key={m}>{m}</option>)}</select></Field>
              <Field label="Ref"><input className="input sm:w-32" value={pay.ref} onChange={(e) => setPay({ ...pay, ref: e.target.value })} /></Field>
              <Field label="Tax withheld (Rs)"><input className="input sm:w-32" type="number" min={0} placeholder="0" value={pay.wht} onChange={(e) => setPay({ ...pay, wht: e.target.value })} /></Field>
              <div className="col-span-2 w-full"><AccountPicker method={pay.method} label="Paid from which bank? · کس بینک سے" value={account} onChange={setAccount} /></div>
              <button className="btn-primary col-span-2" disabled={busy || (pay.method === "Cheque" && !photos.length)}><Wallet size={15} /> Record payment</button>
              <div className="col-span-2 w-full"><ProofPhotos value={photos} onChange={setPhotos} required={pay.method === "Cheque"} hint="pay order, cheque, bank / 1LINK receipt" /></div>
            </form>
          </div>
          <div className="max-h-96 overflow-auto rounded-lg border border-slate-200 print:hidden">
            <ul className="divide-y divide-slate-100 sm:hidden">
              {s.lines.map((t: any) => (
                <li key={t.id} className="px-3 py-2">
                  <div className="flex items-start justify-between gap-2">
                    <span className="min-w-0"><Badge tone={t.type === "purchase" ? "blue" : t.type === "payment" ? "green" : "violet"}>{t.type}</Badge> <span className="text-xs text-slate-500">{dt(t.txn_date)}</span></span>
                    <span className="shrink-0 text-right tabular-nums">{t.debit ? <span className="block font-semibold">{pkr(t.debit)}</span> : null}{t.credit ? <span className="block font-semibold text-emerald-700">−{pkr(t.credit)}</span> : null}<span className="text-[11px] text-slate-500">owe {pkr(t.balance)}</span></span>
                  </div>
                  <div className="break-words text-xs text-slate-600">{t.product ? `${num(t.litres)} L ${PRODUCTS[t.product]} @ Rs ${t.rate}` : t.method} <span className="text-slate-500">{[t.ref, t.note].filter(Boolean).join(" · ")}</span></div>
                  <div className="flex items-center justify-between gap-2"><ProofThumbs ids={t.proof_ids} />
                    {printable(t) && <button className="btn-secondary ml-auto min-h-9 !px-2 !py-1" aria-label="Print voucher" onClick={() => setSlip(voucherOf(t, t.balance))}><Printer size={14} /></button>}</div>
                </li>
              ))}
            </ul>
            <table className="hidden w-full sm:table">
              <thead className="sticky top-0"><tr><th className="th">Date</th><th className="th">Entry</th><th className="th">Details</th><th className="th text-right">Purchased</th><th className="th text-right">Paid</th><th className="th text-right">We owe</th><th className="th" /></tr></thead>
              <tbody>{s.lines.map((t: any) => (
                <tr key={t.id}>
                  <td className="td text-xs">{dt(t.txn_date)}</td>
                  <td className="td"><Badge tone={t.type === "purchase" ? "blue" : t.type === "payment" ? "green" : "violet"}>{t.type}</Badge></td>
                  <td className="td text-xs">{t.product ? `${num(t.litres)} L ${PRODUCTS[t.product]} @ Rs ${t.rate}` : t.method} <span className="text-slate-500">{[t.ref, t.note].filter(Boolean).join(" · ")}</span> <ProofThumbs ids={t.proof_ids} /></td>
                  <td className="td text-right tabular-nums">{t.debit ? pkr(t.debit) : ""}</td>
                  <td className="td text-right tabular-nums text-emerald-700">{t.credit ? pkr(t.credit) : ""}</td>
                  <td className="td text-right font-medium tabular-nums">{pkr(t.balance)}</td>
                  <td className="td">{printable(t) && <button className="btn-secondary !px-2 !py-1" aria-label="Print voucher" title="Print voucher" onClick={() => setSlip(voucherOf(t, t.balance))}><Printer size={14} /></button>}</td>
                </tr>
              ))}</tbody>
            </table>
            {!s.lines.length && <Empty>No entries</Empty>}
          </div>
        </div>
      )}
    </Modal>
  );
}

import { useState } from "react";
import { Hourglass, Check } from "lucide-react";
import { api, useApi } from "../lib/api";
import { Loading, PageHeader, useAction } from "../components/ui";
import { PRODUCTS, pkr, num, dt } from "../lib/format";
import { PhotoButton, PhotoThumb } from "../components/Capture";

/**
 * Khata fuel given on trust: the card / parchi comes days later. Each hold waits here at NO locked rate; when the
 * card arrives the owner / cashier / manager clears it (one or many at once) and it is billed to the khata at the
 * rate chosen for the clear day — today's pump price by default, editable.
 */
export default function CardPending() {
  const { data, reload } = useApi<any[]>("/sales/pending");
  const { busy, run } = useAction();
  const [sel, setSel] = useState<Set<number>>(new Set());
  const [rate, setRate] = useState("");       // one rate applied to all selected; blank = each at its own current pump price
  const [photo, setPhoto] = useState<number | null>(null);
  if (!data) return <Loading />;

  const rows = data;
  const toggle = (id: number) => setSel((s) => { const n = new Set(s); n.has(id) ? n.delete(id) : n.add(id); return n; });
  const selRows = rows.filter((r) => sel.has(r.id));
  // only when every selected hold is the same product can one rate box make sense; otherwise each uses its own current price
  const products = new Set(selRows.map((r) => r.product));
  const oneProduct = products.size === 1;
  const defaultRate = oneProduct ? selRows[0]?.current_rate : null;
  const billRate = (r: any) => (oneProduct && Number(rate) > 0 ? Number(rate) : r.current_rate);
  const selTotal = selRows.reduce((a, r) => a + r.litres * billRate(r), 0);

  const clear = async () => {
    const body: any = { ids: [...sel] };
    if (oneProduct && Number(rate) > 0) body.rate = Number(rate);
    if (photo) body.photo_id = photo;
    const r = await run(() => api("/sales/clear", { body }),
      (x: any) => `Cleared ${x.cleared.length} — billed ${pkr(x.total)} to khata`);
    if (r) { setSel(new Set()); setRate(""); setPhoto(null); reload(); }
  };

  const byCustomer = (() => {
    const m = new Map<string, any[]>();
    for (const r of rows) { const k = r.customer_name ?? "—"; (m.get(k) ?? m.set(k, []).get(k)!).push(r); }
    return [...m.entries()];
  })();

  return (
    <div>
      <PageHeader title="Card pending (khata)" subtitle="Fuel given on trust — no rate is locked until the card / parchi comes in. Clear a hold to bill it to the khata at the clear-day rate." />

      {!rows.length ? (
        <div className="card p-8 text-center text-slate-500">
          <Hourglass className="mx-auto mb-2 text-slate-400" /> No card-pending holds. Sab clear hain. 🎉
        </div>
      ) : (
        <div className="grid gap-5 lg:grid-cols-[1fr_340px]">
          <div className="space-y-4">
            {byCustomer.map(([name, list]) => (
              <div key={name} className="card">
                <div className="flex items-center gap-2 border-b border-slate-100 px-4 py-2">
                  <button className="text-sm text-brand-700 underline" onClick={() => setSel((s) => { const n = new Set(s); const all = list.every((r: any) => n.has(r.id)); list.forEach((r: any) => all ? n.delete(r.id) : n.add(r.id)); return n; })}>
                    {list.every((r: any) => sel.has(r.id)) ? "Unselect" : "Select all"}
                  </button>
                  <h2 className="flex-1 font-semibold">📒 {name}</h2>
                  <span className="text-sm text-slate-500">{list.length} hold{list.length === 1 ? "" : "s"}</span>
                </div>
                <table className="w-full">
                  <thead><tr><th className="th w-8"></th><th className="th">Fuel</th><th className="th text-right">Litres</th><th className="th">Filled</th><th className="th text-right">At today's rate</th><th className="th">Slip</th></tr></thead>
                  <tbody>{list.map((r: any) => (
                    <tr key={r.id} className={sel.has(r.id) ? "bg-violet-50" : ""}>
                      <td className="td"><input type="checkbox" checked={sel.has(r.id)} onChange={() => toggle(r.id)} aria-label={`Select hold ${r.id}`} /></td>
                      <td className="td text-sm">{PRODUCTS[r.product] ?? r.product}{r.vehicle_no ? <span className="block text-xs text-slate-500">{r.vehicle_no}</span> : null}</td>
                      <td className="td text-right tabular-nums">{num(r.litres, 2)} L</td>
                      <td className="td text-xs text-slate-500">{dt(r.created_at)}{r.days_pending >= 3 && <span className="ml-1 rounded bg-amber-100 px-1.5 py-0.5 font-semibold text-amber-800">{r.days_pending}d</span>}</td>
                      <td className="td text-right tabular-nums">{pkr(r.projected_amount)}<span className="block text-xs text-slate-400">@ {r.current_rate}</span></td>
                      <td className="td text-xs">{r.slip_no ?? "—"}{r.photo_id ? <PhotoThumb id={r.photo_id} size={7} className="ml-1 inline-block align-middle" /> : null}</td>
                    </tr>
                  ))}</tbody>
                </table>
              </div>
            ))}
          </div>

          {/* clear panel */}
          <aside className="space-y-3">
            <div className="card sticky top-4 space-y-3 p-4">
              <h2 className="font-semibold">Clear & bill to khata</h2>
              {!sel.size ? (
                <p className="text-sm text-slate-500">Select one or more holds on the left to clear them.</p>
              ) : (
                <>
                  <div className="text-sm text-slate-600">{sel.size} hold{sel.size === 1 ? "" : "s"} selected</div>
                  <label className="block text-sm">
                    <span className="font-medium">Rate for the clear day</span>
                    {oneProduct ? (
                      <div className="mt-1 flex items-center gap-1"><span className="text-sm text-slate-500">Rs</span>
                        <input className="input text-right" type="number" step="0.01" placeholder={String(defaultRate ?? "")} value={rate} onChange={(e) => setRate(e.target.value)} /></div>
                    ) : (
                      <p className="mt-1 text-xs text-slate-500">Mixed fuels selected — each is billed at its own current pump price.</p>
                    )}
                    {oneProduct && <p className="mt-1 text-xs text-slate-500">Default is today's pump price (Rs {defaultRate}). Change it if the pump was on a different rate.</p>}
                  </label>
                  <div className="flex items-center justify-between rounded-xl bg-emerald-50 px-4 py-2 ring-1 ring-emerald-200">
                    <span className="text-sm text-emerald-800">Will add to khata</span>
                    <span className="text-2xl font-bold tabular-nums text-emerald-800">{pkr(selTotal)}</span>
                  </div>
                  <div>
                    <div className="mb-1 text-sm font-medium">Photo of the card / parchi <span className="font-normal text-slate-500">(proof)</span></div>
                    {photo ? (
                      <div className="flex items-center gap-3"><PhotoThumb id={photo} size={16} className="rounded-xl" />
                        <button className="rounded-xl bg-slate-100 px-3 py-2 text-sm" onClick={() => setPhoto(null)}>Remove</button></div>
                    ) : (
                      <PhotoButton kind="slip" label="Take card photo" className="w-full" onRead={(_r, id) => setPhoto(id)} />
                    )}
                  </div>
                  <button className="btn-primary w-full" disabled={busy} onClick={clear}><Check size={16} /> Clear {sel.size} & bill {pkr(selTotal)}</button>
                </>
              )}
            </div>
          </aside>
        </div>
      )}
    </div>
  );
}

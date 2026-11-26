import { useEffect, useState } from "react";
import { useParams, Link } from "react-router-dom";
import QRCode from "qrcode";
import { Printer, ArrowLeft, RefreshCw } from "lucide-react";
import { api, useApi } from "../lib/api";
import { Loading, ErrorBox, useAction } from "../components/ui";
import { TYPE_ICON } from "./Pos";

/** Printable QR cards: one for the khata account, one sticker per vehicle. Scanned at the POS. */
export default function Cards() {
  const { id } = useParams();
  const { data, error, reload } = useApi<any>(`/customers/${id}/cards`);
  const { run } = useAction();
  if (error) return <div className="p-6"><ErrorBox error={error} /></div>;
  if (!data) return <Loading />;
  const reissue = (vehicle_id?: number) => run(() => api(`/customers/${id}/cards/reissue`, { body: { vehicle_id } }), "New card made — print it; the old one no longer works").then(reload);
  const cards = [{ key: "acct", code: data.account_code, title: "Khata account card", plate: null as string | null, vid: undefined as number | undefined },
    ...data.vehicles.map((v: any) => ({ key: `v${v.id}`, code: v.code, title: "Vehicle card", plate: v.plate_no, vid: v.id }))];
  return (
    <div className="min-h-screen bg-slate-100 p-4 print:bg-white print:p-0">
      <div className="mx-auto mb-4 flex max-w-4xl flex-wrap items-center gap-2 print:hidden">
        <Link to="/khata" className="btn-secondary"><ArrowLeft size={15} /> Back</Link>
        <h1 className="text-lg font-semibold">QR cards — {data.customer.name}</h1>
        <button className="btn-primary ml-auto" onClick={() => window.print()}><Printer size={15} /> Print cards</button>
      </div>
      <p className="mx-auto mb-3 max-w-4xl text-sm text-slate-600 print:hidden">Stick the vehicle card on the windscreen. At the pump the salesman taps <b>Scan card</b> and the account, vehicle and khata are filled in. Lost a card? Make a new one — the old card stops working.</p>
      <div className="mx-auto grid grid-cols-1 max-w-4xl gap-4 sm:grid-cols-2 print:grid-cols-2 print:gap-2">
        {cards.map((c) => (
          <div key={c.key} className="break-inside-avoid">
            <QrCard business={data.business} name={data.customer.name} type={data.customer.type} title={c.title} plate={c.plate} code={c.code} />
            <button className="mt-1 flex items-center gap-1 text-xs text-slate-500 hover:text-red-600 print:hidden" onClick={() => reissue(c.vid)}><RefreshCw size={12} /> Lost — make a new card</button>
          </div>
        ))}
      </div>
    </div>
  );
}

function QrCard({ business, name, type, title, plate, code }: { business: string; name: string; type: string; title: string; plate: string | null; code: string }) {
  const [svg, setSvg] = useState("");
  useEffect(() => { QRCode.toString(`PUMPAI-${code}`, { type: "svg", margin: 1, errorCorrectionLevel: "M" }).then(setSvg); }, [code]);
  return (
    <div className="flex h-[54mm] w-full max-w-[86mm] overflow-hidden rounded-xl border-2 border-brand-700 bg-white print:max-w-none">
      <div className="flex flex-1 flex-col justify-between p-3">
        <div>
          <div className="text-xs font-semibold uppercase tracking-wide text-brand-700">⛽ {business}</div>
          <div className="mt-1 text-[11px] text-slate-500">{title}</div>
          <div className="text-base font-bold leading-tight">{TYPE_ICON[type] ?? "📒"} {name}</div>
        </div>
        {plate && <div className="self-start rounded-md border-2 border-slate-800 px-2 py-0.5 font-mono text-lg font-bold tracking-wider">{plate}</div>}
        <div className="font-mono text-xs text-slate-500">{code}</div>
      </div>
      <div className="flex w-[46%] items-center justify-center bg-white p-2" aria-label={`QR code ${code}`} dangerouslySetInnerHTML={{ __html: svg }} />
    </div>
  );
}

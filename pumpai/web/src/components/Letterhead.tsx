/**
 * The pump's letterhead on every printed page of the app (statements, vouchers, day book, reports, register, shift report…):
 * logo, name, phone and place at the top; address, email, website and social pages at the bottom. Shown only on paper / PDF.
 */
import { useEffect, useState } from "react";
import { api } from "../lib/api";
import { SOCIALS, handle } from "../lib/social";
import { useAuth } from "../App";
import { phone as fmtPhone } from "../lib/format";

let cache: any = null;
let loading: Promise<any> | null = null;
const listeners = new Set<(b: any) => void>();
/** The business profile, loaded once and shared by every letterhead. */
export function useBusiness() {
  const [b, setB] = useState<any>(cache);
  useEffect(() => {
    listeners.add(setB);
    if (!cache && !loading) loading = api("/business").then((r) => { cache = r; listeners.forEach((f) => f(r)); }).catch(() => { loading = null; });
    return () => { listeners.delete(setB); };
  }, []);
  return b;
}
/** After Settings → Business profile is saved, the letterheads show the new details. */
export function refreshBusiness() { cache = null; loading = null; api("/business").then((r) => { cache = r; listeners.forEach((f) => f(r)); }).catch(() => {}); }

export function PrintHeader() {
  const b = useBusiness();
  const { user } = useAuth();
  if (!b) return null;
  const place = b.place;
  const phone = fmtPhone(b.biz_phone || b.owner_phone);
  return (
    <div className="print-head hidden print:block">
      <div className="flex items-center justify-between gap-4 border-b-[3px] border-brand-600 pb-2.5">
        <div className="flex min-w-0 items-center gap-3">
          {b.logo_url && <img src={b.logo_url} alt="" className="h-12 max-w-[150px] object-contain" />}
          <div className="min-w-0"><div className="text-xl font-extrabold leading-tight text-slate-900">{b.name}</div>
            {b.omc && <div className="text-[11px] text-slate-600">{b.omc} dealer</div>}</div>
        </div>
        <div className="text-right text-[11px] leading-relaxed text-slate-700">
          {phone && <div>☎ {phone}</div>}
          {place && <div>📍 {place}</div>}
          {(b.ntn || b.strn) && <div>{b.ntn && `NTN ${b.ntn}`}{b.ntn && b.strn && " · "}{b.strn && `STRN ${b.strn}`}</div>}
        </div>
      </div>
      <div className="mb-3 mt-1 flex justify-between text-[10px] text-slate-500"><span>{document.title.replace(/ · PumpAI$/, "")}</span>
        <span>Printed {new Date().toLocaleString("en-PK", { dateStyle: "medium", timeStyle: "short" })}{user ? ` by ${user.name}` : ""}</span></div>
    </div>
  );
}

export function PrintFooter() {
  const b = useBusiness();
  if (!b) return null;
  const line = [b.place, b.biz_email, b.website].filter(Boolean).join(" · ");
  const soc = SOCIALS.filter((s) => b[s.key]);
  return (
    <div className="print-foot mt-5 hidden border-t border-slate-300 pt-2 text-center text-[10.5px] leading-relaxed text-slate-600 print:block">
      {line && <div>{line}</div>}
      {soc.length > 0 && <div className="mt-0.5 flex flex-wrap justify-center gap-x-4 gap-y-1">{soc.map((s) => (
        <span key={s.key} className="inline-flex items-center gap-1">
          <svg viewBox="0 0 24 24" className="h-3 w-3 fill-brand-600" aria-hidden dangerouslySetInnerHTML={{ __html: s.svg }} />{handle(b[s.key])}
        </span>))}</div>}
      {b.receipt_footer && <div className="font-semibold">{b.receipt_footer}</div>}
    </div>
  );
}

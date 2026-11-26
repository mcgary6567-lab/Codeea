/** Owner-only strip at the top of every page while the go-live checklist still has pending items (dismissable; reappears from the checklist page). */
import { Link, useLocation } from "react-router-dom";
import { Rocket, X } from "lucide-react";
import { api, useApi } from "../lib/api";
import { useAuth } from "../App";

export function GoLiveBanner() {
  const loc = useLocation();
  const { can } = useAuth();
  const ok = can("settings.manage");
  const { data, reload } = useApi<{ pending: number; done: number; skipped: number; items: { length: number }; dismissed: boolean }>(ok ? "/golive" : null);
  if (!ok || !data || data.dismissed || data.pending === 0 || loc.pathname.startsWith("/go-live")) return null;
  return (
    <div className="mb-4 flex flex-wrap items-center justify-between gap-2 rounded-xl border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-900 print:hidden">
      <div className="flex items-center gap-2"><Rocket size={16} /> <span><span className="font-semibold">Go-live checklist:</span> {data.pending} settings baqi hain (opening cash, bank, staff…). Har ek skip bhi ho sakti hai.</span></div>
      <div className="flex items-center gap-2">
        <Link to="/go-live" className="btn-primary text-xs">Checklist kholen</Link>
        <button aria-label="Dismiss" className="text-amber-700" onClick={async () => { await api("/golive/dismiss", { body: {} }); reload(); }}><X size={16} /></button>
      </div>
    </div>
  );
}

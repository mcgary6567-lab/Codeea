import { createContext, useCallback, useContext, useState, type ReactNode, type InputHTMLAttributes } from "react";
import { PrintFooter, PrintHeader } from "./Letterhead";
import { createPortal } from "react-dom";
import { X, CheckCircle2, AlertTriangle, Loader2, Search } from "lucide-react";

export function PageHeader({ title, subtitle, actions }: { title: string; subtitle?: string; actions?: ReactNode }) {
  return (
    <div className="mb-5 flex flex-wrap items-end justify-between gap-3">
      <div>
        <h1 className="text-xl font-semibold text-slate-900">{title}</h1>
        {subtitle && <p className="mt-0.5 text-sm text-slate-500">{subtitle}</p>}
      </div>
      {actions && <div className="flex flex-wrap gap-2">{actions}</div>}
    </div>
  );
}

export function Stat({ label, value, hint, icon, tone = "slate" }: { label: string; value: ReactNode; hint?: ReactNode; icon?: ReactNode; tone?: "slate" | "green" | "red" | "amber" | "blue" }) {
  const tones = { slate: "bg-slate-100 text-slate-600", green: "bg-emerald-100 text-emerald-700", red: "bg-red-100 text-red-700", amber: "bg-amber-100 text-amber-700", blue: "bg-blue-100 text-blue-700" };
  return (
    <div className="card p-4">
      <div className="flex items-center justify-between">
        <span className="text-xs font-medium uppercase tracking-wide text-slate-500">{label}</span>
        {icon && <span className={`rounded-lg p-1.5 ${tones[tone]}`}>{icon}</span>}
      </div>
      <div className="mt-2 break-words text-xl font-semibold tabular-nums text-slate-900 sm:text-2xl">{value}</div>
      {hint && <div className="mt-1 text-xs text-slate-500">{hint}</div>}
    </div>
  );
}

const badgeTones: Record<string, string> = {
  green: "bg-emerald-100 text-emerald-800", red: "bg-red-100 text-red-800", amber: "bg-amber-100 text-amber-800",
  blue: "bg-blue-100 text-blue-800", slate: "bg-slate-100 text-slate-700", violet: "bg-violet-100 text-violet-800",
};
export const Badge = ({ tone = "slate", children }: { tone?: keyof typeof badgeTones | string; children: ReactNode }) => (
  <span className={`badge ${badgeTones[tone] ?? badgeTones.slate}`}>{children}</span>
);

export const severityTone = (s: string) => (s === "critical" ? "red" : s === "warning" ? "amber" : "blue");
export const statusTone = (s: string) =>
  ({ pending: "amber", confirmed: "blue", dispatched: "violet", delivered: "green", cancelled: "slate", open: "amber", in_progress: "blue", resolved: "green", sent: "green", draft: "slate", sending: "blue", closed: "slate" } as Record<string, string>)[s] ?? "slate";

export function Modal({ open, onClose, title, children, wide }: { open: boolean; onClose: () => void; title: string; children: ReactNode; wide?: boolean }) {
  if (!open) return null;
  // portal to <body> so a modal opened from the sidebar (or any positioned parent) sits above the whole page
  return createPortal(
    <div className="fixed inset-0 z-50 flex items-start justify-center overflow-y-auto bg-slate-900/40 p-4 pt-16" onMouseDown={onClose} role="dialog" aria-modal="true" aria-label={title}>
      <div className={`card w-full ${wide ? "max-w-3xl" : "max-w-lg"} p-5`} onMouseDown={(e) => e.stopPropagation()}>
        <PrintHeader />
        <div className="modal-title mb-4 flex items-center justify-between">
          <h2 className="text-lg font-semibold">{title}</h2>
          <button onClick={onClose} className="-m-1.5 rounded-lg p-2.5 text-slate-400 hover:bg-slate-100 print:hidden" aria-label="Close"><X size={20} /></button>
        </div>
        {children}
        <PrintFooter />
      </div>
    </div>,
    document.body,
  );
}

export const Field = ({ label, children }: { label: string; children: ReactNode }) => (
  <label className="block"><span className="label">{label}</span>{children}</label>
);

/** A phone box that only takes digits (+ a leading country code), so letters/junk can never be typed. Pakistani format. */
export const PhoneInput = ({ value, onChange, required, placeholder, className, ...rest }: {
  value: string | null | undefined; onChange: (v: string) => void; required?: boolean; placeholder?: string; className?: string;
} & Omit<InputHTMLAttributes<HTMLInputElement>, "value" | "onChange">) => (
  <input {...rest} type="tel" inputMode="tel" autoComplete="tel" className={className ?? "input"} required={required}
    placeholder={placeholder ?? "03xx xxxxxxx"} value={value ?? ""} maxLength={18}
    onChange={(e) => onChange(e.target.value.replace(/[^\d+\s-]/g, ""))} />
);

/** A CNIC box that only takes the 13 digits and auto-formats them as xxxxx-xxxxxxx-x. */
export const CnicInput = ({ value, onChange, required, className, ...rest }: {
  value: string | null | undefined; onChange: (v: string) => void; required?: boolean; className?: string;
} & Omit<InputHTMLAttributes<HTMLInputElement>, "value" | "onChange">) => {
  const fmt = (v: string) => {
    const d = v.replace(/\D/g, "").slice(0, 13);
    return [d.slice(0, 5), d.slice(5, 12), d.slice(12, 13)].filter((x) => x).join("-");
  };
  return <input {...rest} inputMode="numeric" className={className ?? "input"} required={required}
    placeholder="xxxxx-xxxxxxx-x" value={value ?? ""} maxLength={15} onChange={(e) => onChange(fmt(e.target.value))} />;
};

export const Spinner = () => <Loader2 className="animate-spin text-slate-400" size={20} />;
export const Loading = () => <div className="flex h-40 items-center justify-center"><Spinner /></div>;
export const Empty = ({ children }: { children: ReactNode }) => <div className="py-10 text-center text-sm text-slate-500">{children}</div>;
export const ErrorBox = ({ error }: { error: string }) => <div className="rounded-lg border border-red-200 bg-red-50 p-3 text-sm text-red-700">{error}</div>;

/** True if every word of the query appears somewhere in the given fields (case-insensitive). Empty query matches all.
 *  Use in a list filter: rows.filter((r) => matchQuery(q, r.name, r.phone, r.company, …)). */
export function matchQuery(q: string, ...fields: (string | number | null | undefined)[]): boolean {
  const s = (q ?? "").trim().toLowerCase();
  if (!s) return true;
  const hay = fields.filter((v) => v != null && v !== "").map(String).join(" ").toLowerCase();
  return s.split(/\s+/).every((w) => hay.includes(w));
}

/** A standard search box: icon on the left, a clear (×) button when filled. Controlled. */
export function SearchBar({ value, onChange, placeholder = "Search… · ڈھونڈیں", className = "", autoFocus }: { value: string; onChange: (v: string) => void; placeholder?: string; className?: string; autoFocus?: boolean }) {
  return (
    <div className={`relative ${className}`}>
      <Search size={16} className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" />
      <input autoFocus={autoFocus} className="input w-full pl-9 pr-9" placeholder={placeholder} value={value} onChange={(e) => onChange(e.target.value)} aria-label="Search" />
      {value && <button type="button" onClick={() => onChange("")} className="absolute right-2 top-1/2 -translate-y-1/2 rounded-full p-1 text-slate-400 hover:bg-slate-100 hover:text-slate-700" aria-label="Clear search"><X size={15} /></button>}
    </div>
  );
}

/** A row of filter chips (one active at a time). `opts` = [value, label]; value "" is usually the "All" chip. */
export function FilterChips({ value, onChange, opts, className = "" }: { value: string; onChange: (v: string) => void; opts: [string, string][]; className?: string }) {
  return (
    <div className={`flex flex-wrap gap-1.5 ${className}`}>
      {opts.map(([v, label]) => (
        <button key={v} type="button" onClick={() => onChange(v)}
          className={`rounded-full px-3 py-1 text-sm font-medium ring-1 transition active:scale-95 ${value === v ? "bg-brand-600 text-white ring-brand-600" : "bg-white text-slate-600 ring-slate-300 hover:ring-brand-400"}`}>{label}</button>
      ))}
    </div>
  );
}

type Toast = { id: number; kind: "ok" | "err"; text: string };
const ToastCtx = createContext<(kind: Toast["kind"], text: string) => void>(() => {});
export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([]);
  const push = useCallback((kind: Toast["kind"], text: string) => {
    const id = Date.now() + Math.random();
    setToasts((t) => [...t, { id, kind, text }]);
    setTimeout(() => setToasts((t) => t.filter((x) => x.id !== id)), kind === "ok" ? 2600 : 5000);
  }, []);
  const dismiss = (id: number) => setToasts((t) => t.filter((x) => x.id !== id));
  return (
    <ToastCtx.Provider value={push}>
      {children}
      {/* Prominent centered pop-up shown whenever anything is saved, approved, sent or fails — system-wide. */}
      {toasts.length > 0 && createPortal(
        <>
          <style>{`@keyframes pumpai-pop{0%{opacity:0;transform:translateY(-12px) scale(.94)}60%{transform:translateY(0) scale(1.02)}100%{opacity:1;transform:translateY(0) scale(1)}}`}</style>
          <div className="pointer-events-none fixed inset-x-0 top-6 z-[100] flex flex-col items-center gap-3 px-4">
            {toasts.map((t) => (
              <div key={t.id} role="status" onClick={() => dismiss(t.id)}
                style={{ animation: "pumpai-pop .28s cubic-bezier(.34,1.56,.64,1) both" }}
                className={`pointer-events-auto flex w-full max-w-sm cursor-pointer items-center gap-3 rounded-2xl px-5 py-4 text-white shadow-2xl ring-1 ${t.kind === "ok" ? "bg-emerald-600 ring-emerald-700/30" : "bg-red-600 ring-red-700/30"}`}>
                <span className={`flex h-9 w-9 shrink-0 items-center justify-center rounded-full ${t.kind === "ok" ? "bg-white/20" : "bg-white/20"}`}>
                  {t.kind === "ok" ? <CheckCircle2 size={22} /> : <AlertTriangle size={22} />}
                </span>
                <div className="min-w-0">
                  <div className="text-sm font-semibold leading-tight">{t.kind === "ok" ? "Ho gaya" : "Ruk gaya"}</div>
                  <div className="text-sm leading-snug text-white/90">{t.text}</div>
                </div>
              </div>
            ))}
          </div>
        </>, document.body)}
    </ToastCtx.Provider>
  );
}
export const useToast = () => useContext(ToastCtx);

/** Run an async action with a busy flag and toast on success/error. */
export function useAction() {
  const toast = useToast();
  const [busy, setBusy] = useState(false);
  const run = useCallback(async <T,>(fn: () => Promise<T>, okText?: string | ((r: T) => string)) => {
    setBusy(true);
    try {
      const r = await fn();
      if (okText) toast("ok", typeof okText === "function" ? okText(r) : okText);
      return r;
    } catch (e: any) {
      toast("err", e.message);
      return undefined;
    } finally {
      setBusy(false);
    }
  }, [toast]);
  return { busy, run };
}

/* ---------------- list tools: one search box + chips + date range, for any list on any page ---------------- */
/** Pakistan calendar day of an ISO timestamp (or a plain YYYY-MM-DD). */
export const pkDay = (iso?: string | null) => (!iso ? "" : /^\d{4}-\d{2}-\d{2}$/.test(iso) ? iso : new Date(Date.parse(iso) + 5 * 3600_000).toISOString().slice(0, 10));
const rowText = (r: unknown): string => {
  if (r == null) return "";
  if (typeof r !== "object") return String(r);
  return Object.values(r as Record<string, unknown>).map((v) => (v && typeof v === "object" ? "" : String(v ?? ""))).join(" ");
};
/**
 * Client-side filter for a list the page already holds: a free-text `q` (matches any column, case-insensitive, all words
 * must hit), an optional `from`/`to` day on `dateKey`, and an optional `keep` predicate (chips). Returns the filtered rows.
 */
export function useListFilter<T>(rows: T[] | null | undefined, f: { q?: string; from?: string; to?: string; dateKey?: keyof T | ((r: T) => string | null | undefined); keep?: (r: T) => boolean; text?: (r: T) => string }): T[] {
  const list = rows ?? [];
  const words = (f.q ?? "").toLowerCase().split(/\s+/).filter(Boolean);
  const dayOf = (r: T) => { const k = f.dateKey; if (!k) return ""; return pkDay(typeof k === "function" ? k(r) : ((r as Record<string, unknown>)[k as string] as string)); };
  return list.filter((r) => {
    if (f.keep && !f.keep(r)) return false;
    if (f.dateKey && (f.from || f.to)) { const d = dayOf(r); if (f.from && d < f.from) return false; if (f.to && d > f.to) return false; }
    if (words.length) { const txt = (f.text ? f.text(r) : rowText(r)).toLowerCase(); if (!words.every((w) => txt.includes(w))) return false; }
    return true;
  });
}
/** The toolbar: search box, optional filter chips and optional from/to. Shows "n of total" so the user sees the filter is on. */
export function ListTools({ q, onQ, placeholder = "Dhoondein… · تلاش", chips, chip, onChip, from, to, onFrom, onTo, shown, total, right }: {
  q: string; onQ: (v: string) => void; placeholder?: string;
  chips?: { k: string; label: string; n?: number }[]; chip?: string; onChip?: (k: string) => void;
  from?: string; to?: string; onFrom?: (v: string) => void; onTo?: (v: string) => void;
  shown?: number; total?: number; right?: ReactNode;
}) {
  return (
    <div className="mb-2 flex flex-wrap items-center gap-2 print:hidden">
      <div className="relative min-w-[180px] flex-1">
        <Search size={15} className="absolute left-2.5 top-1/2 -translate-y-1/2 text-slate-400" />
        <input className="input pl-8" value={q} onChange={(e) => onQ(e.target.value)} placeholder={placeholder} aria-label="Search list" />
        {q && <button type="button" onClick={() => onQ("")} className="absolute right-2 top-1/2 -translate-y-1/2 text-slate-400" aria-label="Clear"><X size={14} /></button>}
      </div>
      {onFrom && <input type="date" className="input w-auto" value={from ?? ""} onChange={(e) => onFrom(e.target.value)} aria-label="From" />}
      {onTo && <input type="date" className="input w-auto" value={to ?? ""} onChange={(e) => onTo(e.target.value)} aria-label="To" />}
      {chips && chips.length > 0 && (
        <div className="flex flex-wrap gap-1">
          {chips.map((c) => (
            <button type="button" key={c.k} onClick={() => onChip?.(c.k)} aria-pressed={chip === c.k}
              className={`rounded-full px-2.5 py-1 text-xs font-semibold ${chip === c.k ? "bg-brand-600 text-white" : "bg-slate-100 text-slate-600 hover:bg-slate-200"}`}>
              {c.label}{c.n != null ? ` ${c.n}` : ""}
            </button>
          ))}
        </div>
      )}
      {right}
      {shown != null && total != null && shown !== total && <span className="text-xs text-slate-500">{shown} / {total}</span>}
    </div>
  );
}

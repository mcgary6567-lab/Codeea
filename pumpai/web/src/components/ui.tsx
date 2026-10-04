import { createContext, useCallback, useContext, useState, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { X, CheckCircle2, AlertTriangle, Loader2 } from "lucide-react";

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
      <div className="mt-2 text-2xl font-semibold tabular-nums text-slate-900">{value}</div>
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
        <div className="mb-4 flex items-center justify-between">
          <h2 className="text-lg font-semibold">{title}</h2>
          <button onClick={onClose} className="rounded p-1 text-slate-400 hover:bg-slate-100" aria-label="Close"><X size={18} /></button>
        </div>
        {children}
      </div>
    </div>,
    document.body,
  );
}

export const Field = ({ label, children }: { label: string; children: ReactNode }) => (
  <label className="block"><span className="label">{label}</span>{children}</label>
);

export const Spinner = () => <Loader2 className="animate-spin text-slate-400" size={20} />;
export const Loading = () => <div className="flex h-40 items-center justify-center"><Spinner /></div>;
export const Empty = ({ children }: { children: ReactNode }) => <div className="py-10 text-center text-sm text-slate-500">{children}</div>;
export const ErrorBox = ({ error }: { error: string }) => <div className="rounded-lg border border-red-200 bg-red-50 p-3 text-sm text-red-700">{error}</div>;

type Toast = { id: number; kind: "ok" | "err"; text: string };
const ToastCtx = createContext<(kind: Toast["kind"], text: string) => void>(() => {});
export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([]);
  const push = useCallback((kind: Toast["kind"], text: string) => {
    const id = Date.now() + Math.random();
    setToasts((t) => [...t, { id, kind, text }]);
    setTimeout(() => setToasts((t) => t.filter((x) => x.id !== id)), 4000);
  }, []);
  return (
    <ToastCtx.Provider value={push}>
      {children}
      <div className="fixed bottom-4 right-4 z-[60] space-y-2">
        {toasts.map((t) => (
          <div key={t.id} className={`flex max-w-sm items-start gap-2 rounded-lg px-4 py-3 text-sm text-white shadow-lg ${t.kind === "ok" ? "bg-slate-900" : "bg-red-600"}`}>
            {t.kind === "ok" ? <CheckCircle2 size={18} className="shrink-0 text-emerald-400" /> : <AlertTriangle size={18} className="shrink-0" />}
            <span>{t.text}</span>
          </div>
        ))}
      </div>
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

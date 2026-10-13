import { useEffect, useState } from "react";
import { MessageCircle, Download, Check, X, Send } from "lucide-react";
import { api, linkToken } from "../lib/api";
import { Modal, useAction } from "./ui";

type Kind = "voucher" | "khata" | "wholesale" | "carriage" | "supplier" | "shift" | "receipt" | "slip";
const Ur = ({ children }: { children: React.ReactNode }) => <span lang="ur" dir="rtl" className="font-urdu">{children}</span>;
const fmt = (p: string) => (p.startsWith("92") && p.length === 12 ? `0${p.slice(2, 5)} ${p.slice(5)}` : p);
const ROLE: Record<string, string> = { owner: "Owner", admin: "CEO", manager: "Manager", cashier: "Cashier", salesman: "Salesman", wholesale: "Wholesale" };

/**
 * "WhatsApp" next to every Print: the document goes as a PDF (signed public link). Pick the party, the pump's own people,
 * a recent number, or type one. With the Cloud API set up it is sent from the server; otherwise the phone's WhatsApp opens
 * with the message + link (wa.me) — works on any pump with no setup.
 */
export function WhatsAppDoc({ kind, id, from, to, label = "WhatsApp", className = "btn-secondary", small = false }: { kind: Kind; id: number; from?: string; to?: string; label?: string; className?: string; small?: boolean }) {
  const [open, setOpen] = useState(false);
  return (
    <>
      <button type="button" className={`${className} ${small ? "!px-2 !py-1 text-xs" : ""}`} onClick={() => setOpen(true)} title="Send as PDF on WhatsApp · واٹس ایپ پر بھیجیں">
        <MessageCircle size={small ? 13 : 15} /> {label}{!small && <> · <Ur>واٹس ایپ</Ur></>}
      </button>
      {open && <WhatsAppDocModal kind={kind} id={id} from={from} to={to} onClose={() => setOpen(false)} />}
    </>
  );
}

export function WhatsAppDocModal({ kind, id, from, to, onClose }: { kind: Kind; id: number; from?: string; to?: string; onClose: () => void }) {
  const qs = new URLSearchParams(Object.entries({ from, to }).filter(([, v]) => v) as [string, string][]).toString();
  const [c, setC] = useState<any>(null);
  const [err, setErr] = useState("");
  const [sel, setSel] = useState<string[]>([]);
  const [manual, setManual] = useState("");
  const [note, setNote] = useState("");
  const [result, setResult] = useState<any>(null);
  const { busy, run } = useAction();
  useEffect(() => {
    api<any>(`/docs/${kind}/${id}/contacts${qs ? `?${qs}` : ""}`).then((d) => { setC(d); if (d.party?.phone) setSel([d.party.phone]); }).catch((e) => setErr(e.message));
  }, [kind, id, qs]);
  const toggle = (p: string) => setSel((s) => (s.includes(p) ? s.filter((x) => x !== p) : [...s, p]));
  const phones = [...sel, ...manual.split(/[,\n;]/).map((x) => x.trim()).filter((x) => x.length >= 7)];
  const send = async () => {
    const r = await run(() => api(`/docs/${kind}/${id}/send`, { body: { phones, note: note || undefined, from, to } }), (x: any) => x.mode === "api" ? `Sent to ${x.results.filter((y: any) => y.ok).length} number(s) · بھیج دیا` : "WhatsApp khul raha hai · واٹس ایپ کھل رہا ہے");
    if (!r) return;
    setResult(r);
    // phone mode: open WhatsApp for the first number right away (the rest have buttons below)
    const first = r.results.find((x: any) => x.ok && x.wa_link);
    if (first) window.open(first.wa_link, "_blank", "noopener");
  };
  const pdfUrl = `/api/docs/${kind}/${id}/pdf?${qs ? `${qs}&` : ""}token=${linkToken()}`;
  const Chip = ({ phone, name, sub }: { phone: string; name: string | null; sub?: string }) => (
    <button type="button" onClick={() => toggle(phone)} aria-pressed={sel.includes(phone)}
      className={`flex items-center gap-2 rounded-xl px-3 py-2 text-left text-sm ring-1 ${sel.includes(phone) ? "bg-emerald-600 text-white ring-emerald-600" : "bg-white text-slate-700 ring-slate-200 hover:bg-slate-50"}`}>
      {sel.includes(phone) ? <Check size={15} /> : <MessageCircle size={15} className="opacity-50" />}
      <span className="min-w-0"><span className="block truncate font-medium">{name || fmt(phone)}</span>{(name || sub) && <span className={`block text-xs ${sel.includes(phone) ? "text-emerald-100" : "text-slate-500"}`}>{[sub, name ? fmt(phone) : ""].filter(Boolean).join(" · ")}</span>}</span>
    </button>
  );
  return (
    <Modal open onClose={onClose} title={c ? `WhatsApp: ${c.title}` : "WhatsApp · واٹس ایپ"}>
      {err ? <p className="rounded-lg bg-red-50 p-3 text-sm text-red-700">{err}</p> : !c ? <p className="py-6 text-center text-sm text-slate-500">Preparing the PDF…</p> : result ? (
        <div className="space-y-3">
          <div className="rounded-xl bg-emerald-50 p-3 text-sm text-emerald-900">{result.mode === "api" ? "PDF WhatsApp par bhej diya gaya." : "Har number ke liye WhatsApp kholein aur Send dabayen (PDF link message mein hai)."}</div>
          <ul className="divide-y divide-slate-100 rounded-xl ring-1 ring-slate-200">
            {result.results.map((r: any, i: number) => (
              <li key={i} className="flex items-center gap-2 px-3 py-2 text-sm">
                <span className="min-w-0 flex-1"><b>{r.name || fmt(r.phone)}</b>{r.name && <span className="text-slate-500"> · {fmt(r.phone)}</span>}{!r.ok && <span className="block text-xs text-red-600">{r.error}</span>}</span>
                {r.ok && r.channel === "api" && <span className="text-xs font-semibold text-emerald-700">✓ sent</span>}
                {r.ok && r.wa_link && <a className="btn-primary !py-1 text-xs" href={r.wa_link} target="_blank" rel="noreferrer"><Send size={13} /> Open WhatsApp</a>}
              </li>
            ))}
          </ul>
          <div className="flex flex-wrap justify-end gap-2"><a className="btn-secondary" href={pdfUrl} target="_blank" rel="noreferrer"><Download size={15} /> PDF</a><button className="btn-primary" onClick={onClose}>Done</button></div>
        </div>
      ) : (
        <div className="space-y-3">
          <p className="text-xs text-slate-500">{c.api ? "PDF seedha WhatsApp par bhejega (Cloud API)." : "Aap ke phone / computer ka WhatsApp khulega, message aur PDF link ke saath — bas Send dabayen."} · <Ur>نمبر چنیں</Ur></p>
          {c.party && <div><div className="label">Party · <Ur>پارٹی</Ur></div><div className="flex flex-wrap gap-2"><Chip phone={c.party.phone} name={c.party.name} /></div></div>}
          {c.staff?.length > 0 && <div><div className="label">Pump ke log · <Ur>اپنے لوگ</Ur></div><div className="grid grid-cols-2 gap-2">{c.staff.map((s: any) => <Chip key={s.phone} phone={s.phone} name={s.name} sub={ROLE[s.role] ?? s.role} />)}</div></div>}
          {c.recent?.filter((r: any) => r.phone !== c.party?.phone && !c.staff.some((s: any) => s.phone === r.phone)).length > 0 && <div><div className="label">Recent · <Ur>حالیہ</Ur></div><div className="flex flex-wrap gap-2">{c.recent.filter((r: any) => r.phone !== c.party?.phone && !c.staff.some((s: any) => s.phone === r.phone)).map((r: any) => <Chip key={r.phone} phone={r.phone} name={r.name} />)}</div></div>}
          <label className="block"><span className="label">Koi aur number · <Ur>دوسرا نمبر</Ur></span><input className="input" inputMode="tel" placeholder="0300 1234567 (comma se kai)" value={manual} onChange={(e) => setManual(e.target.value)} /></label>
          <label className="block"><span className="label">Note (optional)</span><input className="input" maxLength={300} placeholder="e.g. Aaj ka voucher, check kar lein" value={note} onChange={(e) => setNote(e.target.value)} /></label>
          {c.sent?.length > 0 && <p className="text-xs text-slate-500">Pehle bheja: {c.sent.slice(0, 3).map((s: any) => `${s.to_name || fmt(s.to_phone)} (${new Date(s.created_at).toLocaleString("en-PK", { dateStyle: "short", timeStyle: "short" })})`).join(", ")}</p>}
          <div className="flex flex-wrap items-center justify-end gap-2">
            <a className="btn-secondary mr-auto" href={pdfUrl} target="_blank" rel="noreferrer"><Download size={15} /> PDF</a>
            <button className="btn-secondary" onClick={onClose}><X size={15} /> Cancel</button>
            <button className="btn-primary" disabled={busy || !phones.length} onClick={send}><Send size={15} /> Send{phones.length > 1 ? ` (${phones.length})` : ""} · <Ur>بھیجیں</Ur></button>
          </div>
        </div>
      )}
    </Modal>
  );
}

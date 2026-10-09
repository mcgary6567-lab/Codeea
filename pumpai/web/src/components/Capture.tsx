import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { Camera, Loader2, Mic, MicOff, X, ChevronLeft, ChevronRight, Trash2, ZoomIn, Download } from "lucide-react";
import { api, linkToken } from "../lib/api";
import { enhanceImage } from "../lib/enhance";
import { useAuth } from "../App";
import { Modal, useToast } from "./ui";

/** Document kinds get straightened + margin-trimmed; camera selfies / meters only get brightened. */
const DOC_KINDS = new Set(["invoice", "receipt", "bill", "slip", "proof"]);

/** Clean up + shrink a camera photo before upload (fix sideways/tilt, brighten, sharpen, crop). */
export async function resizeImage(file: File, max = 1600, doc = true): Promise<string> {
  return enhanceImage(file, { max, doc });
}

export const photoUrl = (id: number) => `/api/photos/${id}?token=${linkToken()}`;

/* -------------------- Full-screen photo viewer (lightbox) -------------------- */

type LbReq = { ids: number[]; index: number; onDeleted?: (id: number) => void };
let lbEmit: ((r: LbReq | null) => void) | null = null;

/**
 * Open the full-screen photo viewer. Pass one id, a CSV ("12,13") or a list; everyone can view,
 * only the CEO/admin sees a delete button. `onDeleted` lets the caller drop the id from its row.
 */
export function openPhoto(ids: number | string | Array<number | string | null | undefined> | null | undefined, index = 0, onDeleted?: (id: number) => void) {
  const list = (Array.isArray(ids) ? ids : String(ids ?? "").split(",")).map(Number).filter(Boolean);
  if (list.length && lbEmit) lbEmit({ ids: list, index: Math.max(0, Math.min(index, list.length - 1)), onDeleted });
}

/** Mounted once (in App): shows the enlarged picture with next/prev and an admin-only delete. */
export function LightboxHost() {
  const [req, setReq] = useState<LbReq | null>(null);
  const [i, setI] = useState(0);
  const [busy, setBusy] = useState(false);
  const { can } = useAuth();
  const toast = useToast();
  useEffect(() => {
    lbEmit = (r) => { setReq(r); setI(r?.index ?? 0); };
    return () => { lbEmit = null; };
  }, []);
  useEffect(() => {
    if (!req) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setReq(null);
      else if (e.key === "ArrowRight") setI((x) => Math.min(x + 1, req.ids.length - 1));
      else if (e.key === "ArrowLeft") setI((x) => Math.max(x - 1, 0));
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [req]);
  if (!req) return null;
  const id = req.ids[i];
  const many = req.ids.length > 1;
  const del = async () => {
    if (busy || !confirm("Delete this photo for everyone? This cannot be undone. · تصویر مستقل طور پر حذف کریں؟")) return;
    setBusy(true);
    try {
      await api(`/photos/${id}`, { method: "DELETE" });
      req.onDeleted?.(id);
      const left = req.ids.filter((x) => x !== id);
      toast("ok", "Photo deleted");
      if (!left.length) setReq(null);
      else { setReq({ ...req, ids: left }); setI((x) => Math.min(x, left.length - 1)); }
    } catch (e: any) { toast("err", e.message); }
    finally { setBusy(false); }
  };
  return createPortal(
    <div className="fixed inset-0 z-[70] flex flex-col bg-black/90 print:hidden" onClick={() => setReq(null)}>
      <div className="flex items-center justify-end gap-2 p-3" onClick={(e) => e.stopPropagation()}>
        {many && <span className="mr-auto rounded-full bg-white/15 px-3 py-1 text-sm font-medium text-white">{i + 1} / {req.ids.length}</span>}
        <a href={photoUrl(id)} target="_blank" rel="noreferrer" title="Open / download" className="flex h-11 w-11 items-center justify-center rounded-full bg-white/15 text-white active:scale-95"><Download size={20} /></a>
        {can("photos.delete") && (
          <button type="button" onClick={del} disabled={busy} title="Delete photo (CEO only)" className="flex h-11 w-11 items-center justify-center rounded-full bg-red-600 text-white active:scale-95 disabled:opacity-50">
            {busy ? <Loader2 size={20} className="animate-spin" /> : <Trash2 size={20} />}
          </button>
        )}
        <button type="button" onClick={() => setReq(null)} aria-label="Close" className="flex h-11 w-11 items-center justify-center rounded-full bg-white/15 text-white active:scale-95"><X size={22} /></button>
      </div>
      <div className="relative flex flex-1 items-center justify-center overflow-hidden p-2" onClick={(e) => e.stopPropagation()}>
        {many && i > 0 && <button type="button" onClick={() => setI(i - 1)} aria-label="Previous" className="absolute left-2 flex h-12 w-12 items-center justify-center rounded-full bg-white/15 text-white active:scale-95"><ChevronLeft size={26} /></button>}
        <img src={photoUrl(id)} alt="Photo" className="max-h-full max-w-full rounded-lg object-contain" />
        {many && i < req.ids.length - 1 && <button type="button" onClick={() => setI(i + 1)} aria-label="Next" className="absolute right-2 flex h-12 w-12 items-center justify-center rounded-full bg-white/15 text-white active:scale-95"><ChevronRight size={26} /></button>}
      </div>
    </div>,
    document.body,
  );
}

/** A photo thumbnail: hover shows a zoom (+) badge; click opens the full-screen viewer. */
export function PhotoThumb({ id, group, size = 10, onDeleted, className = "" }: {
  id: number; group?: Array<number | string> | string; size?: number; onDeleted?: (id: number) => void; className?: string;
}) {
  const [gone, setGone] = useState(false);
  const ids = group ?? [id];
  const list = (Array.isArray(ids) ? ids : String(ids).split(",")).map(Number).filter(Boolean);
  const idx = Math.max(0, list.indexOf(id));
  if (gone) return null; // the photo was deleted or is missing — hide the thumbnail
  return (
    <button type="button" onClick={() => openPhoto(list, idx, onDeleted)} title="Tap to enlarge · بڑا کریں"
      className={`group relative inline-flex shrink-0 overflow-hidden rounded-lg border border-slate-200 align-middle active:scale-95 ${className}`} style={{ height: `${size * 0.25}rem`, width: `${size * 0.25}rem` }}>
      <img src={photoUrl(id)} alt="Photo" className="h-full w-full object-cover" onError={() => setGone(true)} />
      <span className="absolute inset-0 hidden items-center justify-center bg-black/35 text-white group-hover:flex"><ZoomIn size={Math.max(14, size * 1.4)} /></span>
    </button>
  );
}

/**
 * Camera button: take a photo of a meter, tanker invoice or receipt. The photo is kept as proof
 * and, when AI is on, the numbers come back in `onRead`.
 */
export function PhotoButton({ kind, hint, onRead, label = "Photo", big, className = "" }: {
  kind: "meter" | "invoice" | "receipt" | "bill" | "slip" | "payment" | "proof" | "selfie"; hint?: string; label?: string; big?: boolean; className?: string;
  onRead: (result: any | null, photoId: number) => void;
}) {
  const input = useRef<HTMLInputElement>(null);
  const [busy, setBusy] = useState(false);
  const toast = useToast();
  const pick = async (f: File | undefined) => {
    if (!f) return;
    setBusy(true);
    try {
      const image = await resizeImage(f, 1600, DOC_KINDS.has(kind));
      const r = await api("/ai/read-photo", { body: { kind, image, hint } });
      if (r.message) toast(r.ai ? "ok" : "err", r.message);
      onRead(r.result, r.photo_id);
    } catch (e: any) { toast("err", e.message); }
    finally { setBusy(false); if (input.current) input.current.value = ""; }
  };
  return (
    <>
      <input ref={input} type="file" accept="image/*" capture="environment" className="hidden" onChange={(e) => pick(e.target.files?.[0])} />
      <button type="button" onClick={() => input.current?.click()} disabled={busy} aria-label={`${label} — take a photo`}
        className={`inline-flex items-center justify-center gap-1.5 rounded-xl bg-sky-600 font-semibold text-white active:scale-95 disabled:bg-slate-400 ${big ? "px-4 py-3 text-lg" : "px-3 py-2 text-sm"} ${className}`}>
        {busy ? <Loader2 className="animate-spin" size={big ? 22 : 16} /> : <Camera size={big ? 22 : 16} />}{busy ? "Reading…" : label}
      </button>
    </>
  );
}

type SR = { lang: string; interimResults: boolean; maxAlternatives: number; start: () => void; stop: () => void; onresult: (e: any) => void; onerror: (e: any) => void; onend: () => void };
export const Recognition: (new () => SR) | undefined = (window as any).SpeechRecognition ?? (window as any).webkitSpeechRecognition;

/**
 * Speak the sale ("police station kahna 20 litre diesel slip 7781"); the parsed sale comes back in
 * `onParsed` to fill the POS. Without speech support (or if it fails) the sentence can be typed.
 */
export function VoiceButton({ onParsed, className = "" }: { onParsed: (sale: any) => void; className?: string }) {
  const [listening, setListening] = useState(false);
  const [busy, setBusy] = useState(false);
  const [typing, setTyping] = useState(false);
  const [text, setText] = useState("");
  const [lang, setLang] = useState<"ur-PK" | "en-PK">("ur-PK");
  const rec = useRef<SR | null>(null);
  const toast = useToast();
  const parse = async (said: string) => {
    if (!said.trim()) return;
    setBusy(true);
    try { onParsed(await api("/ai/parse-sale", { body: { text: said } })); setTyping(false); setText(""); }
    catch (e: any) { toast("err", e.message); }
    finally { setBusy(false); }
  };
  const listen = () => {
    if (!Recognition) return setTyping(true);
    if (listening) return rec.current?.stop();
    const r = new Recognition();
    r.lang = lang; r.interimResults = false; r.maxAlternatives = 1;
    r.onresult = (e) => parse(e.results[0][0].transcript);
    r.onerror = (e) => { if (e.error !== "no-speech" && e.error !== "aborted") { toast("err", "Could not hear clearly — type it instead"); setTyping(true); } };
    r.onend = () => setListening(false);
    rec.current = r;
    setListening(true);
    r.start();
  };
  return (
    <>
      <div className={`flex items-stretch gap-1 ${className}`}>
        <button type="button" onClick={listen} disabled={busy}
          className={`flex min-w-0 flex-1 items-center justify-center gap-2 whitespace-nowrap rounded-2xl px-3 py-3 text-base font-bold sm:px-4 sm:text-lg text-white shadow active:scale-95 ${listening ? "animate-pulse bg-red-600" : "bg-violet-600"}`}>
          {busy ? <Loader2 className="animate-spin" /> : listening ? <MicOff /> : <Mic />}
          {listening ? "Listening… tap to stop" : busy ? "Understanding…" : <>Speak the sale · <span lang="ur" dir="rtl" className="font-urdu font-normal">بول کر</span></>}
        </button>
        {Recognition && !listening && (
          <button type="button" onClick={() => setLang(lang === "ur-PK" ? "en-PK" : "ur-PK")} title="Speech language"
            className="rounded-2xl bg-violet-100 px-3 text-sm font-semibold text-violet-800">{lang === "ur-PK" ? "اردو" : "Eng"}</button>
        )}
        <button type="button" onClick={() => setTyping(true)} className="rounded-2xl bg-violet-100 px-3 text-sm font-semibold text-violet-800">Type<span lang="ur" dir="rtl" className="font-urdu block text-xs font-normal">لکھیں</span></button>
      </div>
      <Modal open={typing} onClose={() => setTyping(false)} title="Say or type the sale · بول کر یا لکھ کر">
        <form className="space-y-3" onSubmit={(e) => { e.preventDefault(); parse(text); }}>
          <input autoFocus className="input py-3 text-lg" value={text} onChange={(e) => setText(e.target.value)} placeholder="e.g. police station kahna 20 litre diesel slip 7781" />
          <p className="text-xs text-slate-500">Examples: “do hazar petrol easypaisa” · “diesel 30 litre rehman transport” · “پیٹرول 500 روپے نقد” · expense: “chai ka kharcha 300” · “جنریٹر کا خرچہ دو ہزار”</p>
          <div className="flex justify-end gap-2"><button type="button" className="btn-secondary" onClick={() => setTyping(false)}>Cancel · منسوخ</button><button className="btn-primary" disabled={busy}>Fill the sale · بھریں</button></div>
        </form>
      </Modal>
    </>
  );
}

/**
 * Photo proof for any entry: cheque, cash receipt, bank slip, invoice, signed chalan…
 * Several photos can be added (camera or gallery); each is kept and linked to the saved entry.
 */
export function ProofPhotos({ value, onChange, label = "Photo proof", hint = "cheque, receipt, slip, invoice", required }: {
  value: number[]; onChange: (ids: number[]) => void; label?: string; hint?: string; required?: boolean;
}) {
  const input = useRef<HTMLInputElement>(null);
  const [busy, setBusy] = useState(false);
  const toast = useToast();
  const add = async (files: FileList | null) => {
    if (!files?.length) return;
    setBusy(true);
    try {
      const ids: number[] = [];
      for (const f of Array.from(files).slice(0, 10 - value.length)) {
        const r = await api("/ai/read-photo", { body: { kind: "proof", image: await resizeImage(f, 1600, true) } });
        ids.push(r.photo_id);
      }
      onChange([...value, ...ids]);
    } catch (e: any) { toast("err", e.message); }
    finally { setBusy(false); if (input.current) input.current.value = ""; }
  };
  return (
    <div>
      <span className="label">{label}{required && <span className="text-red-600"> * required</span>} <span className="font-normal text-slate-400">· {hint}</span></span>
      <div className="flex flex-wrap items-center gap-2">
        {value.map((id) => (
          <span key={id} className="relative">
            <PhotoThumb id={id} group={value} size={14} onDeleted={(d) => onChange(value.filter((x) => x !== d))} />
            <button type="button" onClick={() => onChange(value.filter((x) => x !== id))} aria-label="Remove photo"
              className="absolute -right-1.5 -top-1.5 flex h-5 w-5 items-center justify-center rounded-full bg-slate-800 text-xs text-white">×</button>
          </span>
        ))}
        {value.length < 10 && (
          <button type="button" disabled={busy} onClick={() => input.current?.click()}
            className="flex h-14 items-center gap-1.5 rounded-lg border-2 border-dashed border-slate-300 px-3 text-sm font-medium text-slate-600 hover:border-brand-500 hover:text-brand-700">
            {busy ? <Loader2 size={16} className="animate-spin" /> : <Camera size={16} />}{value.length ? "Add more" : "Take / add photo"}
          </button>
        )}
      </div>
      {required && !value.length && <p className="mt-1 text-xs font-medium text-red-600">Take a photo before saving · تصویر لازمی ہے</p>}
      <input ref={input} type="file" accept="image/*" multiple className="hidden" onChange={(e) => add(e.target.files)} />
    </div>
  );
}

/** Small photo thumbnails for a list row ("proof_ids" = "12,13" from the server). Tap to enlarge. */
export function ProofThumbs({ ids, size = 8, onChanged }: { ids?: string | number[] | null; size?: number; onChanged?: () => void }) {
  const list = (Array.isArray(ids) ? ids : String(ids ?? "").split(",")).map(Number).filter(Boolean);
  if (!list.length) return null;
  return (
    <span className="inline-flex flex-wrap gap-1 align-middle">
      {list.map((id) => <PhotoThumb key={id} id={id} group={list} size={size} onDeleted={onChanged} />)}
    </span>
  );
}

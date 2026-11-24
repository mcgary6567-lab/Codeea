import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { Camera, Loader2, Mic, MicOff, X, ChevronLeft, ChevronRight, Trash2, ZoomIn, Download, Image as ImageIcon, Zap, ZapOff, ScanLine } from "lucide-react";
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

/* -------------------- Scan camera: align digits in a box, capture only that box -------------------- */

/**
 * A live camera with a card-scanner style guide box. The meter's totalizer is lined up inside the
 * rectangle and only that rectangle is captured — so nearby digits (price, amount, other nozzles)
 * are not read by mistake. Falls back to the normal camera/gallery when a live stream isn't allowed.
 */
export function ScanCamera({ open, title, hintUr, aspect = 3.2, onCapture, onClose, onFallback }: {
  open: boolean; title: string; hintUr: string; aspect?: number;
  onCapture: (file: File) => void; onClose: () => void; onFallback: () => void;
}) {
  const videoRef = useRef<HTMLVideoElement>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const boxRef = useRef<HTMLDivElement>(null);
  const [ready, setReady] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [torch, setTorch] = useState<boolean | null>(null); // null = not supported
  const toast = useToast();

  useEffect(() => {
    if (!open) return;
    let cancelled = false;
    setReady(false); setErr(null); setTorch(null);
    (async () => {
      try {
        if (!navigator.mediaDevices?.getUserMedia) throw new Error("no-camera");
        const stream = await navigator.mediaDevices.getUserMedia({
          video: { facingMode: { ideal: "environment" }, width: { ideal: 1920 }, height: { ideal: 1080 } }, audio: false,
        });
        if (cancelled) { stream.getTracks().forEach((t) => t.stop()); return; }
        streamRef.current = stream;
        const v = videoRef.current;
        if (v) { v.srcObject = stream; await v.play().catch(() => {}); }
        const caps: any = stream.getVideoTracks()[0]?.getCapabilities?.() ?? {};
        setTorch("torch" in caps ? false : null);
        setReady(true);
      } catch {
        if (!cancelled) setErr("camera");
      }
    })();
    return () => { cancelled = true; streamRef.current?.getTracks().forEach((t) => t.stop()); streamRef.current = null; };
  }, [open]);

  const toggleTorch = async () => {
    const track = streamRef.current?.getVideoTracks()[0];
    if (!track) return;
    try { const next = !torch; await track.applyConstraints({ advanced: [{ torch: next } as any] }); setTorch(next); }
    catch { toast("err", "Flash not available"); }
  };

  const snap = () => {
    const v = videoRef.current, box = boxRef.current;
    if (!v || !box || !v.videoWidth) return;
    // the video is shown object-contain, so the on-screen box maps 1:1 (by fraction) onto the source frame
    const vw = v.videoWidth, vh = v.videoHeight;
    const rectV = v.getBoundingClientRect(), rectB = box.getBoundingClientRect();
    // size of the actually-painted video inside the <video> element (letterboxed)
    const scale = Math.min(rectV.width / vw, rectV.height / vh);
    const paintW = vw * scale, paintH = vh * scale;
    const padX = (rectV.width - paintW) / 2, padY = (rectV.height - paintH) / 2;
    const sx = Math.max(0, ((rectB.left - rectV.left - padX) / paintW) * vw);
    const sy = Math.max(0, ((rectB.top - rectV.top - padY) / paintH) * vh);
    const sw = Math.min(vw - sx, (rectB.width / paintW) * vw);
    const sh = Math.min(vh - sy, (rectB.height / paintH) * vh);
    const canvas = document.createElement("canvas");
    canvas.width = Math.round(sw); canvas.height = Math.round(sh);
    const ctx = canvas.getContext("2d");
    if (!ctx) return;
    ctx.drawImage(v, sx, sy, sw, sh, 0, 0, canvas.width, canvas.height);
    canvas.toBlob((blob) => { if (blob) onCapture(new File([blob], "meter.jpg", { type: "image/jpeg" })); }, "image/jpeg", 0.95);
  };

  if (!open) return null;
  return createPortal(
    <div className="fixed inset-0 z-[80] flex flex-col bg-black">
      <div className="flex items-center gap-2 px-4 pt-4 pb-2 text-white">
        <ScanLine size={20} className="shrink-0 text-emerald-400" />
        <div className="min-w-0"><div className="truncate text-sm font-bold">{title}</div><div lang="ur" dir="rtl" className="font-urdu text-xs text-emerald-200">{hintUr}</div></div>
        <button type="button" onClick={onClose} aria-label="Close" className="ml-auto flex h-10 w-10 items-center justify-center rounded-full bg-white/15 active:scale-95"><X size={20} /></button>
      </div>

      <div className="relative flex-1 overflow-hidden">
        {err ? (
          <div className="flex h-full flex-col items-center justify-center gap-3 p-6 text-center text-white">
            <Camera size={40} className="text-slate-400" />
            <p className="text-sm">Live camera nahi khul paya — <span lang="ur" dir="rtl" className="font-urdu">کیمرہ کی اجازت دیں یا نیچے سے عام کیمرہ/گیلری سے تصویر لیں۔</span></p>
            <button type="button" onClick={onFallback} className="rounded-xl bg-sky-600 px-4 py-2 text-sm font-semibold">Normal camera / gallery</button>
          </div>
        ) : (
          <>
            <video ref={videoRef} playsInline muted className="h-full w-full object-contain" />
            {!ready && <div className="absolute inset-0 flex items-center justify-center text-white"><Loader2 className="animate-spin" size={28} /></div>}
            {/* guide box */}
            <div ref={boxRef} className="pointer-events-none absolute left-1/2 top-1/2 -translate-x-1/2 -translate-y-1/2"
              style={{ width: "82%", aspectRatio: String(aspect), boxShadow: "0 0 0 9999px rgba(0,0,0,0.55)", borderRadius: 14 }}>
              <div className="absolute inset-0 rounded-[14px] ring-2 ring-emerald-400/90" />
              <span className="absolute -left-0.5 -top-0.5 h-6 w-6 rounded-tl-[14px] border-l-4 border-t-4 border-emerald-300" />
              <span className="absolute -right-0.5 -top-0.5 h-6 w-6 rounded-tr-[14px] border-r-4 border-t-4 border-emerald-300" />
              <span className="absolute -bottom-0.5 -left-0.5 h-6 w-6 rounded-bl-[14px] border-b-4 border-l-4 border-emerald-300" />
              <span className="absolute -bottom-0.5 -right-0.5 h-6 w-6 rounded-br-[14px] border-b-4 border-r-4 border-emerald-300" />
            </div>
            <div className="absolute inset-x-0 bottom-28 text-center text-sm font-medium text-white drop-shadow">
              Meter ke digits box ke andar rakhein · <span lang="ur" dir="rtl" className="font-urdu">میٹر کے ہندسے خانے میں رکھیں</span>
            </div>
          </>
        )}
      </div>

      {!err && (
        <div className="flex items-center justify-center gap-10 px-6 pb-8 pt-3">
          <button type="button" onClick={onFallback} aria-label="Gallery / normal camera" className="flex h-12 w-12 items-center justify-center rounded-full bg-white/15 text-white active:scale-95"><ImageIcon size={22} /></button>
          <button type="button" onClick={snap} disabled={!ready} aria-label="Capture" className="flex h-[76px] w-[76px] items-center justify-center rounded-full bg-white ring-4 ring-white/40 active:scale-95 disabled:opacity-50">
            <span className="h-[58px] w-[58px] rounded-full bg-emerald-500" />
          </button>
          {torch !== null
            ? <button type="button" onClick={toggleTorch} aria-label="Flash" className={`flex h-12 w-12 items-center justify-center rounded-full active:scale-95 ${torch ? "bg-amber-400 text-black" : "bg-white/15 text-white"}`}>{torch ? <Zap size={22} /> : <ZapOff size={22} />}</button>
            : <span className="h-12 w-12" />}
        </div>
      )}
    </div>,
    document.body,
  );
}

/**
 * Camera button: take a photo of a meter, tanker invoice or receipt. The photo is kept as proof
 * and, when AI is on, the numbers come back in `onRead`. For a meter it opens a card-scanner style
 * guide box so only the totalizer digits are captured.
 */
export function PhotoButton({ kind, hint, onRead, label = "Photo", big, className = "" }: {
  kind: "meter" | "invoice" | "receipt" | "bill" | "slip" | "payment" | "proof" | "selfie"; hint?: string; label?: string; big?: boolean; className?: string;
  onRead: (result: any | null, photoId: number) => void;
}) {
  const input = useRef<HTMLInputElement>(null);
  const [busy, setBusy] = useState(false);
  const [scan, setScan] = useState(false);
  const scannable = kind === "meter";
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
      <input ref={input} type="file" accept="image/*" {...(scannable ? {} : { capture: "environment" as const })} className="hidden" onChange={(e) => pick(e.target.files?.[0])} />
      <button type="button" onClick={() => (scannable ? setScan(true) : input.current?.click())} disabled={busy} aria-label={`${label} — take a photo`}
        className={`inline-flex items-center justify-center gap-1.5 rounded-xl bg-sky-600 font-semibold text-white active:scale-95 disabled:bg-slate-400 ${big ? "px-4 py-3 text-lg" : "px-3 py-2 text-sm"} ${className}`}>
        {busy ? <Loader2 className="animate-spin" size={big ? 22 : 16} /> : scannable ? <ScanLine size={big ? 22 : 16} /> : <Camera size={big ? 22 : 16} />}{busy ? "Reading…" : scannable ? "Scan meter" : label}
      </button>
      {scannable && (
        <ScanCamera open={scan} title="Scan the meter totalizer" hintUr="میٹر کا ٹوٹلائزر خانے میں رکھ کر بٹن دبائیں"
          onClose={() => setScan(false)}
          onFallback={() => { setScan(false); input.current?.click(); }}
          onCapture={(f) => { setScan(false); pick(f); }} />
      )}
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

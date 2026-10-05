import { useRef, useState } from "react";
import { Camera, Loader2, Mic, MicOff } from "lucide-react";
import { api, linkToken } from "../lib/api";
import { Modal, useToast } from "./ui";

/** Shrink a camera photo before upload (phones take 4–12 MB pictures). */
export async function resizeImage(file: File, max = 1600): Promise<string> {
  const url = URL.createObjectURL(file);
  try {
    const img = await new Promise<HTMLImageElement>((res, rej) => { const i = new Image(); i.onload = () => res(i); i.onerror = rej; i.src = url; });
    const scale = Math.min(1, max / Math.max(img.width, img.height));
    const c = document.createElement("canvas");
    c.width = Math.round(img.width * scale); c.height = Math.round(img.height * scale);
    c.getContext("2d")!.drawImage(img, 0, 0, c.width, c.height);
    return c.toDataURL("image/jpeg", 0.82);
  } finally { URL.revokeObjectURL(url); }
}

export const photoUrl = (id: number) => `/api/photos/${id}?token=${linkToken()}`;

/**
 * Camera button: take a photo of a meter, tanker invoice or receipt. The photo is kept as proof
 * and, when AI is on, the numbers come back in `onRead`.
 */
export function PhotoButton({ kind, hint, onRead, label = "Photo", big, className = "" }: {
  kind: "meter" | "invoice" | "receipt" | "bill" | "proof" | "selfie"; hint?: string; label?: string; big?: boolean; className?: string;
  onRead: (result: any | null, photoId: number) => void;
}) {
  const input = useRef<HTMLInputElement>(null);
  const [busy, setBusy] = useState(false);
  const toast = useToast();
  const pick = async (f: File | undefined) => {
    if (!f) return;
    setBusy(true);
    try {
      const image = await resizeImage(f);
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
        const r = await api("/ai/read-photo", { body: { kind: "proof", image: await resizeImage(f) } });
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
            <a href={photoUrl(id)} target="_blank" rel="noreferrer"><img src={photoUrl(id)} alt="Proof" className="h-14 w-14 rounded-lg border border-slate-200 object-cover" /></a>
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

/** Small photo links for a list row ("proof_ids" = "12,13" from the server). */
export function ProofThumbs({ ids }: { ids?: string | number[] | null }) {
  const list = (Array.isArray(ids) ? ids : String(ids ?? "").split(",")).map(Number).filter(Boolean);
  if (!list.length) return null;
  return (
    <span className="inline-flex flex-wrap gap-1 align-middle">
      {list.map((id) => <a key={id} href={photoUrl(id)} target="_blank" rel="noreferrer" title="Open photo proof"><img src={photoUrl(id)} alt="Proof" className="h-8 w-8 rounded border border-slate-200 object-cover" /></a>)}
    </span>
  );
}

import { useRef, useState, type ReactNode } from "react";
import { Keyboard, Loader2, Mic, MicOff, Sparkles } from "lucide-react";
import { api } from "../lib/api";
import { useToast } from "./ui";
import { Recognition } from "./Capture";

export const Ur = ({ children, className = "" }: { children: ReactNode; className?: string }) => <span lang="ur" dir="rtl" className={`font-urdu ${className}`}>{children}</span>;

/** Read a sentence aloud in Urdu if the phone has an Urdu voice (else in the default voice). */
export function speak(text: string) {
  try {
    const u = new SpeechSynthesisUtterance(text);
    const v = speechSynthesis.getVoices().find((x) => x.lang.startsWith("ur")) ?? speechSynthesis.getVoices().find((x) => x.lang.startsWith("hi"));
    if (v) { u.voice = v; u.lang = v.lang; } else u.lang = "ur-PK";
    speechSynthesis.cancel(); speechSynthesis.speak(u);
  } catch { /* no speech on this device */ }
}

/**
 * The voice box used in wholesale and khata: big mic, Urdu / English switch, type instead, example
 * sentences; sends what was said to `endpoint` and shows the result card the caller renders.
 */
export function VoiceShell({ endpoint, body, sub, examples, placeholder, compact, result }: {
  endpoint: string; body?: Record<string, unknown>; sub: string; examples: { en: string; ur: string }[]; placeholder: string; compact?: boolean;
  result: (r: any, close: () => void) => ReactNode;
}) {
  const [listening, setListening] = useState(false);
  const [busy, setBusy] = useState(false);
  const [typing, setTyping] = useState(false);
  const [text, setText] = useState("");
  const [lang, setLang] = useState<"ur-PK" | "en-PK">("ur-PK");
  const [res, setRes] = useState<any>(null);
  const rec = useRef<any>(null);
  const toast = useToast();

  const ask = async (said: string) => {
    if (!said.trim()) return;
    setBusy(true);
    try {
      const r = await api(endpoint, { body: { text: said, ...(body ?? {}) } });
      setRes(r); setTyping(false); setText("");
      if (r.answer) speak(r.answer.ur); else if (r.confirm_ur) speak(r.confirm_ur);
    } catch (e: any) { toast("err", e.message); }
    finally { setBusy(false); }
  };
  const listen = () => {
    if (!Recognition) return setTyping(true);
    if (listening) return rec.current?.stop();
    const r = new Recognition();
    r.lang = lang; r.interimResults = false; r.maxAlternatives = 1;
    r.onresult = (e: any) => ask(e.results[0][0].transcript);
    r.onerror = (e: any) => { if (e.error !== "no-speech" && e.error !== "aborted") { toast("err", "Could not hear clearly — type it instead · لکھ دیں"); setTyping(true); } };
    r.onend = () => setListening(false);
    rec.current = r; setListening(true); r.start();
  };

  return (
    <div className="overflow-hidden rounded-2xl bg-gradient-to-br from-violet-700 to-violet-600 text-white shadow-sm print:hidden">
      <div className="flex flex-wrap items-center gap-3 p-4">
        <button type="button" onClick={listen} disabled={busy} aria-label="Speak"
          className={`flex h-14 w-14 shrink-0 items-center justify-center rounded-full shadow-lg active:scale-95 sm:h-16 sm:w-16 ${listening ? "animate-pulse bg-red-500" : "bg-white text-violet-700"}`}>
          {busy ? <Loader2 className="animate-spin" size={28} /> : listening ? <MicOff size={28} /> : <Mic size={30} />}
        </button>
        <div className="min-w-0 flex-1">
          <div className="text-base font-bold leading-tight sm:text-lg"><Sparkles size={16} className="mr-1 inline" />{listening ? "Listening… speak now" : busy ? "Understanding…" : "Speak the entry"}</div>
          <Ur className="block text-base leading-relaxed">{listening ? "بولیں" : "بول کر انٹری کریں — جو کام ہو بول دیں"}</Ur>
          <div className="hidden text-sm opacity-90 sm:block">{sub}</div>
        </div>
        <div className="flex w-full justify-end gap-1.5 sm:w-auto">
          {Recognition && <button type="button" onClick={() => setLang(lang === "ur-PK" ? "en-PK" : "ur-PK")} className="rounded-xl bg-white/15 px-3 py-2 text-sm font-semibold hover:bg-white/25" title="Speech language">{lang === "ur-PK" ? "اردو" : "English"}</button>}
          <button type="button" onClick={() => setTyping(!typing)} className="rounded-xl bg-white/15 px-3 py-2 text-sm font-semibold hover:bg-white/25" title="Type instead"><Keyboard size={16} /></button>
        </div>
      </div>
      {typing && (
        <form className="flex gap-2 px-4 pb-4" onSubmit={(e) => { e.preventDefault(); ask(text); }}>
          <input className="input flex-1 text-slate-900" autoFocus placeholder={placeholder} value={text} onChange={(e) => setText(e.target.value)} />
          <button className="rounded-xl bg-white px-4 font-semibold text-violet-700" disabled={busy || !text.trim()}>Go</button>
        </form>
      )}
      {!res && !compact && (
        <div className="flex gap-2 overflow-x-auto px-4 pb-4">
          {examples.map((x) => (
            <button key={x.en} type="button" onClick={() => ask(x.en)} className="shrink-0 rounded-xl bg-white/10 px-3 py-1.5 text-left text-xs hover:bg-white/20">
              “{x.en}”<Ur className="block opacity-80">{x.ur}</Ur>
            </button>
          ))}
        </div>
      )}
      {res && <div className="bg-white p-4 text-slate-900">{result(res, () => setRes(null))}</div>}
    </div>
  );
}

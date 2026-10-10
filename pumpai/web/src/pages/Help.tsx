import { useEffect, useState } from "react";
import { PlayCircle, ListOrdered, Download, BookOpen, X } from "lucide-react";
import { PageHeader, Loading } from "../components/ui";

type Step = { en: string; ur: string };
type Tip = { icon: string; en: string; ur: string };
type Mod = { id: string; num: number; route: string; title_en: string; title_ur: string; who: string[]; what_en: string; what_ur: string; steps: Step[]; tips: Tip[] };

const Ur = ({ children, className = "" }: { children: React.ReactNode; className?: string }) => <span lang="ur" dir="rtl" className={`font-urdu ${className}`}>{children}</span>;
const pad = (n: number) => String(n).padStart(2, "0");

/** In-app user guide: one card per module with a playable GIF, bilingual steps and a downloadable PDF. */
export default function Help() {
  const [mods, setMods] = useState<Mod[] | null>(null);
  const [open, setOpen] = useState<Record<string, "video" | "steps" | null>>({});
  const [lightbox, setLightbox] = useState<string | null>(null);

  useEffect(() => {
    fetch("/help/guides.json").then((r) => r.json()).then((d) => setMods(d.modules)).catch(() => setMods([]));
    if (location.hash) setTimeout(() => document.getElementById(location.hash.slice(1))?.scrollIntoView({ behavior: "smooth" }), 400);
  }, []);
  if (!mods) return <Loading />;

  const toggle = (id: string, which: "video" | "steps") => setOpen((o) => ({ ...o, [id]: o[id] === which ? null : which }));

  return (
    <div>
      <PageHeader title="User Guide · رہنمائی" subtitle="Har module ka asaan guide — Urdu + English, video aur PDF ke saath" />
      <div className="mb-4 flex items-center gap-2 rounded-xl bg-brand-50 p-3 text-sm text-brand-900 ring-1 ring-brand-100">
        <BookOpen size={18} className="shrink-0" /> Har card par <b>▶ Video</b> dekhein, <b>Steps</b> parhein, ya <b>PDF</b> download karein. · <Ur>ہر ماڈیول کی آسان رہنمائی</Ur>
      </div>

      {/* Featured: full salesman training video (Urdu voice) */}
      <div className="mb-5 overflow-hidden rounded-2xl ring-1 ring-brand-200">
        <div className="flex items-center justify-between gap-2 bg-brand-900 px-4 py-2.5 text-white">
          <div className="font-bold">🎬 Salesman Training (Urdu) · <Ur className="text-emerald-100">سیلزمین ٹریننگ</Ur></div>
          <span className="text-xs text-emerald-100/80">~3 min</span>
        </div>
        <video controls playsInline preload="metadata" poster="/help/pos.gif" className="w-full bg-black"
          src="/help/salesman-training-ur.mp4" style={{ maxHeight: 460 }} />
        <div className="bg-brand-50 px-4 py-2 text-sm text-brand-900">Poori sale ka tareeqa — login se shift band tak, asaan Urdu mein. · <Ur>آسان اردو میں مکمل رہنمائی</Ur></div>
      </div>

      <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
        {mods.map((m) => (
          <div key={m.id} id={m.id} className="card flex flex-col overflow-hidden p-0">
            <div className="flex items-start justify-between gap-2 bg-brand-900 p-3 text-white">
              <div className="min-w-0">
                <div className="flex items-center gap-2"><span className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-white/20 text-xs font-bold">{m.num}</span>
                  <h2 className="truncate text-base font-bold">{m.title_en}</h2></div>
                <Ur className="mt-0.5 block text-sm text-emerald-100">{m.title_ur}</Ur>
              </div>
            </div>
            <div className="flex flex-1 flex-col p-3">
              <div className="mb-2 flex flex-wrap gap-1.5">{m.who.map((w) => <span key={w} className="rounded-full bg-emerald-50 px-2 py-0.5 text-[11px] font-semibold text-emerald-700 ring-1 ring-emerald-100">{w}</span>)}</div>
              <p className="text-sm text-slate-600">{m.what_en}</p>
              <Ur className="mt-1 block text-sm text-slate-500">{m.what_ur}</Ur>

              <div className="mt-3 flex flex-wrap gap-2">
                <button onClick={() => toggle(m.id, "video")} className={`flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-sm font-semibold ${open[m.id] === "video" ? "bg-amber-500 text-white" : "bg-amber-100 text-amber-800"}`}><PlayCircle size={15} /> Video</button>
                <button onClick={() => toggle(m.id, "steps")} className={`flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-sm font-semibold ${open[m.id] === "steps" ? "bg-brand-600 text-white" : "bg-brand-50 text-brand-700"}`}><ListOrdered size={15} /> Steps</button>
                <a href={`/help/PumpAI-${pad(m.num)}-${m.id}.pdf`} target="_blank" rel="noreferrer" className="flex items-center gap-1.5 rounded-lg bg-slate-100 px-3 py-1.5 text-sm font-semibold text-slate-700 hover:bg-slate-200"><Download size={15} /> PDF</a>
              </div>

              {open[m.id] === "video" && (
                <button onClick={() => setLightbox(`/help/${m.id}.gif`)} className="mt-3 overflow-hidden rounded-xl ring-1 ring-slate-200" aria-label="Play larger">
                  <img src={`/help/${m.id}.gif`} alt={`${m.title_en} demo`} loading="lazy" className="w-full" />
                  <div className="bg-slate-50 py-1 text-center text-xs text-slate-500">Tap to enlarge · بڑا دیکھیں</div>
                </button>
              )}

              {open[m.id] === "steps" && (
                <div className="mt-3 space-y-2.5">
                  {m.steps.map((s, i) => (
                    <div key={i} className="flex gap-2.5">
                      <span className="flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-brand-600 text-[11px] font-bold text-white">{i + 1}</span>
                      <div><div className="text-sm font-medium text-slate-800">{s.en}</div><Ur className="block text-sm text-slate-500">{s.ur}</Ur></div>
                    </div>
                  ))}
                  {m.tips.map((t, i) => (
                    <div key={`t${i}`} className="rounded-lg bg-amber-50 p-2 text-xs text-amber-900 ring-1 ring-amber-100">{t.icon} <b>{t.en}</b> · <Ur>{t.ur}</Ur></div>
                  ))}
                </div>
              )}
            </div>
          </div>
        ))}
      </div>

      {lightbox && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-900/70 p-4" onClick={() => setLightbox(null)}>
          <button className="absolute right-4 top-4 rounded-full bg-white/20 p-2 text-white" aria-label="Close"><X size={22} /></button>
          <img src={lightbox} alt="" className="max-h-full max-w-full rounded-xl" onClick={(e) => e.stopPropagation()} />
        </div>
      )}
    </div>
  );
}

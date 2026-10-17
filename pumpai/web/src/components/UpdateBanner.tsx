import { useEffect, useRef, useState } from "react";
import { RefreshCw, X } from "lucide-react";

/**
 * Watches for a new deployed build and offers a one-tap update, so users never sit on a stale
 * version (the usual "did it save? / where is the new screen?" after a deploy). It polls the
 * build stamp written at build time (dist/version.json) — which the service worker never caches —
 * and compares it with the id baked into the running bundle.
 */
const BUILD_ID = typeof __BUILD_ID__ !== "undefined" ? __BUILD_ID__ : "dev";

export function UpdateBanner() {
  const [ready, setReady] = useState(false);
  const readyRef = useRef(false);
  useEffect(() => {
    if (BUILD_ID === "dev" || !import.meta.env.PROD) return; // only the deployed app
    let stop = false;
    const check = async () => {
      try {
        const r = await fetch(`/version.json?t=${Date.now()}`, { cache: "no-store" });
        if (!r.ok) return;
        const j = await r.json();
        if (!stop && j?.v && j.v !== BUILD_ID) { readyRef.current = true; setReady(true); }
      } catch { /* offline — try again later */ }
    };
    check();
    const t = setInterval(check, 90_000);
    const onVis = () => {
      if (document.visibilityState !== "visible") return;
      // coming back to the app with an update waiting is a safe moment to apply it
      if (readyRef.current) location.reload();
      else check();
    };
    document.addEventListener("visibilitychange", onVis);
    return () => { stop = true; clearInterval(t); document.removeEventListener("visibilitychange", onVis); };
  }, []);

  if (!ready) return null;
  return (
    <div className="fixed inset-x-0 bottom-0 z-[60] flex justify-center p-3 print:hidden" role="status">
      <div className="flex items-center gap-3 rounded-xl bg-brand-900 px-4 py-2.5 text-sm text-white shadow-lg ring-1 ring-black/10">
        <RefreshCw size={16} className="shrink-0 text-emerald-300" />
        <span>App ka naya version aa gaya — <span lang="ur" dir="rtl" className="font-urdu">نیا ورژن</span></span>
        <button className="rounded-lg bg-emerald-500 px-3 py-1.5 font-semibold text-emerald-950 hover:bg-emerald-400" onClick={() => location.reload()}>Update</button>
        <button className="p-1 text-emerald-200 hover:text-white" aria-label="Baad mein" onClick={() => setReady(false)}><X size={16} /></button>
      </div>
    </div>
  );
}

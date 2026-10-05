import { useEffect, useRef, useState } from "react";
import jsQR from "jsqr";
import { QrCode, X } from "lucide-react";
import { api } from "../lib/api";
import { ErrorBox } from "./ui";

/**
 * Scan a customer's QR card with the tablet camera (or type the code under it).
 * Returns the khata account and, for a vehicle sticker, the plate number.
 */
export function CardScanner({ onFound, onClose, path = "/pos/card/", title = "Scan card", urdu = "کارڈ سکین", placeholder = "Card code" }: {
  onFound: (r: any) => void; onClose: () => void; path?: string; title?: string; urdu?: string; placeholder?: string;
}) {
  const video = useRef<HTMLVideoElement>(null);
  const [error, setError] = useState<string | null>(null);
  const [code, setCode] = useState("");
  const [camera, setCamera] = useState(true);
  const busy = useRef(false);

  const lookup = async (raw: string) => {
    if (busy.current) return;
    busy.current = true;
    try { onFound(await api(`${path}${encodeURIComponent(raw.trim())}`)); }
    catch (e: any) { setError(e.message); setTimeout(() => { busy.current = false; }, 1500); return; }
  };

  useEffect(() => {
    let stream: MediaStream | null = null, timer: number | undefined, stopped = false;
    const detector = "BarcodeDetector" in window ? new (window as any).BarcodeDetector({ formats: ["qr_code"] }) : null;
    const canvas = document.createElement("canvas");
    (async () => {
      try {
        stream = await navigator.mediaDevices.getUserMedia({ video: { facingMode: "environment" } });
        if (stopped) return;
        video.current!.srcObject = stream;
        await video.current!.play();
        const scan = async () => {
          if (stopped || !video.current || video.current.readyState < 2) { timer = window.setTimeout(scan, 250); return; }
          let text: string | null = null;
          if (detector) { const found = await detector.detect(video.current).catch(() => []); text = found[0]?.rawValue ?? null; }
          else {
            const v = video.current;
            canvas.width = v.videoWidth; canvas.height = v.videoHeight;
            const ctx = canvas.getContext("2d", { willReadFrequently: true })!;
            ctx.drawImage(v, 0, 0);
            text = jsQR(ctx.getImageData(0, 0, canvas.width, canvas.height).data, canvas.width, canvas.height)?.data ?? null;
          }
          if (text && /^PUMPAI-/i.test(text)) await lookup(text);
          timer = window.setTimeout(scan, 250);
        };
        scan();
      } catch { setCamera(false); }
    })();
    return () => { stopped = true; clearTimeout(timer); stream?.getTracks().forEach((t) => t.stop()); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return (
    <div className="fixed inset-0 z-[55] flex items-start justify-center overflow-y-auto bg-slate-900/70 p-3 sm:items-center">
      <div className="w-full max-w-md rounded-2xl bg-white p-4 shadow-xl">
        <div className="mb-3 flex items-center gap-2">
          <QrCode className="text-brand-600" />
          <h2 className="flex-1 text-xl font-bold">{title} · <span lang="ur" dir="rtl" className="font-urdu">{urdu}</span></h2>
          <button onClick={onClose} className="rounded-xl bg-slate-100 p-2" aria-label="Close"><X /></button>
        </div>
        {camera ? (
          <div className="relative overflow-hidden rounded-xl bg-black">
            <video ref={video} className="aspect-[4/3] w-full object-cover" muted playsInline />
            <div className="pointer-events-none absolute inset-8 rounded-xl border-4 border-white/80" />
          </div>
        ) : <p className="rounded-lg bg-slate-50 p-3 text-sm text-slate-600">Camera not available. Type the code printed under the QR.</p>}
        {error && <div className="mt-3"><ErrorBox error={error} /></div>}
        <form className="mt-3 flex gap-2" onSubmit={(e) => { e.preventDefault(); busy.current = false; lookup(code); }}>
          <input className="input py-3 font-mono text-lg uppercase" placeholder={placeholder} value={code} onChange={(e) => setCode(e.target.value)} />
          <button className="btn-primary px-5" disabled={code.trim().length < 6}>OK</button>
        </form>
      </div>
    </div>
  );
}

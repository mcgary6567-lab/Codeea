import { useEffect, useRef, useState } from "react";
import { Camera, Loader2, MapPin, RefreshCw } from "lucide-react";
import { api } from "../lib/api";
import { Modal } from "./ui";

const Ur = ({ children }: { children: React.ReactNode }) => <span lang="ur" dir="rtl" className="font-urdu">{children}</span>;
type Pos = { lat: number; lng: number; accuracy: number };

/**
 * Attendance proof: a selfie from the LIVE front camera (no gallery / old photos) plus the phone's
 * LIVE location. Both are required; the capture button stays off until both are ready.
 */
export function LiveSelfie({ open, title, onClose, onDone }: {
  open: boolean; title: string; onClose: () => void;
  onDone: (proof: { photo_id: number } & Pos) => Promise<void> | void;
}) {
  const video = useRef<HTMLVideoElement>(null);
  const stream = useRef<MediaStream | null>(null);
  const [camErr, setCamErr] = useState<string | null>(null);
  const [camOn, setCamOn] = useState(false);
  const [pos, setPos] = useState<Pos | null>(null);
  const [posErr, setPosErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const locate = () => {
    setPosErr(null); setPos(null);
    if (!navigator.geolocation) return setPosErr("This phone cannot share its location");
    navigator.geolocation.getCurrentPosition(
      (p) => setPos({ lat: p.coords.latitude, lng: p.coords.longitude, accuracy: Math.round(p.coords.accuracy) }),
      (e) => setPosErr(e.code === 1 ? "Location is blocked. Allow location for this site in the browser settings, then tap Retry." : "Could not get the location. Turn on GPS / Location and tap Retry."),
      { enableHighAccuracy: true, timeout: 20_000, maximumAge: 0 },
    );
  };
  const startCam = async () => {
    setCamErr(null);
    if (!navigator.mediaDevices?.getUserMedia) return setCamErr("This browser cannot open the camera. Use Chrome on the phone (the site must be https).");
    try {
      const s = await navigator.mediaDevices.getUserMedia({ video: { facingMode: "user", width: { ideal: 720 }, height: { ideal: 720 } }, audio: false });
      stream.current = s;
      if (video.current) { video.current.srcObject = s; await video.current.play().catch(() => undefined); }
      setCamOn(true);
    } catch (e: any) {
      setCamErr(e?.name === "NotAllowedError" ? "Camera is blocked. Allow the camera for this site in the browser settings, then tap Retry." : "Could not open the camera. Close other camera apps and tap Retry.");
    }
  };
  const stop = () => { stream.current?.getTracks().forEach((t) => t.stop()); stream.current = null; setCamOn(false); };

  useEffect(() => {
    if (!open) return;
    setErr(null); locate(); startCam();
    return stop;
  }, [open]);

  const capture = async () => {
    const v = video.current;
    if (!v || !pos || !v.videoWidth) return;
    setBusy(true); setErr(null);
    try {
      const scale = Math.min(1, 800 / Math.max(v.videoWidth, v.videoHeight));
      const c = document.createElement("canvas");
      c.width = Math.round(v.videoWidth * scale); c.height = Math.round(v.videoHeight * scale);
      const g = c.getContext("2d")!;
      g.drawImage(v, 0, 0, c.width, c.height);
      // stamp time + location on the photo itself
      const stamp = `${new Date().toLocaleString("en-PK")} · ${pos.lat.toFixed(5)}, ${pos.lng.toFixed(5)} (±${pos.accuracy} m)`;
      g.fillStyle = "rgba(0,0,0,.55)"; g.fillRect(0, c.height - 28, c.width, 28);
      g.fillStyle = "#fff"; g.font = "14px sans-serif"; g.fillText(stamp, 8, c.height - 9);
      const photo = await api("/ai/read-photo", { body: { kind: "selfie", image: c.toDataURL("image/jpeg", 0.82) } });
      await onDone({ photo_id: photo.photo_id, ...pos });
      stop();
    } catch (e: any) { setErr(e.message); }
    finally { setBusy(false); }
  };

  const ready = camOn && pos && !busy;
  return (
    <Modal open={open} onClose={() => { stop(); onClose(); }} title={title}>
      <div className="space-y-3">
        <div className="relative aspect-square w-full overflow-hidden rounded-2xl bg-slate-900">
          <video ref={video} playsInline muted className="h-full w-full -scale-x-100 object-cover" />
          {!camOn && !camErr && <div className="absolute inset-0 flex items-center justify-center text-white"><Loader2 className="animate-spin" /> &nbsp;Opening camera…</div>}
          {camErr && <div className="absolute inset-0 flex flex-col items-center justify-center gap-3 p-4 text-center text-white">
            <Camera size={32} /><p>{camErr}</p><button type="button" className="btn-secondary" onClick={startCam}><RefreshCw size={15} /> Retry</button></div>}
        </div>
        <div className={`flex items-center gap-2 rounded-xl px-3 py-2 text-sm ${pos ? "bg-emerald-50 text-emerald-800" : posErr ? "bg-red-50 text-red-700" : "bg-slate-50 text-slate-600"}`}>
          <MapPin size={16} className="shrink-0" />
          <span className="flex-1">{pos ? <>Location ready · <Ur>لوکیشن مل گئی</Ur> (±{pos.accuracy} m)</> : posErr ?? <>Getting live location… · <Ur>لوکیشن</Ur></>}</span>
          {posErr && <button type="button" className="btn-secondary !py-1" onClick={locate}>Retry</button>}
        </div>
        {err && <p className="rounded-xl bg-red-50 px-3 py-2 text-sm text-red-700">{err}</p>}
        <button type="button" disabled={!ready} onClick={capture}
          className="flex w-full items-center justify-center gap-2 rounded-2xl bg-emerald-600 py-4 text-xl font-bold text-white active:scale-95 disabled:bg-slate-300">
          {busy ? <Loader2 className="animate-spin" /> : <Camera />} Take selfie · <Ur>سیلفی لیں</Ur>
        </button>
        <p className="text-center text-xs text-slate-500">Live selfie and live location are both required · <Ur>سیلفی اور لوکیشن دونوں ضروری ہیں</Ur></p>
      </div>
    </Modal>
  );
}

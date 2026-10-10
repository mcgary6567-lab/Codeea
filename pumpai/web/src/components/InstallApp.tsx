import { useState } from "react";
import { Download, Share, SquarePlus, Smartphone, MoreVertical, X } from "lucide-react";
import { useInstallPrompt } from "../lib/brand";

/** Is the app already installed (opened from the home-screen icon)? Then no need to offer install. */
function isStandalone() {
  try {
    return window.matchMedia("(display-mode: standalone)").matches || (navigator as any).standalone === true;
  } catch { return false; }
}
function isIOS() {
  try { return /iphone|ipad|ipod/i.test(navigator.userAgent) && !(window as any).MSStream; } catch { return false; }
}

/**
 * "Install app" button + instructions. Works everywhere:
 *  - Android / Chrome / Edge: fires the native install prompt.
 *  - iPhone / iPad (Safari has no prompt): shows the "Share → Add to Home Screen" steps.
 *  - Any other browser: shows the manual "menu → Install app" steps.
 * Hidden once the app is already installed. After install the app opens offline.
 */
export function InstallAppButton({ variant = "sidebar" }: { variant?: "sidebar" | "login" }) {
  const { canInstall, install } = useInstallPrompt();
  const [help, setHelp] = useState(false);
  if (isStandalone()) return null; // already installed — nothing to do
  const ios = isIOS();

  const onClick = () => { if (canInstall) install(); else setHelp(true); };

  const cls = variant === "login"
    ? "flex w-full items-center justify-center gap-2 rounded-full bg-white/15 px-4 py-2 text-sm font-semibold text-white ring-1 ring-white/40 hover:bg-white/25"
    : "flex w-full items-center justify-center gap-2 rounded-xl bg-white/15 px-3 py-2.5 text-sm font-bold text-white ring-1 ring-white/25 hover:bg-white/25 active:scale-95";

  return (
    <>
      <button onClick={onClick} className={cls} aria-label="Install app">
        {ios ? <Share size={16} /> : <Download size={16} />}
        {ios ? "Install on iPhone" : "Install app · ایپ انسٹال کریں"}
      </button>

      {help && (
        <div className="fixed inset-0 z-[60] flex items-end justify-center bg-slate-900/50 p-4 sm:items-center print:hidden" onMouseDown={() => setHelp(false)}>
          <div className="w-full max-w-sm rounded-2xl bg-white p-5 text-slate-800 shadow-xl" onMouseDown={(e) => e.stopPropagation()}>
            <div className="mb-3 flex items-center justify-between">
              <h2 className="flex items-center gap-2 text-lg font-bold"><Smartphone size={20} /> Install the app</h2>
              <button onClick={() => setHelp(false)} className="-m-1.5 rounded-lg p-2 text-slate-400 hover:bg-slate-100" aria-label="Close"><X size={20} /></button>
            </div>
            <p className="mb-3 text-sm text-slate-600">Install karne par app phone par icon ban jata hai aur <b>internet ke baghair bhi</b> khulta hai. · <span lang="ur" dir="rtl" className="font-urdu">انسٹال کے بعد انٹرنیٹ کے بغیر بھی چلے گا</span></p>
            {ios ? (
              <ol className="space-y-2.5 text-sm">
                <li className="flex items-start gap-2"><span className="mt-0.5 flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-brand-100 font-bold text-brand-700">1</span><span>Safari mein neeche <Share size={15} className="inline" /> <b>Share</b> button dabayein.</span></li>
                <li className="flex items-start gap-2"><span className="mt-0.5 flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-brand-100 font-bold text-brand-700">2</span><span>Scroll karke <SquarePlus size={15} className="inline" /> <b>“Add to Home Screen”</b> chunein.</span></li>
                <li className="flex items-start gap-2"><span className="mt-0.5 flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-brand-100 font-bold text-brand-700">3</span><span><b>Add</b> dabayein — icon home screen par aa jayega.</span></li>
              </ol>
            ) : (
              <ol className="space-y-2.5 text-sm">
                <li className="flex items-start gap-2"><span className="mt-0.5 flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-brand-100 font-bold text-brand-700">1</span><span>Chrome mein upar dayein <MoreVertical size={15} className="inline" /> <b>(3 dots)</b> menu kholein.</span></li>
                <li className="flex items-start gap-2"><span className="mt-0.5 flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-brand-100 font-bold text-brand-700">2</span><span><b>“Install app”</b> ya <b>“Add to Home screen”</b> dabayein.</span></li>
                <li className="flex items-start gap-2"><span className="mt-0.5 flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-brand-100 font-bold text-brand-700">3</span><span><b>Install</b> dabayein — icon phone par aa jayega.</span></li>
              </ol>
            )}
            <button onClick={() => setHelp(false)} className="btn-primary mt-4 w-full">Theek hai · ٹھیک ہے</button>
          </div>
        </div>
      )}
    </>
  );
}

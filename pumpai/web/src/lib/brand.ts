import { useEffect, useState } from "react";

export interface Branding { setup_needed: boolean; demo: boolean; version: string; vendor: { name: string; phone: string; email: string }; name: string; color: string | null; logo_url: string | null }

const rgb = (hex: string) => [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16));
const mix = (c: number[], to: number, k: number) => c.map((v) => Math.round(v + (to - v) * k));

/** Make the 50…900 shades from one colour and put them in the CSS variables Tailwind's brand-* classes use. */
export function applyBrand(hex: string | null) {
  const root = document.documentElement.style;
  if (!hex || !/^#[0-9a-f]{6}$/i.test(hex)) { ["50", "100", "500", "600", "700", "900"].forEach((k) => root.removeProperty(`--brand-${k}`)); return; }
  const c = rgb(hex);
  const shades: Record<string, number[]> = { 50: mix(c, 255, 0.92), 100: mix(c, 255, 0.82), 500: mix(c, 255, 0.18), 600: c, 700: mix(c, 0, 0.18), 900: mix(c, 0, 0.55) };
  for (const [k, v] of Object.entries(shades)) root.setProperty(`--brand-${k}`, v.join(" "));
  document.querySelector('meta[name="theme-color"]')?.setAttribute("content", hex);
}

let cache: Branding | null = null;
const listeners = new Set<(b: Branding) => void>();
export async function loadBranding(): Promise<Branding> {
  const b: Branding = await fetch("/api/branding").then((r) => r.json());
  cache = b;
  applyBrand(b.color);
  if (b.name) document.title = b.setup_needed ? "PumpAI — setup" : `${b.name} · PumpAI`;
  listeners.forEach((f) => f(b));
  return b;
}
export function useBranding() {
  const [b, setB] = useState<Branding | null>(cache);
  useEffect(() => { listeners.add(setB); if (!cache) loadBranding().catch(() => {}); return () => { listeners.delete(setB); }; }, []);
  return b;
}

/** "Install app" (add to home screen / desktop) when the browser offers it. */
let deferred: any = null;
const installListeners = new Set<(v: boolean) => void>();
if (typeof window !== "undefined") {
  window.addEventListener("beforeinstallprompt", (e) => { e.preventDefault(); deferred = e; installListeners.forEach((f) => f(true)); });
  window.addEventListener("appinstalled", () => { deferred = null; installListeners.forEach((f) => f(false)); });
}
export function useInstallPrompt() {
  const [can, setCan] = useState(Boolean(deferred));
  useEffect(() => { installListeners.add(setCan); return () => { installListeners.delete(setCan); }; }, []);
  return { canInstall: can, install: async () => { if (!deferred) return; deferred.prompt(); await deferred.userChoice.catch(() => null); deferred = null; setCan(false); } };
}

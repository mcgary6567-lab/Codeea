/**
 * Fuel products, loaded from the admin's list (/products) at sign-in and kept in these objects.
 * They hold EVERY product (active + hidden) so labels never break; `activeProducts` is the list the
 * pickers (POS, tanks, bypass…) show. The objects are mutated in place so existing `PRODUCTS[x]`
 * usages keep working after the list loads or changes.
 */
export const PRODUCTS: Record<string, string> = { PMG: "Petrol", HOBC: "Hi-Octane", HSD: "Diesel" };
export const PRODUCT_COLORS: Record<string, string> = { PMG: "#2a78d6", HOBC: "#eb6834", HSD: "#1baf7a" };
export const PRODUCT_UR: Record<string, string> = { PMG: "پیٹرول", HOBC: "ہائی آکٹین", HSD: "ڈیزل" };
/** Active products in the admin's order: { code, name, short, colour, ur } — for dropdowns/buttons. */
export let activeProducts: { code: string; name: string; short: string; colour: string; ur: string }[] = [
  { code: "PMG", name: "Petrol", short: "Petrol", colour: "#2a78d6", ur: "پیٹرول" },
  { code: "HOBC", name: "Hi-Octane", short: "Hi-Octane", colour: "#eb6834", ur: "ہائی آکٹین" },
  { code: "HSD", name: "Diesel", short: "Diesel", colour: "#1baf7a", ur: "ڈیزل" },
];
const productSubs = new Set<() => void>();
export const onProductsChanged = (fn: () => void) => { productSubs.add(fn); return () => { productSubs.delete(fn); }; };
/** Fetch the admin's fuel products and refresh the shared maps in place. Call at sign-in and after a change. */
export async function loadProducts() {
  try {
    const { api } = await import("./api");
    const { products } = await api<{ products: any[] }>("/products?all=1");
    if (!products?.length) return;
    for (const k of Object.keys(PRODUCTS)) { delete PRODUCTS[k]; delete PRODUCT_COLORS[k]; delete PRODUCT_UR[k]; }
    for (const p of products) { PRODUCTS[p.code] = p.name; PRODUCT_COLORS[p.code] = p.colour || "#334155"; PRODUCT_UR[p.code] = p.ur || ""; }
    activeProducts = products.filter((p) => p.active).map((p) => ({ code: p.code, name: p.name, short: p.short || p.name, colour: p.colour || "#334155", ur: p.ur || "" }));
    productSubs.forEach((f) => f());
  } catch { /* keep the defaults */ }
}
import { useEffect, useState } from "react";
/** Re-render a component when the product list loads or changes. */
export function useProducts() {
  const [, tick] = useState(0);
  useEffect(() => onProductsChanged(() => tick((x) => x + 1)), []);
  return activeProducts;
}

/** Pakistani number grouping: 12,34,567 */
export const num = (n: number | null | undefined, d = 0) =>
  n == null || isNaN(n) ? "—" : Number(n).toLocaleString("en-IN", { maximumFractionDigits: d, minimumFractionDigits: d });
export const pkr = (n: number | null | undefined) => (n == null ? "—" : `Rs ${num(Math.round(n))}`);
export const pkrShort = (n: number) =>
  Math.abs(n) >= 1e7 ? `Rs ${(n / 1e7).toFixed(2)} Cr` : Math.abs(n) >= 1e5 ? `Rs ${(n / 1e5).toFixed(1)} L` : pkr(n);
export const litres = (n: number) => `${num(n)} L`;

export const ago = (iso?: string | null) => {
  if (!iso) return "—";
  const s = (Date.now() - Date.parse(iso)) / 1000;
  if (s < 60) return "just now";
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
  return `${Math.floor(s / 86400)}d ago`;
};
export const dt = (iso?: string | null) => (iso ? new Date(iso).toLocaleString("en-PK", { dateStyle: "medium", timeStyle: "short" }) : "—");
export const d = (iso?: string | null) => (iso ? new Date(iso).toLocaleDateString("en-PK", { day: "numeric", month: "short" }) : "—");
export const phone = (p: string) => (p?.startsWith("92") ? `+92 ${p.slice(2, 5)} ${p.slice(5)}` : p);

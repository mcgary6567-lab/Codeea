export const PRODUCTS: Record<string, string> = { PMG: "Petrol", HOBC: "Hi-Octane", HSD: "Diesel" };
/** Validated categorical slots 1-3 (first three pass all-pairs CVD checks). */
export const PRODUCT_COLORS: Record<string, string> = { PMG: "#2a78d6", HOBC: "#eb6834", HSD: "#1baf7a" };

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

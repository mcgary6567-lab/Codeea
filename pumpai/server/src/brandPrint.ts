/**
 * The pump's letterhead on every paper we make (statements, bills, receipts, day report): logo, name, phone and place at the top;
 * address, email, website and social pages at the bottom — the same look as the app's own prints.
 */
import { getSetting } from "./db.js";
import { profile, logoTag } from "./routes/setup.js";

const esc = (s: unknown) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]!);

/** Social pages a pump can list (Settings → Business profile). `url` turns a handle or link into a link. */
export const SOCIALS: { key: string; label: string; url: (v: string) => string; svg: string }[] = [
  { key: "facebook", label: "Facebook", url: (v) => link(v, "https://facebook.com/"), svg: '<path d="M14 8h3V4h-3c-2.8 0-5 2.2-5 5v2H7v4h2v9h4v-9h3l1-4h-4V9c0-.6.4-1 1-1z"/>' },
  { key: "instagram", label: "Instagram", url: (v) => link(v, "https://instagram.com/"), svg: '<path d="M7 2h10a5 5 0 0 1 5 5v10a5 5 0 0 1-5 5H7a5 5 0 0 1-5-5V7a5 5 0 0 1 5-5zm0 2a3 3 0 0 0-3 3v10a3 3 0 0 0 3 3h10a3 3 0 0 0 3-3V7a3 3 0 0 0-3-3H7zm5 3.5a4.5 4.5 0 1 1 0 9 4.5 4.5 0 0 1 0-9zm0 2a2.5 2.5 0 1 0 0 5 2.5 2.5 0 0 0 0-5zM17.5 5.5a1 1 0 1 1 0 2 1 1 0 0 1 0-2z"/>' },
  { key: "whatsapp", label: "WhatsApp", url: (v) => (/^https?:/i.test(v) ? v : `https://wa.me/${v.replace(/\D/g, "").replace(/^0/, "92")}`), svg: '<path d="M12 2a10 10 0 0 0-8.6 15.1L2 22l5-1.3A10 10 0 1 0 12 2zm0 2a8 8 0 1 1-4.1 14.9l-.3-.2-3 .8.8-2.9-.2-.3A8 8 0 0 1 12 4zm-3.3 3.5c-.2 0-.6.1-.9.4-.3.3-1.1 1.1-1.1 2.7s1.1 3.1 1.3 3.3c.2.2 2.2 3.4 5.4 4.6 2.6 1 3.2.8 3.7.8.6-.1 1.8-.8 2.1-1.5.3-.7.3-1.4.2-1.5l-.6-.4-2.1-1c-.3-.1-.5-.2-.7.1l-1 1.2c-.2.2-.4.2-.7.1-.3-.2-1.3-.5-2.5-1.5-.9-.8-1.5-1.8-1.7-2.1-.2-.3 0-.5.1-.6l.5-.6.3-.5v-.5l-1-2.3c-.2-.5-.5-.5-.7-.5h-.6z"/>' },
  { key: "tiktok", label: "TikTok", url: (v) => link(v, "https://tiktok.com/@"), svg: '<path d="M16.5 2c.3 2.5 1.8 4.1 4.5 4.3v3.3c-1.6.1-3-.4-4.5-1.3v6.2c0 7.8-8.5 10.3-11.9 4.7-2.2-3.6-.9-9.9 6.2-10.2v3.5c-.5.1-1.1.2-1.6.4-1.6.5-2.5 1.5-2.3 3.3.5 3.4 6.7 4.4 6.2-2.3V2h3.4z"/>' },
  { key: "youtube", label: "YouTube", url: (v) => link(v, "https://youtube.com/@"), svg: '<path d="M23 7.2a3 3 0 0 0-2.1-2.1C19 4.6 12 4.6 12 4.6s-7 0-8.9.5A3 3 0 0 0 1 7.2 31 31 0 0 0 .5 12a31 31 0 0 0 .5 4.8 3 3 0 0 0 2.1 2.1c1.9.5 8.9.5 8.9.5s7 0 8.9-.5a3 3 0 0 0 2.1-2.1 31 31 0 0 0 .5-4.8 31 31 0 0 0-.5-4.8zM9.8 15.1V8.9l5.8 3.1-5.8 3.1z"/>' },
  { key: "twitter", label: "X", url: (v) => link(v, "https://x.com/"), svg: '<path d="M17.8 3h3.3l-7.2 8.2L22.3 21h-6.6l-5.2-6.8L4.6 21H1.3l7.7-8.8L1 3h6.8l4.7 6.2L17.8 3zm-1.2 16.1h1.8L7.5 4.8H5.6l11 14.3z"/>' },
];
function link(v: string, base: string) {
  const s = v.trim();
  return /^https?:\/\//i.test(s) ? s : /^[\w.-]+\.\w{2,}\//.test(s) ? `https://${s}` : base + s.replace(/^@/, "");
}
/** "fb.com/AlMadina" → "AlMadina": the short handle printed next to the icon. */
export const handle = (v: string) => v.trim().replace(/^https?:\/\/(www\.)?/i, "").replace(/^(facebook|fb|instagram|tiktok|youtube|x|twitter)\.com\/@?/i, "").replace(/^wa\.me\//i, "+").replace(/\/$/, "");

/** 923001234567 → +92 300 1234567 */
const fmtPhone = (v?: string | null) => { const d = String(v ?? "").replace(/\D/g, ""); return /^92\d{10}$/.test(d) ? `+92 ${d.slice(2, 5)} ${d.slice(5)}` : v ?? ""; };

export const BRAND_CSS = `
  .lh{display:flex;justify-content:space-between;gap:14px;align-items:center;padding:0 0 10px;border-bottom:3px solid var(--brand,#059669);margin-bottom:12px}
  .lh .who{display:flex;gap:10px;align-items:center;min-width:0}.lh img{height:46px;max-width:150px;object-fit:contain}
  .lh .nm{font-size:19px;font-weight:800;line-height:1.15}.lh .tag{font-size:11px;color:#475569}
  .lh .ct{text-align:right;font-size:11px;color:#334155;line-height:1.5}
  .lf{margin-top:18px;padding-top:8px;border-top:1px solid #cbd5e1;font-size:10.5px;color:#475569;text-align:center;line-height:1.6}
  .lf .soc{display:flex;flex-wrap:wrap;justify-content:center;gap:4px 14px;margin-top:3px}
  .lf .soc a{color:#334155;text-decoration:none;display:inline-flex;align-items:center;gap:4px}
  .lf svg{width:12px;height:12px;fill:var(--brand,#059669)}
  @media (max-width:560px){.lh{flex-direction:column;align-items:flex-start}.lh .ct{text-align:left}}
  @media print{*{-webkit-print-color-adjust:exact;print-color-adjust:exact}}
`;

/** Top of the paper: logo, name, phone, place, tax numbers. */
export function brandHead(t: number, docTitle?: string) {
  const p = profile(t);
  const place = p.place;
  const phone = fmtPhone(p.biz_phone || p.owner_phone);
  return `<div class="lh" style="--brand:${esc(getSetting(t, "brand_color") || "#059669")}"><div class="who">${logoTag(t, "height:46px;max-width:150px;object-fit:contain")}
    <div><div class="nm">${esc(p.name)}</div><div class="tag">${esc([p.omc ? `${p.omc} dealer` : "", docTitle].filter(Boolean).join(" · "))}</div></div></div>
    <div class="ct">${phone ? `☎ ${esc(phone)}<br>` : ""}${place ? `📍 ${esc(place)}<br>` : ""}${p.ntn ? `NTN ${esc(p.ntn)}` : ""}${p.ntn && p.strn ? " · " : ""}${p.strn ? `STRN ${esc(p.strn)}` : ""}</div></div>`;
}

/** Bottom of the paper: address, email, website, social pages, the pump's own line. */
export function brandFoot(t: number) {
  const p = profile(t);
  const soc = SOCIALS.filter((s) => p[s.key]).map((s) => `<a href="${esc(s.url(p[s.key]!))}"><svg viewBox="0 0 24 24" aria-hidden="true">${s.svg}</svg>${esc(handle(p[s.key]!))}</a>`).join("");
  const line = [p.place, p.biz_email, p.website].filter(Boolean).map(esc).join(" · ");
  return `<div class="lf" style="--brand:${esc(getSetting(t, "brand_color") || "#059669")}">${line ? `<div>${line}</div>` : ""}${soc ? `<div class="soc">${soc}</div>` : ""}${p.receipt_footer ? `<div><b>${esc(p.receipt_footer)}</b></div>` : ""}</div>`;
}

/** The same letterhead as plain text lines, for PDFs drawn by hand (salary slip). */
export function brandLines(t: number) {
  const p = profile(t);
  const phone = fmtPhone(p.biz_phone || p.owner_phone);
  return {
    contact: [phone ? `Ph ${phone}` : "", p.place ?? "", [p.ntn ? `NTN ${p.ntn}` : "", p.strn ? `STRN ${p.strn}` : ""].filter(Boolean).join(" · ")].filter(Boolean).join("  |  "),
    foot: [p.place, p.biz_email, p.website].filter(Boolean).join(" · "),
    social: SOCIALS.filter((x) => p[x.key]).map((x) => `${x.label} ${handle(p[x.key]!)}`).join(" · "),
    note: p.receipt_footer ?? "",
  };
}

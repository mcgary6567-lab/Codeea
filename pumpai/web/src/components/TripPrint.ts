/**
 * Printed papers for a tanker trip, made in a hidden frame so the app around them is not printed:
 *  - a delivery challan for each client (client copy + office copy the driver brings back signed)
 *  - the driver's trip sheet (every drop, a signature for each)
 * Neither shows what the fuel cost us or the trip's profit.
 */
import { PRODUCTS, dt, num, phone, pkr } from "../lib/format";

const esc = (v: unknown) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]!);

const CSS = `
  * { box-sizing: border-box; }
  html { -webkit-text-size-adjust: 100%; text-size-adjust: 100%; }
  body { font: 13px/1.4 system-ui, -apple-system, "Segoe UI", Roboto, sans-serif; color: #0f172a; margin: 0; }
  .page { padding: 10mm 12mm; page-break-after: always; }
  .page:last-child { page-break-after: auto; }
  .copy { border: 1.5px solid #0f172a; border-radius: 6px; padding: 10px 12px; }
  .copy + .copy { margin-top: 10mm; }
  .cut { border-top: 1px dashed #64748b; margin: 6mm 0 0; text-align: center; font-size: 10px; color: #64748b; }
  .head { display: flex; justify-content: space-between; gap: 12px; align-items: flex-start; border-bottom: 1px solid #cbd5e1; padding-bottom: 6px; }
  .biz { font-size: 17px; font-weight: 700; }
  .muted { color: #475569; font-size: 11px; }
  .title { text-align: right; }
  .title b { font-size: 15px; display: block; }
  .tag { display: inline-block; border: 1px solid #0f172a; border-radius: 4px; padding: 0 6px; font-size: 10px; font-weight: 700; margin-top: 2px; }
  .grid { display: grid; grid-template-columns: 1fr 1fr; gap: 6px 16px; margin: 8px 0; }
  .grid div span { display: block; font-size: 10px; color: #64748b; text-transform: uppercase; letter-spacing: .03em; }
  table { width: 100%; border-collapse: collapse; margin-top: 4px; }
  th, td { border: 1px solid #94a3b8; padding: 5px 7px; text-align: left; }
  th { background: #f1f5f9; font-size: 11px; }
  .r { text-align: right; font-variant-numeric: tabular-nums; }
  .total td { font-weight: 700; }
  .signs { display: grid; grid-template-columns: repeat(3, 1fr); gap: 14px; margin-top: 22px; }
  .signs div { border-top: 1px solid #0f172a; padding-top: 3px; font-size: 11px; text-align: center; }
  .note { margin-top: 6px; font-size: 11px; color: #334155; }
  img.logo { height: 40px; max-width: 140px; object-fit: contain; display: block; margin-bottom: 2px; }
  @page { size: A4; margin: 0; }
  .bar { position: sticky; top: 0; z-index: 1; display: flex; gap: 8px; align-items: center; justify-content: space-between; padding: 10px 12px; background: #0f172a; color: #fff; }
  .bar button { font: 600 15px system-ui, sans-serif; border: 0; border-radius: 8px; padding: 10px 16px; min-height: 44px; cursor: pointer; }
  .bar .go { background: #059669; color: #fff; } .bar .x { background: #334155; color: #fff; }
  .bar span { font-size: 12px; color: #cbd5e1; }
  @media print { .bar { display: none; } }
  /* reading it on a phone before printing or sharing as PDF */
  @media screen and (max-width: 640px) {
    .page { padding: 12px 10px; }
    .copy { padding: 10px; }
    .head { flex-direction: column; } .title { text-align: left; }
    .grid { grid-template-columns: 1fr; }
    .signs { gap: 8px; } .signs div { font-size: 10px; }
    th, td { padding: 4px 5px; } table { font-size: 12px; }
  }
`;

function header(t: any, title: string, sub: string, tag?: string) {
  const b = t.business ?? {};
  return `<div class="head"><div>${b.logo_url ? `<img class="logo" src="${esc(b.logo_url)}">` : ""}<div class="biz">${esc(b.name)}</div>
    <div class="muted">${esc([b.address, b.phone ? `☎ ${phone(b.phone)}` : ""].filter(Boolean).join(" · "))}</div>
    ${b.ntn || b.strn ? `<div class="muted">${b.ntn ? `NTN ${esc(b.ntn)}` : ""}${b.ntn && b.strn ? " · " : ""}${b.strn ? `STRN ${esc(b.strn)}` : ""}</div>` : ""}</div>
    <div class="title"><b>${esc(title)}</b><div class="muted">${esc(sub)}</div>${tag ? `<span class="tag">${esc(tag)}</span>` : ""}</div></div>`;
}

const vehicle = (t: any) => [t.vehicle_no ? `🚛 ${t.vehicle_no}` : "", t.driver_name ? `${t.driver_name}${t.driver_phone ? ` (${phone(t.driver_phone)})` : ""}` : ""].filter(Boolean).join(" · ") || "—";

/** Challan number: the drop's own slip number, else trip and drop. */
const challanNo = (t: any, d: any, i: number) => (d.ref && d.ref !== `TRIP-${t.id}` ? d.ref : `${t.id}-${i + 1}`);

function challanCopy(t: any, d: any, i: number, copy: string) {
  return `<div class="copy">
    ${header(t, "Delivery challan · ڈیلیوری چالان", `No. ${challanNo(t, d, i)} · ${dt(t.trip_date)}`, copy)}
    <div class="grid">
      <div><span>Delivered to</span><b>${esc(d.client_name)}</b>${d.business_name && d.business_name !== d.client_name ? `<br>${esc(d.business_name)}` : ""}${d.phone ? `<br>☎ ${esc(phone(d.phone))}` : ""}</div>
      <div><span>Drop location</span>${esc(d.location || d.address || "—")}</div>
      <div><span>Tanker / driver</span>${esc(vehicle(t))}</div>
      <div><span>Trip</span>#${esc(t.id)}${t.drops.length > 1 ? ` · drop ${i + 1} of ${t.drops.filter((x: any) => !x.voided).length}` : ""}</div>
    </div>
    <table><thead><tr><th>Fuel</th><th class="r">Litres</th><th class="r">Rate (Rs/L)</th><th class="r">Amount</th></tr></thead>
      <tbody><tr><td>${esc(PRODUCTS[d.product] ?? d.product)}</td><td class="r">${num(d.litres, 2)}</td><td class="r">${esc(d.rate)}</td><td class="r">${pkr(d.amount)}</td></tr>
      <tr class="total"><td colspan="3">Total · کل رقم</td><td class="r">${pkr(d.amount)}</td></tr></tbody></table>
    <div class="note">Received the above fuel in full and in good condition. · اوپر لکھا تیل پورا وصول کیا۔</div>
    <div class="signs"><div>Driver · ڈرائیور</div><div>Received by (name &amp; sign) · وصول کنندہ</div><div>Stamp · مہر</div></div>
    ${t.business?.footer ? `<div class="note" style="text-align:center">${esc(t.business.footer)}</div>` : ""}
  </div>`;
}

/** One A4 page per drop: the client keeps the top copy, the driver brings the bottom one back signed. */
export function challanPages(t: any, only?: number) {
  return t.drops.map((d: any, i: number) => ({ d, i })).filter(({ d }: any) => !d.voided && (only == null || d.id === only))
    .map(({ d, i }: any) => `<div class="page">${challanCopy(t, d, i, "CLIENT COPY")}<div class="cut">✂ — — — — — — — — — —</div>${challanCopy(t, d, i, "OFFICE COPY — sign & return")}</div>`).join("");
}

/** The driver's trip sheet: every drop with a place to sign. */
export function tripSheetPage(t: any) {
  const live = t.drops.filter((d: any) => !d.voided);
  return `<div class="page">${header(t, "Trip sheet · ٹرپ شیٹ", `Trip #${t.id} · ${dt(t.trip_date)}`, "DRIVER COPY")}
    <div class="grid"><div><span>Tanker / driver</span>${esc(vehicle(t))}</div><div><span>Loaded from</span>${esc(t.source === "depot" ? `${t.supplier_name ?? "Depot"}${t.depot_ref ? ` (${t.depot_ref})` : ""}` : t.business?.station ?? "")}</div></div>
    <table><thead><tr><th>#</th><th>Client &amp; place</th><th>Fuel</th><th class="r">Litres</th><th class="r">Amount</th><th style="width:28%">Received by (sign)</th></tr></thead><tbody>
    ${live.map((d: any, i: number) => `<tr><td>${i + 1}</td><td><b>${esc(d.client_name)}</b>${d.phone ? ` · ${esc(phone(d.phone))}` : ""}<br><span class="muted">${esc(d.location || d.address || "")}</span></td>
      <td>${esc(PRODUCTS[d.product] ?? d.product)}</td><td class="r">${num(d.litres, 2)}</td><td class="r">${pkr(d.amount)}</td><td style="height:38px"></td></tr>`).join("")}
    <tr class="total"><td colspan="3">Total (${live.length} drops)</td><td class="r">${num(t.delivered_l, 2)}</td><td class="r">${pkr(t.billed)}</td><td></td></tr></tbody></table>
    <div class="signs"><div>Driver · ڈرائیور</div><div>Loaded by · لوڈ کرنے والا</div><div>Checked by (office) · دفتر</div></div></div>`;
}

/** Print HTML on its own (no app around it) through a hidden frame. */
const doc = (title: string, body: string, bar = "") =>
  `<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>${esc(title)}</title><base href="${location.origin}/"><style>${CSS}</style></head><body>${bar}${body}</body></html>`;

/** Phones print the page around a hidden frame, so there the papers open in their own tab with a Print / PDF button. */
const onPhone = () => window.matchMedia("(pointer: coarse)").matches || window.innerWidth < 768;

export function printPages(title: string, body: string) {
  if (onPhone()) {
    const w = window.open("", "_blank");
    if (w) {
      w.document.open();
      w.document.write(doc(title, body, `<div class="bar"><button class="x" onclick="window.close()">✕ Close</button><span>${esc(title)}</span><button class="go" onclick="window.print()">🖨 Print / PDF</button></div>`));
      w.document.close();
      return;
    }
    // pop-up blocked: fall back to the frame
  }
  const frame = document.createElement("iframe");
  frame.setAttribute("aria-hidden", "true");
  Object.assign(frame.style, { position: "fixed", right: "0", bottom: "0", width: "0", height: "0", border: "0" });
  document.body.appendChild(frame);
  const d = frame.contentDocument!;
  d.open();
  d.write(doc(title, body));
  d.close();
  const go = () => { frame.contentWindow!.focus(); frame.contentWindow!.print(); setTimeout(() => frame.remove(), 60_000); };
  // wait for the logo so it is on the paper
  const imgs = [...d.images].filter((i) => !i.complete);
  if (!imgs.length) setTimeout(go, 50);
  else Promise.all(imgs.map((i) => new Promise((r) => { i.onload = i.onerror = r; }))).then(go);
}

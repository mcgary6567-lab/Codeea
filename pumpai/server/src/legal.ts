/**
 * Privacy notice and terms of use, shown to staff (login page) and customers (khata portal, bills, WhatsApp).
 * Filled in with this pump's own name and contact. The pump owner is responsible for the data; the text is a plain-language
 * starting point and should be read by the owner's legal adviser before going live.
 */
import { get } from "./db.js";
import { getSetting } from "./db.js";
import { config, aiEnabled } from "./config.js";

const esc = (s: string) => s.replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]!);
function who() {
  const t = get("SELECT * FROM tenants ORDER BY id LIMIT 1");
  const name = t?.name ?? "This petrol pump";
  const phone = t ? getSetting(t.id, "owner_phone") || t.owner_phone || "" : "";
  const address = t ? getSetting(t.id, "address") : "";
  return { name: esc(name), phone: esc(phone ? `+${String(phone).replace(/^\+/, "")}` : ""), address: esc(address ?? "") };
}
const page = (title: string, body: string) => `<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>${title}</title><style>body{font:16px/1.6 system-ui,-apple-system,"Segoe UI",sans-serif;margin:0;background:#f8fafc;color:#0f172a}main{max-width:760px;margin:0 auto;padding:24px 16px 60px}
h1{font-size:1.6rem;margin:.2em 0}h2{font-size:1.1rem;margin-top:1.6em}.ur{font-family:"Noto Nastaliq Urdu","Jameel Noori Nastaleeq",serif;direction:rtl;line-height:2.2;background:#fff;border-radius:12px;padding:12px 16px;border:1px solid #e2e8f0}
.muted{color:#64748b;font-size:.9rem}a{color:#047857}</style></head><body><main>${body}</main></body></html>`;

export function privacyPage() {
  const w = who();
  return page(`Privacy — ${w.name}`, `
<h1>Privacy notice</h1><p class=muted>${w.name}${w.address ? ` · ${w.address}` : ""}${w.phone ? ` · ${w.phone}` : ""}</p>
<p>${w.name} uses this app to run the pump: sales, khata (credit), wholesale supply, staff and cash. This notice explains what we keep about you and why.</p>
<h2>What we keep</h2><ul>
<li><b>Customers:</b> name, mobile number, city, vehicle numbers, the fuel you take, payments, khata balance, and the WhatsApp messages you send us.</li>
<li><b>Wholesale clients and suppliers:</b> business name, contact person, phone, supplies, payments, cheques (with a photo as proof).</li>
<li><b>Staff:</b> name, phone, CNIC and licence where needed, attendance (with a selfie and location at check-in), salary, advances and shift cash.</li>
<li>Photos taken as proof (meter readings, receipts, cheques, slips).</li></ul>
<h2>Why</h2><ul><li>To record sales and payments correctly and send you your bill, receipt and khata balance.</li>
<li>To reply to your WhatsApp messages and orders, and to send reminders you would expect (balance due, price change, booking).</li>
<li>To keep the pump's accounts, pay staff and meet legal and tax duties.</li></ul>
<h2>Who sees it</h2><p>Only ${w.name} and its staff, each according to their job. The data is stored on the pump's own server.
WhatsApp messages go through WhatsApp (Meta).${aiEnabled() ? " Some messages and photos are read by an AI service (Anthropic Claude) to answer you and fill in entries; they are not used to train it." : ""}
We do not sell your data or share it for advertising.</p>
<h2>How long</h2><p>Accounts and receipts are kept as long as the law requires for business records. You can ask us to correct your details at any time, and to remove details we no longer need.</p>
<h2>Your choices</h2><p>Reply <b>STOP</b> on WhatsApp to stop offers (bills and payment receipts still come). To see, correct or remove your data, contact ${w.name}${w.phone ? ` on ${w.phone}` : " at the pump"}.</p>
<div class=ur>${w.name} آپ کا نام، موبائل نمبر، گاڑی نمبر، تیل اور ادائیگیوں کا ریکارڈ صرف پمپ کے کام، بل اور کھاتے کے لیے رکھتا ہے۔ یہ ڈیٹا کسی کو بیچا نہیں جاتا۔ پیشکشیں بند کرنے کے لیے واٹس ایپ پر STOP لکھیں۔ اپنی معلومات درست کروانے یا ہٹوانے کے لیے پمپ سے رابطہ کریں۔</div>
<p class=muted>Terms of use: <a href="/terms">/terms</a></p>`);
}

export function termsPage() {
  const w = who();
  return page(`Terms — ${w.name}`, `
<h1>Terms of use</h1><p class=muted>${w.name}${w.phone ? ` · ${w.phone}` : ""}</p>
<h2>Khata (credit)</h2><ul><li>Fuel on khata is given up to the limit the pump sets, and may be paused if payment is late.</li>
<li>Your bill and khata page show every entry with date, litres, rate and vehicle. Tell us within 7 days if anything looks wrong.</li>
<li>A cheque counts as paid only when it clears. A bounced cheque is added back to the khata, and bank charges may apply.</li></ul>
<h2>Your private links</h2><p>Your khata page and bill links are private to you and protected by a PIN. Do not share the PIN. If you think someone else has it, ask the pump to reset it.</p>
<h2>Prices</h2><p>Fuel prices change as set by the government / OGRA and the pump. The price on your receipt is the price at the time of filling.</p>
<h2>Staff</h2><p>Staff accounts are for work at ${w.name} only. Every change is recorded with the person's name and time.</p>
<div class=ur>کھاتے پر تیل پمپ کی مقرر کردہ حد تک ملتا ہے۔ چیک کلیئر ہونے پر ہی ادائیگی شمار ہوتی ہے۔ اپنا PIN کسی کو نہ بتائیں۔ کسی غلطی کی صورت میں سات دن کے اندر پمپ کو بتائیں۔</div>
<p class=muted>Privacy notice: <a href="/privacy">/privacy</a>${config.vendor.name ? ` · Software by ${esc(config.vendor.name)}` : ""}</p>`);
}

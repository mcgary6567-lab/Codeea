import { useState } from "react";
import { useLocation } from "react-router-dom";
import { HelpCircle, Volume2 } from "lucide-react";
import { Modal } from "./ui";

/** Short how-to for each screen, in Urdu and English, with read-aloud for staff who prefer listening. */
const HELP: Record<string, { title: string; ur: string[]; en: string[] }> = {
  "/pos": { title: "Sales / POS",
    ur: ["پہلے تیل چنیں: پیٹرول، ہائی آکٹین یا ڈیزل۔", "روپے یا لیٹر لکھیں، یا جلدی والے بٹن دبائیں۔", "ادائیگی چنیں: نقد، ایزی پیسہ، کھاتہ وغیرہ۔ کھاتہ والی گاڑی کا کارڈ سکین کر سکتے ہیں۔", "سبز 'محفوظ' بٹن دبائیں۔ غلطی ہو تو دو منٹ میں 'واپس' دبائیں۔", "بول کر سیل کریں: مائیک دبائیں اور کہیں 'بیس لیٹر ڈیزل نقد'۔ دکان کی چیزیں 'دکان' والے حصے میں۔"],
    en: ["Choose the fuel.", "Type rupees or litres, or tap a quick amount.", "Choose payment. For khata, scan the vehicle's QR card.", "Press the green Save. Wrong entry? Undo within 2 minutes.", "Or press 'Speak the sale' and say it. Shop items are on the Shop tab. New staff can practise with Training."] },
  "/machines": { title: "Machines",
    ur: ["کوئی مشین خراب ہو (ڈسپنسر، جنریٹر، کمپریسر، پنکھا، لائٹ) تو اس پر دبائیں اور 'خرابی بتائیں' میں لکھیں۔ تصویر بھی لگا سکتے ہیں۔", "مینیجر کو فوراً اطلاع چلی جاتی ہے۔", "سروس کی تاریخ قریب ہو تو پیلا، دیر ہو جائے تو لال نشان آتا ہے۔"],
    en: ["If a machine stops working, tap it and use 'Report fault' (add a photo if you can).", "The manager is told at once.", "Yellow means service is due soon, red means it is late. Managers record services and repairs with the cost."] },
  "/shifts": { title: "Shifts",
    ur: ["شفٹ شروع کرتے وقت ہر نوزل کا میٹر چیک کریں اور تصویر لیں۔", "شفٹ کے دوران خرچہ (چائے وغیرہ) 'خرچہ' میں لکھیں۔", "شفٹ ختم: ہر نوزل کی آخری ریڈنگ اور گنا ہوا نقد لکھیں۔ سسٹم کم یا زیادہ بتا دے گا۔"],
    en: ["At the start, check each meter and take a photo.", "Write cash expenses during the shift.", "At the end, enter each closing reading and the cash counted; the app shows short or over."] },
  "/checklist": { title: "Daily checks",
    ur: ["ہر کام ہونے پر سبز 'OK' دبائیں۔", "جہاں تصویر مانگی گئی ہو وہاں تصویر لیں۔", "پڑھائی (جیسے ڈینسٹی) نمبر میں لکھیں۔ مسئلہ ہو تو لال 'Problem' دبائیں — منیجر کو پتا چل جائے گا۔"],
    en: ["Tap OK when each check is done.", "Take a photo where asked.", "Write readings as numbers. Tap Problem if something is wrong — the manager is told."] },
  "/my-account": { title: "My account",
    ur: ["ڈیوٹی پر آ کر 'حاضری' دبائیں، لوکیشن آن رکھیں اور لائیو سیلفی لیں۔ دونوں ضروری ہیں۔", "چھٹی چاہیے تو 'چھٹی' بٹن دبائیں۔", "یہاں آپ کا ایڈوانس، کم نقد اور تنخواہ کا حساب نظر آتا ہے۔"],
    en: ["Tap Check in, keep Location on and take a live selfie when you arrive — both are required.", "Ask for leave with the leave button.", "Your advances, cash shortages and salary are listed here."] },
  "/khata": { title: "Khata",
    ur: ["کسی کھاتے پر 'Bill' دبا کر پورا حساب، پرچیاں، QR کارڈ اور واٹس ایپ بل دیکھیں۔", "سرکاری اداروں کا ماہانہ بل بنائیں، PO نمبر لکھیں اور چیک آنے پر ادائیگی درج کریں۔", "دیر سے ادائیگی والا کھاتہ خود بخود روک دیا جاتا ہے؛ ادائیگی پر کھل جاتا ہے۔"],
    en: ["Open 'Bill' on an account for the statement, slips, QR cards and WhatsApp bill.", "For offices, make the monthly bill, add the PO number and record the cheque.", "Overdue accounts are put on hold automatically and open again on payment."] },
  "/stock": { title: "Tanks & stock",
    ur: ["ڈپ سینٹی میٹر میں لکھیں — لیٹر خود بن جائیں گے۔", "ٹینکر آئے تو بل کی تصویر لیں؛ لیٹر اور ریٹ خود بھر جائیں گے۔", "ٹینک کم ہو تو 'Order tanker' دبائیں — آرڈر سپلائر کو واٹس ایپ پر چلا جائے گا۔"],
    en: ["Enter the dip in cm; litres come from the dip chart.", "When a tanker arrives, photograph the invoice to fill the form.", "Tap 'Order tanker' on a low tank to send the order to the supplier on WhatsApp."] },
  "/cash": { title: "Cash & bank",
    ur: ["دفتر کا نقد گنیں اور 'Count cash' میں لکھیں۔", "بینک میں جمع کروائیں تو سلپ کی تصویر کے ساتھ درج کریں۔", "سسٹم بتاتا ہے کہ ہاتھ میں کتنا نقد ہونا چاہیے۔"],
    en: ["Count the office cash and enter it.", "Record bank deposits with the slip photo.", "The app shows how much cash should be in hand."] },
  "/shop": { title: "Shop & lubricants",
    ur: ["نئی چیز 'Add item' سے ڈالیں — بارکوڈ، قیمت اور سٹاک۔", "مال آئے تو 'Stock in'؛ گنتی پر 'Count'۔", "کم سٹاک پر الرٹ آ جاتا ہے۔"],
    en: ["Add items with barcode, price and stock.", "Use Stock in when goods arrive and Count for stock counts.", "Low stock raises an alert."] },
  "/bookings": { title: "Bookings",
    ur: ["کار واش، آئل چینج یا ٹائر کی بکنگ یہاں نظر آتی ہے۔ گاہک واٹس ایپ پر خود بھی بک کر سکتا ہے۔", "کام ہو جائے تو 'Done' دبائیں۔"],
    en: ["Car wash, oil change and tyre bookings appear here; customers can also book on WhatsApp.", "Tap Done when the job is finished."] },
  "/": { title: "Dashboard",
    ur: ["اوپر پمپ کی صحت کا اسکور ہے۔ کم نمبر والے حصے پر توجہ دیں۔", "'آج کا حساب' میں آج کی سیل، خرچہ، سپلائی، سٹاک اور اس کی قیمت ہے۔", "مزید تفصیل کے لیے 'Owner insights' اور 'Reports' کھولیں۔"],
    en: ["The health score is at the top; look at the parts with low scores.", "Today's book shows sales, expenses, supply, stock and its value.", "Open Owner insights and Reports for more."] },
};
const DEFAULT = { title: "Help", ur: ["ہر صفحے پر یہ بٹن اس صفحے کا طریقہ بتاتا ہے۔ مسئلہ ہو تو منیجر سے بات کریں۔"], en: ["This button explains the page you are on. Ask the manager if you are stuck."] };

export function HelpButton() {
  const [open, setOpen] = useState(false);
  const { pathname } = useLocation();
  const key = Object.keys(HELP).filter((k) => k === "/" ? pathname === "/" : pathname.startsWith(k)).sort((a, b) => b.length - a.length)[0];
  const h = key ? HELP[key] : DEFAULT;
  const canSpeak = typeof window !== "undefined" && "speechSynthesis" in window;
  const speak = (lang: "ur" | "en") => {
    speechSynthesis.cancel();
    const u = new SpeechSynthesisUtterance((lang === "ur" ? h.ur : h.en).join(" "));
    u.lang = lang === "ur" ? "ur-PK" : "en-US";
    const v = speechSynthesis.getVoices().find((x) => x.lang.startsWith(lang));
    if (v) u.voice = v;
    u.rate = 0.9;
    speechSynthesis.speak(u);
  };
  return (
    <>
      <button onClick={() => setOpen(true)} aria-label="Help · مدد" className={`fixed ${pathname.startsWith("/pos") ? "bottom-36" : "bottom-4"} left-4 z-30 flex h-12 w-12 items-center justify-center rounded-full bg-brand-700 text-white shadow-lg print:hidden lg:left-64`}>
        <HelpCircle size={26} />
      </button>
      <Modal open={open} onClose={() => { setOpen(false); if (canSpeak) speechSynthesis.cancel(); }} title={`Help · مدد — ${h.title}`}>
        <ol lang="ur" dir="rtl" className="list-decimal space-y-2 pr-5 font-urdu text-lg leading-loose">{h.ur.map((x) => <li key={x}>{x}</li>)}</ol>
        <ol className="mt-4 list-decimal space-y-1 pl-5 text-sm text-slate-600">{h.en.map((x) => <li key={x}>{x}</li>)}</ol>
        {canSpeak && <div className="mt-4 flex gap-2">
          <button className="btn-primary" onClick={() => speak("ur")}><Volume2 size={16} /> سنیں (Urdu)</button>
          <button className="btn-secondary" onClick={() => speak("en")}><Volume2 size={16} /> Listen (English)</button>
        </div>}
      </Modal>
    </>
  );
}

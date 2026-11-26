/**
 * Go-live checklist (CEO): the 13 things a new pump sets before its first real day. Each item is auto-detected,
 * can be done right here (small inline form) or on its own page, and every one has a "Skip" — nothing blocks the pump.
 */
import { useState } from "react";
import { Link } from "react-router-dom";
import { api, useApi } from "../lib/api";
import { Field, Loading, PageHeader, Badge, useAction } from "../components/ui";
import { pkr, num } from "../lib/format";
import { Check, SkipForward, RotateCcw, Wallet, Landmark, Users, BookOpenCheck, Clock, BookOpen, Fuel, Bell, FileText, CloudUpload, Bot, FlaskConical, Truck, ExternalLink, Loader2 } from "lucide-react";

type Item = { key: string; status: "done" | "skipped" | "pending"; auto: boolean; mark: string } & Record<string, any>;
type State = { items: Item[]; pending: number; done: number; skipped: number; dismissed: boolean };

const META: Record<string, { icon: any; en: string; ur: string; why: string; link?: { to: string; label: string } }> = {
  cash: { icon: Wallet, en: "Opening cash count", ur: "پہلی نقدی گنتی", why: "Aaj daraaz / office me jitna cash hai wo likhein — yahi pehle din ka opening cash hai. Bina iske cash tally nahi dikhta.", link: { to: "/cash", label: "Cash page" } },
  bank: { icon: Landmark, en: "Pump ka bank account", ur: "بینک اکاؤنٹ", why: "Card, JazzCash, Easypaisa aur transfer ka paisa kisi bank/wallet account me jata hai. Kam az kam ek account banayein.", link: { to: "/banks", label: "Banks page" } },
  staff: { icon: Users, en: "Staff users (manager, cashier, salesman)", ur: "عملے کے لاگ ان", why: "Har banda apne PIN se login kare — shift, cash aur entries uske naam par likhi jayengi.", link: { to: "/users", label: "Users & Roles" } },
  opening: { icon: BookOpenCheck, en: "Purana hisaab (opening balances)", ur: "پرانا حساب", why: "Khata customers ka baqaya, suppliers ko dena, bank balance, tank stock, shop stock — purane register se aaj ki tareekh par le aayein.", link: { to: "/opening-balances", label: "Opening balances" } },
  shift: { icon: Clock, en: "Shift ki lambai", ur: "شفٹ کا دورانیہ", why: "12 ghante (din/raat do shift) ya 24 ghante (ek din on, ek off). Isi se shift-close ka reminder aur salary ka hisaab chalta hai." },
  khata: { icon: BookOpen, en: "Khata rules", ur: "کھاتہ قواعد", why: "Kitne din payment na aaye to khata khud hold ho, aur salesman kitna discount de sakta hai." },
  reorder: { icon: Fuel, en: "Tank reorder level (litre)", ur: "ٹینک آرڈر لیول", why: "Jis litre par 'order karo' ka alert aaye. Abhi 25% capacity par hai." },
  alerts: { icon: Bell, en: "Alert sensitivity", ur: "الرٹ حساسیت", why: "Cash short, dip variance, delivery short — kis had par owner ko alert jaye.", link: { to: "/settings", label: "Settings → Alerts" } },
  letterhead: { icon: FileText, en: "Letterhead / statement footer", ur: "لیٹر ہیڈ", why: "Har voucher, bill aur statement par pump ka phone, pata aur social links chhapte hain." },
  backup: { icon: CloudUpload, en: "Cloud backup copy", ur: "کلاؤڈ بیک اپ", why: "Rozana backup ki ek copy Google Drive / Dropbox par bhi jaye.", link: { to: "/settings", label: "Settings → Backups" } },
  whatsapp_ai: { icon: Bot, en: "WhatsApp + AI", ur: "واٹس ایپ اور اے آئی", why: "Khata reminders, receipts aur Ask-AI ke liye. SaaS par platform ki keys khud lagti hain.", link: { to: "/settings", label: "Settings → Integrations" } },
  sample: { icon: FlaskConical, en: "Sample pump dekhein", ur: "نمونہ ڈیٹا", why: "Khali screens ke bajaye ek bhara hua sample pump dekh kar seekhein. Aap ka apna data saaf rehta hai." },
  wholesale: { icon: Truck, en: "Wholesale & carriage module", ur: "ہول سیل ماڈیول", why: "Kya ye pump doosre pumps/dealers ko tanker se fuel supply ya kiraya (carriage) karta hai?" },
};

export default function GoLive() {
  const { data, reload } = useApi<State>("/golive");
  if (!data) return <Loading />;
  const total = data.items.length;
  const pct = Math.round(((data.done + data.skipped) / total) * 100);
  return (
    <div className="space-y-4">
      <PageHeader title="Go-live checklist" subtitle="Naye pump ki 13 zaroori settings — har ek yahin set karein ya Skip kar dein · نئے پمپ کی تیاری" />
      <div className="card p-4">
        <div className="flex flex-wrap items-center justify-between gap-2 text-sm">
          <div><span className="font-semibold">{data.done} done</span> · {data.skipped} skipped · <span className={data.pending ? "font-semibold text-amber-700" : ""}>{data.pending} baqi</span></div>
          <div className="text-xs text-slate-500">{pct}% mukammal</div>
        </div>
        <div className="mt-2 h-2 overflow-hidden rounded-full bg-slate-100"><div className="h-full bg-brand-600 transition-all" style={{ width: `${pct}%` }} /></div>
        {data.pending === 0 && <div className="mt-2 rounded-lg bg-emerald-50 px-3 py-2 text-sm text-emerald-800"><Check size={14} className="inline" /> Pump go-live ke liye tayyar hai.</div>}
      </div>
      <div className="stagger space-y-3">
        {data.items.map((it, i) => <Row key={it.key} n={i + 1} it={it} reload={reload} />)}
      </div>
    </div>
  );
}

function Row({ n, it, reload }: { n: number; it: Item; reload: () => void }) {
  const m = META[it.key];
  const [open, setOpen] = useState(false);
  const { run, busy } = useAction();
  const post = (p: string, body?: any, msg?: string) => run(() => api(p, { body: body ?? {} }), msg).then((r) => { if (r) { reload(); setOpen(false); } });
  const tone = it.status === "done" ? "green" : it.status === "skipped" ? "slate" : "amber";
  const Icon = m.icon;
  return (
    <div className={`card p-4 ${it.status === "pending" ? "ring-1 ring-amber-200" : ""}`}>
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="flex min-w-0 items-start gap-3">
          <div className={`grid h-9 w-9 shrink-0 place-items-center rounded-xl ${it.status === "done" ? "bg-emerald-50 text-emerald-700" : it.status === "skipped" ? "bg-slate-100 text-slate-500" : "bg-amber-50 text-amber-700"}`}>{it.status === "done" ? <Check size={18} /> : <Icon size={18} />}</div>
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-2"><span className="text-xs text-slate-400">{n}.</span><span className="font-semibold">{m.en}</span><span className="font-urdu text-xs text-slate-500" lang="ur" dir="rtl">{m.ur}</span><Badge tone={tone}>{it.status === "done" ? (it.auto ? "Set hai" : "Done") : it.status === "skipped" ? "Skipped" : "Baqi"}</Badge></div>
            <div className="mt-0.5 text-xs text-slate-500">{m.why}</div>
            <Summary it={it} />
          </div>
        </div>
        <div className="flex shrink-0 flex-wrap items-center gap-1.5">
          {m.link && <Link to={m.link.to} className="btn-secondary text-xs"><ExternalLink size={13} /> {m.link.label}</Link>}
          {it.status !== "done" && <button className="btn-primary text-xs" onClick={() => setOpen(!open)}>{open ? "Band karein" : "Set karein"}</button>}
          {it.status === "pending" && <button className="btn-secondary text-xs" disabled={busy} onClick={() => post(`/golive/${it.key}/skip`, {}, "Skip kiya")}><SkipForward size={13} /> Skip</button>}
          {it.status === "pending" && !it.auto && <button className="btn-secondary text-xs" disabled={busy} onClick={() => post(`/golive/${it.key}/done`, {}, "Done")}><Check size={13} /> Ho gaya</button>}
          {it.status !== "pending" && it.mark && <button className="btn-secondary text-xs" disabled={busy} title="Dobara pending karein" onClick={() => post(`/golive/${it.key}/reset`)}><RotateCcw size={13} /></button>}
        </div>
      </div>
      {open && <div className="mt-3 border-t border-slate-100 pt-3"><Form it={it} post={post} busy={busy} /></div>}
    </div>
  );
}

function Summary({ it }: { it: Item }) {
  const c = "mt-1 text-xs text-slate-600";
  switch (it.key) {
    case "cash": return it.last ? <div className={c}>Aakhri count {pkr(it.last.amount)}</div> : null;
    case "bank": return it.accounts?.length ? <div className={c}>{it.accounts.map((a: any) => `${a.bank}${a.title ? ` · ${a.title}` : ""}`).join(", ")}</div> : null;
    case "staff": return it.users?.length ? <div className={c}>{it.users.map((u: any) => `${u.name} (${u.role})`).join(", ")}</div> : null;
    case "opening": return <div className={c}>Khata rows {it.khata_rows} · parties/banks {it.parties} · shop bookings {it.shop_bookings}</div>;
    case "shift": return <div className={c}>Abhi: {it.shift_hours} ghante</div>;
    case "khata": return <div className={c}>Hold {it.block_days} din ({it.auto_block ? "auto on" : "auto off"}) · salesman discount max {pkr(it.khata_discount_max)}</div>;
    case "reorder": return <div className={c}>{it.tanks?.map((k: any) => `${k.name} ${num(k.reorder_l)} L`).join(" · ")}</div>;
    case "alerts": return <div className={c}>Cash short {pkr(it.sensitivity?.cash_short_warn)} / {pkr(it.sensitivity?.cash_short_crit)} · dip {it.sensitivity?.shift_dip_alert_l} L · low stock {it.sensitivity?.low_stock_days} din</div>;
    case "letterhead": return <div className={c}>{it.biz_phone || "phone —"} · {it.biz_address || "pata —"}</div>;
    case "backup": return <div className={c}>{it.cloud ? "Cloud copy chalu" : it.saas ? "Platform rozana backup rakhta hai; apni copy ke liye Drive/Dropbox jorein" : "Sirf local backup"}</div>;
    case "whatsapp_ai": return <div className={c}>WhatsApp {it.whatsapp ? "connected" : "nahi"} · AI {it.ai === "none" ? "rules only" : it.ai}</div>;
    case "sample": return <div className={c}>{it.available ? "Sample pump is server par mojood hai" : "Is server par sample pump nahi (production)"}</div>;
    case "wholesale": return <div className={c}>Module {it.on ? "ON" : "OFF"}</div>;
    default: return null;
  }
}

function Form({ it, post, busy }: { it: Item; post: (p: string, body?: any, msg?: string) => Promise<void>; busy: boolean }) {
  const [f, setF] = useState<Record<string, any>>({});
  const v = (k: string, d: any = "") => (f[k] ?? d);
  const set = (k: string, val: any) => setF({ ...f, [k]: val });
  const Btn = ({ onClick, label = "Save" }: { onClick: () => void; label?: string }) => <button className="btn-primary" disabled={busy} onClick={onClick}>{busy ? <Loader2 size={15} className="animate-spin" /> : <Check size={15} />} {label}</button>;
  switch (it.key) {
    case "cash": return (
      <div className="flex flex-wrap items-end gap-2">
        <Field label="Aaj daraaz / office me cash (Rs)"><input className="input w-48 text-xl tabular-nums" type="number" inputMode="decimal" min={0} value={v("amount")} onChange={(e) => set("amount", e.target.value)} autoFocus /></Field>
        <Btn label="Opening cash likhein" onClick={() => post("/cash/count", { amount: Number(v("amount")) || 0, note: "Opening cash (go-live)" }, "Opening cash likh diya")} />
      </div>);
    case "bank": return (
      <div className="grid gap-2 sm:grid-cols-4">
        <Field label="Bank / wallet *"><input className="input" value={v("bank")} onChange={(e) => set("bank", e.target.value)} placeholder="HBL, Meezan, JazzCash…" autoFocus /></Field>
        <Field label="Title"><input className="input" value={v("title")} onChange={(e) => set("title", e.target.value)} placeholder="Pump ke naam" /></Field>
        <Field label="Account no"><input className="input" value={v("account_no")} onChange={(e) => set("account_no", e.target.value)} /></Field>
        <Field label="Aaj ka balance (Rs)"><input className="input tabular-nums" type="number" inputMode="decimal" value={v("opening_balance")} onChange={(e) => set("opening_balance", e.target.value)} /></Field>
        <div className="sm:col-span-4"><Btn label="Account banayein" onClick={() => post("/bank/accounts", { bank: v("bank"), title: v("title") || null, account_no: v("account_no") || null, kind: /jazz|easy|sada|upaisa|wallet/i.test(v("bank")) ? "wallet" : "current", opening_balance: Number(v("opening_balance")) || 0 }, "Bank account ban gaya")} /></div>
      </div>);
    case "staff": {
      const rows: { name: string; role: string; phone: string; pin: string }[] = v("rows", [{ name: "", role: "manager", phone: "", pin: "" }, { name: "", role: "cashier", phone: "", pin: "" }, { name: "", role: "salesman", phone: "", pin: "" }, { name: "", role: "salesman", phone: "", pin: "" }]);
      const upd = (i: number, k: string, val: string) => set("rows", rows.map((r, j) => (j === i ? { ...r, [k]: val } : r)));
      return (
        <div className="space-y-2">
          <div className="text-xs text-slate-500">Naam aur 4-digit PIN kaafi hai — wo PIN se login karenge. Khali rows chhor dein.</div>
          {rows.map((r, i) => (
            <div key={i} className="grid gap-2 sm:grid-cols-4">
              <input className="input" placeholder="Naam" value={r.name} onChange={(e) => upd(i, "name", e.target.value)} />
              <select className="input" value={r.role} onChange={(e) => upd(i, "role", e.target.value)}>{(it.roles ?? ["manager", "cashier", "salesman", "wholesale", "staff"]).map((x: string) => <option key={x} value={x}>{x}</option>)}</select>
              <input className="input" placeholder="Phone (optional)" value={r.phone} onChange={(e) => upd(i, "phone", e.target.value)} />
              <input className="input tabular-nums" placeholder="PIN (4 digit)" inputMode="numeric" maxLength={4} value={r.pin} onChange={(e) => upd(i, "pin", e.target.value.replace(/\D/g, "").slice(0, 4))} />
            </div>))}
          <div className="flex gap-2"><button className="btn-secondary text-xs" onClick={() => set("rows", [...rows, { name: "", role: "salesman", phone: "", pin: "" }])}>+ row</button>
            <Btn label="Users banayein" onClick={() => post("/golive/staff", { users: rows.filter((r) => r.name.trim().length >= 2).map((r) => ({ name: r.name.trim(), role: r.role, phone: r.phone || null, pin: r.pin || "" })) }, "Staff users ban gaye")} /></div>
        </div>);
    }
    case "shift": return (
      <div className="flex flex-wrap items-end gap-2">
        {[12, 24].map((h) => <button key={h} className={`rounded-xl border px-4 py-3 text-sm ${Number(v("shift_hours", it.shift_hours)) === h ? "border-brand-600 bg-brand-50 font-semibold" : "border-slate-200"}`} onClick={() => set("shift_hours", h)}>{h} ghante<div className="text-[11px] font-normal text-slate-500">{h === 12 ? "din / raat do shift" : "ek din on, ek off"}</div></button>)}
        <Btn onClick={() => post("/settings", { limits: { shift_hours: Number(v("shift_hours", it.shift_hours)) } }, "Shift length set").then(() => post(`/golive/shift/done`))} />
      </div>);
    case "khata": return (
      <div className="flex flex-wrap items-end gap-2">
        <Field label="Hold after (din bina payment)"><input className="input w-32 tabular-nums" type="number" inputMode="numeric" min={15} max={365} value={v("block_days", it.block_days)} onChange={(e) => set("block_days", e.target.value)} /></Field>
        <Field label="Salesman khata discount max (Rs)"><input className="input w-36 tabular-nums" type="number" inputMode="decimal" min={0} value={v("khata_discount_max", it.khata_discount_max)} onChange={(e) => set("khata_discount_max", e.target.value)} /></Field>
        <Btn onClick={() => post("/settings", { khata_rules: { block_days: Number(v("block_days", it.block_days)) }, limits: { khata_discount_max: Number(v("khata_discount_max", it.khata_discount_max)) } }, "Khata rules set").then(() => post(`/golive/khata/done`))} />
      </div>);
    case "reorder": return (
      <div className="space-y-2">
        {(it.tanks ?? []).map((k: any) => (
          <div key={k.id} className="flex flex-wrap items-center gap-2 text-sm"><span className="w-56 truncate">{k.station} · {k.name} ({k.product}, {num(k.capacity_l)} L)</span>
            <input className="input w-32 tabular-nums" type="number" inputMode="decimal" min={0} value={v(`t${k.id}`, k.reorder_l)} onChange={(e) => set(`t${k.id}`, e.target.value)} /> <span className="text-xs text-slate-500">litre par alert</span></div>))}
        <Btn onClick={() => post("/golive/reorder", { tanks: (it.tanks ?? []).map((k: any) => ({ id: k.id, reorder_l: Number(v(`t${k.id}`, k.reorder_l)) })) }, "Reorder levels set")} />
      </div>);
    case "alerts": {
      const s = it.sensitivity ?? {};
      return (
        <div className="flex flex-wrap items-end gap-2">
          <Field label="Cash short — warning (Rs)"><input className="input w-32 tabular-nums" type="number" inputMode="decimal" value={v("cash_short_warn", s.cash_short_warn)} onChange={(e) => set("cash_short_warn", e.target.value)} /></Field>
          <Field label="Cash short — critical (Rs)"><input className="input w-32 tabular-nums" type="number" inputMode="decimal" value={v("cash_short_crit", s.cash_short_crit)} onChange={(e) => set("cash_short_crit", e.target.value)} /></Field>
          <Field label="Shift dip variance (L)"><input className="input w-28 tabular-nums" type="number" inputMode="decimal" value={v("shift_dip_alert_l", s.shift_dip_alert_l)} onChange={(e) => set("shift_dip_alert_l", e.target.value)} /></Field>
          <Field label="Low stock (din)"><input className="input w-24 tabular-nums" type="number" inputMode="decimal" step="0.5" value={v("low_stock_days", s.low_stock_days)} onChange={(e) => set("low_stock_days", e.target.value)} /></Field>
          <Btn onClick={() => post("/settings", { sensitivity: { cash_short_warn: Number(v("cash_short_warn", s.cash_short_warn)), cash_short_crit: Number(v("cash_short_crit", s.cash_short_crit)), shift_dip_alert_l: Number(v("shift_dip_alert_l", s.shift_dip_alert_l)), low_stock_days: Number(v("low_stock_days", s.low_stock_days)) } }, "Alert sensitivity set").then(() => post(`/golive/alerts/done`))} />
        </div>);
    }
    case "letterhead": return (
      <div className="grid gap-2 sm:grid-cols-3">
        <Field label="Pump phone"><input className="input" value={v("biz_phone", it.biz_phone)} onChange={(e) => set("biz_phone", e.target.value)} /></Field>
        <Field label="Pata (address)"><input className="input" value={v("biz_address", it.biz_address)} onChange={(e) => set("biz_address", e.target.value)} /></Field>
        <Field label="Footer line"><input className="input" value={v("receipt_footer", it.receipt_footer)} onChange={(e) => set("receipt_footer", e.target.value)} placeholder="Shukriya — phir tashreef layein" /></Field>
        <Field label="Facebook"><input className="input" value={v("facebook", it.facebook)} onChange={(e) => set("facebook", e.target.value)} /></Field>
        <Field label="Instagram"><input className="input" value={v("instagram", it.instagram)} onChange={(e) => set("instagram", e.target.value)} /></Field>
        <Field label="WhatsApp"><input className="input" value={v("whatsapp", it.whatsapp)} onChange={(e) => set("whatsapp", e.target.value)} /></Field>
        <div className="sm:col-span-3"><Btn onClick={() => post("/golive/letterhead", { biz_phone: v("biz_phone", it.biz_phone), biz_address: v("biz_address", it.biz_address), receipt_footer: v("receipt_footer", it.receipt_footer), facebook: v("facebook", it.facebook), instagram: v("instagram", it.instagram), whatsapp: v("whatsapp", it.whatsapp) }, "Letterhead save")} /></div>
      </div>);
    case "backup": return <div className="text-sm text-slate-600">Settings → Backups me Google Drive ya Dropbox jorein; jurte hi ye item khud "Set hai" ho jayega. <Link className="underline" to="/settings">Settings kholen</Link></div>;
    case "whatsapp_ai": return <div className="text-sm text-slate-600">{it.saas ? "SaaS par WhatsApp aur AI platform ki taraf se chalte hain — kuch nahi karna. Apni WhatsApp number lagani ho to Settings → Integrations." : "Settings → Integrations me WhatsApp Cloud API token aur AI key (Gemini free) lagayein."} <Link className="underline" to="/settings">Settings kholen</Link></div>;
    case "sample": return it.available
      ? <div className="text-sm text-slate-600">Sample pump: <a className="underline" href="/login?role=admin" target="_blank" rel="noreferrer">admin@pumpai.pk / demo1234</a> (naye tab me). Dekh kar wapas aayein aur "Ho gaya" dabayein.</div>
      : <div className="text-sm text-slate-600">Is server par sample data nahi hai. Training ke liye <Link className="underline" to="/help">User guide</Link> ki videos dekhein, phir Skip karein.</div>;
    case "wholesale": return (
      <div className="flex flex-wrap items-center gap-2">
        <button className={`rounded-xl border px-4 py-3 text-sm ${it.on ? "border-brand-600 bg-brand-50 font-semibold" : "border-slate-200"}`} onClick={() => post("/golive/wholesale", { on: true }, "Wholesale module ON")}>Haan — module ON</button>
        <button className={`rounded-xl border px-4 py-3 text-sm ${!it.on ? "border-brand-600 bg-brand-50 font-semibold" : "border-slate-200"}`} onClick={() => post("/golive/wholesale", { on: false }, "Wholesale module OFF")}>Nahi — OFF rakhein</button>
        <span className="text-xs text-slate-500">Badalne ke baad dobara login karein taake menu update ho.</span>
      </div>);
    default: return null;
  }
}

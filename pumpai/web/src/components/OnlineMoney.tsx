import { Ur } from "./VoiceShell";
import { Empty } from "./ui";
import { pkr } from "../lib/format";

const ONLINE_NAME: Record<string, [string, string]> = { card: ["💳 Card machine", "کارڈ مشین"], jazzcash: ["📱 JazzCash", "جاز کیش"], easypaisa: ["📱 Easypaisa", "ایزی پیسہ"], raast: ["🏦 Raast / QR", "راست"] };

/** Today's online money: not in the cash bag — it lands in the bank account each method is linked to. */
export function OnlineToday({ o, onBank, title = "Online money today", titleUr = "آج کی آن لائن رقم" }: { o: any; onBank?: () => void; title?: string; titleUr?: string }) {
  if (!o) return null;
  return (
    <div className="card">
      <div className="flex items-start justify-between gap-3 p-4 pb-1">
        <h2 className="font-semibold">{title} · <Ur>{titleUr}</Ur></h2>
        <span className="whitespace-nowrap font-semibold tabular-nums">{pkr(o.total)}</span>
      </div>
      <p className="px-4 text-xs text-slate-500">Not in the cash bag — it goes straight to the bank. Match it with the card machine slip and the bank / app statement · <Ur>کیش میں نہیں، بینک میں آتی ہے</Ur></p>
      <ul className="mt-1 divide-y divide-slate-100">
        {o.methods.map((m: any) => (
          <li key={m.method} className="px-4 py-2.5 text-sm">
            <div className="flex items-baseline justify-between gap-2"><span className="font-medium">{ONLINE_NAME[m.method]?.[0] ?? m.method} · <Ur className="text-slate-500">{ONLINE_NAME[m.method]?.[1]}</Ur></span><span className="shrink-0 font-semibold tabular-nums">{pkr(m.total)}</span></div>
            <div className="text-xs text-slate-500">
              {[m.on_pos > 0 && `on the POS ${pkr(m.on_pos)}`, m.at_close > 0 && `added at shift close ${pkr(m.at_close)}`, m.shop > 0 && `shop ${pkr(m.shop)}`].filter(Boolean).join(" + ")}
            </div>
            <div className={`text-xs ${m.account ? "text-sky-700" : "text-amber-700"}`}>{m.account ? <>→ {m.account}</> : <>Not linked to a bank account — set it in Cash & bank · <Ur>بینک منتخب نہیں</Ur></>}</div>
          </li>
        ))}
        {!o.methods.length && <li><Empty>No online money yet today · <Ur>آج ابھی کوئی آن لائن رقم نہیں</Ur></Empty></li>}
      </ul>
      {o.month?.length > 0 && (
        <div className="border-t border-slate-100 px-4 py-2.5 text-sm">
          <div className="flex items-baseline justify-between gap-2 font-medium"><span>This month · <Ur>اس مہینے</Ur></span><span className="shrink-0 font-semibold tabular-nums">{pkr(o.month_total)}</span></div>
          <div className="mt-0.5 flex flex-wrap gap-x-3 gap-y-0.5 text-xs text-slate-500">{o.month.map((m: any) => <span key={m.method} className="whitespace-nowrap">{(ONLINE_NAME[m.method]?.[0] ?? m.method).replace(/^\S+ /, "")} {pkr(m.total)}</span>)}</div>
        </div>
      )}
      {(o.open_shifts > 0 || onBank) && (
        <div className="flex flex-wrap items-center justify-between gap-2 border-t border-slate-100 px-4 py-2 text-xs text-slate-500">
          {o.open_shifts > 0 ? <span>{o.open_shifts} shift{o.open_shifts === 1 ? " is" : "s are"} still open — more may be added when {o.open_shifts === 1 ? "it closes" : "they close"} · <Ur>شفٹ بند ہونے پر مزید</Ur></span> : <span />}
          {onBank && <button className="min-h-9 font-medium text-brand-700 hover:underline" onClick={onBank}>Banks · <Ur>بینک</Ur> →</button>}
        </div>
      )}
    </div>
  );
}


import { Link, useNavigate } from "react-router-dom";
import { AlertTriangle, ArrowDownCircle, ArrowUpCircle, Banknote, Landmark, Scale, TrendingUp, Users, FileCheck2 } from "lucide-react";
import { useApi } from "../lib/api";
import { Loading } from "./ui";
import { Ur } from "./VoiceShell";
import { BankLogo } from "./BankParts";
import { OnlineToday } from "./OnlineMoney";
import { num, pkr, pkrShort } from "../lib/format";

const Row = ({ label, ur, v, to, tone = "", note }: { label: string; ur: string; v: number; to?: string; tone?: string; note?: string }) => {
  const body = (
    <div className="flex items-baseline justify-between gap-3 py-1.5 text-sm">
      <span className="min-w-0">{label} · <Ur className="text-slate-500">{ur}</Ur>{note && <span className="block text-xs text-slate-500">{note}</span>}</span>
      <span className={`shrink-0 font-semibold tabular-nums ${tone}`}>{pkr(v)}</span>
    </div>
  );
  return to ? <Link to={to} className="block rounded-lg px-1 -mx-1 hover:bg-slate-50">{body}</Link> : body;
};

/** The owner's first look: where the money is, who owes what, today across every module, and this month's profit. */
export function OwnerOverview() {
  const { data } = useApi<any>("/owner/overview", 60_000);
  const nav = useNavigate();
  if (!data) return <div className="card"><Loading /></div>;
  const m = data.money, o = data.owed_to_us, w = data.we_owe, t = data.today, mo = data.month;
  return (
    <div className="space-y-4">
      {/* where the money is */}
      <div className="overflow-hidden rounded-2xl bg-gradient-to-br from-slate-900 to-slate-700 p-4 text-white shadow">
        <div className="flex items-center gap-1.5 text-sm opacity-90"><Banknote size={16} /> Your money right now · <Ur>آپ کا پیسہ ابھی</Ur></div>
        <div className="text-3xl font-bold tabular-nums sm:text-4xl">{pkr(m.total)}</div>
        <div className="mt-3 grid grid-cols-3 gap-2 text-sm">
          <Link to="/cash" className="rounded-xl bg-white/10 p-2 hover:bg-white/15"><div className="text-xs opacity-80">Office cash · <Ur>نقد</Ur></div><Amt v={m.office_cash} /></Link>
          <Link to="/shifts" className="rounded-xl bg-white/10 p-2 hover:bg-white/15"><div className="text-xs opacity-80">With salesmen · <Ur>سیلزمین</Ur></div><Amt v={m.with_salesmen} /></Link>
          <Link to="/cash" className="rounded-xl bg-white/10 p-2 hover:bg-white/15"><div className="text-xs opacity-80">Banks · <Ur>بینک</Ur></div><Amt v={m.banks} /></Link>
        </div>
        {m.cheques_in.n > 0 && <Link to="/cashier?tab=cheques" className="mt-2 flex items-center gap-1.5 text-xs opacity-90 hover:underline"><FileCheck2 size={14} /> + {pkr(m.cheques_in.amount)} in {m.cheques_in.n} cheque{m.cheques_in.n === 1 ? "" : "s"} not cleared yet · <Ur>چیک ابھی کلیئر نہیں</Ur></Link>}
        {!m.last_count && <p className="mt-2 flex items-start gap-1.5 rounded-lg bg-amber-400/20 px-2 py-1.5 text-xs text-amber-100"><AlertTriangle size={14} className="mt-0.5 shrink-0" />Office cash has never been counted — ask the cashier to count it once so the book starts right · <Ur>ایک بار کیش گنوائیں</Ur></p>}
        {m.accounts.length > 0 && (
          <div className="mt-3 flex gap-2 overflow-x-auto pb-1">
            {m.accounts.map((a: any) => (
              <Link key={a.id} to="/cash" className="flex shrink-0 items-center gap-2 rounded-xl bg-white/10 px-2.5 py-1.5 text-xs hover:bg-white/15">
                <BankLogo name={a.bank} size={22} /><span><span className="block opacity-80">{a.bank.replace(/\s*\(.*\)/, "")}</span><b className="tabular-nums">{pkrShort(a.balance)}</b></span>
              </Link>
            ))}
          </div>
        )}
      </div>

      <div className="grid gap-4 lg:grid-cols-3 [&>*]:min-w-0">
        {/* lena / dena */}
        <div className="card p-4">
          <h2 className="mb-1 flex items-center gap-2 font-semibold"><Scale size={17} /> Owed to us / we owe · <Ur>لینا / دینا</Ur></h2>
          <div className="text-xs font-semibold uppercase tracking-wide text-emerald-700">Others owe us · <Ur>لینا</Ur></div>
          <Row label="Khata customers" ur="کھاتہ" v={o.khata} to="/khata" />
          <Row label="Wholesale clients" ur="ہول سیل" v={o.wholesale} to="/wholesale?tab=collect" />
          {o.staff_advances > 0 && <Row label="Staff advances" ur="ملازمین" v={o.staff_advances} to="/staff" />}
          <div className="mt-2 text-xs font-semibold uppercase tracking-wide text-rose-700">We owe · <Ur>دینا</Ur></div>
          <Row label="Suppliers (depots)" ur="سپلائر" v={w.suppliers} to="/suppliers" tone="text-rose-700"
            note={w.cheques_issued.n ? `${pkr(w.cheques_issued.amount)} of it in ${w.cheques_issued.n} cheque${w.cheques_issued.n === 1 ? "" : "s"} given, not yet paid by the bank` : undefined} />
          {w.customer_advances > 0 && <Row label="Customer advances" ur="ایڈوانس" v={w.customer_advances} tone="text-rose-700" />}
          {w.unused_coupons > 0 && <Row label="Unused fuel coupons" ur="کوپن" v={w.unused_coupons} to="/prepaid" tone="text-rose-700" />}
          {w.withholding_tax > 0 && <Row label="Tax to deposit (FBR)" ur="ٹیکس" v={w.withholding_tax} to="/accounts" tone="text-rose-700" />}
          {w.pending_expenses > 0 && <Row label="Expenses waiting approval" ur="منظوری" v={w.pending_expenses} to="/expenses" tone="text-rose-700" />}
          <div className="mt-2 flex items-baseline justify-between border-t-2 border-slate-800 pt-2">
            <span className="font-semibold">Net position · <Ur>کل مالیت</Ur><span className="block text-xs font-normal text-slate-500">money + owed to us − we owe (stock not included)</span></span>
            <span className={`shrink-0 text-xl font-bold tabular-nums ${data.net_position < 0 ? "text-red-600" : "text-emerald-700"}`}>{pkr(data.net_position)}</span>
          </div>
        </div>

        {/* today */}
        <div className="card p-4">
          <h2 className="mb-1 flex items-center gap-2 font-semibold"><TrendingUp size={17} /> Today · <Ur>آج</Ur></h2>
          <Row label={`Fuel sold (${num(t.retail_litres)} L)`} ur="فیول سیل" v={t.retail_sales} to="/reports" />
          {t.wholesale ? <Row label={`Wholesale (${num(t.wholesale_litres)} L)`} ur="ہول سیل" v={t.wholesale} to="/wholesale" /> : null}
          {t.shop ? <Row label="Shop & lubricants" ur="دکان" v={t.shop} to="/shop" /> : null}
          <div className="mt-1 grid grid-cols-2 gap-2 text-sm">
            <div className="rounded-xl bg-emerald-50 p-2"><div className="flex items-center gap-1 text-xs text-emerald-800"><ArrowDownCircle size={13} /> Money in · <Ur>آیا</Ur></div>
              <div className="font-semibold tabular-nums text-emerald-800">{pkr(t.money_in.cash + t.money_in.bank)}</div><div className="text-[11px] text-emerald-700">cash {pkrShort(t.money_in.cash)} · bank {pkrShort(t.money_in.bank)}</div></div>
            <div className="rounded-xl bg-rose-50 p-2"><div className="flex items-center gap-1 text-xs text-rose-800"><ArrowUpCircle size={13} /> Money out · <Ur>گیا</Ur></div>
              <div className="font-semibold tabular-nums text-rose-800">{pkr(t.money_out.cash + t.money_out.bank)}</div><div className="text-[11px] text-rose-700">cash {pkrShort(t.money_out.cash)} · bank {pkrShort(t.money_out.bank)}</div></div>
          </div>
          {t.deposited > 0 && <Row label="Cash put in bank" ur="بینک میں جمع" v={t.deposited} to="/cash" />}
          {t.profit_estimate != null && <Row label="Profit on today's sales (est.)" ur="منافع" v={t.profit_estimate} to="/insights" tone="text-emerald-700" />}
          <Link to="/cashier?tab=daybook" className="mt-1 inline-block py-1 text-xs font-medium text-brand-700 hover:underline">Day book — every entry · <Ur>روزنامچہ</Ur> →</Link>
        </div>

        {/* this month */}
        <div className="card p-4">
          <h2 className="mb-1 flex items-center gap-2 font-semibold"><Landmark size={17} /> This month · <Ur>اس مہینے</Ur></h2>
          <Row label="Sales (all)" ur="کل سیل" v={mo.income} to="/reports" />
          <Row label="Gross profit" ur="مجموعی منافع" v={mo.gross_profit} />
          <Row label="Expenses" ur="خرچے" v={mo.expenses} to="/expenses" tone="text-rose-700" />
          <div className="mt-2 flex items-baseline justify-between border-t-2 border-slate-800 pt-2">
            <span className="font-semibold">Net profit · <Ur>خالص منافع</Ur><span className="block text-xs font-normal text-slate-500">{mo.margin_pct}% of sales</span></span>
            <span className={`shrink-0 text-xl font-bold tabular-nums ${mo.net_profit < 0 ? "text-red-600" : "text-emerald-700"}`}>{pkr(mo.net_profit)}</span>
          </div>
          <Link to="/insights" className="mt-1 inline-block py-1 text-xs font-medium text-brand-700 hover:underline">Profit & loss, balance sheet → </Link>
        </div>
      </div>

      {/* online money: not in the cash, it goes to the bank */}
      <OnlineToday o={data.online} onBank={() => nav("/cash")} />

      {/* every module today */}
      <div className="card p-4">
        <h2 className="mb-2 flex items-center gap-2 font-semibold"><Users size={17} /> What happened today · <Ur>آج کیا ہوا</Ur></h2>
        <div className="grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-6">
          {data.activity.map((a: any) => (
            <Link key={a.key} to={a.to} className={`rounded-xl p-2.5 ring-1 ring-slate-200 hover:bg-slate-50 ${a.n ? "" : "opacity-60"}`}>
              <div className="text-xs text-slate-500">{a.en}<Ur className="block">{a.ur}</Ur></div>
              <div className="font-semibold tabular-nums">{a.v != null ? pkrShort(a.v) : a.l != null ? `${num(a.l)} L` : a.n}</div>
              <div className="text-[11px] text-slate-500">{a.n} {a.n === 1 ? "entry" : "entries"}</div>
            </Link>
          ))}
        </div>
      </div>
    </div>
  );
}

/** Full rupees where there is room; short ("Rs 39.2 L") in the small boxes on a phone. */
const Amt = ({ v }: { v: number }) => (
  <div className="font-semibold tabular-nums"><span className="sm:hidden">{pkrShort(v)}</span><span className="hidden sm:inline">{pkr(v)}</span></div>
);

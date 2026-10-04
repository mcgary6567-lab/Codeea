import { useEffect, useMemo, useState, type ReactNode } from "react";
import { Link } from "react-router-dom";
import { Fuel, Delete, Banknote, Smartphone, CreditCard, BookOpen, Check, Search, X, Clock, Zap } from "lucide-react";
import { api, useApi } from "../lib/api";
import { Loading, useAction } from "../components/ui";
import { num, pkr } from "../lib/format";
import { useAuth } from "../App";
import { useNotifications } from "../components/Notifications";

/* Big, colourful, bilingual (English + Urdu) point of sale designed for one-hand use on a tablet. */

const FUEL: Record<string, { en: string; ur: string; bg: string; ring: string }> = {
  PMG: { en: "Petrol", ur: "پیٹرول", bg: "bg-[#2a78d6]", ring: "ring-[#2a78d6]" },
  HOBC: { en: "Hi-Octane", ur: "ہائی آکٹین", bg: "bg-[#eb6834]", ring: "ring-[#eb6834]" },
  HSD: { en: "Diesel", ur: "ڈیزل", bg: "bg-[#1baf7a]", ring: "ring-[#1baf7a]" },
};

const PAY = [
  { key: "cash", en: "Cash", ur: "نقد", icon: Banknote, cls: "bg-emerald-600" },
  { key: "easypaisa", en: "Easypaisa", ur: "ایزی پیسہ", icon: Smartphone, cls: "bg-[#0f9d58]" },
  { key: "jazzcash", en: "JazzCash", ur: "جاز کیش", icon: Smartphone, cls: "bg-[#c8102e]" },
  { key: "card", en: "Card", ur: "کارڈ", icon: CreditCard, cls: "bg-slate-700" },
  { key: "raast", en: "Raast", ur: "راست", icon: Zap, cls: "bg-[#4a3aa7]" },
  { key: "khata", en: "Khata", ur: "کھاتہ", icon: BookOpen, cls: "bg-amber-600" },
] as const;

export const TYPE_ICON: Record<string, string> = { police: "🚓", school: "🏫", government: "🏛️", hospital: "🚑", fleet: "🚚", farmer: "🚜", business: "🏢", retail: "🚗" };
const TYPE_LABEL: Record<string, string> = { police: "Police", school: "School", government: "Govt.", hospital: "Health", fleet: "Fleet", farmer: "Farmer", business: "Business", retail: "Customer" };
const QUICK = { amount: [500, 1000, 2000, 5000], litres: [5, 10, 20, 50] };

const Ur = ({ children, className = "" }: { children: ReactNode; className?: string }) => <span lang="ur" dir="rtl" className={`font-urdu ${className}`}>{children}</span>;

export default function Pos() {
  const { user } = useAuth();
  const isSalesman = user?.role === "salesman";
  const stations = useApi<any[]>(isSalesman ? null : "/stations");
  const [stationId, setStationId] = useState<number | null>(null);
  const today = useApi<any>(isSalesman ? "/pos/today" : stationId ? `/pos/today?station_id=${stationId}` : null);
  useEffect(() => { if (!isSalesman && stations.data && !stationId) setStationId(stations.data[0].id); }, [stations.data]);
  const notif = useNotifications();
  const priceLock = isSalesman && notif.data?.pending_ack.some((n) => n.type === "price_change");
  const { busy, run } = useAction();

  const [product, setProduct] = useState<string | null>(null);
  const [mode, setMode] = useState<"amount" | "litres">("amount");
  const [entry, setEntry] = useState("");
  const [pay, setPay] = useState<string | null>(null);
  const [khata, setKhata] = useState<{ account: any; vehicle: string; slip: string } | null>(null);
  const [pickKhata, setPickKhata] = useState(false);
  const [done, setDone] = useState<any>(null);

  const d = today.data;
  const rate = product && d ? d.prices[product] : 0;
  const value = Number(entry) || 0;
  const litres = mode === "litres" ? value : rate ? value / rate : 0;
  const amount = mode === "amount" ? value : value * rate;
  const ready = Boolean(product && value > 0 && pay && (pay !== "khata" || khata));
  const reset = () => { setProduct(null); setEntry(""); setPay(null); setKhata(null); setMode("amount"); };

  const press = (k: string) => {
    if (k === "C") return setEntry("");
    if (k === "⌫") return setEntry((e) => e.slice(0, -1));
    if (k === "." && entry.includes(".")) return;
    if (entry.replace(".", "").length >= 7) return;
    setEntry((e) => (e === "0" && k !== "." ? k : e + k));
  };

  const save = async () => {
    if (!ready || !d) return;
    const body: any = { station_id: d.station.id, product, payment_method: pay, [mode]: value };
    if (pay === "khata" && khata) Object.assign(body, { customer_id: khata.account.id, vehicle_no: khata.vehicle || null, slip_no: khata.slip || null });
    const r = await run(() => api("/sales", { body }));
    if (r) {
      setDone({ ...r, khata_name: khata?.account.name });
      reset();
      today.reload();
      setTimeout(() => setDone(null), 2500);
    }
  };

  if (!isSalesman && !stations.data) return <Loading />;
  if (!d) return <Loading />;
  const shiftOpen = Boolean(d.shift);

  return (
    <div className="-m-4 min-h-[calc(100vh-56px)] bg-slate-100 p-3 lg:-m-6 lg:min-h-screen lg:p-4">
      {/* top strip */}
      <div className="mb-3 flex flex-wrap items-center gap-3">
        <div className="flex items-center gap-2 text-lg font-semibold"><Fuel className="text-brand-600" /> {d.station.name}</div>
        {!isSalesman && <select className="input w-auto" value={stationId ?? ""} onChange={(e) => { setStationId(Number(e.target.value)); reset(); }}>{stations.data!.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}</select>}
        <div className="ml-auto flex items-center gap-2">
          {shiftOpen ? (
            <Link to="/shifts" className={`flex items-center gap-2 rounded-xl px-4 py-2 font-medium text-white ${d.shift.hours_open >= 12 ? "bg-red-600" : "bg-slate-800"}`}>
              <Clock size={18} /> {d.shift.attendant} · {Math.floor(d.shift.hours_open)}h {String(Math.floor((d.shift.hours_open % 1) * 60)).padStart(2, "0")}m
              <span className="rounded-lg bg-white/20 px-2 py-0.5 text-sm">End shift →</span>
            </Link>
          ) : <span className="rounded-xl bg-amber-100 px-4 py-2 font-medium text-amber-800">No open shift</span>}
        </div>
      </div>

      <div className="grid gap-3 xl:grid-cols-[1fr_340px]">
        <div className="space-y-3">
          {/* 1. fuel */}
          <Step n={1} en="Choose fuel" ur="تیل چنیں">
            <div className="grid grid-cols-3 gap-3">
              {d.products.map((p: string) => (
                <button key={p} aria-pressed={product === p} onClick={() => setProduct(p)}
                  className={`flex flex-col items-center justify-center rounded-2xl p-4 text-white shadow transition active:scale-95 ${FUEL[p].bg} ${product === p ? `ring-4 ring-offset-2 ${FUEL[p].ring} scale-[1.02]` : product ? "opacity-50" : ""}`}>
                  <Fuel size={34} />
                  <span className="mt-1 text-xl font-bold sm:text-2xl">{FUEL[p].en}</span>
                  <Ur className="text-lg leading-loose">{FUEL[p].ur}</Ur>
                  <span className="rounded-lg bg-white/20 px-3 py-0.5 text-lg font-semibold tabular-nums">Rs {d.prices[p]?.toFixed(2)}</span>
                </button>
              ))}
            </div>
          </Step>

          {/* 2. amount */}
          <Step n={2} en="How much?" ur="کتنا؟">
            <div className="grid gap-3 lg:grid-cols-[1fr_300px]">
              <div className="space-y-3">
                <div className="grid grid-cols-2 gap-2">
                  {(["amount", "litres"] as const).map((m) => (
                    <button key={m} aria-pressed={mode === m} onClick={() => { setMode(m); setEntry(""); }}
                      className={`rounded-xl py-3 text-lg font-semibold ${mode === m ? "bg-slate-900 text-white" : "bg-white text-slate-700 ring-1 ring-slate-300"}`}>
                      {m === "amount" ? <>Rupees · <Ur>روپے</Ur></> : <>Litres · <Ur>لیٹر</Ur></>}
                    </button>
                  ))}
                </div>
                <div className="rounded-2xl bg-white p-4 text-right shadow-inner ring-1 ring-slate-200">
                  <div className="text-sm text-slate-500">{mode === "amount" ? "Rs" : "Litres"}</div>
                  <div className="min-h-[3.5rem] text-5xl font-bold tabular-nums text-slate-900">{entry || "0"}</div>
                  <div className="mt-1 min-h-[1.75rem] text-xl font-semibold tabular-nums text-brand-700">
                    {product && value > 0 ? (mode === "amount" ? `= ${num(litres, 2)} L` : `= ${pkr(amount)}`) : product ? "" : "Choose fuel first"}
                  </div>
                </div>
                <div className="grid grid-cols-4 gap-2">
                  {QUICK[mode].map((q) => (
                    <button key={q} onClick={() => setEntry(String(q))} className="rounded-xl bg-white py-3 text-lg font-semibold text-slate-800 ring-1 ring-slate-300 active:bg-slate-200">
                      {mode === "amount" ? q.toLocaleString("en-IN") : `${q} L`}
                    </button>
                  ))}
                </div>
              </div>
              <div className="grid grid-cols-3 gap-2">
                {["7", "8", "9", "4", "5", "6", "1", "2", "3", ".", "0", "⌫"].map((k) => (
                  <button key={k} onClick={() => press(k)} aria-label={k === "⌫" ? "Backspace" : k}
                    className="flex h-16 items-center justify-center rounded-xl bg-white text-3xl font-semibold text-slate-900 shadow-sm ring-1 ring-slate-300 active:bg-slate-200">
                    {k === "⌫" ? <Delete size={28} /> : k}
                  </button>
                ))}
                <button onClick={() => press("C")} className="col-span-3 h-12 rounded-xl bg-slate-200 text-lg font-semibold text-slate-700 active:bg-slate-300">Clear · <Ur>صاف</Ur></button>
              </div>
            </div>
          </Step>

          {/* 3. payment */}
          <Step n={3} en="Payment" ur="ادائیگی">
            <div className="grid grid-cols-3 gap-3 sm:grid-cols-6">
              {PAY.map((p) => (
                <button key={p.key} aria-pressed={pay === p.key} onClick={() => { setPay(p.key); if (p.key === "khata") setPickKhata(true); else setKhata(null); }}
                  className={`flex flex-col items-center justify-center rounded-2xl py-3 text-white shadow transition active:scale-95 ${p.cls} ${pay === p.key ? "scale-[1.03] ring-4 ring-slate-900 ring-offset-2" : pay ? "opacity-50" : ""}`}>
                  <p.icon size={28} />
                  <span className="mt-1 text-base font-bold">{p.en}</span>
                  <Ur className="text-sm leading-loose">{p.ur}</Ur>
                </button>
              ))}
            </div>
            {pay === "khata" && khata && (
              <button onClick={() => setPickKhata(true)} className="mt-3 flex w-full items-center gap-3 rounded-xl bg-amber-50 p-3 text-left ring-1 ring-amber-300">
                <span className="text-3xl">{TYPE_ICON[khata.account.type] ?? "📒"}</span>
                <span className="flex-1"><span className="block text-lg font-semibold">{khata.account.name}</span>
                  <span className="text-sm text-slate-600">{[khata.vehicle && `Vehicle ${khata.vehicle}`, khata.slip && `Slip ${khata.slip}`].filter(Boolean).join(" · ") || "No vehicle / slip"}</span></span>
                <span className="text-sm text-amber-700 underline">Change</span>
              </button>
            )}
          </Step>

          {/* 4. save */}
          <div className="sticky bottom-0 z-10 rounded-2xl bg-white p-3 shadow-lg ring-1 ring-slate-200">
            <div className="flex flex-wrap items-center gap-3">
              <div className="min-w-0 flex-1 text-lg">
                {product && value > 0 ? (
                  <><b>{FUEL[product].en}</b> {num(litres, 2)} L × Rs {rate} = <b className="text-2xl">{pkr(amount)}</b>{pay && <> · <span className="capitalize">{pay === "khata" ? `Khata${khata ? ` (${khata.account.name})` : ""}` : pay}</span></>}</>
                ) : <span className="text-slate-500">Choose fuel, amount and payment · <Ur>تیل، رقم اور ادائیگی چنیں</Ur></span>}
              </div>
              <button onClick={reset} className="rounded-xl bg-slate-200 px-5 py-4 text-lg font-semibold text-slate-700 active:bg-slate-300"><X className="inline" size={20} /> Cancel</button>
              <button onClick={save} disabled={!ready || busy || (!shiftOpen && isSalesman) || !!priceLock}
                className="flex items-center gap-2 rounded-xl bg-emerald-600 px-8 py-4 text-xl font-bold text-white shadow active:scale-95 disabled:bg-slate-300">
                <Check size={26} /> Save · <Ur>محفوظ</Ur>
              </button>
            </div>
          </div>
        </div>

        <ShiftPanel d={d} />
      </div>

      {/* blocking states */}
      {isSalesman && !shiftOpen && <Blocker><StartShift onStarted={() => today.reload()} /></Blocker>}
      {priceLock && shiftOpen && (
        <Blocker>
          <div className="text-6xl">⛽</div>
          <h2 className="mt-2 text-2xl font-bold">Price changed · <Ur>ریٹ تبدیل ہو گیا</Ur></h2>
          <p className="mt-1 text-slate-600">Update the dispenser rate and confirm to continue.</p>
          <button className="mt-4 rounded-xl bg-emerald-600 px-8 py-4 text-xl font-bold text-white" onClick={() => notif.setSnoozed(null)}>Confirm new price</button>
        </Blocker>
      )}
      {pickKhata && <KhataPicker onClose={() => { setPickKhata(false); if (!khata) setPay(null); }} onPick={(k) => { setKhata(k); setPickKhata(false); }} initial={khata} />}
      {done && (
        <div role="status" className="fixed inset-0 z-50 flex items-center justify-center bg-emerald-600/95 p-6 text-center text-white" onClick={() => setDone(null)}>
          <div>
            <div className="mx-auto flex h-24 w-24 items-center justify-center rounded-full bg-white text-emerald-600"><Check size={64} strokeWidth={3} /></div>
            <div className="mt-4 text-3xl font-bold">Sale saved · <Ur>سیل محفوظ</Ur></div>
            <div className="mt-2 text-2xl">{FUEL[done.product].en} {num(done.litres, 2)} L × Rs {done.rate}</div>
            <div className="text-5xl font-bold tabular-nums">{pkr(done.amount)}</div>
            <div className="mt-2 text-xl capitalize">{done.payment_method === "khata" ? `Khata — ${done.khata_name}` : done.payment_method}</div>
          </div>
        </div>
      )}
    </div>
  );
}

function Step({ n, en, ur, children }: { n: number; en: string; ur: string; children: ReactNode }) {
  return (
    <section className="rounded-2xl bg-white p-3 shadow-sm ring-1 ring-slate-200">
      <h2 className="mb-2 flex items-center gap-2 text-lg font-semibold">
        <span className="flex h-8 w-8 items-center justify-center rounded-full bg-slate-900 text-white">{n}</span>{en}
        <Ur className="ml-auto text-xl text-slate-500">{ur}</Ur>
      </h2>
      {children}
    </section>
  );
}

const Blocker = ({ children }: { children: ReactNode }) => (
  <div className="fixed inset-0 z-40 flex items-center justify-center bg-slate-900/60 p-4 lg:left-60"><div className="w-full max-w-md rounded-2xl bg-white p-6 text-center shadow-xl">{children}</div></div>
);

function StartShift({ onStarted }: { onStarted: () => void }) {
  const { busy, run } = useAction();
  return (
    <>
      <div className="text-6xl">🕘</div>
      <h2 className="mt-2 text-2xl font-bold">Start your shift · <Ur>شفٹ شروع کریں</Ur></h2>
      <p className="mt-1 text-slate-600">Meter readings are taken automatically.</p>
      <button className="mt-4 w-full rounded-xl bg-emerald-600 py-5 text-2xl font-bold text-white active:scale-95" disabled={busy}
        onClick={() => run(() => api("/shifts/open", { body: {} }), "Shift started").then((r) => r && onStarted())}>Start shift</button>
    </>
  );
}

function ShiftPanel({ d }: { d: any }) {
  const s = d.shift?.summary;
  const cash = s?.by_payment.find((p: any) => p.method === "cash")?.amount ?? 0;
  return (
    <aside className="space-y-3">
      <div className="rounded-2xl bg-slate-900 p-4 text-white">
        <div className="text-sm text-slate-300">Cash in hand (should be) · <Ur>نقد</Ur></div>
        <div className="text-4xl font-bold tabular-nums">{pkr(cash)}</div>
        <div className="mt-3 grid grid-cols-2 gap-2 text-sm">
          <div className="rounded-lg bg-white/10 p-2"><div className="text-slate-300">Total sales</div><div className="text-lg font-semibold tabular-nums">{pkr(s?.amount ?? 0)}</div></div>
          <div className="rounded-lg bg-white/10 p-2"><div className="text-slate-300">Litres</div><div className="text-lg font-semibold tabular-nums">{num(s?.litres ?? 0, 1)} L</div></div>
        </div>
        <div className="mt-2 space-y-1 text-sm">
          {(s?.by_payment ?? []).filter((p: any) => p.method !== "cash").map((p: any) => <div key={p.method} className="flex justify-between capitalize"><span className="text-slate-300">{p.method}</span><span className="tabular-nums">{pkr(p.amount)}</span></div>)}
        </div>
      </div>
      <div className="rounded-2xl bg-white p-3 ring-1 ring-slate-200">
        <h3 className="mb-2 font-semibold">Last sales · <Ur className="text-slate-500">آخری سیل</Ur></h3>
        <ul className="divide-y divide-slate-100">
          {d.recent.map((r: any) => (
            <li key={r.id} className="flex items-center gap-2 py-2 text-sm">
              <span className={`h-3 w-3 shrink-0 rounded-full ${FUEL[r.product]?.bg}`} />
              <span className="flex-1"><b>{num(r.litres, 2)} L</b> {FUEL[r.product]?.en}<span className="block text-xs text-slate-500">{new Date(r.created_at).toLocaleTimeString("en-PK", { hour: "2-digit", minute: "2-digit" })} · {r.payment_method === "khata" ? `📒 ${r.customer_name}${r.slip_no ? ` · ${r.slip_no}` : ""}` : r.payment_method}</span></span>
              <span className="font-semibold tabular-nums">{pkr(r.amount)}</span>
            </li>
          ))}
          {!d.recent.length && <li className="py-4 text-center text-sm text-slate-500">No sales yet</li>}
        </ul>
      </div>
    </aside>
  );
}

function KhataPicker({ initial, onClose, onPick }: { initial: any; onClose: () => void; onPick: (k: { account: any; vehicle: string; slip: string }) => void }) {
  const { data } = useApi<any[]>("/pos/khata-accounts");
  const [q, setQ] = useState("");
  const [type, setType] = useState("");
  const [account, setAccount] = useState<any>(initial?.account ?? null);
  const [vehicle, setVehicle] = useState(initial?.vehicle ?? "");
  const [slip, setSlip] = useState(initial?.slip ?? "");
  const list = useMemo(() => (data ?? []).filter((a) => (!type || (type === "institution" ? ["police", "school", "government", "hospital"].includes(a.type) : a.type === type)) && a.name.toLowerCase().includes(q.toLowerCase())), [data, q, type]);
  const institution = account && ["police", "school", "government", "hospital"].includes(account.type);

  return (
    <div className="fixed inset-0 z-50 flex items-start justify-center overflow-y-auto bg-slate-900/60 p-3 sm:p-6">
      <div className="w-full max-w-4xl rounded-2xl bg-white p-4 shadow-xl">
        <div className="mb-3 flex items-center gap-2">
          <h2 className="flex-1 text-2xl font-bold">Khata account · <Ur>کھاتہ</Ur></h2>
          <button onClick={onClose} className="rounded-xl bg-slate-100 p-3" aria-label="Close"><X /></button>
        </div>
        {!account ? (
          <>
            <div className="relative mb-3"><Search className="absolute left-3 top-3.5 text-slate-400" /><input autoFocus className="input py-3 pl-11 text-lg" placeholder="Search name…" value={q} onChange={(e) => setQ(e.target.value)} /></div>
            <div className="mb-3 flex flex-wrap gap-2">
              {[["", "All"], ["institution", "🏛️ Police / Govt / School"], ["fleet", "🚚 Fleet"], ["farmer", "🚜 Farmer"], ["business", "🏢 Business"]].map(([k, l]) => (
                <button key={k} onClick={() => setType(k)} className={`rounded-full px-4 py-2 text-base ${type === k ? "bg-slate-900 text-white" : "bg-slate-100"}`}>{l}</button>
              ))}
            </div>
            {!data ? <Loading /> : (
              <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
                {list.map((a) => (
                  <button key={a.id} disabled={a.status === "full"} onClick={() => { setAccount(a); setVehicle(""); }}
                    className={`flex items-center gap-3 rounded-2xl p-4 text-left ring-2 transition active:scale-95 ${a.status === "full" ? "cursor-not-allowed bg-red-50 ring-red-200 opacity-70" : "bg-white ring-slate-200 hover:ring-amber-400"}`}>
                    <span className="text-4xl">{TYPE_ICON[a.type] ?? "📒"}</span>
                    <span className="min-w-0 flex-1">
                      <span className="block text-lg font-semibold leading-tight">{a.name} {a.is_new && <span className="ml-1 rounded-full bg-emerald-600 px-2 py-0.5 align-middle text-xs font-bold text-white">NEW</span>}</span>
                      <span className="text-sm text-slate-500">{TYPE_LABEL[a.type] ?? a.type}{a.city ? ` · ${a.city}` : ""}</span>
                      {a.status !== "ok" && <span className={`mt-1 block text-sm font-semibold ${a.status === "full" ? "text-red-600" : "text-amber-600"}`}>{a.status === "full" ? "Limit full — ask manager" : "Near limit"}</span>}
                    </span>
                  </button>
                ))}
                {!list.length && <div className="col-span-full py-8 text-center text-slate-500">No khata account found. Ask the manager to open one.</div>}
              </div>
            )}
          </>
        ) : (
          <div className="space-y-4">
            <div className="flex items-center gap-3 rounded-2xl bg-amber-50 p-4 ring-1 ring-amber-200">
              <span className="text-5xl">{TYPE_ICON[account.type] ?? "📒"}</span>
              <div className="flex-1"><div className="text-2xl font-bold">{account.name}</div><div className="text-slate-600">{TYPE_LABEL[account.type]}</div></div>
              <button className="rounded-xl bg-white px-4 py-2 ring-1 ring-slate-300" onClick={() => setAccount(null)}>Change</button>
            </div>
            <div>
              <div className="mb-2 text-lg font-semibold">Vehicle · <Ur>گاڑی</Ur></div>
              <div className="flex flex-wrap gap-2">
                {account.vehicles.map((v: string) => (
                  <button key={v} onClick={() => setVehicle(v)} aria-pressed={vehicle === v} className={`rounded-xl px-4 py-3 text-lg font-semibold ${vehicle === v ? "bg-slate-900 text-white" : "bg-slate-100"}`}>{v}</button>
                ))}
              </div>
              <input className="input mt-2 py-3 text-lg uppercase" placeholder="Other vehicle no. (e.g. LEA-1234)" value={account.vehicles.includes(vehicle) ? "" : vehicle} onChange={(e) => setVehicle(e.target.value)} />
            </div>
            <div>
              <div className="mb-2 text-lg font-semibold">Slip / parchi no. · <Ur>پرچی نمبر</Ur></div>
              <input className="input py-3 text-2xl" inputMode="numeric" placeholder="e.g. 4521" value={slip} onChange={(e) => setSlip(e.target.value)} />
              {institution && !slip && <p className="mt-1 text-sm text-amber-700">Government offices pay against the slip — please write the slip number.</p>}
            </div>
            <p className="text-sm text-slate-500">Today's rate is saved with this entry, so the bill always shows the price on that day.</p>
            <button className="w-full rounded-xl bg-emerald-600 py-4 text-2xl font-bold text-white active:scale-95" onClick={() => onPick({ account, vehicle: vehicle.trim(), slip: slip.trim() })}>
              <Check className="inline" size={26} /> Done · <Ur>ٹھیک ہے</Ur>
            </button>
          </div>
        )}
      </div>
    </div>
  );
}

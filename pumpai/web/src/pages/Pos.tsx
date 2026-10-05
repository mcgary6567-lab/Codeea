import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { Link } from "react-router-dom";
import { Fuel, Delete, Banknote, Smartphone, CreditCard, BookOpen, Check, Search, X, Clock, Zap, Undo2, WifiOff, CloudUpload, Gift, ShoppingBasket, Plus, Minus, ScanBarcode, Ticket, Wallet } from "lucide-react";
import QRCode from "qrcode";
import { api, useApi } from "../lib/api";
import { Loading, useAction, useToast } from "../components/ui";
import { newUid, isOffline, queueSale, dropQueued, dismissFailed, useOfflineQueue, cacheGet, cacheSet } from "../lib/offline";
import { num, pkr } from "../lib/format";
import { useAuth } from "../App";
import { useNotifications } from "../components/Notifications";
import { ShiftExpenses, StartShiftSheet } from "../components/ShiftParts";
import { VoiceButton } from "../components/Capture";
import { CardScanner } from "../components/CardScanner";

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
  { key: "loyalty", en: "Points", ur: "پوائنٹس", icon: Gift, cls: "bg-pink-600" },
  { key: "coupon", en: "Coupon", ur: "کوپن", icon: Ticket, cls: "bg-orange-600" },
  { key: "wallet", en: "Wallet", ur: "والٹ", icon: Wallet, cls: "bg-sky-700" },
] as const;
/** Payment options switched off on this pump's POS (points and coupons are not used). */
const OFF_PAY: string[] = ["loyalty", "coupon"];
/** Paid before (coupon / wallet / points): needs internet to check, cannot be kept offline. */
const ONLINE_ONLY = ["loyalty", "coupon", "wallet"];

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
  const toast = useToast();
  const queue = useOfflineQueue(() => today.reload());
  const [saving, setSaving] = useState(false);
  const cacheKey = `pos_today_${isSalesman ? "me" : stationId}`;
  useEffect(() => { if (today.data) cacheSet(cacheKey, today.data); }, [today.data, cacheKey]);

  const [product, setProduct] = useState<string | null>(null);
  const [mode, setMode] = useState<"amount" | "litres">("amount");
  const [entry, setEntry] = useState("");
  const [pay, setPay] = useState<string | null>(null);
  const [khata, setKhata] = useState<{ account: any; vehicle: string; slip: string } | null>(null);
  const [pickKhata, setPickKhata] = useState(false);
  const [scan, setScan] = useState(false);
  const [done, setDone] = useState<any>(null);
  const [heard, setHeard] = useState<string | null>(null);
  const [tab, setTab] = useState<"fuel" | "shop">("fuel");
  // practice mode for new staff: nothing is sent to the server
  const [training, setTraining] = useState(() => { try { return sessionStorage.getItem("pumpai_training") === "1"; } catch { return false; } });
  const toggleTraining = () => setTraining((x) => { try { sessionStorage.setItem("pumpai_training", x ? "0" : "1"); } catch { /* ignore */ } return !x; });
  const [pointsCust, setPointsCust] = useState<any>(null);
  const [pickPoints, setPickPoints] = useState(false);
  const [coupon, setCoupon] = useState<any>(null);
  const [scanCoupon, setScanCoupon] = useState(false);
  const [walletAcct, setWalletAcct] = useState<any>(null);
  const [pickWallet, setPickWallet] = useState(false);

  const d = today.data ?? cacheGet<any>(cacheKey);
  const rate = product && d ? d.prices[product] : 0;
  const value = Number(entry) || 0;
  const litres = mode === "litres" ? value : rate ? value / rate : 0;
  const amount = mode === "amount" ? value : value * rate;
  const ready = Boolean(product && value > 0 && pay && (pay !== "khata" || khata) && (pay !== "loyalty" || pointsCust) && (pay !== "coupon" || coupon) && (pay !== "wallet" || walletAcct));
  const reset = () => { setProduct(null); setEntry(""); setPay(null); setKhata(null); setMode("amount"); setHeard(null); setPointsCust(null); setCoupon(null); setWalletAcct(null); };

  /** Fill the POS from a spoken sentence; the salesman checks it and presses Save. */
  const applyVoice = async (v: any) => {
    if (v.product && d?.products.includes(v.product)) setProduct(v.product);
    if (v.litres) { setMode("litres"); setEntry(String(v.litres)); } else if (v.amount) { setMode("amount"); setEntry(String(v.amount)); }
    if (v.payment_method) setPay(v.payment_method);
    if (v.payment_method === "khata") {
      let accts = cacheGet<any[]>("khata_accounts");
      if (!accts) try { accts = await api<any[]>("/pos/khata-accounts"); cacheSet("khata_accounts", accts); } catch { accts = []; }
      const acc = accts!.find((a) => a.id === v.customer_id);
      if (acc && acc.status !== "full") setKhata({ account: acc, vehicle: v.vehicle_no ?? "", slip: v.slip_no ?? "" });
      else { setKhata(null); setPickKhata(true); }
    } else setKhata(null);
    setHeard(v.heard);
  };

  const press = (k: string) => {
    if (k === "C") return setEntry("");
    if (k === "⌫") return setEntry((e) => e.slice(0, -1));
    if (k === "." && entry.includes(".")) return;
    if (entry.replace(".", "").length >= 7) return;
    setEntry((e) => (e === "0" && k !== "." ? k : e + k));
  };

  const save = async () => {
    if (!ready || !d || saving) return;
    const body: any = { station_id: d.station.id, product, payment_method: pay, [mode]: value, client_uid: newUid() };
    if (pay === "khata" && khata) Object.assign(body, { customer_id: khata.account.id, vehicle_no: khata.vehicle || null, slip_no: khata.slip || null });
    if (pay === "loyalty" && pointsCust) body.customer_id = pointsCust.id;
    if (pay === "coupon" && coupon) body.coupon_code = coupon.code;
    if (pay === "wallet" && walletAcct) body.customer_id = walletAcct.id;
    const shown = { product, litres, rate, amount, payment_method: pay, khata_name: khata?.account.name ?? walletAcct?.name, client_uid: body.client_uid };
    if (training) { setDone({ ...shown, training: true }); reset(); return; }
    setSaving(true);
    try {
      const r = await api("/sales", { body });
      setDone({ ...r, khata_name: khata?.account.name ?? walletAcct?.name });
      today.reload();
    } catch (e: any) {
      if (!isOffline(e)) { toast("err", e.message); return; }
      if (ONLINE_ONLY.includes(pay!)) { toast("err", "Points, coupons and wallets need internet · انٹرنیٹ ضروری ہے"); return; }
      // no internet: keep it on the tablet, it uploads by itself when the connection is back
      queueSale(body, `${FUEL[product!].en} ${num(litres, 2)} L · ${pkr(amount)} · ${pay}`);
      setDone({ ...shown, offline: true });
    } finally { setSaving(false); }
    reset();
  };
  useEffect(() => { if (!done) return; const id = setTimeout(() => setDone(null), 6000); return () => clearTimeout(id); }, [done]);
  const undo = async (sale: any) => {
    if (sale.offline) { dropQueued(sale.client_uid); setDone(null); toast("ok", "Sale removed"); return; }
    if (await run(() => api(sale.shop ? `/shop/sales/${sale.id}/undo` : `/sales/${sale.id}/undo`, { body: {} }), "Sale undone · سیل واپس")) { setDone(null); today.reload(); }
  };

  if (!isSalesman && !stations.data) return <Loading />;
  if (!d) return <Loading />;
  const shiftOpen = Boolean(d.shift);

  return (
    <div className="-m-4 min-h-[calc(100vh-56px)] bg-slate-100 p-3 lg:-m-6 lg:min-h-screen lg:p-4">
      {training && <div className="mb-3 rounded-xl bg-amber-500 px-4 py-3 text-lg font-semibold text-white">🎓 Training mode — practice only, nothing is saved · <Ur>مشق — کچھ محفوظ نہیں ہوگا</Ur></div>}
      {(!queue.online || queue.pending.length > 0) && (
        <div className={`mb-3 flex items-center gap-3 rounded-xl px-4 py-3 text-white ${queue.online ? "bg-blue-600" : "bg-slate-800"}`} role="status">
          {queue.online ? <CloudUpload /> : <WifiOff />}
          <span className="text-lg font-semibold">{queue.online ? "Uploading sales…" : "No internet · انٹرنیٹ نہیں"}</span>
          <span className="ml-auto">{queue.pending.length ? `${queue.pending.length} sale${queue.pending.length === 1 ? "" : "s"} saved on tablet` : "Sales will be saved on this tablet"}</span>
        </div>
      )}
      {queue.failed.length > 0 && (
        <div className="mb-3 rounded-xl bg-red-50 p-3 text-sm text-red-800 ring-1 ring-red-200">
          <b>Not uploaded — show the manager:</b>
          {queue.failed.map((f) => (
            <div key={f.body.client_uid} className="mt-1 flex items-center gap-2">
              <span className="flex-1">{new Date(f.queued_at).toLocaleString("en-PK", { dateStyle: "short", timeStyle: "short" })} · {f.label} — {f.error}</span>
              <button className="rounded bg-white px-2 py-1 ring-1 ring-red-200" onClick={() => dismissFailed(f.body.client_uid)}>OK</button>
            </div>
          ))}
        </div>
      )}
      {/* top bar: who / where / shift, and the big buttons (same look as the customer khata page) */}
      <div className="mb-3 rounded-2xl bg-brand-900 p-3 text-white shadow">
        <div className="flex flex-wrap items-center gap-3">
          <div className="min-w-0">
            <div className="flex items-center gap-2 text-lg font-bold"><Fuel size={20} /> {d.station.name}</div>
            <div className="text-sm text-emerald-100/90">{user?.name}{shiftOpen ? <> · ⏱ {Math.floor(d.shift.hours_open)}h {String(Math.floor((d.shift.hours_open % 1) * 60)).padStart(2, "0")}m</> : <> · <span className="font-semibold text-amber-300">No open shift · <Ur>شفٹ شروع نہیں</Ur></span></>}</div>
          </div>
          <div className="flex rounded-xl bg-white/10 p-1" role="tablist">
            {([["fuel", "Fuel", "تیل", Fuel], ["shop", "Shop", "دکان", ShoppingBasket]] as const).map(([k, en, ur, I]) => (
              <button key={k} role="tab" aria-selected={tab === k} onClick={() => setTab(k)}
                className={`flex items-center gap-2 rounded-lg px-4 py-2 text-base font-semibold ${tab === k ? "bg-white text-brand-900" : "text-white/90"}`}><I size={18} /> {en} · <Ur>{ur}</Ur></button>
            ))}
          </div>
          {!isSalesman && <select className="input w-auto text-slate-900" value={stationId ?? ""} onChange={(e) => { setStationId(Number(e.target.value)); reset(); }}>{stations.data!.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}</select>}
          {training && <span className="rounded-lg bg-amber-400 px-3 py-1 text-sm font-bold text-amber-950">🎓 TRAINING — nothing is saved · <Ur>مشق</Ur></span>}
        </div>
        <div className="mt-3 grid grid-cols-3 gap-2 sm:grid-cols-6">
          {[
            { k: "scan", i: "📷", en: "Scan card", ur: "کارڈ سکین", go: () => { setTab("fuel"); setScan(true); } },
            { k: "exp", i: "💸", en: "Expense", ur: "خرچہ", go: () => document.getElementById("pos-expenses")?.scrollIntoView({ behavior: "smooth", block: "center" }) },
            { k: "checks", i: "✅", en: "Daily checks", ur: "روزانہ چیک", to: "/checklist" },
            { k: "att", i: "🙋", en: "Attendance", ur: "حاضری", to: "/my-account" },
            { k: "train", i: "🎓", en: training ? "Training ON" : "Training", ur: "مشق", go: toggleTraining, on: training },
            { k: "end", i: "⏹️", en: "End shift", ur: "شفٹ ختم", to: "/shifts", warn: shiftOpen && d.shift.hours_open >= 12 },
          ].map((b) => {
            const cls = `flex flex-col items-center justify-center gap-0.5 rounded-xl px-2 py-2 text-sm font-semibold active:scale-95 ${b.on ? "bg-amber-400 text-amber-950" : b.warn ? "bg-red-600 text-white" : "bg-white/10 text-white hover:bg-white/15"}`;
            const body = <><span className="text-2xl leading-none">{b.i}</span><span>{b.en}</span><Ur className="text-xs font-normal opacity-90">{b.ur}</Ur></>;
            return b.to ? <Link key={b.k} to={b.to} className={cls}>{body}</Link> : <button key={b.k} type="button" onClick={b.go} className={cls}>{body}</button>;
          })}
        </div>
      </div>

      <div className="grid gap-3 xl:grid-cols-[1fr_340px]">
        {tab === "shop" ? <ShopPos d={d} training={training} disabled={!training && ((!shiftOpen && isSalesman) || !!priceLock)} onSaved={(r) => { setDone({ ...r, shop: true }); if (!r.training) today.reload(); }} /> : <div className="space-y-3">
          <VoiceButton onParsed={applyVoice} />
          {heard && <div className="rounded-xl bg-violet-50 px-4 py-2 text-violet-900 ring-1 ring-violet-200">🎤 Heard: “{heard}” — check below and press <b>Save</b></div>}
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
            <div className="grid grid-cols-3 gap-3 sm:grid-cols-4 xl:grid-cols-7">
              {PAY.filter((p) => !OFF_PAY.includes(p.key)).map((p) => (
                <button key={p.key} aria-pressed={pay === p.key} onClick={() => {
                  setPay(p.key);
                  if (p.key === "khata") setPickKhata(true); else setKhata(null);
                  if (p.key === "loyalty") setPickPoints(true); else setPointsCust(null);
                  if (p.key === "coupon") setScanCoupon(true); else setCoupon(null);
                  if (p.key === "wallet") setPickWallet(true); else setWalletAcct(null);
                }}
                  className={`flex flex-col items-center justify-center rounded-2xl py-3 text-white shadow transition active:scale-95 ${p.cls} ${pay === p.key ? "scale-[1.03] ring-4 ring-slate-900 ring-offset-2" : pay ? "opacity-50" : ""}`}>
                  <p.icon size={28} />
                  <span className="mt-1 text-base font-bold">{p.en}</span>
                  <Ur className="text-sm leading-loose">{p.ur}</Ur>
                </button>
              ))}
            </div>
            {pay === "loyalty" && pointsCust && (
              <button onClick={() => setPickPoints(true)} className="mt-3 flex w-full items-center gap-3 rounded-xl bg-pink-50 p-3 text-left ring-1 ring-pink-300">
                <Gift className="text-pink-600" /><span className="flex-1"><b>{pointsCust.name}</b><span className="block text-sm text-slate-600">{pointsCust.loyalty_points} points = Rs {pointsCust.loyalty_points}</span></span>
                {amount > pointsCust.loyalty_points && <span className="text-sm font-semibold text-red-600">Not enough points</span>}
              </button>
            )}
            {pay === "coupon" && coupon && (
              <button onClick={() => setScanCoupon(true)} className="mt-3 flex w-full items-center gap-3 rounded-xl bg-orange-50 p-3 text-left ring-1 ring-orange-300">
                <Ticket className="text-orange-600" /><span className="flex-1"><b>Coupon {coupon.code}</b><span className="block text-sm text-slate-600">Worth {pkr(coupon.value)}{coupon.product ? ` · ${FUEL[coupon.product]?.en} only` : ""} · one time</span></span>
                <span className="text-sm text-orange-700 underline">Change</span>
              </button>
            )}
            {pay === "wallet" && walletAcct && (
              <button onClick={() => setPickWallet(true)} className="mt-3 flex w-full items-center gap-3 rounded-xl bg-sky-50 p-3 text-left ring-1 ring-sky-300">
                <Wallet className="text-sky-700" /><span className="flex-1"><b>{walletAcct.name}</b><span className="block text-sm text-slate-600">Wallet {pkr(walletAcct.wallet_balance)}</span></span>
                {amount > walletAcct.wallet_balance && <span className="text-sm font-semibold text-red-600">Not enough · کم ہے</span>}
              </button>
            )}
            <button onClick={() => setScan(true)} className="mt-3 flex w-full items-center justify-center gap-2 rounded-xl bg-amber-100 py-3 text-lg font-semibold text-amber-900 ring-1 ring-amber-300 active:scale-95">
              <span className="text-2xl">📷</span> Scan khata card · <Ur>کارڈ سکین کریں</Ur>
            </button>
            {pay === "khata" && khata && (
              <button onClick={() => setPickKhata(true)} className="mt-3 flex w-full items-center gap-3 rounded-xl bg-amber-50 p-3 text-left ring-1 ring-amber-300">
                <span className="text-3xl">{TYPE_ICON[khata.account.type] ?? "📒"}</span>
                <span className="flex-1"><span className="block text-lg font-semibold">{khata.account.name}</span>
                  <span className="text-sm text-slate-600">{[khata.vehicle && `Vehicle ${khata.vehicle}`, khata.slip && `Slip ${khata.slip}`].filter(Boolean).join(" · ") || "No vehicle / slip"}</span></span>
                <span className="text-sm text-amber-700 underline">Change</span>
              </button>
            )}
          </Step>

          {/* 4. save — reads like a receipt: what, how much, how paid, and the amount to collect */}
          <div className="sticky bottom-0 z-10 rounded-2xl bg-white p-3 shadow-xl ring-2 ring-brand-600/30">
            {product && value > 0 ? (
              <div className="mb-3 grid gap-2 sm:grid-cols-[1fr_auto] sm:items-center">
                <div className="flex flex-wrap items-center gap-2 text-lg">
                  <span className={`rounded-lg px-3 py-1 font-bold text-white ${FUEL[product].bg}`}>⛽ {FUEL[product].en} · <Ur>{FUEL[product].ur}</Ur></span>
                  <span className="font-semibold tabular-nums">{num(litres, 2)} L × Rs {rate}</span>
                  {pay && <span className="rounded-lg bg-slate-100 px-3 py-1 font-semibold">{PAY.find((x) => x.key === pay)?.en} · <Ur>{PAY.find((x) => x.key === pay)?.ur}</Ur>{pay === "khata" && khata ? ` — ${khata.account.name}` : pay === "wallet" && walletAcct ? ` — ${walletAcct.name}` : ""}</span>}
                </div>
                <div className="rounded-xl bg-emerald-50 px-4 py-2 text-right ring-1 ring-emerald-200">
                  <div className="text-sm text-emerald-800">{pay === "khata" ? <>Add to khata · <Ur>کھاتے میں</Ur></> : <>Collect · <Ur>وصول کریں</Ur></>}</div>
                  <div className="text-3xl font-extrabold tabular-nums text-emerald-800">{pkr(amount)}</div>
                </div>
              </div>
            ) : <div className="mb-3 text-center text-lg text-slate-500">① fuel · ② amount · ③ payment — <Ur>تیل، رقم اور ادائیگی چنیں</Ur></div>}
            <div className="grid grid-cols-[auto_1fr] gap-3">
              <button onClick={reset} className="rounded-xl bg-slate-200 px-5 py-4 text-lg font-semibold text-slate-700 active:bg-slate-300"><X className="inline" size={20} /> Cancel · <Ur>منسوخ</Ur></button>
              <button onClick={save} disabled={!ready || saving || (!training && ((!shiftOpen && isSalesman) || !!priceLock))}
                className="flex items-center justify-center gap-2 rounded-xl bg-emerald-600 px-8 py-4 text-2xl font-bold text-white shadow active:scale-95 disabled:bg-slate-300">
                <Check size={28} /> Save · <Ur>محفوظ کریں</Ur>
              </button>
            </div>
          </div>
        </div>}

        <ShiftPanel d={d} reload={today.reload} onUndo={undo} myId={user?.id} />
      </div>

      {/* blocking states */}
      {isSalesman && !shiftOpen && !training && <Blocker><StartShift onStarted={() => today.reload()} />
        <button onClick={toggleTraining} className="mt-3 w-full rounded-xl bg-amber-100 py-3 text-lg font-semibold text-amber-900">🎓 Practice first (training, nothing saved) · <Ur>پہلے مشق</Ur></button></Blocker>}
      {priceLock && shiftOpen && !training && (
        <Blocker>
          <div className="text-6xl">⛽</div>
          <h2 className="mt-2 text-2xl font-bold">Price changed · <Ur>ریٹ تبدیل ہو گیا</Ur></h2>
          <p className="mt-1 text-slate-600">Update the dispenser rate and confirm to continue.</p>
          <button className="mt-4 rounded-xl bg-emerald-600 px-8 py-4 text-xl font-bold text-white" onClick={() => notif.setSnoozed(null)}>Confirm new price</button>
        </Blocker>
      )}
      {scan && <CardScanner onClose={() => setScan(false)} onFound={(f) => {
        setScan(false);
        if (f.account.status === "full") { toast("err", `${f.account.name}: khata limit full — ask the manager`); return; }
        setPay("khata");
        // open the account straight on the vehicle / slip step
        setKhata({ account: f.account, vehicle: f.vehicle ?? "", slip: "" });
        setPickKhata(true);
      }} />}
      {scanCoupon && <CardScanner path="/pos/coupon/" title="Scan coupon" urdu="کوپن سکین" placeholder="Coupon code" onClose={() => { setScanCoupon(false); if (!coupon) setPay(null); }} onFound={(c) => {
        setScanCoupon(false); setCoupon(c); setPay("coupon"); setMode("amount"); setEntry(String(c.value));
        if (c.product && d.products.includes(c.product)) setProduct(c.product);
      }} />}
      {pickWallet && <WalletPicker onClose={() => { setPickWallet(false); if (!walletAcct) setPay(null); }} onPick={(a) => { setWalletAcct(a); setPickWallet(false); }} />}
      {pickPoints && <PointsPicker onClose={() => { setPickPoints(false); if (!pointsCust) setPay(null); }} onPick={(c) => { setPointsCust(c); setPickPoints(false); }} />}
      {pickKhata && <KhataPicker onClose={() => { setPickKhata(false); if (!khata) setPay(null); }} onPick={(k) => { setKhata(k); setPickKhata(false); }} initial={khata} />}
      {done && (
        <div role="status" className={`fixed inset-0 z-50 flex items-center justify-center p-6 text-center text-white ${done.training ? "bg-amber-500/95" : done.offline ? "bg-slate-800/95" : "bg-emerald-600/95"}`} onClick={() => setDone(null)}>
          <div>
            <div className={`mx-auto flex h-24 w-24 items-center justify-center rounded-full bg-white ${done.offline ? "text-slate-800" : "text-emerald-600"}`}>{done.offline ? <WifiOff size={56} /> : <Check size={64} strokeWidth={3} />}</div>
            <div className="mt-4 text-3xl font-bold">{done.training ? <>Practice sale · <Ur>مشق</Ur></> : done.offline ? <>Saved on tablet · <Ur>ٹیبلٹ میں محفوظ</Ur></> : <>Sale saved · <Ur>سیل محفوظ</Ur></>}</div>
            {done.training && <div className="mt-1 text-lg">Well done! In real mode this sale would be saved.</div>}
            {done.offline && <div className="mt-1 text-lg">No internet — it will upload by itself</div>}
            {done.shop
              ? <div className="mt-2 text-xl">{done.lines.map((l: any) => `${num(l.qty)} × ${l.name}`).join(", ")}</div>
              : <div className="mt-2 text-2xl">{FUEL[done.product].en} {num(done.litres, 2)} L × Rs {done.rate}</div>}
            <div className="text-5xl font-bold tabular-nums">{pkr(done.shop ? done.total : done.amount)}</div>
            <div className="mt-2 text-xl capitalize">{done.payment_method === "khata" ? `Khata — ${done.khata_name ?? ""}` : done.payment_method === "loyalty" ? "Paid with points" : done.payment_method === "wallet" ? `Wallet — ${done.khata_name ?? ""}` : done.payment_method === "coupon" ? "Coupon · کوپن" : done.payment_method}</div>
            {done.receipt_url && <ReceiptQr url={done.receipt_url} />}
            <div className="mt-6 flex justify-center gap-3">
              {!done.training && <button onClick={(e) => { e.stopPropagation(); undo(done); }} className="flex items-center gap-2 rounded-xl bg-white/20 px-6 py-4 text-xl font-bold ring-2 ring-white active:scale-95">
                <Undo2 size={24} /> Undo · <Ur>واپس</Ur>
              </button>}
              <button onClick={() => setDone(null)} className="rounded-xl bg-white px-8 py-4 text-xl font-bold text-emerald-700 active:scale-95">OK</button>
            </div>
            {!done.offline && !done.training && <div className="mt-2 text-sm text-white/80">Wrong entry? Undo within 2 minutes</div>}
          </div>
        </div>
      )}
    </div>
  );
}

function Step({ n, en, ur, children }: { n: number; en: string; ur: string; children: ReactNode }) {
  return (
    <section className="rounded-2xl bg-white p-3 shadow-sm ring-1 ring-slate-200">
      <h2 className="mb-3 flex items-center gap-3 text-xl font-bold">
        <span className="flex h-10 w-10 items-center justify-center rounded-full bg-brand-600 text-xl text-white">{n}</span>{en}
        <Ur className="ml-auto text-2xl text-slate-600">{ur}</Ur>
      </h2>
      {children}
    </section>
  );
}

const Blocker = ({ children }: { children: ReactNode }) => (
  // my-auto centres a short box but lets a tall one scroll from its top (items-center would cut the top off)
  <div className="fixed inset-0 z-40 flex justify-center overflow-y-auto bg-slate-900/60 p-3 sm:p-6 lg:left-60"><div className="my-auto w-full max-w-3xl rounded-2xl bg-white p-4 text-center shadow-xl sm:p-6">{children}</div></div>
);

function StartShift({ onStarted }: { onStarted: () => void }) {
  return (
    <>
      <h2 className="text-xl font-bold sm:text-2xl">🕘 Start your shift · <Ur>شفٹ شروع کریں</Ur></h2>
      <p className="mb-3 mt-1 text-sm text-slate-600">Check each meter and confirm the reading · <Ur>ہر میٹر کی ریڈنگ چیک کریں</Ur></p>
      <StartShiftSheet big onStarted={onStarted} />
    </>
  );
}

function ShiftPanel({ d, reload, onUndo, myId }: { d: any; reload: () => void; onUndo: (sale: any) => void; myId?: number }) {
  const s = d.shift?.summary;
  // server clock offset, so the 2-minute undo window matches the server even if the tablet clock is wrong
  const skew = d.server_time ? Date.parse(d.server_time) - Date.now() : 0;
  const [, tick] = useState(0);
  const [confirm, setConfirm] = useState<number | null>(null);
  const undoable = (r: any) => r.created_by === myId && Date.now() + skew - Date.parse(r.created_at) < (d.undo_seconds ?? 120) * 1000;
  useEffect(() => {
    if (!d.recent?.some(undoable)) return;
    const id = setInterval(() => tick((x) => x + 1), 5000);
    return () => clearInterval(id);
  });
  return (
    <aside className="space-y-3">
      <div className="rounded-2xl bg-slate-900 p-4 text-white">
        <div className="flex items-center justify-between text-sm text-slate-300"><span>💰 Cash in bag (should be)</span><Ur className="text-base">بیگ میں نقد</Ur></div>
        <div className="text-4xl font-bold tabular-nums">{pkr(s?.cash_expected ?? 0)}</div>
        <div className="mt-3 grid grid-cols-2 gap-2 text-sm">
          <div className="rounded-lg bg-white/10 p-2"><div className="text-slate-300">🧾 Total sales · <Ur>کل سیل</Ur></div><div className="text-lg font-semibold tabular-nums">{pkr(s?.amount ?? 0)}</div></div>
          <div className="rounded-lg bg-white/10 p-2"><div className="text-slate-300">⛽ Litres · <Ur>لیٹر</Ur></div><div className="text-lg font-semibold tabular-nums">{num(s?.litres ?? 0, 1)} L</div></div>
        </div>
        <div className="mt-2 space-y-1 text-sm">
          <div className="flex justify-between"><span className="text-slate-300">💵 Cash · <Ur>نقد</Ur></span><span className="tabular-nums">{pkr(s?.cash_sales ?? 0)}</span></div>
          <div className="flex justify-between"><span className="text-slate-300">📒 Khata · <Ur>کھاتہ</Ur></span><span className="tabular-nums">{pkr(s?.khata ?? 0)}</span></div>
          <div className="flex justify-between"><span className="text-slate-300">📱 Digital · <Ur>آن لائن</Ur></span><span className="tabular-nums">{pkr(s?.digital ?? 0)}</span></div>
          {s?.shop?.total > 0 && <div className="flex justify-between"><span className="text-slate-300">🛒 Shop (cash {pkr(s.shop.cash)})</span><span className="tabular-nums">{pkr(s.shop.total)}</span></div>}
          {s?.points > 0 && <div className="flex justify-between"><span className="text-slate-300">🎁 Paid with points</span><span className="tabular-nums">{pkr(s.points)}</span></div>}
          {s?.prepaid > 0 && <div className="flex justify-between"><span className="text-slate-300">🎟️ Coupons / wallet</span><span className="tabular-nums">{pkr(s.prepaid)}</span></div>}
          <div className="flex justify-between text-red-300"><span>− Expenses · <Ur>خرچہ</Ur></span><span className="tabular-nums">{pkr(s?.expenses_total ?? 0)}</span></div>
        </div>
      </div>
      {d.shift && <div id="pos-expenses" className="rounded-2xl bg-white p-3 ring-1 ring-slate-200"><ShiftExpenses shiftId={d.shift.id} expenses={s.expenses} onChange={reload} /></div>}
      <div className="rounded-2xl bg-white p-3 ring-1 ring-slate-200">
        <h3 className="mb-2 font-semibold">Last sales · <Ur className="text-slate-500">آخری سیل</Ur></h3>
        <ul className="divide-y divide-slate-100">
          {d.recent.map((r: any) => (
            <li key={r.id} className="flex items-center gap-2 py-2 text-sm">
              <span className={`h-3 w-3 shrink-0 rounded-full ${FUEL[r.product]?.bg}`} />
              <span className="flex-1"><b>{num(r.litres, 2)} L</b> {FUEL[r.product]?.en}<span className="block text-xs text-slate-500">{new Date(r.created_at).toLocaleTimeString("en-PK", { hour: "2-digit", minute: "2-digit" })} · {r.payment_method === "khata" ? `📒 ${r.customer_name}${r.slip_no ? ` · ${r.slip_no}` : ""}` : r.payment_method}</span></span>
              <span className="font-semibold tabular-nums">{pkr(r.amount)}</span>
              {undoable(r) && (confirm === r.id
                ? <button onClick={() => { setConfirm(null); onUndo(r); }} className="rounded-lg bg-red-600 px-2 py-1.5 text-xs font-bold text-white">Sure? Undo</button>
                : <button onClick={() => { setConfirm(r.id); setTimeout(() => setConfirm(null), 4000); }} aria-label="Undo this sale" className="rounded-lg bg-slate-100 p-1.5 text-slate-600 hover:bg-slate-200"><Undo2 size={16} /></button>)}
            </li>
          ))}
          {!d.recent.length && <li className="py-4 text-center text-sm text-slate-500">No sales yet</li>}
        </ul>
      </div>
    </aside>
  );
}

function KhataPicker({ initial, onClose, onPick }: { initial: any; onClose: () => void; onPick: (k: { account: any; vehicle: string; slip: string }) => void }) {
  const { data: live } = useApi<any[]>("/pos/khata-accounts");
  useEffect(() => { if (live) cacheSet("khata_accounts", live); }, [live]);
  const data = live ?? cacheGet<any[]>("khata_accounts");
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

/** QR on the success screen: the customer scans it to keep a digital receipt. */
function ReceiptQr({ url }: { url: string }) {
  const [src, setSrc] = useState("");
  useEffect(() => { QRCode.toDataURL(url, { margin: 1, width: 160 }).then(setSrc); }, [url]);
  return src ? (
    <div className="mx-auto mt-4 w-fit rounded-xl bg-white p-2 text-center text-xs font-medium text-slate-700" onClick={(e) => e.stopPropagation()}>
      <img src={src} alt="QR code for the digital receipt" className="h-32 w-32" />Scan for receipt · <Ur>رسید</Ur>
    </div>
  ) : null;
}

/** Company that pays from its prepaid wallet. */
function WalletPicker({ onClose, onPick }: { onClose: () => void; onPick: (c: any) => void }) {
  const [q, setQ] = useState("");
  const { data } = useApi<any[]>("/pos/wallet-accounts");
  const list = (data ?? []).filter((c) => !q.trim() || c.name.toLowerCase().includes(q.trim().toLowerCase()));
  return (
    <div className="fixed inset-0 z-50 flex items-start justify-center overflow-y-auto bg-slate-900/60 p-3 sm:p-6">
      <div className="w-full max-w-xl rounded-2xl bg-white p-4 shadow-xl">
        <div className="mb-3 flex items-center gap-2"><Wallet className="text-sky-700" /><h2 className="flex-1 text-2xl font-bold">Pay from wallet · <Ur>والٹ</Ur></h2>
          <button onClick={onClose} className="rounded-xl bg-slate-100 p-3" aria-label="Close"><X /></button></div>
        <input autoFocus className="input py-3 text-lg" placeholder="Company name" value={q} onChange={(e) => setQ(e.target.value)} />
        <div className="mt-3 space-y-2">
          {list.map((c) => (
            <button key={c.id} onClick={() => onPick(c)} disabled={c.wallet_balance <= 0} className="flex w-full items-center gap-3 rounded-xl p-3 text-left ring-2 ring-slate-200 hover:ring-sky-400 disabled:opacity-50">
              <span className="text-3xl">{TYPE_ICON[c.type] ?? "🏢"}</span>
              <span className="flex-1 text-lg font-semibold">{c.name}</span>
              <span className={`rounded-lg px-3 py-1 font-semibold ${c.wallet_balance > 0 ? "bg-sky-100 text-sky-800" : "bg-red-100 text-red-700"}`}>{pkr(c.wallet_balance)}</span>
            </button>
          ))}
          {data && !list.length && <p className="py-4 text-center text-slate-500">No wallet found. Ask the manager.</p>}
        </div>
      </div>
    </div>
  );
}

/** Find the customer who pays with loyalty points (by phone or name). */
function PointsPicker({ onClose, onPick }: { onClose: () => void; onPick: (c: any) => void }) {
  const [q, setQ] = useState("");
  const { data } = useApi<any[]>(q.trim().length >= 3 ? `/customers?q=${encodeURIComponent(q.trim())}` : null);
  return (
    <div className="fixed inset-0 z-50 flex items-start justify-center overflow-y-auto bg-slate-900/60 p-3 sm:p-6">
      <div className="w-full max-w-xl rounded-2xl bg-white p-4 shadow-xl">
        <div className="mb-3 flex items-center gap-2"><Gift className="text-pink-600" /><h2 className="flex-1 text-2xl font-bold">Pay with points · <Ur>پوائنٹس</Ur></h2>
          <button onClick={onClose} className="rounded-xl bg-slate-100 p-3" aria-label="Close"><X /></button></div>
        <input autoFocus className="input py-3 text-lg" placeholder="Customer phone or name" value={q} onChange={(e) => setQ(e.target.value)} />
        <div className="mt-3 space-y-2">
          {(data ?? []).filter((c) => c.loyalty_points > 0).slice(0, 8).map((c) => (
            <button key={c.id} onClick={() => onPick(c)} className="flex w-full items-center gap-3 rounded-xl p-3 text-left ring-2 ring-slate-200 hover:ring-pink-400">
              <span className="flex-1"><b className="text-lg">{c.name}</b><span className="block text-sm text-slate-500">{c.phone}</span></span>
              <span className="rounded-lg bg-pink-100 px-3 py-1 font-semibold text-pink-800">{c.loyalty_points} pts</span>
            </button>
          ))}
          {q.trim().length >= 3 && data && !data.some((c) => c.loyalty_points > 0) && <p className="py-4 text-center text-slate-500">No customer with points found</p>}
        </div>
      </div>
    </div>
  );
}

const CAT: Record<string, string> = { lubricant: "🛢️ Oil", filter: "🧰 Filters", coolant: "💧 Coolant", tyre: "🛞 Tyres", battery: "🔋 Battery", tuck: "🥤 Tuck shop", service: "🔧 Service", other: "📦 Other" };

/** Shop sale on the POS: tap items (or scan the barcode), choose payment, save. */
function ShopPos({ d, disabled, training, onSaved }: { d: any; disabled: boolean; training?: boolean; onSaved: (r: any) => void }) {
  const { data: live, reload } = useApi<any[]>(`/shop/items?station_id=${d.station.id}`);
  useEffect(() => { if (live) cacheSet(`shop_items_${d.station.id}`, live); }, [live, d.station.id]);
  const items = live ?? cacheGet<any[]>(`shop_items_${d.station.id}`) ?? [];
  const [cat, setCat] = useState("");
  const [q, setQ] = useState("");
  const [cart, setCart] = useState<Record<number, number>>({});
  const [pay, setPay] = useState<string | null>(null);
  const [khata, setKhata] = useState<any>(null);
  const [pick, setPick] = useState(false);
  const [scan, setScan] = useState(false);
  const [saving, setSaving] = useState(false);
  const toast = useToast();
  const byId = Object.fromEntries(items.map((i) => [i.id, i]));
  const add = (i: any, n = 1) => setCart((c) => {
    const qty = Math.max(0, Math.min(i.stock, (c[i.id] ?? 0) + n));
    const next = { ...c, [i.id]: qty };
    if (!qty) delete next[i.id];
    if ((c[i.id] ?? 0) + n > i.stock) toast("err", `Only ${i.stock} ${i.unit} of ${i.name} in stock`);
    return next;
  });
  const lines = Object.entries(cart).map(([id, qty]) => ({ i: byId[Number(id)], qty })).filter((l) => l.i);
  const total = lines.reduce((a, l) => a + l.qty * l.i.price, 0);
  const shown = items.filter((i) => (!cat || i.category === cat) && i.name.toLowerCase().includes(q.toLowerCase()));
  const found = (code: string) => {
    const i = items.find((x) => x.barcode === code || x.sku === code);
    if (i) { add(i); toast("ok", `Added ${i.name}`); } else toast("err", `No item with code ${code}`);
  };
  const save = async () => {
    if (!lines.length || !pay || (pay === "khata" && !khata) || saving) return;
    if (training) {
      setCart({}); setPay(null); setKhata(null);
      onSaved({ training: true, total, payment_method: pay, lines: lines.map((l) => ({ qty: l.qty, name: l.i.name })) });
      return;
    }
    setSaving(true);
    try {
      const r = await api("/shop/sales", { body: { station_id: d.station.id, payment_method: pay, customer_id: khata?.account.id ?? null, client_uid: newUid(), lines: lines.map((l) => ({ item_id: l.i.id, qty: l.qty })) } });
      setCart({}); setPay(null); setKhata(null); reload();
      onSaved({ ...r, khata_name: khata?.account.name });
    } catch (e: any) { toast("err", isOffline(e) ? "Shop sale needs internet" : e.message); }
    finally { setSaving(false); }
  };
  return (
    <div className="space-y-3">
      <section className="rounded-2xl bg-white p-3 shadow-sm ring-1 ring-slate-200">
        <div className="mb-3 flex flex-wrap gap-2">
          <div className="relative min-w-[200px] flex-1"><Search className="absolute left-3 top-3 text-slate-400" size={20} /><input className="input py-2.5 pl-10 text-lg" placeholder="Search item…" value={q} onChange={(e) => setQ(e.target.value)} /></div>
          <button onClick={() => setScan(true)} className="flex items-center gap-2 rounded-xl bg-slate-900 px-4 py-2 font-semibold text-white"><ScanBarcode /> Scan barcode</button>
        </div>
        <div className="mb-3 flex flex-wrap gap-2">
          {[["", "All"], ...Object.entries(CAT).filter(([k]) => items.some((i) => i.category === k))].map(([k, l]) => (
            <button key={k} onClick={() => setCat(k)} className={`rounded-full px-4 py-1.5 ${cat === k ? "bg-slate-900 text-white" : "bg-slate-100"}`}>{l}</button>
          ))}
        </div>
        <div className="grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-4">
          {shown.map((i) => (
            <button key={i.id} disabled={i.stock <= 0} onClick={() => add(i)}
              className={`relative rounded-xl p-3 text-left ring-2 active:scale-95 ${cart[i.id] ? "bg-emerald-50 ring-emerald-500" : "bg-white ring-slate-200"} disabled:opacity-40`}>
              {cart[i.id] ? <span className="absolute right-2 top-2 rounded-full bg-emerald-600 px-2 text-sm font-bold text-white">{cart[i.id]}</span> : null}
              <div className="pr-6 font-semibold leading-tight">{i.name}</div>
              <div className="mt-1 text-lg font-bold tabular-nums">Rs {num(i.price)}</div>
              <div className={`text-xs ${i.stock <= i.reorder_level ? "font-semibold text-red-600" : "text-slate-500"}`}>{i.stock <= 0 ? "Out of stock" : `${num(i.stock)} ${i.unit} left`}</div>
            </button>
          ))}
          {!shown.length && <p className="col-span-full py-6 text-center text-slate-500">No items. The manager adds them on the Shop page.</p>}
        </div>
      </section>
      <section className="rounded-2xl bg-white p-3 shadow-sm ring-1 ring-slate-200">
        <h2 className="mb-2 text-lg font-semibold">Payment · <Ur>ادائیگی</Ur></h2>
        <div className="grid grid-cols-3 gap-3 sm:grid-cols-6">
          {PAY.filter((p) => !ONLINE_ONLY.includes(p.key)).map((p) => (
            <button key={p.key} aria-pressed={pay === p.key} onClick={() => { setPay(p.key); if (p.key === "khata") setPick(true); else setKhata(null); }}
              className={`flex flex-col items-center justify-center rounded-2xl py-3 text-white shadow active:scale-95 ${p.cls} ${pay === p.key ? "ring-4 ring-slate-900 ring-offset-2" : pay ? "opacity-50" : ""}`}>
              <p.icon size={26} /><span className="mt-1 font-bold">{p.en}</span>
            </button>
          ))}
        </div>
        {pay === "khata" && khata && <div className="mt-2 rounded-xl bg-amber-50 p-2 text-sm ring-1 ring-amber-300">📒 {khata.account.name}</div>}
      </section>
      <div className="sticky bottom-0 z-10 rounded-2xl bg-white p-3 shadow-lg ring-1 ring-slate-200">
        {lines.length > 0 && <ul className="mb-2 max-h-40 space-y-1 overflow-auto">
          {lines.map((l) => (
            <li key={l.i.id} className="flex items-center gap-2">
              <span className="flex-1 text-sm">{l.i.name}</span>
              <button onClick={() => add(l.i, -1)} className="rounded-lg bg-slate-100 p-1.5" aria-label={`One less ${l.i.name}`}><Minus size={16} /></button>
              <span className="w-8 text-center font-bold tabular-nums">{l.qty}</span>
              <button onClick={() => add(l.i, 1)} className="rounded-lg bg-slate-100 p-1.5" aria-label={`One more ${l.i.name}`}><Plus size={16} /></button>
              <span className="w-24 text-right tabular-nums">{pkr(l.qty * l.i.price)}</span>
            </li>
          ))}
        </ul>}
        <div className="flex flex-wrap items-center gap-3">
          <div className="flex-1 text-lg">{lines.length ? <>Total <b className="text-2xl">{pkr(total)}</b></> : <span className="text-slate-500">Tap items to add · <Ur>چیزیں چنیں</Ur></span>}</div>
          <button onClick={() => { setCart({}); setPay(null); setKhata(null); }} className="rounded-xl bg-slate-200 px-5 py-4 text-lg font-semibold text-slate-700"><X className="inline" size={20} /> Clear</button>
          <button onClick={save} disabled={disabled || saving || !lines.length || !pay || (pay === "khata" && !khata)}
            className="flex items-center gap-2 rounded-xl bg-emerald-600 px-8 py-4 text-xl font-bold text-white shadow active:scale-95 disabled:bg-slate-300"><Check size={26} /> Save · <Ur>محفوظ</Ur></button>
        </div>
      </div>
      {pick && <KhataPicker onClose={() => { setPick(false); if (!khata) setPay(null); }} onPick={(k) => { setKhata(k); setPick(false); }} initial={khata} />}
      {scan && <BarcodeScanner onClose={() => setScan(false)} onCode={(c) => { setScan(false); found(c); }} />}
    </div>
  );
}

/** Camera barcode scanner (EAN / UPC / Code 128 / QR) with a type-in fallback. */
function BarcodeScanner({ onCode, onClose }: { onCode: (code: string) => void; onClose: () => void }) {
  const video = useRef<HTMLVideoElement>(null);
  const [camera, setCamera] = useState(true);
  const [code, setCode] = useState("");
  useEffect(() => {
    let stream: MediaStream | null = null, timer: number | undefined, stopped = false;
    const Detector = (window as any).BarcodeDetector;
    if (!Detector) { setCamera(false); return; }
    const det = new Detector({ formats: ["ean_13", "ean_8", "upc_a", "upc_e", "code_128", "qr_code"] });
    (async () => {
      try {
        stream = await navigator.mediaDevices.getUserMedia({ video: { facingMode: "environment" } });
        if (stopped) return;
        video.current!.srcObject = stream; await video.current!.play();
        const tick = async () => {
          if (stopped) return;
          const r = await det.detect(video.current).catch(() => []);
          if (r[0]?.rawValue) { onCode(r[0].rawValue); return; }
          timer = window.setTimeout(tick, 200);
        };
        tick();
      } catch { setCamera(false); }
    })();
    return () => { stopped = true; clearTimeout(timer); stream?.getTracks().forEach((t) => t.stop()); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  return (
    <div className="fixed inset-0 z-[55] flex items-start justify-center overflow-y-auto bg-slate-900/70 p-3 sm:items-center">
      <div className="w-full max-w-md rounded-2xl bg-white p-4 shadow-xl">
        <div className="mb-3 flex items-center gap-2"><ScanBarcode /><h2 className="flex-1 text-xl font-bold">Scan barcode</h2><button onClick={onClose} className="rounded-xl bg-slate-100 p-2" aria-label="Close"><X /></button></div>
        {camera ? <video ref={video} className="aspect-[4/3] w-full rounded-xl bg-black object-cover" muted playsInline />
          : <p className="rounded-lg bg-slate-50 p-3 text-sm text-slate-600">Camera scanning is not available on this device. Type the number under the barcode.</p>}
        <form className="mt-3 flex gap-2" onSubmit={(e) => { e.preventDefault(); if (code.trim()) onCode(code.trim()); }}>
          <input className="input py-3 font-mono text-lg" inputMode="numeric" placeholder="Barcode number" value={code} onChange={(e) => setCode(e.target.value)} />
          <button className="btn-primary px-5">OK</button>
        </form>
      </div>
    </div>
  );
}

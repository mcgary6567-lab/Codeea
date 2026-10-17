import { useEffect, useMemo, useState } from "react";
import { PrintFooter, PrintHeader } from "./Letterhead";
import { Link, NavLink, Outlet, useLocation, useNavigate } from "react-router-dom";
import {
  LayoutDashboard, MessageCircle, Users, Fuel, Clock, Droplets, Tag, Truck, Megaphone, Bell, Bot, Settings, LogOut, Menu, X, MessageSquareWarning, BookOpen, UserCog, MapPin, Container, Receipt, FileBarChart, Factory, Wallet, Landmark, ShoppingBasket, ClipboardCheck, ShieldCheck, HeartPulse, CalendarClock, ScrollText, Ticket, Star, Calculator, History, Wrench, Route, UserPlus, ChevronDown, Ban, HandCoins, ClipboardList, Banknote, ArrowDownCircle, ArrowUpCircle, FileCheck2, BookOpenText, BookOpenCheck, Building2, ListChecks, Lightbulb, UserCheck, Hourglass, Search, CircleHelp, Ruler, Sun, Moon,
} from "lucide-react";
import { useAuth, ROLE_LABEL } from "../App";
import { api, useApi, useLiveEvents } from "../lib/api";
import { pkr } from "../lib/format";
import { NotificationsProvider, NotificationBell } from "./Notifications";
import { QuickAddButton } from "./QuickAdd";
import { useBranding } from "../lib/brand";
import { InstallAppButton } from "./InstallApp";
import { useOfflineQueue } from "../lib/offline";

/** Shortcuts under "Wholesale Supply" in the menu (open the right tab or form). */
const WHOLESALE_SUB = [
  { to: "/wholesale?tab=clients", label: "Clients", ur: "کلائنٹس", icon: Users, perm: "wholesale.view" },
  { to: "/wholesale?tab=orders", label: "Order book", ur: "آرڈر بک", icon: ClipboardList, perm: "wholesale.view" },
  { to: "/wholesale?tab=collect", label: "Recovery", ur: "وصولی", icon: HandCoins, perm: "wholesale.view" },
  { to: "/wholesale?tab=trips", label: "Tanker trips", ur: "ٹینکر ٹرپ", icon: Route, perm: "wholesale.view" },
  { to: "/wholesale?do=rate", label: "Change rate", ur: "ریٹ", icon: Tag, perm: "wholesale.rates" },
  { to: "/wholesale?tab=fleet&do=tanker", label: "Add tanker", ur: "نیا ٹینکر", icon: Truck, perm: "wholesale.manage" },
  { to: "/wholesale?tab=fleet&do=driver", label: "Add driver", ur: "نیا ڈرائیور", icon: UserPlus, perm: "wholesale.manage" },
];
/** Shortcuts under "Cashier desk" (open the right tab). */
const CASHIER_SUB = [
  { to: "/cashier?tab=receive", label: "Receive money", ur: "وصولی", icon: ArrowDownCircle, perm: "cash.receive" },
  { to: "/cashier?tab=pay", label: "Pay", ur: "ادائیگی", icon: ArrowUpCircle, perm: "cash.pay" },
  { to: "/cashier?tab=cheques", label: "Cheques", ur: "چیک", icon: FileCheck2, perm: "cheques.manage" },
  { to: "/cashier?tab=handover", label: "Salesmen", ur: "سیلزمین کیش", icon: Users, perm: "shifts.handover" },
  { to: "/cashier?tab=daybook", label: "Day book", ur: "روزنامچہ", icon: BookOpenText, perm: "cashier.desk" },
  { to: "/cashier?tab=bank", label: "Bank", ur: "کیش اور بینک", icon: Landmark, perm: "cash.book" },
];
const SUBS: Record<string, typeof WHOLESALE_SUB> = { "/wholesale": WHOLESALE_SUB, "/cashier": CASHIER_SUB };
/** Urdu next to the English in the short menus (salesman, wholesale officer, cashier). */
const NAV_UR: Record<string, string> = {
  "/": "ڈیش بورڈ", "/reports": "رپورٹس", "/sales-history": "سیل کی تاریخ", "/insights": "مالک کی نظر", "/owner-report": "ماہانہ رپورٹ", "/discounts-report": "رعایتیں", "/other-entries": "متفرق آمدن/خرچہ",
  "/margins": "مارجن و ہدف", "/profit": "منافع کی وضاحت", "/suggestions": "تجاویز", "/inbox": "واٹس ایپ",
  "/pos": "سیل", "/shifts": "شفٹ", "/customers": "گاہک", "/khata": "کھاتہ", "/prepaid": "کوپن و والٹ", "/card-pending": "کارڈ پینڈنگ", "/bookings": "بکنگ",
  "/wholesale": "ہول سیل", "/carriage": "کرایہ", "/cashier": "کیشیئر", "/expenses": "خرچے", "/cash": "کیش بک", "/accounts": "اکاؤنٹس و ٹیکس", "/suppliers": "سپلائر", "/property": "جائیداد و کرایہ",
  "/orders": "آرڈر", "/complaints": "شکایات", "/campaigns": "مہم", "/stock": "ٹینک و اسٹاک", "/dip-charts": "ڈپ چارٹ", "/register": "اسٹاک رجسٹر", "/shop": "شاپ", "/prices": "ریٹ", "/alerts": "الرٹ",
  "/automations": "آٹومیشن", "/staff": "عملہ کھاتہ", "/kiosk": "حاضری", "/roster": "ڈیوٹی روسٹر", "/team": "ریٹنگ و کمیشن", "/checklist": "روزانہ چیک", "/compliance": "لائسنس",
  "/machines": "مشینیں", "/my-account": "میرا حساب", "/help": "رہنمائی", "/users": "صارفین", "/audit": "آڈٹ لاگ", "/corrections": "درستگی", "/opening-balances": "پرانا حساب", "/lists": "لسٹیں", "/settings": "سیٹنگز",
};
/** Manager / owner menu groups (other roles have short menus and see them flat). */
const GROUPS: { key: string; label: string; ur: string; icon: any; items: string[] }[] = [
  { key: "sales", label: "Sales & shifts", ur: "سیل و شفٹ", icon: Fuel, items: ["/pos", "/shifts", "/bookings", "/card-pending"] },
  { key: "customers", label: "Customers & khata", ur: "گاہک و کھاتہ", icon: Users, items: ["/customers", "/khata", "/prepaid", "/orders", "/complaints", "/campaigns"] },
  { key: "wholesale", label: "Wholesale & carriage", ur: "ہول سیل و کرایہ", icon: Container, items: ["/wholesale", "/carriage"] },
  { key: "stock", label: "Stock & prices", ur: "اسٹاک و ریٹ", icon: Droplets, items: ["/stock", "/dip-charts", "/register", "/prices", "/shop", "/suppliers"] },
  { key: "money", label: "Money & accounts", ur: "پیسہ و حساب", icon: Landmark, items: ["/cashier", "/cash", "/expenses", "/accounts", "/property", "/other-entries"] },
  { key: "reports", label: "Reports & profit", ur: "رپورٹ و منافع", icon: FileBarChart, items: ["/reports", "/sales-history", "/insights", "/owner-report", "/profit", "/margins", "/discounts-report", "/suggestions"] },
  { key: "staff", label: "Staff", ur: "عملہ", icon: Wallet, items: ["/staff", "/kiosk", "/roster", "/team", "/my-account"] },
  { key: "safety", label: "Safety & machines", ur: "حفاظت و مشینیں", icon: ShieldCheck, items: ["/compliance", "/checklist", "/machines"] },
  { key: "system", label: "System & setup", ur: "سسٹم و سیٹ اپ", icon: Settings, items: ["/settings", "/users", "/lists", "/automations", "/opening-balances", "/corrections", "/audit"] },
];
/** Always on top of the grouped menu (daily first look), and always at the bottom. */
const TOP = ["/", "/inbox", "/alerts"], BOTTOM = ["/help"];
const PINS_KEY = "pumpai.nav.pins", RECENT_KEY = "pumpai.nav.recent";
const readList = (k: string): string[] => { try { return JSON.parse(localStorage.getItem(k) ?? "[]"); } catch { return []; } };
const writeList = (k: string, v: string[]) => { try { localStorage.setItem(k, JSON.stringify(v)); } catch { /* private mode */ } };
/** Which user-guide module explains a page (Help page anchors). */
export const ROUTE_GUIDE: Record<string, string> = { "/pos": "pos", "/shifts": "shifts", "/my-account": "attendance", "/kiosk": "attendance", "/customers": "customers", "/khata": "customers", "/prices": "prices", "/stock": "stock", "/dip-charts": "dip-charts", "/register": "stock",
  "/wholesale": "wholesale", "/carriage": "carriage", "/cashier": "cashier", "/accounts": "banks", "/cash": "banks", "/expenses": "expenses", "/owner-report": "reports", "/reports": "reports", "/insights": "reports", "/profit": "reports",
  "/suppliers": "suppliers", "/settings": "settings", "/users": "settings", "/opening-balances": "opening", "/corrections": "corrections", "/other-entries": "other-entries" };
const itemCls = (on: boolean) => `flex items-center gap-3 rounded-lg px-3 py-2 text-sm transition ${on ? "bg-white/15 text-white" : "text-emerald-100/90 hover:bg-white/10"}`;
const Badge = ({ n }: { n: number }) => n > 0 ? <span className="rounded-full bg-emerald-400 px-1.5 text-xs font-semibold text-emerald-950">{n}</span> : null;

type NavItem = (typeof NAV)[number];
/** The side menu: flat for short menus; for the manager / owner, pinned + recent on top, then one group open at a time. */
function NavMenu({ items, grouped, badges, onGo }: { items: NavItem[]; grouped: boolean; badges: Record<string, number>; onGo: () => void }) {
  const { can } = useAuth();
  const loc = useLocation();
  // on a shortcut (e.g. /cashier?tab=pay) the shortcut is lit, not its parent
  const onShortcut = (base: string) => loc.pathname === base && (SUBS[base] ?? []).some((x) => x.to.slice(base.length) === loc.search);
  const isOn = (to: string, end?: boolean) => (end ? loc.pathname === to : loc.pathname === to || loc.pathname.startsWith(to + "/")) && !onShortcut(to);
  const [openKey, setOpenKey] = useState<string | null>(() => { try { return localStorage.getItem("pumpai.nav.openKey"); } catch { return null; } });
  const toggle = (k: string) => setOpenKey((cur) => { const next = cur === k ? null : k; try { localStorage.setItem("pumpai.nav.openKey", next ?? ""); } catch { /* private mode */ } return next; });
  const [pins, setPins] = useState<string[]>(() => readList(PINS_KEY));
  const togglePin = (to: string) => setPins((cur) => { const next = cur.includes(to) ? cur.filter((x) => x !== to) : [...cur, to].slice(-8); writeList(PINS_KEY, next); return next; });
  const [recent, setRecent] = useState<string[]>(() => readList(RECENT_KEY));
  const byTo = (to: string) => items.find((n) => n.to === to);
  // a nav target can be a top-level page or a sub-shortcut (e.g. /cashier?tab=pay); resolve either to a renderable entry
  const resolve = (to: string): { to: string; label: string; icon: any; ur?: string; perm?: string; badge?: string } | undefined => {
    if (to.includes("?")) { const base = to.slice(0, to.indexOf("?")); const x = (SUBS[base] ?? []).find((s) => s.to === to); return x && byTo(base) ? { to: x.to, label: x.label, icon: x.icon, ur: x.ur, perm: x.perm } : undefined; }
    const n = byTo(to); return n ? { to: n.to, label: n.label, icon: n.icon, ur: NAV_UR[n.to], perm: (n as any).perm, badge: (n as any).badge } : undefined;
  };
  const okTarget = (to: string) => { const t = resolve(to); return !!t && (!t.perm || can(t.perm)); };
  const activeOf = (to: string) => { if (to.includes("?")) { const base = to.slice(0, to.indexOf("?")); return loc.pathname === base && loc.search === to.slice(base.length); } return isOn(to, (byTo(to) as any)?.end); };
  // remember the last pages/shortcuts opened (not the home page), newest first — for every role, not just the owner
  useEffect(() => {
    if (loc.pathname === "/" || loc.pathname === "/help") return;
    const key = (SUBS[loc.pathname] ?? []).find((x) => x.to === loc.pathname + loc.search)?.to ?? loc.pathname;
    if (!okTarget(key)) return;
    setRecent((cur) => { const next = [key, ...cur.filter((x) => x !== key)].slice(0, 4); writeList(RECENT_KEY, next); return next; });
  }, [loc.pathname, loc.search]); // eslint-disable-line react-hooks/exhaustive-deps

  const link = (n: NavItem, size = 17, pinnable = false) => (
    <div key={n.to} className="group/item relative">
      <NavLink to={n.to} end={(n as any).end} onClick={onGo} title={NAV_UR[n.to] ? `${n.label} · ${NAV_UR[n.to]}` : n.label} className={() => `${itemCls(isOn(n.to, (n as any).end))}${pinnable ? " pr-8" : ""}`}>
        <n.icon size={size} className="shrink-0" />
        <span className="min-w-0 flex-1 truncate">{n.label}</span>
        {!grouped && NAV_UR[n.to] && <span lang="ur" dir="rtl" className="font-urdu shrink-0 text-sm opacity-70">{NAV_UR[n.to]}</span>}
        {(n as any).badge && <Badge n={badges[(n as any).badge] ?? 0} />}
      </NavLink>
      {pinnable && (
        <button type="button" onClick={(e) => { e.preventDefault(); togglePin(n.to); }} aria-label={pins.includes(n.to) ? "Unpin" : "Pin to top"} title={pins.includes(n.to) ? "Unpin · ہٹائیں" : "Pin to top · اوپر رکھیں"}
          className={`absolute right-2 top-1/2 -translate-y-1/2 rounded p-0.5 transition ${pins.includes(n.to) ? "text-amber-300" : "text-white/0 group-hover/item:text-emerald-100/70 hover:!text-amber-300"}`}>
          <Star size={14} fill={pins.includes(n.to) ? "currentColor" : "none"} />
        </button>
      )}
    </div>
  );
  const sub = (base: string) => (
    <div key={`${base}-sub`} className="ml-4 space-y-0.5 border-l border-white/15 pl-2">
      {SUBS[base].filter((x) => can(x.perm)).map((x) => (
        <div key={x.to} className="group/item relative">
          <Link to={x.to} onClick={onGo} className={`${itemCls(loc.pathname === base && loc.search === x.to.slice(base.length))} pr-8`}>
            <x.icon size={16} className="shrink-0" /><span className="min-w-0 flex-1 truncate">{x.label}</span>
            {!grouped && <span lang="ur" dir="rtl" className="font-urdu shrink-0 text-sm opacity-70">{x.ur}</span>}
          </Link>
          <button type="button" onClick={(e) => { e.preventDefault(); togglePin(x.to); }} aria-label={pins.includes(x.to) ? "Unpin" : "Pin to top"} title={pins.includes(x.to) ? "Unpin · ہٹائیں" : "Pin to top · اوپر رکھیں"}
            className={`absolute right-2 top-1/2 -translate-y-1/2 rounded p-0.5 transition ${pins.includes(x.to) ? "text-amber-300" : "text-white/0 group-hover/item:text-emerald-100/70 hover:!text-amber-300"}`}>
            <Star size={14} fill={pins.includes(x.to) ? "currentColor" : "none"} />
          </button>
        </div>
      ))}
    </div>
  );
  // a Pinned / Recent entry (works for a page or a sub-shortcut)
  const pinEntry = (to: string) => {
    const t = resolve(to); if (!t || (t.perm && !can(t.perm))) return null;
    return (
      <div key={`pin-${to}`} className="group/item relative">
        <Link to={t.to} onClick={onGo} title={t.ur ? `${t.label} · ${t.ur}` : t.label} className={`${itemCls(activeOf(to))} pr-8`}>
          <t.icon size={16} className="shrink-0" /><span className="min-w-0 flex-1 truncate">{t.label}</span>
          {t.ur && <span lang="ur" dir="rtl" className="font-urdu shrink-0 text-sm opacity-70">{t.ur}</span>}
          {t.badge && <Badge n={badges[t.badge] ?? 0} />}
        </Link>
        <button type="button" onClick={(e) => { e.preventDefault(); togglePin(to); }} aria-label={pins.includes(to) ? "Unpin" : "Pin"} title={pins.includes(to) ? "Unpin · ہٹائیں" : "Pin to top · اوپر رکھیں"}
          className={`absolute right-2 top-1/2 -translate-y-1/2 rounded p-0.5 transition ${pins.includes(to) ? "text-amber-300" : "text-white/0 group-hover/item:text-emerald-100/70 hover:!text-amber-300"}`}>
          <Star size={14} fill={pins.includes(to) ? "currentColor" : "none"} />
        </button>
      </div>
    );
  };
  const pinSection = (label: string, ur: string, tos: string[]) => tos.length ? (
    <div key={label} className="pt-1">
      <div className="flex items-center justify-between px-3 pb-0.5 pt-1 text-[11px] font-semibold uppercase tracking-wide text-emerald-200/60"><span>{label}</span><span lang="ur" dir="rtl" className="font-urdu text-[13px] normal-case tracking-normal">{ur}</span></div>
      {tos.map(pinEntry)}
    </div>
  ) : null;
  const pinnedTos = pins.filter(okTarget);
  const recentTos = recent.filter((to) => !pins.includes(to) && okTarget(to));
  const withSub = (n: NavItem, size?: number, pinnable = false) => (SUBS[n.to] ? [link(n, size, pinnable), sub(n.to)] : [link(n, size, pinnable)]);

  if (!grouped) return (
    <>
      {pinSection("Pinned", "پسندیدہ", pinnedTos)}
      {pinSection("Recent", "حالیہ", recentTos)}
      {(pinnedTos.length || recentTos.length) ? <div className="my-1 border-t border-white/10" /> : null}
      {items.flatMap((n) => withSub(n, 17, true))}
    </>
  );

  const inGroup = new Set([...GROUPS.flatMap((g) => g.items), ...TOP, ...BOTTOM]);
  const strays = items.filter((n) => !inGroup.has(n.to)); // anything new that nobody filed yet still shows
  return (
    <>
      {TOP.map(byTo).filter(Boolean).map((n) => link(n as NavItem))}
      {pinSection("Pinned", "پسندیدہ", pinnedTos)}
      {pinSection("Recent", "حالیہ", recentTos)}
      <div className="my-1 border-t border-white/10" />
      {GROUPS.map((g) => {
        const kids = g.items.map(byTo).filter(Boolean) as NavItem[];
        if (!kids.length) return null;
        const active = kids.some((n) => isOn(n.to) || (SUBS[n.to] && loc.pathname.startsWith(n.to)));
        const open = active || openKey === g.key;
        const count = kids.reduce((a, n) => a + ((n as any).badge ? badges[(n as any).badge] ?? 0 : 0), 0);
        return (
          <div key={g.key} className="pt-0.5">
            <button type="button" onClick={() => toggle(g.key)} aria-expanded={open}
              className={`flex w-full items-center gap-3 rounded-lg px-3 py-1.5 text-left text-sm transition ${active ? "bg-white/10 text-white" : "text-emerald-100/90"} hover:bg-white/10`}>
              <g.icon size={17} className="shrink-0" />
              <span className="min-w-0 flex-1 leading-tight"><span className="block truncate font-medium">{g.label}</span><span lang="ur" dir="rtl" className="font-urdu block truncate text-left text-[13px] leading-5 opacity-80">{g.ur}</span></span>
              {!open && <Badge n={count} />}
              <ChevronDown size={15} className={`opacity-70 transition ${open ? "rotate-180" : ""}`} />
            </button>
            {open && (
              <div className="ml-4 space-y-0.5 border-l border-white/15 pl-2">
                {kids.flatMap((n) => withSub(n, 16, true))}
              </div>
            )}
          </div>
        );
      })}
      {strays.length > 0 && <div className="pt-1">{strays.map((n) => link(n, 16, true))}</div>}
      <div className="my-1 border-t border-white/10" />
      {BOTTOM.map(byTo).filter(Boolean).map((n) => link(n as NavItem))}
    </>
  );
}

const NAV = [
  { to: "/", label: "Dashboard", icon: LayoutDashboard, end: true, perm: "dashboard.view" },
  { to: "/reports", label: "Reports", icon: FileBarChart, perm: "reports.view" },
  { to: "/sales-history", label: "Sales history", icon: Receipt, perm: "sales.view" },
  { to: "/insights", label: "Owner insights", icon: HeartPulse, perm: "reports.view" },
  { to: "/owner-report", label: "Monthly report (PDF)", icon: FileBarChart, perm: "reports.view" },
  { to: "/discounts-report", label: "Discounts & overrides", icon: Tag, perm: "reports.view" },
  { to: "/other-entries", label: "Other income/expense", icon: HandCoins, perm: "other_entries.manage" },
  { to: "/margins", label: "Margins & targets", icon: Calculator, perm: "reports.view" },
  { to: "/profit", label: "Profit explainer", icon: Lightbulb, perm: "reports.view" },
  { to: "/suggestions", label: "Suggestions", icon: Lightbulb, perm: "dashboard.view|wholesale.view" },
  { to: "/inbox", label: "WhatsApp", icon: MessageCircle, badge: "unread", perm: "whatsapp.inbox" },
  { to: "/pos", label: "Sales / POS", icon: Fuel, perm: "sales.create" },
  { to: "/shifts", label: "Shifts", icon: Clock, perm: "shifts.manage" },
  { to: "/customers", label: "Customers", icon: Users, perm: "customers.edit" },
  { to: "/khata", label: "Khata (Credit)", icon: BookOpen, perm: "khata.manage" },
  { to: "/prepaid", label: "Coupons & wallets", icon: Ticket, perm: "khata.manage" },
  { to: "/card-pending", label: "Card pending (khata)", icon: Hourglass, perm: "khata.clear_pending" },
  { to: "/wholesale", label: "Wholesale Supply", icon: Container, perm: "wholesale.view" },
  { to: "/carriage", label: "Carriage / kiraya", icon: Truck, perm: "carriage.view" },
  { to: "/cashier", label: "Cashier desk", icon: Banknote, perm: "cashier.desk" },
  { to: "/expenses", label: "Expenses", icon: Receipt, perm: "expenses.view" },
  { to: "/cash", label: "Cash & bank", icon: Landmark, perm: "expenses.view" },
  { to: "/accounts", label: "Accounts & tax", icon: Calculator, perm: "reports.view" },
  { to: "/suppliers", label: "Suppliers", icon: Factory, perm: "suppliers.manage" },
  { to: "/property", label: "Property & rent", icon: Building2, perm: "expenses.view" },
  { to: "/orders", label: "Orders", icon: Truck, badge: "orders", perm: "orders.manage" },
  { to: "/bookings", label: "Bookings", icon: CalendarClock, perm: "sales.create" },
  { to: "/complaints", label: "Complaints", icon: MessageSquareWarning, perm: "complaints.manage" },
  { to: "/campaigns", label: "Campaigns", icon: Megaphone, perm: "campaigns.manage" },
  { to: "/stock", label: "Tanks & Stock", icon: Droplets, perm: "stock.manage" },
  { to: "/dip-charts", label: "Dip charts", icon: Ruler, perm: "stock.manage" },
  { to: "/register", label: "Stock register", icon: ScrollText, perm: "stock.manage" },
  { to: "/shop", label: "Shop & lubricants", icon: ShoppingBasket, perm: "stock.manage" },
  { to: "/prices", label: "Prices", icon: Tag, perm: "prices.update" },
  { to: "/alerts", label: "Alerts", icon: Bell, badge: "alerts", perm: "alerts.view" },
  { to: "/automations", label: "AI Automations", icon: Bot, perm: "automations.manage" },
  { to: "/staff", label: "Staff accounts", icon: Wallet, perm: "staff.manage" },
  { to: "/kiosk", label: "Staff attendance", icon: UserCheck, perm: "staff.manage" },
  { to: "/roster", label: "Duty roster", icon: CalendarClock, perm: "staff.manage" },
  { to: "/team", label: "Ratings & commission", icon: Star, perm: "staff.manage" },
  { to: "/checklist", label: "Daily checks", icon: ClipboardCheck, perm: "sales.create", only: ["salesman"] },
  { to: "/compliance", label: "Licences & checklist", icon: ShieldCheck, perm: "alerts.view" },
  { to: "/machines", label: "Machines", icon: Wrench, perm: "sales.create" },
  { to: "/my-account", label: "My account", icon: Wallet, perm: "", only: ["salesman", "wholesale", "manager", "cashier"] },
  { to: "/help", label: "User guide", icon: BookOpenText, perm: "" },
  { to: "/users", label: "Users & Roles", icon: UserCog, perm: "users.manage" },
  { to: "/audit", label: "Audit log", icon: History, perm: "audit.view" },
  { to: "/corrections", label: "Corrections (void)", icon: Ban, perm: "corrections.manage" },
  { to: "/opening-balances", label: "Opening balances (go-live)", icon: BookOpenCheck, perm: "opening.manage" },
  { to: "/lists", label: "Lists", icon: ListChecks, perm: "settings.manage" },
  { to: "/settings", label: "Settings", icon: Settings, perm: "settings.manage" },
];

export default function Layout() {
  // sales saved on the tablet without internet upload from whichever page is open (not only the POS)
  useOfflineQueue();
  const { user, tenant, logout, can } = useAuth();
  const nav = useNavigate();
  const [open, setOpen] = useState(false);
  const brand = useBranding();
  const counts = useApi<any>(can("dashboard.view") ? "/dashboard" : null, 60_000);
  useLiveEvents(() => counts.reload(), can("whatsapp.inbox"));
  const k = counts.data?.kpis;
  const badgeVal: Record<string, number> = { unread: k?.whatsapp.unread ?? 0, orders: k?.pending_orders ?? 0, alerts: k?.open_alerts ?? 0 };
  const [q, setQ] = useState("");
  const visible = NAV.filter((n) => (!n.perm || n.perm.split("|").some(can)) && (!("only" in n) || (n.only as string[]).includes(user?.role ?? "")));
  // flat, searchable list of every menu item the role can see, plus sub-items (e.g. Day book)
  const searchHits = (() => {
    const query = q.trim().toLowerCase();
    if (!query) return [];
    const hits: { to: string; label: string; icon: NavItem["icon"] }[] = [];
    for (const n of visible) {
      if (n.label.toLowerCase().includes(query) || (NAV_UR[n.to] ?? "").includes(q.trim())) hits.push({ to: n.to, label: n.label, icon: n.icon });
      for (const s of SUBS[n.to] ?? []) if (can(s.perm) && (s.label.toLowerCase().includes(query) || (s.ur ?? "").includes(q.trim()))) hits.push({ to: s.to, label: `${n.label} · ${s.label}`, icon: s.icon });
    }
    return hits.slice(0, 12);
  })();

  /* ---- global command palette (Ctrl/⌘ K): jump to any page or shortcut from anywhere ---- */
  const [cmd, setCmd] = useState(false);
  const [cq, setCq] = useState("");
  const [ci, setCi] = useState(0);
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") { e.preventDefault(); setCmd((o) => !o); setCq(""); setCi(0); }
      else if (e.key === "Escape") setCmd(false);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);
  const cmdItems = useMemo(() => {
    const base = visible.map((n) => ({ to: n.to, label: n.label, ur: NAV_UR[n.to] ?? "", icon: n.icon }));
    const subs = visible.flatMap((n) => (SUBS[n.to] ?? []).filter((s) => can(s.perm)).map((s) => ({ to: s.to, label: `${n.label} · ${s.label}`, ur: s.ur ?? "", icon: s.icon })));
    return [...base, ...subs];
  }, [visible, can]);
  // records too: after 2+ letters the server looks up customers, vehicles, clients, thekedars, suppliers, staff, vouchers, cheques, machines
  const [recHits, setRecHits] = useState<{ kind: string; label: string; sub?: string; amount?: number | null; to: string }[]>([]);
  useEffect(() => {
    const query = cq.trim();
    if (!cmd || query.length < 2) { setRecHits([]); return; }
    let live = true;
    const tmr = setTimeout(() => { api<any>(`/search?q=${encodeURIComponent(query)}`).then((r) => { if (live) setRecHits(r?.hits ?? []); }).catch(() => undefined); }, 180);
    return () => { live = false; clearTimeout(tmr); };
  }, [cq, cmd]);
  const REC_ICON: Record<string, any> = { customer: Users, vehicle: Truck, client: Users, thekedar: Users, supplier: Factory, staff: UserPlus, voucher: Receipt, cheque: FileCheck2, machine: Wrench, tanker: Truck, driver: UserPlus };
  const cmdHits = (() => {
    const query = cq.trim().toLowerCase();
    const list = query ? cmdItems.filter((it) => it.label.toLowerCase().includes(query) || it.ur.includes(cq.trim()) || it.to.includes(query)) : cmdItems;
    const recs = recHits.map((h) => ({ to: h.to, label: h.label, ur: h.sub ?? "", icon: REC_ICON[h.kind] ?? Search, amount: h.amount ?? null, rec: true as const }));
    return [...recs, ...list].slice(0, 50);
  })();
  useEffect(() => { setCi(0); }, [cq]);
  const goCmd = (to: string) => { setCmd(false); setQ(""); nav(to); };

  /* ---- light / dark theme toggle (opt-in, saved per device) ---- */
  const [dark, setDark] = useState(() => { try { return localStorage.getItem("pumpai.theme") === "dark"; } catch { return false; } });
  const toggleTheme = () => setDark((d) => { const nd = !d; try { localStorage.setItem("pumpai.theme", nd ? "dark" : "light"); } catch { /* private mode */ } document.documentElement.classList.toggle("dark", nd); return nd; });

  const sidebar = (
    <nav className="flex h-full flex-col bg-brand-900 text-emerald-50">
      <div className="flex items-center gap-2 px-5 py-5">
        {brand?.logo_url ? <img src={brand.logo_url} alt="" className="h-9 w-9 rounded-lg bg-white object-contain p-0.5" /> : <span className="text-2xl">⛽</span>}
        <div className="min-w-0 flex-1">
          <div className="font-semibold leading-tight">PumpAI</div>
          <div className="text-xs text-emerald-200/80 truncate max-w-[9rem]">{tenant?.name}</div>
        </div>
        <div className="hidden lg:block"><NotificationBell dark /></div>
      </div>
      <QuickAddButton />
      <div className="px-3 pb-2">
        <div className="relative">
          <Search size={15} className="pointer-events-none absolute left-2.5 top-2.5 text-emerald-200/70" />
          <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search menu… · مینو تلاش"
            aria-label="Search menu"
            onKeyDown={(e) => { if (e.key === "Enter" && searchHits[0]) { nav(searchHits[0].to); setQ(""); setOpen(false); } if (e.key === "Escape") setQ(""); }}
            className="w-full rounded-lg bg-white/10 py-1.5 pl-8 pr-7 text-sm text-white placeholder:text-emerald-200/60 outline-none ring-1 ring-white/15 focus:bg-white/15 focus:ring-white/40" />
          {q && <button aria-label="Clear" onClick={() => setQ("")} className="absolute right-1.5 top-1.5 rounded p-0.5 text-emerald-200/70 hover:text-white"><X size={15} /></button>}
        </div>
      </div>
      <div className="flex-1 space-y-0.5 overflow-y-auto px-3">
        {q.trim() ? (
          searchHits.length ? searchHits.map((hit) => (
            <NavLink key={hit.to + hit.label} to={hit.to} onClick={() => { setQ(""); setOpen(false); }} className={itemCls(false)}>
              <hit.icon size={17} className="shrink-0" /><span className="min-w-0 flex-1 truncate">{hit.label}</span>
            </NavLink>
          )) : <p className="px-3 py-4 text-center text-sm text-emerald-200/70">Kuch nahi mila · <span lang="ur" dir="rtl" className="font-urdu">کچھ نہیں ملا</span></p>
        ) : (
          <NavMenu items={visible} grouped={["admin", "manager"].includes(user?.role ?? "")} badges={badgeVal} onGo={() => setOpen(false)} />
        )}
      </div>
      <div className="border-t border-white/10 p-4 text-sm">
        <div className="font-medium">{user?.name}</div>
        <div className="text-xs text-emerald-200/80">{ROLE_LABEL[user?.role ?? ""]}</div>
        {user?.station_name && <div className="mt-0.5 flex items-center gap-1 text-xs text-emerald-200/80"><MapPin size={11} />{user.station_name}</div>}
        <div className="mt-3"><InstallAppButton /></div>
        <button onClick={toggleTheme} className="mt-3 flex items-center gap-2 text-xs text-emerald-200 hover:text-white">
          {dark ? <Sun size={14} /> : <Moon size={14} />} {dark ? "Light mode · روشن" : "Dark mode · گہرا"}
        </button>
        <button onClick={() => { logout(); nav("/login"); }} className="mt-3 flex items-center gap-2 text-xs text-emerald-200 hover:text-white">
          <LogOut size={14} /> Sign out
        </button>
      </div>
    </nav>
  );

  return (
    <NotificationsProvider>
    <div className="flex min-h-screen">
      {cmd && (
        <div className="fixed inset-0 z-[60] flex items-start justify-center bg-black/40 p-4 pt-[12vh] print:hidden" onClick={() => setCmd(false)}>
          <div className="w-full max-w-lg overflow-hidden rounded-2xl bg-white shadow-2xl" onClick={(e) => e.stopPropagation()}>
            <div className="flex items-center gap-2 border-b border-slate-100 px-3">
              <Search size={16} className="shrink-0 text-slate-400" />
              <input autoFocus value={cq} onChange={(e) => setCq(e.target.value)}
                onKeyDown={(e) => { if (e.key === "ArrowDown") { e.preventDefault(); setCi((x) => Math.min(x + 1, cmdHits.length - 1)); } else if (e.key === "ArrowUp") { e.preventDefault(); setCi((x) => Math.max(x - 1, 0)); } else if (e.key === "Enter" && cmdHits[ci]) { e.preventDefault(); goCmd(cmdHits[ci].to); } }}
                placeholder="Page, customer, gaari number, client, supplier, voucher no… sab yahan dhoondein" aria-label="Command palette"
                className="w-full bg-transparent py-3 text-sm outline-none" />
              <kbd className="mr-1 hidden shrink-0 rounded border border-slate-200 px-1.5 py-0.5 text-[10px] text-slate-400 sm:block">ESC</kbd>
            </div>
            <ul className="max-h-[52vh] overflow-y-auto py-1">
              {cmdHits.map((it, idx) => (
                <li key={it.to + it.label}>
                  <button onMouseEnter={() => setCi(idx)} onClick={() => goCmd(it.to)}
                    className={`flex w-full items-center gap-3 px-3 py-2 text-left text-sm ${idx === ci ? "bg-brand-50 text-brand-700" : "text-slate-700"}`}>
                    <it.icon size={16} className="shrink-0 opacity-70" />
                    <span className="min-w-0 flex-1 truncate">{it.label}{(it as any).rec && it.ur && <span className="ml-2 text-xs text-slate-400">{it.ur}</span>}</span>
                    {(it as any).rec && (it as any).amount != null && <span className="shrink-0 text-xs font-semibold tabular-nums text-slate-600">{pkr((it as any).amount)}</span>}
                    {!(it as any).rec && it.ur && <span lang="ur" dir="rtl" className="font-urdu shrink-0 text-xs text-slate-400">{it.ur}</span>}
                  </button>
                </li>
              ))}
              {!cmdHits.length && <li className="px-3 py-6 text-center text-sm text-slate-400">Kuch nahi mila · <span lang="ur" dir="rtl" className="font-urdu">کچھ نہیں ملا</span></li>}
            </ul>
          </div>
        </div>
      )}
      <aside className="fixed inset-y-0 left-0 hidden w-64 lg:block print:hidden">{sidebar}</aside>
      {open && (
        <div className="fixed inset-0 z-40 lg:hidden">
          <div className="absolute inset-0 bg-black/40" onClick={() => setOpen(false)} />
          <aside className="absolute inset-y-0 left-0 w-64">{sidebar}</aside>
        </div>
      )}
      <div className="flex-1 lg:pl-64 min-w-0 print:pl-0">
        <header className="sticky top-0 z-30 flex items-center gap-3 border-b border-slate-200 bg-white/90 px-4 py-3 backdrop-blur lg:hidden print:hidden">
          <button onClick={() => setOpen(!open)} aria-label="Menu">{open ? <X /> : <Menu />}</button>
          <span className="flex-1 font-semibold">⛽ PumpAI</span>
          <button onClick={() => { setCmd(true); setCq(""); setCi(0); }} aria-label="Jump to…" className="text-slate-500"><Search size={20} /></button>
          <button onClick={toggleTheme} aria-label="Theme" className="text-slate-500">{dark ? <Sun size={20} /> : <Moon size={20} />}</button>
          <NotificationBell />
        </header>
        <main className="mx-auto max-w-7xl p-4 lg:p-6 print:max-w-none print:p-0"><PrintHeader /><Outlet /><PrintFooter />
          </main>
        <PageGuideButton />
      </div>
    </div>
    </NotificationsProvider>
  );
}


/** A small "?" on every page that opens the user guide at the module for THIS page. */
function PageGuideButton() {
  const loc = useLocation();
  if (loc.pathname === "/help") return null;
  const base = "/" + loc.pathname.split("/")[1];
  const id = ROUTE_GUIDE[base];
  return (
    <Link to={id ? `/help#${id}` : "/help"} title={id ? "Is page ka guide · اس صفحے کی رہنمائی" : "User guide · رہنمائی"} aria-label="User guide for this page"
      className="fixed bottom-20 right-4 z-30 flex h-11 w-11 items-center justify-center rounded-full bg-brand-900 text-white shadow-lg ring-2 ring-white/70 hover:bg-brand-700 lg:bottom-6 print:hidden">
      <CircleHelp size={22} />
    </Link>
  );
}

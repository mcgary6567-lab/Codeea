import { useState } from "react";
import { PrintFooter, PrintHeader } from "./Letterhead";
import { Link, NavLink, Outlet, useLocation, useNavigate } from "react-router-dom";
import {
  LayoutDashboard, MessageCircle, Users, Fuel, Clock, Droplets, Tag, Truck, Megaphone, Bell, Bot, Settings, LogOut, Menu, X, MessageSquareWarning, BookOpen, UserCog, MapPin, Container, Receipt, FileBarChart, Factory, Wallet, Landmark, ShoppingBasket, ClipboardCheck, ShieldCheck, HeartPulse, CalendarClock, ScrollText, Ticket, Star, Calculator, History, Wrench, Route, UserPlus, ChevronDown, Ban,
  HandCoins, ClipboardList, Banknote, ArrowDownCircle, ArrowUpCircle, FileCheck2, BookOpenText,
  Building2, ListChecks, Lightbulb, UserCheck, Hourglass, Search,
} from "lucide-react";
import { useAuth, ROLE_LABEL } from "../App";
import { useApi, useLiveEvents } from "../lib/api";
import { NotificationsProvider, NotificationBell } from "./Notifications";
import { QuickAddButton } from "./QuickAdd";
import { useBranding } from "../lib/brand";
import { InstallAppButton } from "./InstallApp";

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
  "/pos": "سیل", "/shifts": "شفٹ", "/customers": "گاہک", "/bookings": "بکنگ", "/prices": "ریٹ", "/checklist": "روزانہ چیک",
  "/machines": "مشینیں", "/my-account": "میرا حساب", "/help": "رہنمائی", "/wholesale": "ہول سیل", "/carriage": "کرایہ", "/cashier": "کیشیئر", "/cash": "کیش بک",
  "/other-entries": "متفرق آمدن/خرچہ",
};
/** Manager / owner menu groups (other roles have short menus and see them flat). */
const GROUPS: { key: string; label: string; icon: any; items: string[] }[] = [
  { key: "sales", label: "Sales & shifts", icon: Fuel, items: ["/pos", "/shifts", "/bookings"] },
  { key: "customers", label: "Customers & khata", icon: Users, items: ["/customers", "/khata", "/prepaid", "/inbox", "/orders", "/complaints", "/campaigns"] },
  { key: "wholesale", label: "Wholesale", icon: Container, items: ["/wholesale", "/carriage"] },
  { key: "stock", label: "Stock & prices", icon: Droplets, items: ["/stock", "/register", "/prices", "/shop"] },
  { key: "money", label: "Money & accounts", icon: Landmark, items: ["/cashier", "/expenses", "/cash", "/accounts", "/suppliers", "/property", "/other-entries"] },
  { key: "staff", label: "Staff", icon: Wallet, items: ["/staff", "/kiosk", "/team", "/my-account"] },
  { key: "safety", label: "Safety", icon: ShieldCheck, items: ["/compliance", "/checklist", "/machines", "/alerts"] },
  { key: "system", label: "System", icon: Settings, items: ["/automations", "/users", "/audit", "/corrections", "/lists", "/settings"] },
];
const itemCls = (on: boolean) => `flex items-center gap-3 rounded-lg px-3 py-2 text-sm transition ${on ? "bg-white/15 text-white" : "text-emerald-100/90 hover:bg-white/10"}`;
const Badge = ({ n }: { n: number }) => n > 0 ? <span className="rounded-full bg-emerald-400 px-1.5 text-xs font-semibold text-emerald-950">{n}</span> : null;

type NavItem = (typeof NAV)[number];
/** The side menu: flat for short menus; for the manager / owner, groups that open and close. */
function NavMenu({ items, grouped, badges, onGo }: { items: NavItem[]; grouped: boolean; badges: Record<string, number>; onGo: () => void }) {
  const { can } = useAuth();
  const loc = useLocation();
  // on a shortcut (e.g. /cashier?tab=pay) the shortcut is lit, not its parent
  const onShortcut = (base: string) => loc.pathname === base && (SUBS[base] ?? []).some((x) => x.to.slice(base.length) === loc.search);
  const isOn = (to: string, end?: boolean) => (end ? loc.pathname === to : loc.pathname === to || loc.pathname.startsWith(to + "/")) && !onShortcut(to);
  const [openSet, setOpenSet] = useState<string[]>(() => { try { return JSON.parse(localStorage.getItem("pumpai.nav.open") ?? "[]"); } catch { return []; } });
  const toggle = (k: string) => setOpenSet((cur) => { const next = cur.includes(k) ? cur.filter((x) => x !== k) : [...cur, k]; try { localStorage.setItem("pumpai.nav.open", JSON.stringify(next)); } catch { /* private mode */ } return next; });

  const link = (n: NavItem, size = 17) => (
    <NavLink key={n.to} to={n.to} end={(n as any).end} onClick={onGo} className={() => itemCls(isOn(n.to, (n as any).end))}>
      <n.icon size={size} className="shrink-0" />
      <span className="min-w-0 flex-1 truncate">{n.label}</span>
      {!grouped && NAV_UR[n.to] && <span lang="ur" dir="rtl" className="font-urdu shrink-0 text-sm opacity-75">{NAV_UR[n.to]}</span>}
      {(n as any).badge && <Badge n={badges[(n as any).badge] ?? 0} />}
    </NavLink>
  );
  const sub = (base: string) => (
    <div key={`${base}-sub`} className="ml-4 space-y-0.5 border-l border-white/15 pl-2">
      {SUBS[base].filter((x) => can(x.perm)).map((x) => (
        <Link key={x.to} to={x.to} onClick={onGo} className={itemCls(loc.pathname === base && loc.search === x.to.slice(base.length))}>
          <x.icon size={16} /><span className="min-w-0 flex-1 truncate">{x.label}</span>
          {!grouped && <span lang="ur" dir="rtl" className="font-urdu shrink-0 text-sm opacity-75">{x.ur}</span>}
        </Link>
      ))}
    </div>
  );
  const withSub = (n: NavItem, size?: number) => (SUBS[n.to] ? [link(n, size), sub(n.to)] : [link(n, size)]);

  if (!grouped) return <>{items.flatMap((n) => withSub(n))}</>;

  const inGroup = new Set(GROUPS.flatMap((g) => g.items));
  return (
    <>
      {items.filter((n) => !inGroup.has(n.to)).map((n) => link(n))}
      {GROUPS.map((g) => {
        const kids = g.items.map((to) => items.find((n) => n.to === to)).filter(Boolean) as NavItem[];
        if (!kids.length) return null;
        const active = kids.some((n) => isOn(n.to) || (SUBS[n.to] && loc.pathname.startsWith(n.to)));
        const open = active || openSet.includes(g.key);
        const count = kids.reduce((a, n) => a + ((n as any).badge ? badges[(n as any).badge] ?? 0 : 0), 0);
        return (
          <div key={g.key} className="pt-1">
            <button type="button" onClick={() => toggle(g.key)} aria-expanded={open}
              className={`flex w-full items-center gap-3 rounded-lg px-3 py-2 text-left text-sm transition ${active ? "text-white" : "text-emerald-100/90"} hover:bg-white/10`}>
              <g.icon size={17} className="shrink-0" />
              <span className="min-w-0 flex-1 truncate font-medium">{g.label}</span>
              {!open && <Badge n={count} />}
              <ChevronDown size={15} className={`opacity-70 transition ${open ? "rotate-180" : ""}`} />
            </button>
            {open && (
              <div className="ml-4 space-y-0.5 border-l border-white/15 pl-2">
                {kids.flatMap((n) => withSub(n, 16))}
              </div>
            )}
          </div>
        );
      })}
    </>
  );
}

const NAV = [
  { to: "/", label: "Dashboard", icon: LayoutDashboard, end: true, perm: "dashboard.view" },
  { to: "/reports", label: "Reports", icon: FileBarChart, perm: "reports.view" },
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
  { to: "/lists", label: "Lists", icon: ListChecks, perm: "settings.manage" },
  { to: "/settings", label: "Settings", icon: Settings, perm: "settings.manage" },
];

export default function Layout() {
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
        <button onClick={() => { logout(); nav("/login"); }} className="mt-3 flex items-center gap-2 text-xs text-emerald-200 hover:text-white">
          <LogOut size={14} /> Sign out
        </button>
      </div>
    </nav>
  );

  return (
    <NotificationsProvider>
    <div className="flex min-h-screen">
      <aside className="fixed inset-y-0 left-0 hidden w-60 lg:block print:hidden">{sidebar}</aside>
      {open && (
        <div className="fixed inset-0 z-40 lg:hidden">
          <div className="absolute inset-0 bg-black/40" onClick={() => setOpen(false)} />
          <aside className="absolute inset-y-0 left-0 w-64">{sidebar}</aside>
        </div>
      )}
      <div className="flex-1 lg:pl-60 min-w-0 print:pl-0">
        <header className="sticky top-0 z-30 flex items-center gap-3 border-b border-slate-200 bg-white/90 px-4 py-3 backdrop-blur lg:hidden print:hidden">
          <button onClick={() => setOpen(!open)} aria-label="Menu">{open ? <X /> : <Menu />}</button>
          <span className="flex-1 font-semibold">⛽ PumpAI</span>
          <NotificationBell />
        </header>
        <main className="mx-auto max-w-7xl p-4 lg:p-6 print:max-w-none print:p-0"><PrintHeader /><Outlet /><PrintFooter />
          </main>
      </div>
    </div>
    </NotificationsProvider>
  );
}

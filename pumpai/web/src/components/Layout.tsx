import { useState } from "react";
import { NavLink, Outlet, useNavigate } from "react-router-dom";
import {
  LayoutDashboard, MessageCircle, Users, Fuel, Clock, Droplets, Tag, Truck, Megaphone, Bell, Bot, Settings, LogOut, Menu, X, MessageSquareWarning, BookOpen, UserCog, MapPin, Container, Receipt, FileBarChart, Factory, Wallet, Landmark, ShoppingBasket, ClipboardCheck, ShieldCheck, HeartPulse, CalendarClock, ScrollText, Ticket, Star, Calculator, History, Wrench,
} from "lucide-react";
import { useAuth, ROLE_LABEL } from "../App";
import { useApi, useLiveEvents } from "../lib/api";
import { NotificationsProvider, NotificationBell } from "./Notifications";
import { QuickAddButton } from "./QuickAdd";
import { HelpButton } from "./Help";
import { useBranding, useInstallPrompt } from "../lib/brand";

const NAV = [
  { to: "/", label: "Dashboard", icon: LayoutDashboard, end: true, perm: "dashboard.view" },
  { to: "/reports", label: "Reports", icon: FileBarChart, perm: "reports.view" },
  { to: "/insights", label: "Owner insights", icon: HeartPulse, perm: "reports.view" },
  { to: "/inbox", label: "WhatsApp Inbox", icon: MessageCircle, badge: "unread", perm: "whatsapp.inbox" },
  { to: "/pos", label: "Sales / POS", icon: Fuel, perm: "sales.create" },
  { to: "/shifts", label: "Shifts", icon: Clock, perm: "shifts.manage" },
  { to: "/customers", label: "Customers", icon: Users, perm: "customers.view" },
  { to: "/khata", label: "Khata (Credit)", icon: BookOpen, perm: "khata.manage" },
  { to: "/prepaid", label: "Coupons & wallets", icon: Ticket, perm: "khata.manage" },
  { to: "/wholesale", label: "Wholesale Supply", icon: Container, perm: "wholesale.view" },
  { to: "/expenses", label: "Expenses", icon: Receipt, perm: "expenses.view" },
  { to: "/cash", label: "Cash & bank", icon: Landmark, perm: "expenses.view" },
  { to: "/accounts", label: "Accounts & tax", icon: Calculator, perm: "reports.view" },
  { to: "/suppliers", label: "Suppliers", icon: Factory, perm: "suppliers.manage" },
  { to: "/orders", label: "Orders", icon: Truck, badge: "orders", perm: "orders.manage" },
  { to: "/bookings", label: "Bookings", icon: CalendarClock, perm: "sales.create" },
  { to: "/complaints", label: "Complaints", icon: MessageSquareWarning, perm: "complaints.manage" },
  { to: "/campaigns", label: "Campaigns", icon: Megaphone, perm: "campaigns.manage" },
  { to: "/stock", label: "Tanks & Stock", icon: Droplets, perm: "stock.manage" },
  { to: "/register", label: "Stock register", icon: ScrollText, perm: "stock.manage" },
  { to: "/shop", label: "Shop & lubricants", icon: ShoppingBasket, perm: "stock.manage" },
  { to: "/prices", label: "Prices", icon: Tag, perm: "prices.view" },
  { to: "/alerts", label: "Alerts", icon: Bell, badge: "alerts", perm: "alerts.view" },
  { to: "/automations", label: "AI Automations", icon: Bot, perm: "automations.manage" },
  { to: "/staff", label: "Staff accounts", icon: Wallet, perm: "staff.manage" },
  { to: "/team", label: "Ratings & commission", icon: Star, perm: "staff.manage" },
  { to: "/checklist", label: "Daily checks", icon: ClipboardCheck, perm: "sales.create", only: ["salesman"] },
  { to: "/compliance", label: "Licences & checklist", icon: ShieldCheck, perm: "alerts.view" },
  { to: "/machines", label: "Machines", icon: Wrench, perm: "sales.create" },
  { to: "/my-account", label: "My account", icon: Wallet, perm: "", only: ["salesman", "wholesale", "manager"] },
  { to: "/users", label: "Users & Roles", icon: UserCog, perm: "users.manage" },
  { to: "/audit", label: "Audit log", icon: History, perm: "audit.view" },
  { to: "/settings", label: "Settings", icon: Settings, perm: "settings.manage" },
];

export default function Layout() {
  const { user, tenant, logout, can } = useAuth();
  const nav = useNavigate();
  const [open, setOpen] = useState(false);
  const brand = useBranding();
  const { canInstall, install } = useInstallPrompt();
  const counts = useApi<any>(can("dashboard.view") ? "/dashboard" : null, 60_000);
  useLiveEvents(() => counts.reload(), can("whatsapp.inbox"));
  const k = counts.data?.kpis;
  const badgeVal: Record<string, number> = { unread: k?.whatsapp.unread ?? 0, orders: k?.pending_orders ?? 0, alerts: k?.open_alerts ?? 0 };

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
      <div className="flex-1 space-y-0.5 overflow-y-auto px-3">
        {NAV.filter((n) => (!n.perm || can(n.perm)) && (!("only" in n) || (n.only as string[]).includes(user?.role ?? ""))).map((n) => (
          <NavLink key={n.to} to={n.to} end={n.end} onClick={() => setOpen(false)}
            className={({ isActive }) => `flex items-center gap-3 rounded-lg px-3 py-2 text-sm transition ${isActive ? "bg-white/15 text-white" : "text-emerald-100/90 hover:bg-white/10"}`}>
            <n.icon size={17} />
            <span className="flex-1">{n.label}</span>
            {n.badge && badgeVal[n.badge] > 0 && <span className="rounded-full bg-emerald-400 px-1.5 text-xs font-semibold text-emerald-950">{badgeVal[n.badge]}</span>}
          </NavLink>
        ))}
      </div>
      <div className="border-t border-white/10 p-4 text-sm">
        <div className="font-medium">{user?.name}</div>
        <div className="text-xs text-emerald-200/80">{ROLE_LABEL[user?.role ?? ""]}</div>
        {user?.station_name && <div className="mt-0.5 flex items-center gap-1 text-xs text-emerald-200/80"><MapPin size={11} />{user.station_name}</div>}
        {canInstall && <button onClick={install} className="mt-3 block text-xs text-emerald-100 underline hover:text-white">⬇ Install app on this device</button>}
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
        <main className="mx-auto max-w-7xl p-4 lg:p-6 print:max-w-none print:p-0"><Outlet />
          <div className="print:hidden"><HelpButton /></div></main>
      </div>
    </div>
    </NotificationsProvider>
  );
}

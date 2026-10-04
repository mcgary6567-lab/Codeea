import { useState } from "react";
import { NavLink, Outlet, useNavigate } from "react-router-dom";
import {
  LayoutDashboard, MessageCircle, Users, Fuel, Clock, Droplets, Tag, Truck, Megaphone, Bell, Bot, Settings, LogOut, Menu, X, MessageSquareWarning, BookOpen,
} from "lucide-react";
import { useAuth } from "../App";
import { useApi, useLiveEvents } from "../lib/api";

const NAV = [
  { to: "/", label: "Dashboard", icon: LayoutDashboard, end: true },
  { to: "/inbox", label: "WhatsApp Inbox", icon: MessageCircle, badge: "unread" },
  { to: "/customers", label: "Customers", icon: Users },
  { to: "/khata", label: "Khata (Credit)", icon: BookOpen },
  { to: "/orders", label: "Orders", icon: Truck, badge: "orders" },
  { to: "/complaints", label: "Complaints", icon: MessageSquareWarning },
  { to: "/campaigns", label: "Campaigns", icon: Megaphone },
  { to: "/pos", label: "Sales / POS", icon: Fuel },
  { to: "/shifts", label: "Shifts", icon: Clock },
  { to: "/stock", label: "Tanks & Stock", icon: Droplets },
  { to: "/prices", label: "Prices", icon: Tag },
  { to: "/alerts", label: "Alerts", icon: Bell, badge: "alerts" },
  { to: "/automations", label: "AI Automations", icon: Bot },
  { to: "/settings", label: "Settings", icon: Settings },
];

export default function Layout() {
  const { user, tenant, logout } = useAuth();
  const nav = useNavigate();
  const [open, setOpen] = useState(false);
  const counts = useApi<any>("/dashboard", 60_000);
  useLiveEvents(() => counts.reload());
  const k = counts.data?.kpis;
  const badgeVal: Record<string, number> = { unread: k?.whatsapp.unread ?? 0, orders: k?.pending_orders ?? 0, alerts: k?.open_alerts ?? 0 };

  const sidebar = (
    <nav className="flex h-full flex-col bg-brand-900 text-emerald-50">
      <div className="flex items-center gap-2 px-5 py-5">
        <span className="text-2xl">⛽</span>
        <div>
          <div className="font-semibold leading-tight">PumpAI</div>
          <div className="text-xs text-emerald-200/80 truncate max-w-[10rem]">{tenant?.name}</div>
        </div>
      </div>
      <div className="flex-1 space-y-0.5 overflow-y-auto px-3">
        {NAV.map((n) => (
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
        <div className="text-xs capitalize text-emerald-200/80">{user?.role}</div>
        <button onClick={() => { logout(); nav("/login"); }} className="mt-3 flex items-center gap-2 text-xs text-emerald-200 hover:text-white">
          <LogOut size={14} /> Sign out
        </button>
      </div>
    </nav>
  );

  return (
    <div className="flex min-h-screen">
      <aside className="fixed inset-y-0 left-0 hidden w-60 lg:block">{sidebar}</aside>
      {open && (
        <div className="fixed inset-0 z-40 lg:hidden">
          <div className="absolute inset-0 bg-black/40" onClick={() => setOpen(false)} />
          <aside className="absolute inset-y-0 left-0 w-64">{sidebar}</aside>
        </div>
      )}
      <div className="flex-1 lg:pl-60 min-w-0">
        <header className="sticky top-0 z-30 flex items-center gap-3 border-b border-slate-200 bg-white/90 px-4 py-3 backdrop-blur lg:hidden">
          <button onClick={() => setOpen(!open)} aria-label="Menu">{open ? <X /> : <Menu />}</button>
          <span className="font-semibold">⛽ PumpAI</span>
        </header>
        <main className="mx-auto max-w-7xl p-4 lg:p-6"><Outlet /></main>
      </div>
    </div>
  );
}

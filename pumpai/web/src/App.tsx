import { createContext, useContext, useEffect, useState, type ReactNode } from "react";
import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";
import { api, getToken, setToken } from "./lib/api";
import { ToastProvider, Loading } from "./components/ui";
import Layout from "./components/Layout";
import Login from "./pages/Login";
import Dashboard from "./pages/Dashboard";
import Inbox from "./pages/Inbox";
import Customers from "./pages/Customers";
import Khata from "./pages/Khata";
import Orders from "./pages/Orders";
import Complaints from "./pages/Complaints";
import Campaigns from "./pages/Campaigns";
import Pos from "./pages/Pos";
import Shifts from "./pages/Shifts";
import Stock from "./pages/Stock";
import Prices from "./pages/Prices";
import Alerts from "./pages/Alerts";
import Automations from "./pages/Automations";
import SettingsPage from "./pages/Settings";
import Users from "./pages/Users";
import Wholesale from "./pages/Wholesale";
import Expenses from "./pages/Expenses";
import Reports from "./pages/Reports";
import Suppliers from "./pages/Suppliers";
import Staff from "./pages/Staff";
import MyAccount from "./pages/MyAccount";
import Cards from "./pages/Cards";
import Cash from "./pages/Cash";
import Shop from "./pages/Shop";
import Compliance from "./pages/Compliance";
import ChecklistPage from "./pages/ChecklistPage";
import Insights from "./pages/Insights";
import Bookings from "./pages/Bookings";
import Register from "./pages/Register";
import Prepaid, { CouponSheet } from "./pages/Prepaid";
import Team from "./pages/Team";
import Accounts from "./pages/Accounts";
import Setup from "./pages/Setup";
import Audit from "./pages/Audit";
import Machines from "./pages/Machines";
import { loadBranding } from "./lib/brand";

loadBranding().catch(() => {});

type User = { id: number; name: string; email: string; role: "admin" | "manager" | "salesman" | "wholesale"; tenant_id: number; station_id: number | null; station_name: string | null };
type Auth = {
  user: User | null; tenant: { id: number; name: string } | null; permissions: string[];
  can: (perm: string) => boolean; login: (token: string) => Promise<void>; logout: () => void;
};
export const ROLE_LABEL: Record<string, string> = { admin: "Admin (CEO)", manager: "Manager", salesman: "Salesman", wholesale: "Wholesale Officer" };
const AuthCtx = createContext<Auth>(null as unknown as Auth);
export const useAuth = () => useContext(AuthCtx);

function AuthProvider({ children }: { children: ReactNode }) {
  const empty = { user: null, tenant: null, permissions: [] as string[], ready: true };
  const [state, setState] = useState<{ user: User | null; tenant: Auth["tenant"]; permissions: string[]; ready: boolean }>({ ...empty, ready: false });
  const load = async () => {
    if (!getToken()) return setState(empty);
    try {
      const me = await api("/me");
      setState({ user: me.user, tenant: me.tenant, permissions: me.permissions, ready: true });
    } catch {
      setState(empty);
    }
  };
  useEffect(() => { load(); }, []);
  const value: Auth = {
    user: state.user, tenant: state.tenant, permissions: state.permissions,
    can: (perm) => state.permissions.includes(perm),
    login: async (token) => { setToken(token); await load(); },
    logout: () => { setToken(null); setState(empty); },
  };
  if (!state.ready) return <Loading />;
  return <AuthCtx.Provider value={value}>{children}</AuthCtx.Provider>;
}

function Protected({ children }: { children: ReactNode }) {
  const { user } = useAuth();
  return user ? <>{children}</> : <Navigate to="/login" replace />;
}

/** Route guard: shows a friendly "no access" page instead of the screen. */
function Need({ perm, children }: { perm: string; children: ReactNode }) {
  const { can, user } = useAuth();
  if (can(perm)) return <>{children}</>;
  return (
    <div className="card mx-auto mt-10 max-w-md p-6 text-center">
      <div className="text-4xl">🔒</div>
      <h1 className="mt-2 text-lg font-semibold">No access</h1>
      <p className="mt-1 text-sm text-slate-600">Your role ({ROLE_LABEL[user?.role ?? ""]}) can't open this page. Ask your admin if you need access.</p>
    </div>
  );
}

/** Managers/admins land on the dashboard; other roles land on their main screen. */
function Home() {
  const { can } = useAuth();
  if (can("dashboard.view")) return <Dashboard />;
  return <Navigate to={can("wholesale.view") ? "/wholesale" : "/pos"} replace />;
}

export default function App() {
  return (
    <ToastProvider>
      <AuthProvider>
        <BrowserRouter>
          <Routes>
            <Route path="/login" element={<Login />} />
            <Route path="/setup" element={<Setup />} />
            <Route element={<Protected><Layout /></Protected>}>
              <Route index element={<Home />} />
              <Route path="inbox" element={<Need perm="whatsapp.inbox"><Inbox /></Need>} />
              <Route path="inbox/:id" element={<Need perm="whatsapp.inbox"><Inbox /></Need>} />
              <Route path="customers" element={<Need perm="customers.view"><Customers /></Need>} />
              <Route path="customers/:id" element={<Need perm="customers.view"><Customers /></Need>} />
              <Route path="khata" element={<Need perm="khata.manage"><Khata /></Need>} />
              <Route path="orders" element={<Need perm="orders.manage"><Orders /></Need>} />
              <Route path="complaints" element={<Need perm="complaints.manage"><Complaints /></Need>} />
              <Route path="campaigns" element={<Need perm="campaigns.manage"><Campaigns /></Need>} />
              <Route path="pos" element={<Need perm="sales.create"><Pos /></Need>} />
              <Route path="shifts" element={<Need perm="shifts.manage"><Shifts /></Need>} />
              <Route path="stock" element={<Need perm="stock.manage"><Stock /></Need>} />
              <Route path="prices" element={<Need perm="prices.view"><Prices /></Need>} />
              <Route path="alerts" element={<Need perm="alerts.view"><Alerts /></Need>} />
              <Route path="automations" element={<Need perm="automations.manage"><Automations /></Need>} />
              <Route path="wholesale" element={<Need perm="wholesale.view"><Wholesale /></Need>} />
              <Route path="wholesale/:id" element={<Need perm="wholesale.view"><Wholesale /></Need>} />
              <Route path="expenses" element={<Need perm="expenses.view"><Expenses /></Need>} />
              <Route path="reports" element={<Need perm="reports.view"><Reports /></Need>} />
              <Route path="suppliers" element={<Need perm="suppliers.manage"><Suppliers /></Need>} />
              <Route path="users" element={<Need perm="users.manage"><Users /></Need>} />
              <Route path="settings" element={<Need perm="settings.manage"><SettingsPage /></Need>} />
              <Route path="staff" element={<Need perm="staff.manage"><Staff /></Need>} />
              <Route path="my-account" element={<MyAccount />} />
              <Route path="cash" element={<Need perm="expenses.view"><Cash /></Need>} />
              <Route path="shop" element={<Need perm="stock.manage"><Shop /></Need>} />
              <Route path="compliance" element={<Need perm="alerts.view"><Compliance /></Need>} />
              <Route path="checklist" element={<Need perm="sales.create"><ChecklistPage /></Need>} />
              <Route path="insights" element={<Need perm="reports.view"><Insights /></Need>} />
              <Route path="bookings" element={<Need perm="sales.create"><Bookings /></Need>} />
              <Route path="register" element={<Need perm="stock.manage"><Register /></Need>} />
              <Route path="prepaid" element={<Need perm="khata.manage"><Prepaid /></Need>} />
              <Route path="team" element={<Need perm="staff.manage"><Team /></Need>} />
              <Route path="accounts" element={<Need perm="reports.view"><Accounts /></Need>} />
              <Route path="audit" element={<Need perm="audit.view"><Audit /></Need>} />
              <Route path="machines" element={<Need perm="sales.create"><Machines /></Need>} />
            </Route>
            <Route path="/cards/:id" element={<Protected><Need perm="khata.manage"><Cards /></Need></Protected>} />
            <Route path="/coupons/:batch" element={<Protected><Need perm="khata.manage"><CouponSheet /></Need></Protected>} />
            <Route path="*" element={<Navigate to="/" replace />} />
          </Routes>
        </BrowserRouter>
      </AuthProvider>
    </ToastProvider>
  );
}

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

type User = { id: number; name: string; email: string; role: string; tenant_id: number };
type Auth = { user: User | null; tenant: { id: number; name: string } | null; login: (token: string) => Promise<void>; logout: () => void };
const AuthCtx = createContext<Auth>(null as unknown as Auth);
export const useAuth = () => useContext(AuthCtx);

function AuthProvider({ children }: { children: ReactNode }) {
  const [state, setState] = useState<{ user: User | null; tenant: Auth["tenant"]; ready: boolean }>({ user: null, tenant: null, ready: false });
  const load = async () => {
    if (!getToken()) return setState({ user: null, tenant: null, ready: true });
    try {
      const me = await api("/me");
      setState({ user: me.user, tenant: me.tenant, ready: true });
    } catch {
      setState({ user: null, tenant: null, ready: true });
    }
  };
  useEffect(() => { load(); }, []);
  const value: Auth = {
    user: state.user, tenant: state.tenant,
    login: async (token) => { setToken(token); await load(); },
    logout: () => { setToken(null); setState({ user: null, tenant: null, ready: true }); },
  };
  if (!state.ready) return <Loading />;
  return <AuthCtx.Provider value={value}>{children}</AuthCtx.Provider>;
}

function Protected({ children }: { children: ReactNode }) {
  const { user } = useAuth();
  return user ? <>{children}</> : <Navigate to="/login" replace />;
}

export default function App() {
  return (
    <ToastProvider>
      <AuthProvider>
        <BrowserRouter>
          <Routes>
            <Route path="/login" element={<Login />} />
            <Route element={<Protected><Layout /></Protected>}>
              <Route index element={<Dashboard />} />
              <Route path="inbox" element={<Inbox />} />
              <Route path="inbox/:id" element={<Inbox />} />
              <Route path="customers" element={<Customers />} />
              <Route path="customers/:id" element={<Customers />} />
              <Route path="khata" element={<Khata />} />
              <Route path="orders" element={<Orders />} />
              <Route path="complaints" element={<Complaints />} />
              <Route path="campaigns" element={<Campaigns />} />
              <Route path="pos" element={<Pos />} />
              <Route path="shifts" element={<Shifts />} />
              <Route path="stock" element={<Stock />} />
              <Route path="prices" element={<Prices />} />
              <Route path="alerts" element={<Alerts />} />
              <Route path="automations" element={<Automations />} />
              <Route path="settings" element={<SettingsPage />} />
            </Route>
            <Route path="*" element={<Navigate to="/" replace />} />
          </Routes>
        </BrowserRouter>
      </AuthProvider>
    </ToastProvider>
  );
}

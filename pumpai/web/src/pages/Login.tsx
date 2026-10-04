import { useState } from "react";
import { Navigate, useNavigate } from "react-router-dom";
import { api } from "../lib/api";
import { useAuth } from "../App";
import { ErrorBox } from "../components/ui";

export default function Login() {
  const { user, login } = useAuth();
  const nav = useNavigate();
  const [email, setEmail] = useState("owner@pumpai.pk");
  const [password, setPassword] = useState("demo1234");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  if (user) return <Navigate to="/" replace />;

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const r = await api("/auth/login", { body: { email, password } });
      await login(r.token);
      nav("/");
    } catch (err: any) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="flex min-h-screen items-center justify-center bg-gradient-to-br from-brand-900 via-brand-700 to-emerald-500 p-4">
      <div className="w-full max-w-md">
        <div className="mb-6 text-center text-white">
          <div className="text-5xl">⛽</div>
          <h1 className="mt-2 text-2xl font-bold">PumpAI</h1>
          <p className="text-emerald-100">AI WhatsApp CRM & forecourt management</p>
        </div>
        <form onSubmit={submit} className="card space-y-4 p-6">
          {error && <ErrorBox error={error} />}
          <label className="block"><span className="label">Email</span><input className="input" type="email" value={email} onChange={(e) => setEmail(e.target.value)} required /></label>
          <label className="block"><span className="label">Password</span><input className="input" type="password" value={password} onChange={(e) => setPassword(e.target.value)} required /></label>
          <button className="btn-primary w-full" disabled={busy}>{busy ? "Signing in…" : "Sign in"}</button>
          <p className="text-center text-xs text-slate-500">
            Demo accounts (password <b>demo1234</b>): owner@, manager@, accounts@, attendant@pumpai.pk
          </p>
        </form>
      </div>
    </div>
  );
}

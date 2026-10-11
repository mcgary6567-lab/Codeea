import { useQueryClient } from '@tanstack/react-query';
import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from 'react';
import { isApiError, setAuthToken } from '@/api/client';
import { api } from '@/api/endpoints';
import { keys } from '@/api/queryKeys';
import type { Entitlements, LoginBody, MeResponse, RegisterBody, User } from '@/api/types';
import { on } from '@/lib/events';
import { getItem, getJson, setItem, setJson, storageKeys } from '@/lib/storage';
import { FREE_FALLBACK } from './freeEntitlements';
import { disablePushAlerts } from '@/notifications/push';

type Status = 'loading' | 'guest' | 'authed';

interface AuthState {
  status: Status;
  isAuthed: boolean;
  user: User | null;
  entitlements: Entitlements;
  /** User picked "Browse without account" (or signed out). */
  guestChosen: boolean;
  onboardingSeen: boolean;
  signIn: (body: LoginBody) => Promise<void>;
  signUp: (body: RegisterBody) => Promise<void>;
  signOut: () => Promise<void>;
  deleteAccount: () => Promise<void>;
  continueAsGuest: () => Promise<void>;
  markOnboardingSeen: () => Promise<void>;
  /** Apply a fresh {user, entitlements} (e.g. after a verified purchase). */
  applySession: (s: MeResponse) => Promise<void>;
  refresh: () => Promise<void>;
}

const AuthContext = createContext<AuthState | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const qc = useQueryClient();
  const [status, setStatus] = useState<Status>('loading');
  const [session, setSession] = useState<MeResponse | null>(null);
  const [guestChosen, setGuestChosen] = useState(false);
  const [onboardingSeen, setOnboardingSeen] = useState(false);
  const [freeEntitlements, setFreeEntitlements] = useState<Entitlements>(FREE_FALLBACK);

  const clearSession = useCallback(async () => {
    setAuthToken(null);
    setSession(null);
    setStatus('guest');
    await Promise.all([setItem(storageKeys.token, null), setItem(storageKeys.session, null)]);
    qc.clear();
  }, [qc]);

  const applySession = useCallback(
    async (s: MeResponse) => {
      setSession(s);
      setStatus('authed');
      await setJson(storageKeys.session, s);
      // Plan may have changed: everything plan-dependent (delay, insights) must refetch.
      await qc.invalidateQueries();
    },
    [qc],
  );

  const startSession = useCallback(
    async (token: string, s: MeResponse) => {
      setAuthToken(token);
      await setItem(storageKeys.token, token);
      qc.clear();
      await applySession(s);
    },
    [applySession, qc],
  );

  // Boot: restore token + cached session, then refresh /me in the background.
  useEffect(() => {
    let cancelled = false;
    (async () => {
      const [token, cached, seen, guest] = await Promise.all([
        getItem(storageKeys.token),
        getJson<MeResponse>(storageKeys.session),
        getItem(storageKeys.onboardingSeen),
        getItem(storageKeys.guest),
      ]);
      if (cancelled) return;
      setOnboardingSeen(seen === '1');
      setGuestChosen(guest === '1');
      if (!token) {
        setStatus('guest');
        return;
      }
      setAuthToken(token);
      if (cached) {
        setSession(cached);
        setStatus('authed');
      }
      try {
        const me = await api.me();
        if (!cancelled) await applySession(me);
      } catch (e) {
        if (cancelled) return;
        if (isApiError(e) && e.status === 401) await clearSession();
        else if (!cached) setStatus('authed'); // offline with a token: keep it, entitlements fall back to free
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [applySession, clearSession]);

  // Guests use the server's free-plan entitlements.
  useEffect(() => {
    api
      .plans()
      .then((r) => {
        qc.setQueryData(keys.plans, r);
        const free = r.plans.find((p) => p.id === 'free');
        if (free) setFreeEntitlements(free.entitlements);
      })
      .catch(() => undefined);
  }, [qc]);

  // Any 401 on an authenticated request logs the user out.
  useEffect(() => on('unauthorized', () => void clearSession()), [clearSession]);

  const value = useMemo<AuthState>(
    () => ({
      status,
      isAuthed: status === 'authed',
      user: session?.user ?? null,
      entitlements: session?.entitlements ?? freeEntitlements,
      guestChosen,
      onboardingSeen,
      signIn: async (body) => {
        const r = await api.login(body);
        await startSession(r.token, { user: r.user, entitlements: r.entitlements });
      },
      signUp: async (body) => {
        const r = await api.register(body);
        await startSession(r.token, { user: r.user, entitlements: r.entitlements });
      },
      signOut: async () => {
        // Stop alerts to this phone for the account that's leaving (needs the token still set).
        await disablePushAlerts().catch(() => {});
        setGuestChosen(true);
        await setItem(storageKeys.guest, '1');
        await clearSession();
      },
      deleteAccount: async () => {
        await api.deleteMe();
        await clearSession();
      },
      continueAsGuest: async () => {
        setGuestChosen(true);
        await setItem(storageKeys.guest, '1');
      },
      markOnboardingSeen: async () => {
        setOnboardingSeen(true);
        await setItem(storageKeys.onboardingSeen, '1');
      },
      applySession,
      refresh: async () => {
        if (status !== 'authed') return;
        await applySession(await api.me());
      },
    }),
    [status, session, freeEntitlements, guestChosen, onboardingSeen, startSession, clearSession, applySession],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthState {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error('useAuth must be used inside <AuthProvider>');
  return ctx;
}

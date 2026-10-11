import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import { Platform } from 'react-native';
import * as Application from 'expo-application';
import { errorMessage } from '@/api/client';
import { api } from '@/api/endpoints';
import { usePlans } from '@/api/hooks';
import { useAuth } from '@/auth/AuthContext';
import { config, playManageSubscriptionUrl } from '@/config';
import { getBillingAvailability, loadIap, type BillingAvailability, type StorePurchase } from './native';
import { offersFromProduct, productIdForPlan, productIdsFromPlans, type StoreOffer } from './offers';

export type BillingNotice = { kind: 'success' | 'error' | 'info'; message: string } | null;

interface BillingState {
  availability: BillingAvailability;
  /** Connected to Google Play and products loaded (or attempted). */
  ready: boolean;
  offers: StoreOffer[];
  loadingOffers: boolean;
  /** productId currently being purchased/verified */
  busyProductId: string | null;
  restoring: boolean;
  notice: BillingNotice;
  clearNotice: () => void;
  purchase: (offer: StoreOffer) => Promise<void>;
  restore: () => Promise<void>;
  manageSubscriptionUrl: string;
}

const BillingContext = createContext<BillingState | null>(null);

const UNAVAILABLE_MESSAGE =
  'Billing is only available in a development or production build (not in Expo Go). Run `npx expo run:android` or an EAS development build.';

export function billingUnavailableMessage(a: BillingAvailability): string | null {
  if (a === 'available') return null;
  if (a === 'unsupported-platform') return 'Subscriptions are only available in the Android app.';
  return UNAVAILABLE_MESSAGE;
}

function isUserCancelled(e: unknown): boolean {
  return (e as { code?: string })?.code === 'user-cancelled';
}

export function BillingProvider({ children }: { children: ReactNode }) {
  const { isAuthed, user, applySession } = useAuth();
  const plans = usePlans();
  const availability = useMemo(getBillingAvailability, []);
  const [ready, setReady] = useState(false);
  const [offers, setOffers] = useState<StoreOffer[]>([]);
  const [loadingOffers, setLoadingOffers] = useState(false);
  const [busyProductId, setBusyProductId] = useState<string | null>(null);
  const [restoring, setRestoring] = useState(false);
  const [notice, setNotice] = useState<BillingNotice>(null);
  const inFlight = useRef(new Set<string>());
  const authedRef = useRef(isAuthed);
  authedRef.current = isAuthed;

  const productIds = useMemo(() => productIdsFromPlans(plans.data?.plans), [plans.data]);

  /**
   * Server-first: send the token to the backend, and only acknowledge
   * (finishTransaction) after the server verified it. An unacknowledged
   * purchase is retried on next launch; Play refunds it after 3 days otherwise.
   */
  const verifyAndFinish = useCallback(
    async (p: StorePurchase): Promise<boolean> => {
      const iap = loadIap();
      const token = p.purchaseToken;
      if (!iap || !token || !authedRef.current) return false;
      if (p.purchaseState === 'pending') {
        setNotice({ kind: 'info', message: 'Your payment is pending. Your plan activates once Google Play confirms it.' });
        return false;
      }
      if (inFlight.current.has(token)) return false;
      inFlight.current.add(token);
      try {
        const session = await api.verifyGooglePurchase(p.productId, token);
        await applySession(session);
        await iap.finishTransaction({ purchase: p, isConsumable: false });
        return true;
      } finally {
        inFlight.current.delete(token);
      }
    },
    [applySession],
  );

  // Connect to Play, listen for purchase updates.
  useEffect(() => {
    const iap = loadIap();
    if (!iap) return;
    let mounted = true;
    const updated = iap.purchaseUpdatedListener((p) => {
      verifyAndFinish(p)
        .then((ok) => ok && setNotice({ kind: 'success', message: 'Subscription active. Thanks for supporting HillSignal!' }))
        .catch((e) => setNotice({ kind: 'error', message: `Purchase received but not verified yet: ${errorMessage(e)}` }))
        .finally(() => setBusyProductId(null));
    });
    const failed = iap.purchaseErrorListener((e) => {
      setBusyProductId(null);
      if (!isUserCancelled(e)) setNotice({ kind: 'error', message: e.message || 'Purchase failed.' });
    });
    iap
      .initConnection()
      .then(() => mounted && setReady(true))
      .catch((e) => mounted && setNotice({ kind: 'error', message: `Google Play Billing unavailable: ${errorMessage(e)}` }));
    return () => {
      mounted = false;
      updated.remove();
      failed.remove();
      void iap.endConnection().catch(() => undefined);
    };
  }, [verifyAndFinish]);

  // Fetch localized products once connected.
  useEffect(() => {
    const iap = loadIap();
    if (!iap || !ready || productIds.length === 0) return;
    let mounted = true;
    setLoadingOffers(true);
    iap
      .fetchProducts({ skus: productIds, type: 'subs' })
      .then((products) => {
        if (!mounted) return;
        const subs = (products ?? []).filter((p) => p.type === 'subs');
        setOffers(subs.flatMap((p) => ('subscriptionOffers' in p ? offersFromProduct(p) : [])));
      })
      .catch((e) => mounted && setNotice({ kind: 'error', message: `Couldn't load prices: ${errorMessage(e)}` }))
      .finally(() => mounted && setLoadingOffers(false));
    return () => {
      mounted = false;
    };
  }, [ready, productIds]);

  // Retry verification of purchases that were paid but never acknowledged (e.g. app killed mid-flow).
  useEffect(() => {
    const iap = loadIap();
    if (!iap || !ready || !isAuthed) return;
    iap
      .getAvailablePurchases()
      .then((purchases) =>
        Promise.all(
          purchases
            .filter((p) => productIds.includes(p.productId))
            .filter((p) => 'isAcknowledgedAndroid' in p && p.isAcknowledgedAndroid === false)
            .map((p) => verifyAndFinish(p).catch(() => false)),
        ),
      )
      .catch(() => undefined);
  }, [ready, isAuthed, productIds, verifyAndFinish]);

  const purchase = useCallback(
    async (offer: StoreOffer) => {
      const iap = loadIap();
      if (!iap) {
        setNotice({ kind: 'error', message: billingUnavailableMessage(availability) ?? 'Billing unavailable.' });
        return;
      }
      setNotice(null);
      setBusyProductId(offer.productId);
      try {
        await iap.requestPurchase({
          type: 'subs',
          request: {
            google: {
              skus: [offer.productId],
              subscriptionOffers: [{ sku: offer.productId, offerToken: offer.offerToken }],
              // Lets the backend / RTDN map the Play purchase to this account.
              obfuscatedAccountId: user?.id ?? null,
            },
          },
        });
        // The result is delivered to purchaseUpdatedListener.
      } catch (e) {
        setBusyProductId(null);
        if (!isUserCancelled(e)) setNotice({ kind: 'error', message: errorMessage(e) });
      }
    },
    [availability, user?.id],
  );

  const restore = useCallback(async () => {
    const iap = loadIap();
    if (!iap) {
      setNotice({ kind: 'error', message: billingUnavailableMessage(availability) ?? 'Billing unavailable.' });
      return;
    }
    if (!authedRef.current) {
      setNotice({ kind: 'info', message: 'Sign in first, then restore to attach your subscription to your account.' });
      return;
    }
    setRestoring(true);
    setNotice(null);
    try {
      const purchases = (await iap.getAvailablePurchases()).filter((p) => productIds.includes(p.productId) && p.purchaseToken);
      if (purchases.length === 0) {
        setNotice({ kind: 'info', message: 'No active subscriptions found for this Google account.' });
        return;
      }
      for (const p of purchases) {
        const session = await api.verifyGooglePurchase(p.productId, p.purchaseToken as string);
        await applySession(session);
        if ('isAcknowledgedAndroid' in p && p.isAcknowledgedAndroid === false) {
          await iap.finishTransaction({ purchase: p, isConsumable: false });
        }
      }
      setNotice({ kind: 'success', message: 'Purchases restored.' });
    } catch (e) {
      setNotice({ kind: 'error', message: `Restore failed: ${errorMessage(e)}` });
    } finally {
      setRestoring(false);
    }
  }, [availability, applySession, productIds]);

  const packageName = Platform.OS === 'android' ? (Application.applicationId ?? config.ANDROID_PACKAGE) : config.ANDROID_PACKAGE;
  const currentSku = user && user.plan !== 'free' ? productIdForPlan(user.plan, plans.data?.plans) : null;

  const value = useMemo<BillingState>(
    () => ({
      availability,
      ready,
      offers,
      loadingOffers,
      busyProductId,
      restoring,
      notice,
      clearNotice: () => setNotice(null),
      purchase,
      restore,
      manageSubscriptionUrl: playManageSubscriptionUrl(currentSku, packageName),
    }),
    [availability, ready, offers, loadingOffers, busyProductId, restoring, notice, purchase, restore, currentSku, packageName],
  );

  return <BillingContext.Provider value={value}>{children}</BillingContext.Provider>;
}

export function useBilling(): BillingState {
  const ctx = useContext(BillingContext);
  if (!ctx) throw new Error('useBilling must be used inside <BillingProvider>');
  return ctx;
}

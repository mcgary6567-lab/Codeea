import { useCallback, useEffect, useState } from 'react';
import { errorMessage } from '@/api/client';
import { getItem, setItem, storageKeys } from '@/lib/storage';
import { disablePushAlerts, enablePushAlerts, setForegroundAlerts } from './push';

/**
 * On/off switch for trade alerts. Turning on asks permission and registers the
 * device; turning off unregisters it so the server stops pushing.
 */
export function useAlerts() {
  const [enabled, setEnabled] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    void getItem(storageKeys.alerts).then((v) => {
      setEnabled(v === '1');
      setForegroundAlerts(v === '1');
    });
  }, []);

  const setAlerts = useCallback(async (on: boolean) => {
    setError(null);
    if (!on) {
      setEnabled(false);
      setForegroundAlerts(false);
      await setItem(storageKeys.alerts, '0');
      try {
        await disablePushAlerts();
      } catch (e) {
        setError(errorMessage(e));
      }
      return;
    }
    setBusy(true);
    try {
      await enablePushAlerts();
      setEnabled(true);
      setForegroundAlerts(true);
      await setItem(storageKeys.alerts, '1');
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setBusy(false);
    }
  }, []);

  return { enabled, busy, error, setAlerts };
}

import * as Notifications from 'expo-notifications';
import { router } from 'expo-router';
import { useEffect, useRef } from 'react';
import { tradeIdFromNotification } from './push';

/** Opens the trade screen when a `{type:"trade", tradeId}` push is tapped (cold start included). */
export function useNotificationRouting(enabled: boolean) {
  const last = Notifications.useLastNotificationResponse();
  const handled = useRef<string | null>(null);

  useEffect(() => {
    if (!enabled || !last) return;
    const id = last.notification.request.identifier;
    if (handled.current === id) return;
    const tradeId = tradeIdFromNotification(last.notification.request.content.data);
    if (!tradeId) return;
    handled.current = id;
    router.push({ pathname: '/trade/[id]', params: { id: tradeId } });
  }, [enabled, last]);
}

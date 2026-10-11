import Constants, { ExecutionEnvironment } from 'expo-constants';
import * as Device from 'expo-device';
import * as Notifications from 'expo-notifications';
import { Platform } from 'react-native';
import { api } from '@/api/endpoints';

export const TRADE_CHANNEL_ID = 'trades';

let foregroundAlertsOn = true;
/** When alerts are switched off locally, suppress alerts that arrive while the app is open. */
export function setForegroundAlerts(on: boolean) {
  foregroundAlertsOn = on;
}

Notifications.setNotificationHandler({
  handleNotification: async () => ({
    shouldShowBanner: foregroundAlertsOn,
    shouldShowList: foregroundAlertsOn,
    shouldPlaySound: false,
    shouldSetBadge: false,
  }),
});

export class PushSetupError extends Error {}

async function ensureAndroidChannel() {
  if (Platform.OS !== 'android') return;
  await Notifications.setNotificationChannelAsync(TRADE_CHANNEL_ID, {
    name: 'Trade alerts',
    description: 'New trades from politicians and companies you follow',
    importance: Notifications.AndroidImportance.DEFAULT,
  });
}

const easProjectId = () =>
  (Constants.expoConfig?.extra as { eas?: { projectId?: string } } | undefined)?.eas?.projectId ??
  Constants.easConfig?.projectId;

/** Tells the server to stop pushing to this device. No-op if it was never registered. */
export async function disablePushAlerts(): Promise<void> {
  const projectId = easProjectId();
  if (!Device.isDevice || !projectId || !(await Notifications.getPermissionsAsync()).granted) return;
  const { data: token } = await Notifications.getExpoPushTokenAsync({ projectId });
  await api.unregisterDevice(token);
}

/**
 * Asks for notification permission (called when the user turns alerts on),
 * gets the Expo push token and registers it with POST /devices.
 */
export async function enablePushAlerts(): Promise<string> {
  if (!Device.isDevice) throw new PushSetupError('Push notifications need a physical device.');
  if (Constants.executionEnvironment === ExecutionEnvironment.StoreClient && Platform.OS === 'android') {
    throw new PushSetupError('Push notifications are not supported in Expo Go on Android. Use a development build.');
  }
  await ensureAndroidChannel();

  const current = await Notifications.getPermissionsAsync();
  let granted = current.granted;
  if (!granted && current.canAskAgain) granted = (await Notifications.requestPermissionsAsync()).granted;
  if (!granted) throw new PushSetupError('Notifications are blocked. Enable them in system settings to get trade alerts.');

  const projectId = easProjectId();
  if (!projectId) throw new PushSetupError('Missing EAS project id (set EAS_PROJECT_ID and rebuild).');

  const { data: token } = await Notifications.getExpoPushTokenAsync({ projectId });
  await api.registerDevice(token, Platform.OS === 'ios' ? 'ios' : 'android');
  return token;
}

/** Extracts a trade id from a push payload `{ type: "trade", tradeId }`. */
export function tradeIdFromNotification(data: unknown): string | null {
  const d = data as { type?: unknown; tradeId?: unknown } | null | undefined;
  if (d?.type === 'trade' && (typeof d.tradeId === 'string' || typeof d.tradeId === 'number')) return String(d.tradeId);
  return null;
}

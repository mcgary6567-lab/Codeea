/**
 * The ONLY file that touches the native IAP library (expo-iap, the Expo build of
 * react-native-iap / OpenIAP). Everything else goes through src/billing/index.ts.
 */
import Constants, { ExecutionEnvironment } from 'expo-constants';
import { requireOptionalNativeModule } from 'expo';
import { Platform } from 'react-native';

export type IapModule = typeof import('expo-iap');
export type {
  Purchase as StorePurchase,
  PurchaseError as StorePurchaseError,
  ProductSubscription as StoreSubscription,
  ProductSubscriptionAndroid as StoreSubscriptionAndroid,
} from 'expo-iap';

export type BillingAvailability = 'available' | 'expo-go' | 'no-native-module' | 'unsupported-platform';

export function getBillingAvailability(): BillingAvailability {
  if (Platform.OS !== 'android' && Platform.OS !== 'ios') return 'unsupported-platform';
  if (Constants.executionEnvironment === ExecutionEnvironment.StoreClient) return 'expo-go';
  if (!requireOptionalNativeModule('ExpoIap')) return 'no-native-module';
  return 'available';
}

let cached: IapModule | null = null;

/** Lazily loads expo-iap; returns null where the native module is missing (Expo Go, web). */
export function loadIap(): IapModule | null {
  if (cached) return cached;
  if (getBillingAvailability() !== 'available') return null;
  try {
    // eslint-disable-next-line @typescript-eslint/no-require-imports
    cached = require('expo-iap') as IapModule;
    return cached;
  } catch {
    return null;
  }
}

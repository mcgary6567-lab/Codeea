import * as SecureStore from 'expo-secure-store';

/** Small key/value storage. Uses SecureStore (encrypted) for everything; values are tiny. */
export const storageKeys = {
  token: 'hs.token',
  session: 'hs.session',
  onboardingSeen: 'hs.onboardingSeen',
  guest: 'hs.guest',
  alerts: 'hs.alertsEnabled',
} as const;

export async function getItem(key: string): Promise<string | null> {
  try {
    return await SecureStore.getItemAsync(key);
  } catch {
    return null;
  }
}

export async function setItem(key: string, value: string | null): Promise<void> {
  try {
    if (value === null) await SecureStore.deleteItemAsync(key);
    else await SecureStore.setItemAsync(key, value);
  } catch {
    // Storage failures are non-fatal: the user just has to sign in again.
  }
}

export async function getJson<T>(key: string): Promise<T | null> {
  const raw = await getItem(key);
  if (!raw) return null;
  try {
    return JSON.parse(raw) as T;
  } catch {
    return null;
  }
}

export const setJson = (key: string, value: unknown) => setItem(key, value === null ? null : JSON.stringify(value));

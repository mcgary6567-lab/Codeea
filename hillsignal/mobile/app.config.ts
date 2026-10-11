import type { ConfigContext, ExpoConfig } from 'expo/config';
import BRAND from './src/brand.json';

const variant = process.env.APP_VARIANT ?? 'development';
const suffix = variant === 'production' ? '' : variant === 'preview' ? ' (Preview)' : ' (Dev)';
const projectId = process.env.EAS_PROJECT_ID;

export default ({ config }: ConfigContext): ExpoConfig => ({
  ...config,
  name: `${BRAND.appName}${suffix}`,
  slug: 'hillsignal',
  version: '1.0.0',
  platforms: ['android', 'ios'],
  orientation: 'portrait',
  scheme: BRAND.scheme,
  userInterfaceStyle: 'automatic',
  icon: './assets/icon.png',
  backgroundColor: BRAND.colors.ink,
  android: {
    // Placeholder package id: change before the first Play upload (it is permanent after that).
    package: BRAND.androidPackage,
    versionCode: 1,
    adaptiveIcon: {
      foregroundImage: './assets/adaptive-icon.png',
      monochromeImage: './assets/adaptive-icon-mono.png',
      backgroundColor: BRAND.colors.ink,
    },
    // expo-iap pulls in Play Billing, whose manifest already declares this;
    // listing it explicitly keeps it visible in review.
    permissions: ['com.android.vending.BILLING', 'android.permission.POST_NOTIFICATIONS'],
    blockedPermissions: ['android.permission.RECORD_AUDIO'],
  },
  ios: {
    bundleIdentifier: BRAND.androidPackage,
    supportsTablet: false,
  },
  plugins: [
    'expo-router',
    'expo-secure-store',
    [
      'expo-splash-screen',
      {
        image: './assets/splash-icon.png',
        imageWidth: 160,
        backgroundColor: BRAND.colors.ink,
        dark: { image: './assets/splash-icon.png', backgroundColor: BRAND.colors.ink },
      },
    ],
    [
      'expo-notifications',
      {
        icon: './assets/notification-icon.png',
        color: BRAND.colors.accent,
        defaultChannel: 'trades',
      },
    ],
    'expo-iap',
  ],
  extra: {
    appVariant: variant,
    ...(projectId ? { eas: { projectId } } : {}),
  },
});

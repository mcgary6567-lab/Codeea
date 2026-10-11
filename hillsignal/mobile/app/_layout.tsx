import { QueryClientProvider } from '@tanstack/react-query';
import { Stack, router } from 'expo-router';
import * as SplashScreen from 'expo-splash-screen';
import { StatusBar } from 'expo-status-bar';
import * as SystemUI from 'expo-system-ui';
import { useEffect } from 'react';
import { SafeAreaProvider } from 'react-native-safe-area-context';
import { queryClient } from '@/api/queryClient';
import { AuthProvider, useAuth } from '@/auth/AuthContext';
import { BillingProvider } from '@/billing';
import { on } from '@/lib/events';
import { useNotificationRouting } from '@/notifications/useNotificationRouting';
import { useTheme } from '@/theme';

void SplashScreen.preventAutoHideAsync().catch(() => undefined);

function RootNavigator() {
  const t = useTheme();
  const { status } = useAuth();

  useEffect(() => {
    void SystemUI.setBackgroundColorAsync(t.bg);
  }, [t.bg]);

  useEffect(() => {
    if (status !== 'loading') void SplashScreen.hideAsync().catch(() => undefined);
  }, [status]);

  // 402 from any user action → paywall.
  useEffect(
    () =>
      on('upgradeRequired', ({ requiredPlan }) =>
        router.push({ pathname: '/paywall', params: requiredPlan ? { plan: requiredPlan } : {} }),
      ),
    [],
  );

  useNotificationRouting(status !== 'loading');

  return (
    <>
      <StatusBar style={t.scheme === 'dark' ? 'light' : 'dark'} />
      <Stack
        screenOptions={{
          headerStyle: { backgroundColor: t.bg },
          headerTintColor: t.text,
          headerTitleStyle: { fontWeight: '700' },
          headerShadowVisible: false,
          contentStyle: { backgroundColor: t.bg },
        }}
      >
        <Stack.Screen name="index" options={{ headerShown: false }} />
        <Stack.Screen name="onboarding" options={{ headerShown: false, animation: 'fade' }} />
        <Stack.Screen name="auth/sign-in" options={{ title: 'Sign in' }} />
        <Stack.Screen name="auth/sign-up" options={{ title: 'Create account' }} />
        <Stack.Screen name="(tabs)" options={{ headerShown: false }} />
        <Stack.Screen name="trade/[id]" options={{ title: 'Trade' }} />
        <Stack.Screen name="politician/[id]" options={{ title: 'Politician' }} />
        <Stack.Screen name="issuer/[id]" options={{ title: 'Company' }} />
        <Stack.Screen name="paywall" options={{ presentation: 'modal', title: 'Upgrade', animation: 'slide_from_bottom' }} />
      </Stack>
    </>
  );
}

export default function RootLayout() {
  return (
    <SafeAreaProvider>
      <QueryClientProvider client={queryClient}>
        <AuthProvider>
          <BillingProvider>
            <RootNavigator />
          </BillingProvider>
        </AuthProvider>
      </QueryClientProvider>
    </SafeAreaProvider>
  );
}

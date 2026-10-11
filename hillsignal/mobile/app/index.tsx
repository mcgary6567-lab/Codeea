import { Redirect } from 'expo-router';
import { View } from 'react-native';
import { useAuth } from '@/auth/AuthContext';
import { useTheme } from '@/theme';

/** Entry gate: onboarding (once) → sign in, or straight to the tabs. */
export default function Index() {
  const { status, onboardingSeen, guestChosen } = useAuth();
  const t = useTheme();
  if (status === 'loading') return <View style={{ flex: 1, backgroundColor: t.bg }} />;
  if (!onboardingSeen) return <Redirect href="/onboarding" />;
  if (status === 'authed' || guestChosen) return <Redirect href="/(tabs)" />;
  return <Redirect href="/auth/sign-in" />;
}

import { router } from 'expo-router';
import { useState } from 'react';
import { KeyboardAvoidingView, Platform, ScrollView, View } from 'react-native';
import { errorMessage, isApiError } from '@/api/client';
import { useAuth } from '@/auth/AuthContext';
import { config } from '@/config';
import { spacing, useTheme } from '@/theme';
import { Button } from './Button';
import { Field } from './Field';
import { Notice } from './Notice';
import { Txt } from './Txt';

const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

/** Shared sign-in / create-account form. */
export function AuthForm({ mode }: { mode: 'sign-in' | 'sign-up' }) {
  const t = useTheme();
  const { signIn, signUp, continueAsGuest } = useAuth();
  const [name, setName] = useState('');
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const isSignUp = mode === 'sign-up';

  // Opened from inside the app (e.g. "Sign in to follow") → go back; from onboarding → enter the tabs.
  const done = () => {
    if (router.canGoBack()) router.back();
    else router.replace('/(tabs)');
  };

  const submit = async () => {
    setError(null);
    if (!EMAIL_RE.test(email.trim())) return setError('Enter a valid email address.');
    if (isSignUp && password.length < 8) return setError('Password must be at least 8 characters.');
    if (!password) return setError('Enter your password.');
    setBusy(true);
    try {
      if (isSignUp) await signUp({ email: email.trim(), password, ...(name.trim() ? { name: name.trim() } : {}) });
      else await signIn({ email: email.trim(), password });
      done();
    } catch (e) {
      if (isApiError(e) && e.status === 401) setError('Wrong email or password.');
      else if (isApiError(e) && e.code === 'conflict') setError('An account with this email already exists. Sign in instead.');
      else setError(errorMessage(e));
    } finally {
      setBusy(false);
    }
  };

  const browse = async () => {
    await continueAsGuest();
    done();
  };

  return (
    <KeyboardAvoidingView style={{ flex: 1, backgroundColor: t.bg }} behavior={Platform.OS === 'ios' ? 'padding' : undefined}>
      <ScrollView contentContainerStyle={{ padding: spacing.xl, gap: spacing.lg }} keyboardShouldPersistTaps="handled">
        <View style={{ gap: spacing.xs, marginBottom: spacing.sm }}>
          <Txt mono weight="800" tone="accent">
            {config.APP_NAME.toUpperCase()}
          </Txt>
          <Txt variant="display">{isSignUp ? 'Create your account' : 'Welcome back'}</Txt>
          <Txt tone="muted">{isSignUp ? 'Free forever. Upgrade any time for real-time data and AI.' : config.TAGLINE}</Txt>
        </View>
        {error ? <Notice kind="error" message={error} /> : null}
        {isSignUp ? <Field label="Name (optional)" value={name} onChangeText={setName} autoComplete="name" textContentType="name" /> : null}
        <Field
          label="Email"
          value={email}
          onChangeText={setEmail}
          autoCapitalize="none"
          autoComplete="email"
          keyboardType="email-address"
          textContentType="emailAddress"
        />
        <Field
          label="Password"
          value={password}
          onChangeText={setPassword}
          secureTextEntry
          autoComplete={isSignUp ? 'new-password' : 'current-password'}
          textContentType={isSignUp ? 'newPassword' : 'password'}
          onSubmitEditing={() => void submit()}
          placeholder={isSignUp ? 'At least 8 characters' : undefined}
        />
        <Button title={isSignUp ? 'Create account' : 'Sign in'} onPress={() => void submit()} loading={busy} />
        <Button
          title={isSignUp ? 'I already have an account' : 'Create an account'}
          variant="ghost"
          onPress={() => (isSignUp ? router.replace('/auth/sign-in') : router.replace('/auth/sign-up'))}
        />
        <View style={{ height: 1, backgroundColor: t.border, marginVertical: spacing.sm }} />
        <Button title="Browse without account" variant="secondary" icon="eye-outline" onPress={() => void browse()} />
        <Txt variant="small" tone="faint" align="center">
          Guests get the free plan: delayed trades and no watchlist.
        </Txt>
      </ScrollView>
    </KeyboardAvoidingView>
  );
}

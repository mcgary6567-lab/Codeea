import { Ionicons } from '@expo/vector-icons';
import { ActivityIndicator, View } from 'react-native';
import { errorMessage } from '@/api/client';
import { spacing, useTheme } from '@/theme';
import { Button } from './Button';
import { Txt } from './Txt';

interface EmptyProps {
  icon?: keyof typeof Ionicons.glyphMap;
  title: string;
  message?: string;
  actionLabel?: string;
  onAction?: () => void;
}

export function EmptyState({ icon = 'pulse-outline', title, message, actionLabel, onAction }: EmptyProps) {
  const t = useTheme();
  return (
    <View style={{ alignItems: 'center', padding: spacing.xxl, gap: spacing.sm }}>
      <View
        style={{
          width: 56,
          height: 56,
          borderRadius: 28,
          backgroundColor: t.accentSoft,
          alignItems: 'center',
          justifyContent: 'center',
          marginBottom: spacing.sm,
        }}
      >
        <Ionicons name={icon} size={26} color={t.accent} />
      </View>
      <Txt variant="heading" align="center">
        {title}
      </Txt>
      {message ? (
        <Txt tone="muted" align="center">
          {message}
        </Txt>
      ) : null}
      {actionLabel && onAction ? (
        <Button title={actionLabel} onPress={onAction} small style={{ marginTop: spacing.md }} />
      ) : null}
    </View>
  );
}

export function ErrorState({ error, onRetry }: { error: unknown; onRetry?: () => void }) {
  return (
    <EmptyState
      icon="cloud-offline-outline"
      title="Couldn't load this"
      message={errorMessage(error)}
      actionLabel={onRetry ? 'Try again' : undefined}
      onAction={onRetry}
    />
  );
}

export function FooterSpinner({ visible }: { visible: boolean }) {
  const t = useTheme();
  if (!visible) return <View style={{ height: spacing.xl }} />;
  return (
    <View style={{ padding: spacing.lg }}>
      <ActivityIndicator color={t.accent} />
    </View>
  );
}

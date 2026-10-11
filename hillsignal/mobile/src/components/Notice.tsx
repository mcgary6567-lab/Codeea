import { Ionicons } from '@expo/vector-icons';
import { Pressable, View } from 'react-native';
import { radius, spacing, useTheme } from '@/theme';
import { Txt } from './Txt';

type Kind = 'info' | 'success' | 'error' | 'warning';

interface Props {
  kind?: Kind;
  message: string;
  actionLabel?: string;
  onAction?: () => void;
  onDismiss?: () => void;
}

/** Inline banner for status messages (delay notice, billing results, errors). */
export function Notice({ kind = 'info', message, actionLabel, onAction, onDismiss }: Props) {
  const t = useTheme();
  const color = { info: t.accent, success: t.accent, error: t.danger, warning: t.warning }[kind];
  const icon = { info: 'information-circle', success: 'checkmark-circle', error: 'alert-circle', warning: 'time' }[kind] as
    | 'information-circle'
    | 'checkmark-circle'
    | 'alert-circle'
    | 'time';
  return (
    <View
      accessibilityRole="alert"
      style={{
        flexDirection: 'row',
        alignItems: 'center',
        gap: spacing.sm,
        padding: spacing.md,
        borderRadius: radius.md,
        borderWidth: 1,
        borderColor: `${color}55`,
        backgroundColor: `${color}18`,
      }}
    >
      <Ionicons name={icon} size={18} color={color} />
      <Txt variant="small" style={{ flex: 1 }}>
        {message}
      </Txt>
      {actionLabel && onAction ? (
        <Pressable onPress={onAction} hitSlop={8} accessibilityRole="button">
          <Txt variant="small" weight="700" style={{ color }}>
            {actionLabel}
          </Txt>
        </Pressable>
      ) : null}
      {onDismiss ? (
        <Pressable onPress={onDismiss} hitSlop={8} accessibilityLabel="Dismiss">
          <Ionicons name="close" size={16} color={t.textMuted} />
        </Pressable>
      ) : null}
    </View>
  );
}

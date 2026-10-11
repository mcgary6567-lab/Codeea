import { Ionicons } from '@expo/vector-icons';
import { ActivityIndicator, Pressable, View, type StyleProp, type ViewStyle } from 'react-native';
import { radius, spacing, useTheme } from '@/theme';
import { Txt } from './Txt';

type Variant = 'primary' | 'secondary' | 'ghost' | 'danger';

interface Props {
  title: string;
  onPress?: () => void;
  variant?: Variant;
  icon?: keyof typeof Ionicons.glyphMap;
  loading?: boolean;
  disabled?: boolean;
  small?: boolean;
  style?: StyleProp<ViewStyle>;
}

export function Button({ title, onPress, variant = 'primary', icon, loading, disabled, small, style }: Props) {
  const t = useTheme();
  const bg = { primary: t.accent, secondary: t.surfaceAlt, ghost: 'transparent', danger: 'transparent' }[variant];
  const fg = { primary: t.onAccent, secondary: t.text, ghost: t.accent, danger: t.danger }[variant];
  const border = variant === 'danger' ? t.danger : variant === 'secondary' ? t.border : 'transparent';
  const inactive = disabled || loading;
  return (
    <Pressable
      onPress={onPress}
      disabled={inactive}
      accessibilityRole="button"
      accessibilityState={{ disabled: !!inactive, busy: !!loading }}
      style={({ pressed }) => [
        {
          backgroundColor: bg,
          borderColor: border,
          borderWidth: 1,
          borderRadius: radius.md,
          paddingVertical: small ? spacing.sm : spacing.md + 2,
          paddingHorizontal: small ? spacing.md : spacing.lg,
          opacity: inactive ? 0.55 : pressed ? 0.85 : 1,
        },
        style,
      ]}
    >
      <View style={{ flexDirection: 'row', alignItems: 'center', justifyContent: 'center', gap: spacing.sm }}>
        {loading ? (
          <ActivityIndicator size="small" color={fg} />
        ) : icon ? (
          <Ionicons name={icon} size={small ? 15 : 18} color={fg} />
        ) : null}
        <Txt variant={small ? 'small' : 'body'} weight="700" style={{ color: fg }}>
          {title}
        </Txt>
      </View>
    </Pressable>
  );
}

import { Ionicons } from '@expo/vector-icons';
import type { ReactNode } from 'react';
import { Pressable, View } from 'react-native';
import { spacing, useTheme } from '@/theme';
import { Txt } from './Txt';

interface Props {
  icon: keyof typeof Ionicons.glyphMap;
  label: string;
  detail?: string;
  onPress?: () => void;
  right?: ReactNode;
  danger?: boolean;
  last?: boolean;
}

/** Settings-style row. */
export function ListRow({ icon, label, detail, onPress, right, danger, last }: Props) {
  const t = useTheme();
  const color = danger ? t.danger : t.text;
  return (
    <Pressable
      onPress={onPress}
      disabled={!onPress}
      accessibilityRole={onPress ? 'button' : undefined}
      android_ripple={onPress ? { color: t.accentSoft } : undefined}
      style={{
        flexDirection: 'row',
        alignItems: 'center',
        gap: spacing.md,
        paddingVertical: spacing.md + 2,
        paddingHorizontal: spacing.lg,
        borderBottomWidth: last ? 0 : 1,
        borderColor: t.border,
      }}
    >
      <Ionicons name={icon} size={19} color={danger ? t.danger : t.textMuted} />
      <View style={{ flex: 1 }}>
        <Txt style={{ color }}>{label}</Txt>
        {detail ? (
          <Txt variant="small" tone="faint">
            {detail}
          </Txt>
        ) : null}
      </View>
      {right ?? (onPress ? <Ionicons name="chevron-forward" size={16} color={t.textFaint} /> : null)}
    </Pressable>
  );
}

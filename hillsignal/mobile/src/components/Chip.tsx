import { Ionicons } from '@expo/vector-icons';
import { Pressable, View } from 'react-native';
import { radius, spacing, useTheme } from '@/theme';
import { Txt } from './Txt';

interface Props {
  label: string;
  selected?: boolean;
  onPress?: () => void;
  /** Dot color, e.g. for party chips. */
  dotColor?: string;
  icon?: keyof typeof Ionicons.glyphMap;
  onRemove?: () => void;
}

/** Pill used for filters and tags. */
export function Chip({ label, selected, onPress, dotColor, icon, onRemove }: Props) {
  const t = useTheme();
  const content = (
    <View
      style={{
        flexDirection: 'row',
        alignItems: 'center',
        gap: 6,
        paddingHorizontal: spacing.md,
        paddingVertical: 6,
        borderRadius: radius.pill,
        borderWidth: 1,
        borderColor: selected ? t.accent : t.border,
        backgroundColor: selected ? t.accentSoft : t.surfaceAlt,
      }}
    >
      {dotColor ? <View style={{ width: 8, height: 8, borderRadius: 4, backgroundColor: dotColor }} /> : null}
      {icon ? <Ionicons name={icon} size={13} color={selected ? t.accent : t.textMuted} /> : null}
      <Txt variant="small" weight="600" style={{ color: selected ? t.accent : t.text }}>
        {label}
      </Txt>
      {onRemove ? (
        <Pressable onPress={onRemove} hitSlop={8} accessibilityLabel={`Remove ${label}`}>
          <Ionicons name="close" size={14} color={t.textMuted} />
        </Pressable>
      ) : null}
    </View>
  );
  if (!onPress) return content;
  return (
    <Pressable onPress={onPress} accessibilityRole="button" accessibilityState={{ selected: !!selected }}>
      {content}
    </Pressable>
  );
}

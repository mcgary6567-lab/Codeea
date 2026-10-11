import { Pressable, View } from 'react-native';
import { radius, useTheme } from '@/theme';
import { Txt } from './Txt';

interface Props<T extends string> {
  options: { value: T; label: string }[];
  value: T;
  onChange: (v: T) => void;
}

export function SegmentedControl<T extends string>({ options, value, onChange }: Props<T>) {
  const t = useTheme();
  return (
    <View
      accessibilityRole="tablist"
      style={{ flexDirection: 'row', backgroundColor: t.surfaceAlt, borderRadius: radius.md, padding: 3, borderWidth: 1, borderColor: t.border }}
    >
      {options.map((o) => {
        const active = o.value === value;
        return (
          <Pressable
            key={o.value}
            onPress={() => onChange(o.value)}
            accessibilityRole="tab"
            accessibilityState={{ selected: active }}
            style={{ flex: 1, paddingVertical: 8, borderRadius: radius.sm, backgroundColor: active ? t.surface : 'transparent', alignItems: 'center' }}
          >
            <Txt variant="small" weight="700" style={{ color: active ? t.text : t.textMuted }}>
              {o.label}
            </Txt>
          </Pressable>
        );
      })}
    </View>
  );
}

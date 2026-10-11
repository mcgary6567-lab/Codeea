import { Text, type TextProps, type TextStyle } from 'react-native';
import { fonts, type as typeScale, useTheme } from '@/theme';

type Variant = keyof typeof typeScale;
type Tone = 'default' | 'muted' | 'faint' | 'accent' | 'danger' | 'buy' | 'sell' | 'onAccent';

export interface TxtProps extends TextProps {
  variant?: Variant;
  tone?: Tone;
  mono?: boolean;
  weight?: TextStyle['fontWeight'];
  align?: TextStyle['textAlign'];
}

/** Themed text. All text in the app goes through this. */
export function Txt({ variant = 'body', tone = 'default', mono, weight, align, style, ...rest }: TxtProps) {
  const t = useTheme();
  const color = {
    default: t.text,
    muted: t.textMuted,
    faint: t.textFaint,
    accent: t.accent,
    danger: t.danger,
    buy: t.buy,
    sell: t.sell,
    onAccent: t.onAccent,
  }[tone];
  return (
    <Text
      {...rest}
      style={[
        typeScale[variant],
        { color },
        mono && { fontFamily: fonts.mono },
        weight !== undefined && { fontWeight: weight },
        align !== undefined && { textAlign: align },
        variant === 'caption' && { textTransform: 'uppercase' },
        style,
      ]}
    />
  );
}

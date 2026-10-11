import { TextInput, View, type TextInputProps } from 'react-native';
import { radius, spacing, useTheme } from '@/theme';
import { Txt } from './Txt';

export function Field({ label, error, ...props }: TextInputProps & { label?: string; error?: string | null }) {
  const t = useTheme();
  return (
    <View style={{ gap: 6 }}>
      {label ? (
        <Txt variant="caption" tone="muted">
          {label}
        </Txt>
      ) : null}
      <TextInput
        placeholderTextColor={t.textFaint}
        selectionColor={t.accent}
        {...props}
        style={[
          {
            backgroundColor: t.surfaceAlt,
            color: t.text,
            borderWidth: 1,
            borderColor: error ? t.danger : t.border,
            borderRadius: radius.md,
            paddingHorizontal: spacing.md,
            paddingVertical: spacing.md,
            fontSize: 15,
          },
          props.style,
        ]}
      />
      {error ? (
        <Txt variant="small" tone="danger">
          {error}
        </Txt>
      ) : null}
    </View>
  );
}

import { Ionicons } from '@expo/vector-icons';
import { View } from 'react-native';
import { config } from '@/config';
import { spacing, useTheme } from '@/theme';
import { Txt } from './Txt';

/** Mandatory under every AI output: not financial advice. */
export function Disclaimer({ text }: { text?: string | null }) {
  const t = useTheme();
  return (
    <View style={{ flexDirection: 'row', gap: spacing.xs + 2, alignItems: 'flex-start', marginTop: spacing.sm }}>
      <Ionicons name="shield-checkmark-outline" size={13} color={t.textFaint} style={{ marginTop: 1 }} />
      <Txt variant="small" tone="faint" style={{ flex: 1, fontSize: 11.5, lineHeight: 16 }}>
        {text?.trim() || config.AI_DISCLAIMER}
      </Txt>
    </View>
  );
}

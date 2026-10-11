import { Pressable, View } from 'react-native';
import { spacing } from '@/theme';
import { Txt } from './Txt';

export function SectionHeader({ title, action, onAction }: { title: string; action?: string; onAction?: () => void }) {
  return (
    <View style={{ flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', marginTop: spacing.lg, marginBottom: spacing.sm }}>
      <Txt variant="caption" tone="muted">
        {title}
      </Txt>
      {action && onAction ? (
        <Pressable onPress={onAction} hitSlop={8} accessibilityRole="button">
          <Txt variant="small" tone="accent" weight="700">
            {action}
          </Txt>
        </Pressable>
      ) : null}
    </View>
  );
}

import { Ionicons } from '@expo/vector-icons';
import { View } from 'react-native';
import type { Plan } from '@/api/types';
import { planLabel } from '@/lib/format';
import { openPaywall } from '@/lib/navigation';
import { spacing, useTheme } from '@/theme';
import { Button } from './Button';
import { Card } from './Card';
import { Txt } from './Txt';

interface Props {
  title: string;
  message: string;
  requiredPlan: Plan;
}

/** Placeholder for a feature the current plan doesn't include. Tapping opens the paywall. */
export function LockedCard({ title, message, requiredPlan }: Props) {
  const t = useTheme();
  return (
    <Card style={{ gap: spacing.sm, borderStyle: 'dashed' }}>
      <View style={{ flexDirection: 'row', alignItems: 'center', gap: spacing.sm }}>
        <Ionicons name="lock-closed" size={16} color={t.accent} />
        <Txt variant="heading">{title}</Txt>
      </View>
      <Txt tone="muted" variant="small">
        {message}
      </Txt>
      <Button
        title={`Unlock with ${planLabel(requiredPlan)}`}
        icon="sparkles"
        small
        onPress={() => openPaywall(requiredPlan)}
        style={{ alignSelf: 'flex-start', marginTop: spacing.xs }}
      />
    </Card>
  );
}

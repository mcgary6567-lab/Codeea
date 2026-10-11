import { Ionicons } from '@expo/vector-icons';
import type { UseQueryResult } from '@tanstack/react-query';
import { View } from 'react-native';
import { errorMessage, isApiError } from '@/api/client';
import type { Insight } from '@/api/types';
import { dualDate } from '@/lib/format';
import { spacing, useTheme } from '@/theme';
import { Card } from './Card';
import { Disclaimer } from './Disclaimer';
import { LockedCard } from './LockedCard';
import { CardSkeleton } from './Skeleton';
import { Button } from './Button';
import { Txt } from './Txt';

interface Props {
  /** User's plan includes AI insights. */
  unlocked: boolean;
  query: UseQueryResult<{ insight: Insight }, Error>;
  lockedMessage: string;
}

function Bullets({ items, icon }: { items: string[]; icon: 'checkmark' | 'alert' }) {
  const t = useTheme();
  return (
    <View style={{ gap: 6 }}>
      {items.map((item, i) => (
        <View key={i} style={{ flexDirection: 'row', gap: spacing.sm }}>
          <Ionicons name={icon === 'checkmark' ? 'chevron-forward' : 'alert-circle-outline'} size={14} color={icon === 'checkmark' ? t.accent : t.warning} style={{ marginTop: 3 }} />
          <Txt variant="small" style={{ flex: 1, lineHeight: 19 }}>
            {item}
          </Txt>
        </View>
      ))}
    </View>
  );
}

export function InsightCard({ unlocked, query, lockedMessage }: Props) {
  const t = useTheme();
  if (!unlocked || (isApiError(query.error) && query.error.status === 402)) {
    const required = isApiError(query.error) ? (query.error.requiredPlan ?? 'pro') : 'pro';
    return <LockedCard title="AI insight" message={lockedMessage} requiredPlan={required} />;
  }
  if (query.isPending) return <CardSkeleton lines={4} />;
  if (query.isError) {
    return (
      <Card style={{ gap: spacing.sm }}>
        <Txt variant="heading">AI insight</Txt>
        <Txt tone="muted" variant="small">
          {errorMessage(query.error)}
        </Txt>
        {isApiError(query.error) && query.error.code === 'quota_exceeded' ? null : (
          <Button title="Retry" small variant="secondary" onPress={() => void query.refetch()} style={{ alignSelf: 'flex-start' }} />
        )}
      </Card>
    );
  }
  const { insight } = query.data;
  return (
    <Card style={{ gap: spacing.md, borderColor: `${t.accent}55` }}>
      <View style={{ flexDirection: 'row', alignItems: 'center', gap: spacing.sm }}>
        <Ionicons name="sparkles" size={16} color={t.accent} />
        <Txt variant="caption" tone="accent">
          AI insight
        </Txt>
      </View>
      <Txt variant="heading">{insight.headline}</Txt>
      <Txt tone="muted" style={{ lineHeight: 21 }}>
        {insight.summary}
      </Txt>
      {insight.keyPoints.length ? <Bullets items={insight.keyPoints} icon="checkmark" /> : null}
      {insight.caveats.length ? (
        <View style={{ gap: 6 }}>
          <Txt variant="caption" tone="muted">
            Caveats
          </Txt>
          <Bullets items={insight.caveats} icon="alert" />
        </View>
      ) : null}
      <Txt variant="small" tone="faint" style={{ fontSize: 11 }}>
        Generated {dualDate(insight.generatedAt)}
      </Txt>
      <Disclaimer text={insight.disclaimer} />
    </Card>
  );
}

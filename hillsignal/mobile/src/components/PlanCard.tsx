import { Ionicons } from '@expo/vector-icons';
import { View } from 'react-native';
import type { PlanInfo } from '@/api/types';
import type { StoreOffer } from '@/billing';
import { radius, spacing, useTheme } from '@/theme';
import { Button } from './Button';
import { Card } from './Card';
import { Skeleton } from './Skeleton';
import { Txt } from './Txt';

interface Props {
  plan: PlanInfo;
  offer: StoreOffer | undefined;
  period: 'monthly' | 'yearly';
  isCurrent: boolean;
  highlighted: boolean;
  loadingPrice: boolean;
  busy: boolean;
  canBuy: boolean;
  onBuy: () => void;
}

export function PlanCard({ plan, offer, period, isCurrent, highlighted, loadingPrice, busy, canBuy, onBuy }: Props) {
  const t = useTheme();
  const isFree = plan.id === 'free';
  return (
    <Card style={{ gap: spacing.md, borderColor: highlighted ? t.accent : t.border, borderWidth: highlighted ? 2 : 1 }}>
      <View style={{ flexDirection: 'row', justifyContent: 'space-between', alignItems: 'flex-start' }}>
        <View style={{ flex: 1, gap: 2 }}>
          <Txt variant="title">{plan.name}</Txt>
          <Txt variant="small" tone="muted">
            {plan.tagline}
          </Txt>
        </View>
        {highlighted && !isCurrent ? (
          <View style={{ backgroundColor: t.accent, borderRadius: radius.pill, paddingHorizontal: 8, paddingVertical: 2 }}>
            <Txt variant="caption" tone="onAccent">
              Recommended
            </Txt>
          </View>
        ) : null}
      </View>
      <View style={{ flexDirection: 'row', alignItems: 'baseline', gap: 6 }}>
        {isFree ? (
          <Txt mono variant="display">
            $0
          </Txt>
        ) : offer ? (
          <>
            <Txt mono variant="display">
              {offer.price}
            </Txt>
            <Txt tone="muted">/ {period === 'monthly' ? 'month' : 'year'}</Txt>
          </>
        ) : loadingPrice ? (
          <Skeleton width={120} height={28} />
        ) : (
          <Txt tone="faint" variant="small">
            Price shown in the Google Play app build
          </Txt>
        )}
      </View>
      {offer?.introLabel ? (
        <Txt variant="small" tone="accent" weight="700">
          {offer.introLabel}
        </Txt>
      ) : null}
      <View style={{ gap: 8 }}>
        {plan.features.map((f) => (
          <View key={f} style={{ flexDirection: 'row', gap: spacing.sm }}>
            <Ionicons name="checkmark" size={16} color={t.accent} style={{ marginTop: 2 }} />
            <Txt variant="small" style={{ flex: 1, lineHeight: 19 }}>
              {f}
            </Txt>
          </View>
        ))}
      </View>
      {isFree ? null : isCurrent ? (
        <Button title="Current plan" variant="secondary" disabled />
      ) : (
        <Button title={`Get ${plan.name}`} onPress={onBuy} loading={busy} disabled={!canBuy || !offer} />
      )}
    </Card>
  );
}

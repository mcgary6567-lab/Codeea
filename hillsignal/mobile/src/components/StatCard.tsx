import { View } from 'react-native';
import { radius, spacing, useTheme } from '@/theme';
import { Card } from './Card';
import { Txt } from './Txt';

export function StatCard({ label, value, hint }: { label: string; value: string; hint?: string }) {
  return (
    <Card style={{ flex: 1, gap: 4 }}>
      <Txt variant="caption" tone="muted">
        {label}
      </Txt>
      <Txt mono variant="title" weight="800" numberOfLines={1} adjustsFontSizeToFit>
        {value}
      </Txt>
      {hint ? (
        <Txt variant="small" tone="faint">
          {hint}
        </Txt>
      ) : null}
    </Card>
  );
}

/** Horizontal buy/sell split bar. */
export function RatioBar({ buys, sells }: { buys: number; sells: number }) {
  const t = useTheme();
  const total = buys + sells;
  const buyPct = total ? Math.round((buys / total) * 100) : 50;
  return (
    <View style={{ gap: spacing.sm }} accessibilityLabel={`${buyPct}% buys, ${100 - buyPct}% sells`}>
      <View style={{ flexDirection: 'row', height: 10, borderRadius: radius.pill, overflow: 'hidden', backgroundColor: t.surfaceAlt }}>
        {total ? (
          <>
            <View style={{ flex: buys, backgroundColor: t.buy }} />
            <View style={{ width: 2, backgroundColor: t.surface }} />
            <View style={{ flex: sells, backgroundColor: t.sell }} />
          </>
        ) : null}
      </View>
      <View style={{ flexDirection: 'row', justifyContent: 'space-between' }}>
        <Txt variant="small" tone="buy" mono weight="700">
          BUY {buys} · {buyPct}%
        </Txt>
        <Txt variant="small" tone="sell" mono weight="700">
          {total ? 100 - buyPct : 50}% · {sells} SELL
        </Txt>
      </View>
    </View>
  );
}

import { View } from 'react-native';
import type { Chamber, Party, Plan, TxType } from '@/api/types';
import { chamberLabel, partyLabel, txLabel } from '@/lib/format';
import { partyColors, radius, useTheme } from '@/theme';
import { Txt } from './Txt';

function Pill({ text, color, solid }: { text: string; color: string; solid?: boolean }) {
  return (
    <View
      style={{
        paddingHorizontal: 7,
        paddingVertical: 2,
        borderRadius: radius.sm - 2,
        backgroundColor: solid ? color : `${color}22`,
        borderWidth: solid ? 0 : 1,
        borderColor: `${color}55`,
        alignSelf: 'flex-start',
      }}
    >
      <Txt variant="caption" style={{ color: solid ? '#FFFFFF' : color, letterSpacing: 0.4 }}>
        {text}
      </Txt>
    </View>
  );
}

/** Small party chip; neutral grey "—" when the politician isn't matched yet (party = null). */
export function PartyChip({ party }: { party: Party | null }) {
  return (
    <View accessibilityLabel={partyLabel(party)}>
      <Pill text={party ?? '—'} color={party ? partyColors[party] : partyColors.none} solid />
    </View>
  );
}

export function ChamberTag({ chamber }: { chamber: Chamber }) {
  const t = useTheme();
  return <Pill text={chamberLabel[chamber]} color={t.neutral} />;
}

export function TxBadge({ txType }: { txType: TxType }) {
  const t = useTheme();
  const color = txType === 'buy' ? t.buy : txType === 'sell' ? t.sell : t.neutral;
  return <Pill text={txLabel[txType]} color={color} />;
}

export function PlanBadge({ plan }: { plan: Plan }) {
  const t = useTheme();
  const color = plan === 'free' ? t.neutral : t.accent;
  return <Pill text={plan.toUpperCase()} color={color} />;
}

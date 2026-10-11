import { View } from 'react-native';
import { spacing, useTheme } from '@/theme';
import { Card } from './Card';
import { Txt } from './Txt';

export interface Fact {
  label: string;
  value: string;
  hint?: string;
  mono?: boolean;
}

/** Two-column label/value grid for detail screens. */
export function FactGrid({ facts }: { facts: Fact[] }) {
  const t = useTheme();
  return (
    <Card padded={false} style={{ flexDirection: 'row', flexWrap: 'wrap' }}>
      {facts.map((f, i) => (
        <View
          key={f.label}
          style={{
            width: '50%',
            padding: spacing.lg,
            gap: 3,
            borderColor: t.border,
            borderRightWidth: i % 2 === 0 ? 1 : 0,
            borderBottomWidth: i < facts.length - (facts.length % 2 === 0 ? 2 : 1) ? 1 : 0,
          }}
        >
          <Txt variant="caption" tone="muted">
            {f.label}
          </Txt>
          <Txt weight="700" mono={f.mono}>
            {f.value}
          </Txt>
          {f.hint ? (
            <Txt variant="small" tone="faint">
              {f.hint}
            </Txt>
          ) : null}
        </View>
      ))}
    </Card>
  );
}

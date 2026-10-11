import { useEffect, useState, type ReactNode } from 'react';
import { View } from 'react-native';
import type { Chamber, Party, TradeQuery, TradeSort, TxType } from '@/api/types';
import { compactUsd, daysAgo, isIsoDay, toIsoDay } from '@/lib/format';
import { partyColors, spacing } from '@/theme';
import { Button } from './Button';
import { Chip } from './Chip';
import { Field } from './Field';
import { Sheet } from './Sheet';
import { Txt } from './Txt';

export type TradeFilterState = Omit<TradeQuery, 'politicianId' | 'issuerId'>;

const AMOUNTS = [1_000, 15_000, 50_000, 100_000, 250_000, 1_000_000];
const DATE_PRESETS: { key: string; label: string; from: () => string | undefined }[] = [
  { key: 'any', label: 'Any time', from: () => undefined },
  { key: '30', label: '30 days', from: () => daysAgo(30) },
  { key: '90', label: '90 days', from: () => daysAgo(90) },
  { key: '365', label: '1 year', from: () => daysAgo(365) },
  { key: 'ytd', label: 'This year', from: () => toIsoDay(new Date(new Date().getFullYear(), 0, 1)) },
];
const SORTS: { value: TradeSort; label: string }[] = [
  { value: '-pubDate', label: 'Newest filed' },
  { value: '-txDate', label: 'Newest traded' },
  { value: '-amount', label: 'Largest' },
];

function Group({ title, children }: { title: string; children: ReactNode }) {
  return (
    <View style={{ gap: spacing.sm }}>
      <Txt variant="caption" tone="muted">
        {title}
      </Txt>
      <View style={{ flexDirection: 'row', flexWrap: 'wrap', gap: spacing.sm }}>{children}</View>
    </View>
  );
}

function OptionChips<T extends string>({ options, value, onChange, dot }: {
  options: { value: T; label: string }[];
  value: T | undefined;
  onChange: (v: T | undefined) => void;
  dot?: (v: T) => string | undefined;
}) {
  return (
    <>
      <Chip label="Any" selected={value === undefined} onPress={() => onChange(undefined)} />
      {options.map((o) => (
        <Chip key={o.value} label={o.label} dotColor={dot?.(o.value)} selected={value === o.value} onPress={() => onChange(value === o.value ? undefined : o.value)} />
      ))}
    </>
  );
}

interface Props {
  visible: boolean;
  value: TradeFilterState;
  onApply: (v: TradeFilterState) => void;
  onClose: () => void;
}

export function FilterSheet({ visible, value, onApply, onClose }: Props) {
  const [draft, setDraft] = useState<TradeFilterState>(value);
  const [dateError, setDateError] = useState<string | null>(null);
  useEffect(() => {
    if (visible) {
      setDraft(value);
      setDateError(null);
    }
  }, [visible, value]);

  const set = <K extends keyof TradeFilterState>(k: K, v: TradeFilterState[K]) => setDraft((d) => ({ ...d, [k]: v }));
  const presetKey = DATE_PRESETS.find((p) => p.from() === draft.from && !draft.to)?.key;

  const apply = () => {
    for (const d of [draft.from, draft.to]) {
      if (d && !isIsoDay(d)) return setDateError('Use the format YYYY-MM-DD.');
    }
    if (draft.from && draft.to && draft.from > draft.to) return setDateError('"From" must be before "To".');
    onApply(draft);
    onClose();
  };

  return (
    <Sheet
      visible={visible}
      title="Filter trades"
      onClose={onClose}
      footer={
        <>
          <Button title="Reset" variant="secondary" style={{ flex: 1 }} onPress={() => setDraft({ ticker: draft.ticker })} />
          <Button title="Apply" style={{ flex: 2 }} onPress={apply} />
        </>
      }
    >
      <Group title="Chamber">
        <OptionChips<Chamber> options={[{ value: 'house', label: 'House' }, { value: 'senate', label: 'Senate' }]} value={draft.chamber} onChange={(v) => set('chamber', v)} />
      </Group>
      <Group title="Party">
        <OptionChips<Party>
          options={[{ value: 'D', label: 'Democrat' }, { value: 'R', label: 'Republican' }, { value: 'I', label: 'Independent' }]}
          value={draft.party}
          onChange={(v) => set('party', v)}
          dot={(v) => partyColors[v]}
        />
      </Group>
      <Group title="Transaction">
        <OptionChips<TxType>
          options={[{ value: 'buy', label: 'Buy' }, { value: 'sell', label: 'Sell' }, { value: 'exchange', label: 'Exchange' }]}
          value={draft.txType}
          onChange={(v) => set('txType', v)}
        />
      </Group>
      <Group title="Traded">
        {DATE_PRESETS.map((p) => (
          <Chip key={p.key} label={p.label} selected={presetKey === p.key} onPress={() => setDraft((d) => ({ ...d, from: p.from(), to: undefined }))} />
        ))}
      </Group>
      <View style={{ flexDirection: 'row', gap: spacing.md }}>
        <View style={{ flex: 1 }}>
          <Field label="From" placeholder="YYYY-MM-DD" value={draft.from ?? ''} onChangeText={(s) => set('from', s || undefined)} keyboardType="numbers-and-punctuation" maxLength={10} />
        </View>
        <View style={{ flex: 1 }}>
          <Field label="To" placeholder="YYYY-MM-DD" value={draft.to ?? ''} onChangeText={(s) => set('to', s || undefined)} keyboardType="numbers-and-punctuation" maxLength={10} />
        </View>
      </View>
      {dateError ? (
        <Txt variant="small" tone="danger">
          {dateError}
        </Txt>
      ) : null}
      <Group title="Minimum amount">
        <Chip label="Any" selected={!draft.minAmount} onPress={() => set('minAmount', undefined)} />
        {AMOUNTS.map((a) => (
          <Chip key={a} label={`${compactUsd(a)}+`} selected={draft.minAmount === a} onPress={() => set('minAmount', a)} />
        ))}
      </Group>
      <Group title="Sort">
        {SORTS.map((s) => (
          <Chip key={s.value} label={s.label} selected={(draft.sort ?? '-pubDate') === s.value} onPress={() => set('sort', s.value)} />
        ))}
      </Group>
    </Sheet>
  );
}

/** Human-readable chips for the active filters. */
export function describeFilters(f: TradeFilterState): { key: keyof TradeFilterState; label: string }[] {
  const out: { key: keyof TradeFilterState; label: string }[] = [];
  if (f.chamber) out.push({ key: 'chamber', label: f.chamber === 'house' ? 'House' : 'Senate' });
  if (f.party) out.push({ key: 'party', label: `Party ${f.party}` });
  if (f.state) out.push({ key: 'state', label: f.state });
  if (f.txType) out.push({ key: 'txType', label: f.txType[0]!.toUpperCase() + f.txType.slice(1) });
  if (f.ticker) out.push({ key: 'ticker', label: `$${f.ticker}` });
  if (f.from) out.push({ key: 'from', label: `From ${f.from}` });
  if (f.to) out.push({ key: 'to', label: `To ${f.to}` });
  if (f.minAmount) out.push({ key: 'minAmount', label: `${compactUsd(f.minAmount)}+` });
  if (f.sort && f.sort !== '-pubDate') out.push({ key: 'sort', label: SORTS.find((s) => s.value === f.sort)?.label ?? f.sort });
  return out;
}

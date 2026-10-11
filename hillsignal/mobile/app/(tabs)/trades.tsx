import { Ionicons } from '@expo/vector-icons';
import { Tabs, useLocalSearchParams } from 'expo-router';
import { useEffect, useMemo, useState } from 'react';
import { Alert, Pressable, ScrollView, TextInput, View } from 'react-native';
import { errorMessage } from '@/api/client';
import { flattenPages, useTradesInfinite } from '@/api/hooks';
import { useAuth } from '@/auth/AuthContext';
import { Chip } from '@/components/Chip';
import { DelayBanner } from '@/components/DelayBanner';
import { FilterSheet, describeFilters, type TradeFilterState } from '@/components/FilterSheet';
import { TradeList } from '@/components/TradeList';
import { Txt } from '@/components/Txt';
import { exportTradesCsv } from '@/lib/exportCsv';
import { formatInt } from '@/lib/format';
import { openPaywall } from '@/lib/navigation';
import { filtersFromParams } from '@/lib/routeFilters';
import { useDebounced } from '@/lib/useDebounced';
import { radius, spacing, useTheme } from '@/theme';

function ExportButton({ filters }: { filters: TradeFilterState }) {
  const t = useTheme();
  const { entitlements } = useAuth();
  const [busy, setBusy] = useState(false);
  const onPress = async () => {
    if (!entitlements.csvExport) return openPaywall('premium');
    setBusy(true);
    try {
      await exportTradesCsv(filters);
    } catch (e) {
      Alert.alert('Export failed', errorMessage(e));
    } finally {
      setBusy(false);
    }
  };
  return (
    <Pressable onPress={() => void onPress()} disabled={busy} hitSlop={10} accessibilityLabel="Export CSV" style={{ paddingHorizontal: spacing.md, opacity: busy ? 0.5 : 1 }}>
      <Ionicons name={entitlements.csvExport ? 'download-outline' : 'lock-closed-outline'} size={21} color={t.text} />
    </Pressable>
  );
}

export default function Trades() {
  const t = useTheme();
  const params = useLocalSearchParams();
  const paramKey = JSON.stringify(params);
  const [filters, setFilters] = useState<TradeFilterState>(() => filtersFromParams(params));
  const [tickerText, setTickerText] = useState(filters.ticker ?? '');
  const [sheetOpen, setSheetOpen] = useState(false);

  // Navigating here with new params (e.g. a ticker from Home) resets the filters.
  useEffect(() => {
    const next = filtersFromParams(JSON.parse(paramKey) as Record<string, string>);
    if (Object.keys(next).length === 0) return;
    setFilters(next);
    setTickerText(next.ticker ?? '');
  }, [paramKey]);

  const ticker = useDebounced(tickerText.trim().toUpperCase());
  useEffect(() => setFilters((f) => ({ ...f, ticker: ticker || undefined })), [ticker]);

  const query = useTradesInfinite(filters);
  const { meta } = flattenPages(query.data?.pages);
  const active = useMemo(() => describeFilters(filters).filter((c) => c.key !== 'ticker'), [filters]);

  const clear = (key: keyof TradeFilterState) => setFilters((f) => ({ ...f, [key]: undefined }));

  return (
    <>
      <Tabs.Screen options={{ headerRight: () => <ExportButton filters={filters} /> }} />
      <View style={{ paddingHorizontal: spacing.lg, paddingBottom: spacing.sm, gap: spacing.sm, backgroundColor: t.bg }}>
        <View style={{ flexDirection: 'row', gap: spacing.sm }}>
          <View
            style={{
              flex: 1,
              flexDirection: 'row',
              alignItems: 'center',
              gap: spacing.sm,
              backgroundColor: t.surfaceAlt,
              borderRadius: radius.md,
              borderWidth: 1,
              borderColor: t.border,
              paddingHorizontal: spacing.md,
            }}
          >
            <Ionicons name="search" size={17} color={t.textFaint} />
            <TextInput
              value={tickerText}
              onChangeText={setTickerText}
              placeholder="Ticker, e.g. NVDA"
              placeholderTextColor={t.textFaint}
              autoCapitalize="characters"
              autoCorrect={false}
              maxLength={10}
              style={{ flex: 1, color: t.text, paddingVertical: spacing.md - 2, fontSize: 15 }}
              accessibilityLabel="Search by ticker"
            />
            {tickerText ? (
              <Pressable onPress={() => setTickerText('')} hitSlop={8} accessibilityLabel="Clear ticker">
                <Ionicons name="close-circle" size={17} color={t.textFaint} />
              </Pressable>
            ) : null}
          </View>
          <Pressable
            onPress={() => setSheetOpen(true)}
            accessibilityRole="button"
            accessibilityLabel="Filters"
            style={{
              flexDirection: 'row',
              alignItems: 'center',
              gap: 6,
              paddingHorizontal: spacing.md,
              borderRadius: radius.md,
              borderWidth: 1,
              borderColor: active.length ? t.accent : t.border,
              backgroundColor: active.length ? t.accentSoft : t.surfaceAlt,
            }}
          >
            <Ionicons name="options-outline" size={18} color={active.length ? t.accent : t.text} />
            {active.length ? (
              <Txt variant="small" tone="accent" weight="800">
                {active.length}
              </Txt>
            ) : null}
          </Pressable>
        </View>
        {active.length ? (
          <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={{ gap: spacing.sm }}>
            {active.map((c) => (
              <Chip key={c.key} label={c.label} selected onRemove={() => clear(c.key)} />
            ))}
          </ScrollView>
        ) : null}
        {meta ? (
          <Txt variant="small" tone="faint" mono>
            {formatInt(meta.total)} trades
          </Txt>
        ) : null}
        <DelayBanner delayDays={meta?.delayDays} />
      </View>
      <TradeList query={query} emptyTitle="No matching trades" emptyMessage="Try removing a filter or widening the date range." />
      <FilterSheet visible={sheetOpen} value={filters} onApply={(f) => setFilters({ ...f, ticker: filters.ticker })} onClose={() => setSheetOpen(false)} />
    </>
  );
}

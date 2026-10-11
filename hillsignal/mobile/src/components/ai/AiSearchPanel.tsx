import { Ionicons } from '@expo/vector-icons';
import { useMutation } from '@tanstack/react-query';
import { router } from 'expo-router';
import { useState } from 'react';
import { Pressable, TextInput, View } from 'react-native';
import { errorMessage, isApiError } from '@/api/client';
import { api } from '@/api/endpoints';
import type { AiSearchResponse, TradeFilters } from '@/api/types';
import { useAuth } from '@/auth/AuthContext';
import { compactUsd } from '@/lib/format';
import { openPaywall } from '@/lib/navigation';
import { paramsFromAiFilters } from '@/lib/routeFilters';
import { radius, spacing, useTheme } from '@/theme';
import { Button } from '../Button';
import { Card } from '../Card';
import { Chip } from '../Chip';
import { Disclaimer } from '../Disclaimer';
import { LockedCard } from '../LockedCard';
import { Notice } from '../Notice';
import { EmptyState } from '../States';
import { TradeRow } from '../TradeRow';
import { Txt } from '../Txt';

const EXAMPLES = ['Which senators sold Nvidia this year?', 'Biggest tech buys last month', 'Republicans buying defense stocks'];

function filterChips(f: TradeFilters): string[] {
  const out: string[] = [];
  if (f.politicianName) out.push(f.politicianName);
  if (f.chamber) out.push(f.chamber === 'house' ? 'House' : 'Senate');
  if (f.party) out.push(`Party ${f.party}`);
  if (f.state) out.push(f.state);
  if (f.ticker) out.push(`$${f.ticker}`);
  if (f.sector) out.push(f.sector);
  if (f.txType) out.push(f.txType);
  if (f.from) out.push(`from ${f.from}`);
  if (f.to) out.push(`to ${f.to}`);
  if (f.minAmount) out.push(`${compactUsd(f.minAmount)}+`);
  return out;
}

function Results({ data }: { data: AiSearchResponse }) {
  const params = paramsFromAiFilters(data.filters);
  const chips = filterChips(data.filters);
  return (
    <Card padded={false} style={{ overflow: 'hidden' }}>
      <View style={{ padding: spacing.lg, gap: spacing.md }}>
        <Txt style={{ lineHeight: 21 }}>{data.explanation}</Txt>
        {chips.length ? (
          <View style={{ flexDirection: 'row', flexWrap: 'wrap', gap: spacing.sm }}>
            {chips.map((c) => (
              <Chip key={c} label={c} icon="funnel-outline" />
            ))}
          </View>
        ) : null}
        <Disclaimer text={data.disclaimer} />
      </View>
      {data.results.data.length === 0 ? (
        <EmptyState icon="search-outline" title="No trades matched" message="Try rephrasing or widening the time range." />
      ) : (
        <View>
          {data.results.data.slice(0, 10).map((tr) => (
            <TradeRow key={tr.id} trade={tr} />
          ))}
        </View>
      )}
      {Object.keys(params).length && data.results.meta.total > 0 ? (
        <Button
          title={`Open all ${data.results.meta.total} in Trades`}
          variant="ghost"
          icon="arrow-forward"
          onPress={() => router.push({ pathname: '/(tabs)/trades', params })}
        />
      ) : null}
    </Card>
  );
}

export function AiSearchPanel() {
  const t = useTheme();
  const { isAuthed, entitlements } = useAuth();
  const [text, setText] = useState('');
  const [remaining, setRemaining] = useState<number | null>(null);
  const search = useMutation({
    mutationFn: (q: string) => api.aiSearch(q),
    onSuccess: (r) => setRemaining(r.remaining),
    onError: (e) => {
      if (isApiError(e) && e.code === 'quota_exceeded') setRemaining(0);
    },
  });

  if (entitlements.aiSearchPerDay === 0) {
    return <LockedCard title="AI search" message="Ask questions in plain English and get matching trades." requiredPlan="pro" />;
  }

  const submit = (q = text) => {
    const query = q.trim();
    if (!query) return;
    if (!isAuthed) {
      router.push('/auth/sign-in');
      return;
    }
    setText(query);
    search.mutate(query);
  };

  const quota = remaining ?? entitlements.aiSearchPerDay;
  const err = search.error;

  return (
    <View style={{ gap: spacing.md }}>
      <View
        style={{
          flexDirection: 'row',
          alignItems: 'center',
          gap: spacing.sm,
          backgroundColor: t.surface,
          borderRadius: radius.lg,
          borderWidth: 1,
          borderColor: t.accent,
          paddingLeft: spacing.lg,
          paddingRight: spacing.xs,
        }}
      >
        <Ionicons name="sparkles" size={18} color={t.accent} />
        <TextInput
          value={text}
          onChangeText={setText}
          placeholder="Which senators sold Nvidia this year?"
          placeholderTextColor={t.textFaint}
          onSubmitEditing={() => submit()}
          returnKeyType="search"
          style={{ flex: 1, color: t.text, fontSize: 16, paddingVertical: spacing.md + 2 }}
          accessibilityLabel="Ask a question about congressional trades"
        />
        <Pressable
          onPress={() => submit()}
          disabled={search.isPending}
          accessibilityRole="button"
          accessibilityLabel="Search"
          style={{ backgroundColor: t.accent, borderRadius: radius.md, padding: spacing.sm + 2, opacity: search.isPending ? 0.5 : 1 }}
        >
          <Ionicons name="arrow-up" size={18} color={t.onAccent} />
        </Pressable>
      </View>
      <View style={{ flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center' }}>
        <Txt variant="small" tone="faint" mono>
          {isAuthed ? `${quota} search${quota === 1 ? '' : 'es'} left today` : `${entitlements.aiSearchPerDay}/day with a free account`}
        </Txt>
        {entitlements.plan === 'free' ? (
          <Pressable onPress={() => openPaywall('pro')} hitSlop={8}>
            <Txt variant="small" tone="accent" weight="700">
              Get more
            </Txt>
          </Pressable>
        ) : null}
      </View>

      {!search.data && !search.isPending && !err ? (
        <View style={{ flexDirection: 'row', flexWrap: 'wrap', gap: spacing.sm }}>
          {EXAMPLES.map((ex) => (
            <Chip key={ex} label={ex} onPress={() => submit(ex)} />
          ))}
        </View>
      ) : null}

      {!isAuthed ? <Notice message="AI search needs a free account." actionLabel="Sign in" onAction={() => router.push('/auth/sign-in')} /> : null}
      {search.isPending ? <Notice message="Reading the filings…" /> : null}
      {err ? (
        <Notice
          kind={isApiError(err) && ['quota_exceeded', 'rate_limited', 'ai_declined'].includes(err.code) ? 'warning' : 'error'}
          message={errorMessage(err)}
          actionLabel={isApiError(err) && (err.code === 'quota_exceeded' || err.status === 402) ? 'Upgrade' : undefined}
          onAction={() => openPaywall(isApiError(err) ? err.requiredPlan ?? 'pro' : 'pro')}
        />
      ) : null}
      {search.data && !search.isPending ? <Results data={search.data} /> : null}
    </View>
  );
}

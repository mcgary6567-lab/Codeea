import { memo } from 'react';
import { Pressable, View } from 'react-native';
import type { Trade } from '@/api/types';
import { amountRange, relativeDate, absoluteDate } from '@/lib/format';
import { openTrade } from '@/lib/navigation';
import { fonts, spacing, useTheme } from '@/theme';
import { Avatar } from './Avatar';
import { PartyChip, TxBadge } from './Badges';
import { Txt } from './Txt';

function TradeRowBase({ trade, hidePolitician }: { trade: Trade; hidePolitician?: boolean }) {
  const t = useTheme();
  const { politician: p, issuer } = trade;
  const ticker = issuer?.ticker;
  const title = hidePolitician ? (issuer?.name ?? trade.assetName) : p.name;
  const subtitle = hidePolitician ? trade.assetType ?? 'asset' : `${ticker ? `${ticker} · ` : ''}${issuer?.name ?? trade.assetName}`;
  return (
    <Pressable
      onPress={() => openTrade(trade.id)}
      android_ripple={{ color: t.accentSoft }}
      accessibilityRole="button"
      accessibilityLabel={`${p.name} ${trade.txType} ${ticker ?? trade.assetName}, ${amountRange(trade)}`}
      style={({ pressed }) => ({
        flexDirection: 'row',
        alignItems: 'center',
        gap: spacing.md,
        paddingHorizontal: spacing.lg,
        paddingVertical: spacing.md + 2,
        borderBottomWidth: 1,
        borderColor: t.border,
        backgroundColor: pressed ? t.surfaceAlt : 'transparent',
      })}
    >
      {hidePolitician ? (
        <View style={{ width: 40, height: 40, borderRadius: 10, backgroundColor: t.surfaceAlt, alignItems: 'center', justifyContent: 'center' }}>
          <Txt mono weight="700" variant="small" numberOfLines={1}>
            {ticker ?? '—'}
          </Txt>
        </View>
      ) : (
        <Avatar name={p.name} url={p.photoUrl} />
      )}
      <View style={{ flex: 1, gap: 3 }}>
        <View style={{ flexDirection: 'row', alignItems: 'center', gap: 6 }}>
          <Txt weight="700" numberOfLines={1} style={{ flexShrink: 1 }}>
            {title}
          </Txt>
          {hidePolitician ? null : <PartyChip party={p.party} />}
        </View>
        <Txt variant="small" tone="muted" numberOfLines={1}>
          {subtitle}
        </Txt>
      </View>
      <View style={{ alignItems: 'flex-end', gap: 4 }}>
        <View style={{ flexDirection: 'row', alignItems: 'center', gap: 6 }}>
          <TxBadge txType={trade.txType} />
          <Txt mono weight="700" variant="small" style={{ fontFamily: fonts.mono }}>
            {amountRange(trade)}
          </Txt>
        </View>
        <Txt variant="small" tone="faint" style={{ fontSize: 11.5 }}>
          {relativeDate(trade.txDate)} · {absoluteDate(trade.txDate)}
        </Txt>
      </View>
    </Pressable>
  );
}

export const TradeRow = memo(TradeRowBase);

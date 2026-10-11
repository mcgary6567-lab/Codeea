import { useEffect, useRef } from 'react';
import { Animated, View, type DimensionValue } from 'react-native';
import { radius, spacing, useTheme } from '@/theme';

export function Skeleton({ width = '100%', height = 14, round }: { width?: DimensionValue; height?: number; round?: boolean }) {
  const t = useTheme();
  const opacity = useRef(new Animated.Value(0.5)).current;
  useEffect(() => {
    const loop = Animated.loop(
      Animated.sequence([
        Animated.timing(opacity, { toValue: 1, duration: 700, useNativeDriver: true }),
        Animated.timing(opacity, { toValue: 0.5, duration: 700, useNativeDriver: true }),
      ]),
    );
    loop.start();
    return () => loop.stop();
  }, [opacity]);
  return (
    <Animated.View
      style={{ width, height, opacity, backgroundColor: t.skeleton, borderRadius: round ? height / 2 : radius.sm - 2 }}
    />
  );
}

export function TradeRowSkeleton() {
  const t = useTheme();
  return (
    <View
      style={{
        flexDirection: 'row',
        gap: spacing.md,
        padding: spacing.lg,
        borderBottomWidth: 1,
        borderColor: t.border,
        alignItems: 'center',
      }}
    >
      <Skeleton width={40} height={40} round />
      <View style={{ flex: 1, gap: 8 }}>
        <Skeleton width="55%" />
        <Skeleton width="80%" height={12} />
      </View>
      <View style={{ gap: 8, alignItems: 'flex-end' }}>
        <Skeleton width={64} />
        <Skeleton width={48} height={12} />
      </View>
    </View>
  );
}

export function ListSkeleton({ rows = 6 }: { rows?: number }) {
  return (
    <View accessibilityLabel="Loading">
      {Array.from({ length: rows }, (_, i) => (
        <TradeRowSkeleton key={i} />
      ))}
    </View>
  );
}

export function CardSkeleton({ lines = 3 }: { lines?: number }) {
  const t = useTheme();
  return (
    <View style={{ backgroundColor: t.surface, borderColor: t.border, borderWidth: 1, borderRadius: radius.lg, padding: spacing.lg, gap: 10 }}>
      <Skeleton width="40%" height={16} />
      {Array.from({ length: lines }, (_, i) => (
        <Skeleton key={i} width={i === lines - 1 ? '65%' : '100%'} height={12} />
      ))}
    </View>
  );
}

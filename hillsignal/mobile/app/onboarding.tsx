import { Ionicons } from '@expo/vector-icons';
import { router } from 'expo-router';
import { useRef, useState } from 'react';
import { FlatList, Pressable, View, useWindowDimensions, type NativeScrollEvent, type NativeSyntheticEvent } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import { useAuth } from '@/auth/AuthContext';
import { Button } from '@/components/Button';
import { Txt } from '@/components/Txt';
import { config } from '@/config';
import { radius, spacing, useTheme } from '@/theme';

const SLIDES: { icon: keyof typeof Ionicons.glyphMap; kicker: string; title: string; body: string }[] = [
  {
    icon: 'pulse',
    kicker: '01 / Disclosures',
    title: 'Every trade Congress files',
    body: 'House and Senate stock disclosures in one clean feed: who traded, what, when, and how late they reported it.',
  },
  {
    icon: 'sparkles',
    kicker: '02 / AI search',
    title: 'Ask in plain English',
    body: '"Which senators sold Nvidia this year?" HillSignal turns questions into filters and explains what it found.',
  },
  {
    icon: 'notifications',
    kicker: '03 / Watchlist',
    title: 'Follow the signal',
    body: 'Follow politicians and companies, get a personal feed, and an alert the moment a new filing lands.',
  },
];

export default function Onboarding() {
  const t = useTheme();
  const { width } = useWindowDimensions();
  const { markOnboardingSeen } = useAuth();
  const [index, setIndex] = useState(0);
  const listRef = useRef<FlatList>(null);
  const last = index === SLIDES.length - 1;

  const finish = async () => {
    await markOnboardingSeen();
    router.replace('/auth/sign-in');
  };
  const next = () => (last ? void finish() : listRef.current?.scrollToIndex({ index: index + 1 }));
  const onScroll = (e: NativeSyntheticEvent<NativeScrollEvent>) => setIndex(Math.round(e.nativeEvent.contentOffset.x / width));

  return (
    <SafeAreaView style={{ flex: 1, backgroundColor: t.bg }}>
      <View style={{ flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center', padding: spacing.lg }}>
        <Txt mono weight="800" tone="accent">
          {config.APP_NAME.toUpperCase()}
        </Txt>
        {!last ? (
          <Pressable onPress={() => void finish()} hitSlop={10} accessibilityRole="button">
            <Txt tone="muted" weight="600">
              Skip
            </Txt>
          </Pressable>
        ) : null}
      </View>
      <FlatList
        ref={listRef}
        data={SLIDES}
        horizontal
        pagingEnabled
        showsHorizontalScrollIndicator={false}
        onMomentumScrollEnd={onScroll}
        keyExtractor={(s) => s.title}
        getItemLayout={(_, i) => ({ length: width, offset: width * i, index: i })}
        renderItem={({ item }) => (
          <View style={{ width, paddingHorizontal: spacing.xl, justifyContent: 'center', gap: spacing.lg }}>
            <View
              style={{
                width: 120,
                height: 120,
                borderRadius: radius.lg + 12,
                backgroundColor: t.accentSoft,
                borderWidth: 1,
                borderColor: `${t.accent}44`,
                alignItems: 'center',
                justifyContent: 'center',
                marginBottom: spacing.xl,
              }}
            >
              <Ionicons name={item.icon} size={56} color={t.accent} />
            </View>
            <Txt mono variant="small" tone="accent">
              {item.kicker}
            </Txt>
            <Txt variant="display">{item.title}</Txt>
            <Txt tone="muted" style={{ fontSize: 17, lineHeight: 25 }}>
              {item.body}
            </Txt>
          </View>
        )}
      />
      <View style={{ padding: spacing.xl, gap: spacing.xl }}>
        <View style={{ flexDirection: 'row', gap: 6 }} accessibilityLabel={`Slide ${index + 1} of ${SLIDES.length}`}>
          {SLIDES.map((s, i) => (
            <View key={s.title} style={{ height: 6, width: i === index ? 22 : 6, borderRadius: 3, backgroundColor: i === index ? t.accent : t.border }} />
          ))}
        </View>
        <Button title={last ? 'Get started' : 'Next'} onPress={next} icon={last ? 'arrow-forward' : undefined} />
      </View>
    </SafeAreaView>
  );
}

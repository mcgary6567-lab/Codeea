import { Image, View } from 'react-native';
import { initials } from '@/lib/format';
import { useTheme } from '@/theme';
import { Txt } from './Txt';

export function Avatar({ name, url, size = 40 }: { name: string; url?: string | null; size?: number }) {
  const t = useTheme();
  const style = { width: size, height: size, borderRadius: size / 2 };
  if (url) return <Image source={{ uri: url }} style={[style, { backgroundColor: t.surfaceAlt }]} accessibilityIgnoresInvertColors />;
  return (
    <View style={[style, { backgroundColor: t.surfaceAlt, alignItems: 'center', justifyContent: 'center', borderWidth: 1, borderColor: t.border }]}>
      <Txt weight="700" tone="muted" style={{ fontSize: size * 0.36 }}>
        {initials(name)}
      </Txt>
    </View>
  );
}

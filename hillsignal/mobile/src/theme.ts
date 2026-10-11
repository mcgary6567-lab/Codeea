import { Platform, useColorScheme } from 'react-native';
import { BRAND } from './brand';
import type { Party } from './api/types';

export interface Palette {
  scheme: 'dark' | 'light';
  bg: string;
  surface: string;
  surfaceAlt: string;
  border: string;
  text: string;
  textMuted: string;
  textFaint: string;
  accent: string;
  accentSoft: string;
  onAccent: string;
  buy: string;
  sell: string;
  neutral: string;
  danger: string;
  warning: string;
  skeleton: string;
  overlay: string;
}

const dark: Palette = {
  scheme: 'dark',
  bg: BRAND.colors.ink,
  surface: '#121821',
  surfaceAlt: '#18202B',
  border: '#232D3A',
  text: '#E8EDF2',
  textMuted: '#9AA6B4',
  textFaint: '#5E6B7A',
  accent: BRAND.colors.accent,
  accentSoft: 'rgba(46,230,166,0.14)',
  onAccent: '#04130D',
  buy: BRAND.colors.accent,
  sell: '#FFB547',
  neutral: '#8B97A6',
  danger: '#FF6B6B',
  warning: '#FFB547',
  skeleton: '#1C2531',
  overlay: 'rgba(0,0,0,0.6)',
};

const light: Palette = {
  scheme: 'light',
  bg: BRAND.colors.paper,
  surface: '#FFFFFF',
  surfaceAlt: '#EEF1F4',
  border: '#DDE2E8',
  text: '#0D1520',
  textMuted: '#4F5D6D',
  textFaint: '#8A96A3',
  accent: '#0FA978',
  accentSoft: 'rgba(15,169,120,0.12)',
  onAccent: '#FFFFFF',
  buy: '#0FA978',
  sell: '#C77700',
  neutral: '#6B7785',
  danger: '#D64545',
  warning: '#C77700',
  skeleton: '#E3E7EC',
  overlay: 'rgba(10,14,20,0.45)',
};

/** Party colors: only used for the small party chips. */
export const partyColors: Record<Party | 'none', string> = {
  D: '#3B82F6',
  R: '#EF4444',
  I: '#A855F7',
  none: '#7A8593',
};

export const spacing = { xs: 4, sm: 8, md: 12, lg: 16, xl: 24, xxl: 32 } as const;
export const radius = { sm: 8, md: 12, lg: 18, pill: 999 } as const;

export const fonts = {
  /** Monospace for numbers and tickers: the "terminal" feel. */
  mono: Platform.select({ android: 'monospace', ios: 'Menlo', default: 'monospace' }),
} as const;

export const type = {
  display: { fontSize: 28, fontWeight: '800' as const, letterSpacing: -0.5 },
  title: { fontSize: 20, fontWeight: '700' as const },
  heading: { fontSize: 16, fontWeight: '700' as const },
  body: { fontSize: 15, fontWeight: '400' as const },
  small: { fontSize: 13, fontWeight: '400' as const },
  caption: { fontSize: 11, fontWeight: '600' as const, letterSpacing: 0.8 },
};

export const palettes = { dark, light };

/** Follows the system setting; dark is the default when the system has no preference. */
export function useTheme(): Palette {
  const scheme = useColorScheme();
  return scheme === 'light' ? light : dark;
}

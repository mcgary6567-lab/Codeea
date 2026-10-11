/**
 * Brand constants. Rename the app or change its colors in src/brand.json ONLY.
 * JSON so that app.config.ts (evaluated by Node, not Metro) can read it too.
 * - appName: display name
 * - scheme: deep-link URL scheme (hillsignal://trade/123)
 * - androidPackage: placeholder Play package id, permanent after first upload
 * - colors.accent: the single accent color; ink/paper: dark/light backgrounds
 */
import brand from './brand.json';

export const BRAND = brand;

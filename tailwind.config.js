/** Tailwind build config.
 *  Compiled ahead of time into app/static/css/tailwind.css by build/build-css.sh.
 *  The app previously used the Tailwind Play CDN, which compiles in the browser on every
 *  page load - that froze large pages, so it was replaced with this static build.
 */
module.exports = {
  darkMode: 'class',
  content: ['./app/templates/**/*.html', './app/**/*.py'],
  theme: {
    extend: {
      fontFamily: {
        sans: ['Inter', 'ui-sans-serif', 'system-ui', '-apple-system', 'Segoe UI', 'sans-serif'],
        arabic: ['"Amiri Quran"', '"Scheherazade New"', 'Amiri', 'serif'],
        urdu: ['"Noto Nastaliq Urdu"', 'serif'],
      },
      colors: {
        brand: {
          50: '#f0fdfa', 100: '#ccfbf1', 200: '#99f6e4', 300: '#5eead4', 400: '#2dd4bf',
          500: '#14b8a6', 600: '#0d9488', 700: '#0f766e', 800: '#115e59', 900: '#134e4a', 950: '#042f2e',
        },
      },
    },
  },
  // Status badge colours are built at runtime from database values (see templating.badge_class),
  // so Tailwind cannot see them in the templates and they must be safelisted explicitly.
  safelist: [
    { pattern: /(bg|text|ring|border)-(emerald|slate|amber|rose|sky|indigo|orange|violet|teal|brand)-(50|100|200|300|400|500|600|700|800|900)/, variants: ['dark', 'hover'] },
    { pattern: /(ring|bg|text)-(emerald|slate|amber|rose|sky|indigo|orange|violet|teal|brand)-(500|600)\/(10|20|30)/, variants: ['dark'] },
  ],
  plugins: [],
}

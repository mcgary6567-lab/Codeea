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
          // Blue, matching the college's existing ERP theme
          50: '#eef5fd', 100: '#d9e8fa', 200: '#b6d2f4', 300: '#86b3ea', 400: '#4f8fde',
          500: '#2b7fd3', 600: '#1d6fcf', 700: '#1a5bab', 800: '#184c8c', 900: '#173f72', 950: '#0f2747',
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

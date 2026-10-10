/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        // set from the pump's own brand colour at runtime (see src/lib/brand.ts)
        brand: Object.fromEntries([50, 100, 500, 600, 700, 900].map((k) => [k, `rgb(var(--brand-${k}) / <alpha-value>)`])),
        wa: { bg: "#efeae2", out: "#d9fdd3" },
      },
      fontFamily: { sans: ["Inter", "ui-sans-serif", "system-ui", "sans-serif"], urdu: ["\"Noto Nastaliq Urdu\"", "\"Noto Naskh Arabic\"", "serif"] },
    },
  },
  plugins: [],
};

/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  darkMode: "class",
  theme: {
    extend: {
      colors: {
        // set from the pump's own brand colour at runtime (see src/lib/brand.ts)
        brand: Object.fromEntries([50, 100, 500, 600, 700, 900].map((k) => [k, `rgb(var(--brand-${k}) / <alpha-value>)`])),
        wa: { bg: "#efeae2", out: "#d9fdd3" },
      },
      /* motion: transform/opacity only (GPU), 150-300 ms, all switched off under prefers-reduced-motion (index.css) */
      keyframes: {
        "fade-in": { from: { opacity: "0" }, to: { opacity: "1" } },
        "fade-out": { from: { opacity: "1" }, to: { opacity: "0" } },
        "slide-up": { from: { opacity: "0", transform: "translateY(16px) scale(.97)" }, to: { opacity: "1", transform: "translateY(0) scale(1)" } },
        "slide-down": { from: { opacity: "0", transform: "translateY(-10px)" }, to: { opacity: "1", transform: "translateY(0)" } },
        "page-in": { from: { opacity: "0", transform: "translateY(10px)" }, to: { opacity: "1", transform: "translateY(0)" } },
        pop: { "0%": { opacity: "0", transform: "scale(.6)" }, "60%": { opacity: "1", transform: "scale(1.08)" }, "100%": { transform: "scale(1)" } },
        "pop-badge": { "0%": { transform: "scale(1)" }, "40%": { transform: "scale(1.45)" }, "100%": { transform: "scale(1)" } },
        shake: { "0%,100%": { transform: "translateX(0)" }, "20%,60%": { transform: "translateX(-6px)" }, "40%,80%": { transform: "translateX(6px)" } },
        "ring-pulse": { "0%": { boxShadow: "0 0 0 0 rgb(16 185 129 / .55)" }, "100%": { boxShadow: "0 0 0 14px rgb(16 185 129 / 0)" } },
        "row-flash": { "0%": { backgroundColor: "rgb(209 250 229)" }, "100%": { backgroundColor: "transparent" } },
        shimmer: { "0%": { backgroundPosition: "-400px 0" }, "100%": { backgroundPosition: "400px 0" } },
        bell: { "0%,100%": { transform: "rotate(0)" }, "20%": { transform: "rotate(14deg)" }, "40%": { transform: "rotate(-12deg)" }, "60%": { transform: "rotate(8deg)" }, "80%": { transform: "rotate(-4deg)" } },
        "check-draw": { from: { strokeDashoffset: "60" }, to: { strokeDashoffset: "0" } },
      },
      animation: {
        "fade-in": "fade-in .2s ease-out both",
        "fade-out": "fade-out .18s ease-in both",
        "slide-up": "slide-up .24s cubic-bezier(.22,1,.36,1) both",
        "slide-down": "slide-down .2s ease-out both",
        "page-in": "page-in .22s ease-out both",
        pop: "pop .32s cubic-bezier(.34,1.56,.64,1) both",
        "pop-badge": "pop-badge .4s cubic-bezier(.34,1.56,.64,1) both",
        shake: "shake .32s ease-in-out both",
        "ring-pulse": "ring-pulse .9s ease-out both",
        "row-flash": "row-flash 1.6s ease-out both",
        shimmer: "shimmer 1.3s linear infinite",
        bell: "bell .7s ease-in-out both",
        "check-draw": "check-draw .45s ease-out .1s both",
      },
      transitionTimingFunction: { spring: "cubic-bezier(.34,1.56,.64,1)", out: "cubic-bezier(.22,1,.36,1)" },
      fontFamily: { sans: ["Inter", "ui-sans-serif", "system-ui", "sans-serif"], urdu: ["\"Noto Naskh Arabic\"", "\"Noto Nastaliq Urdu\"", "serif"], nastaliq: ["\"Noto Nastaliq Urdu\"", "\"Noto Naskh Arabic\"", "serif"] },
    },
  },
  plugins: [],
};

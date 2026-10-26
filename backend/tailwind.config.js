/** Mirrors the config that used to live inline in templates/base.html (Tailwind
 *  Play CDN). Rebuild with `npm run build:css` after adding new utility classes;
 *  the generated static/css/tailwind.css is committed so deploys need no Node. */
module.exports = {
  content: [
    "./templates/**/*.html",
    "./apps/**/templates/**/*.html",
    "./static/js/**/*.js",
    "./apps/**/*.py",
  ],
  theme: {
    extend: {
      colors: {
        // Wired to CSS variables (see static/css/theme.css) so every
        // bg-ink-900, text-slate-400, etc. flips between dark/light themes.
        // rgb(var(--x) / <alpha-value>) keeps opacity modifiers working.
        ink: {
          950: "rgb(var(--ink-950) / <alpha-value>)",
          900: "rgb(var(--ink-900) / <alpha-value>)",
          800: "rgb(var(--ink-800) / <alpha-value>)",
          700: "rgb(var(--ink-700) / <alpha-value>)",
        },
        brand: {
          400: "rgb(var(--brand-400) / <alpha-value>)",
          500: "rgb(var(--brand-500) / <alpha-value>)",
          600: "rgb(var(--brand-600) / <alpha-value>)",
        },
        mint: {
          400: "rgb(var(--mint-400) / <alpha-value>)",
          500: "rgb(var(--mint-500) / <alpha-value>)",
        },
        slate: {
          50: "rgb(var(--slate-50) / <alpha-value>)",
          100: "rgb(var(--slate-100) / <alpha-value>)",
          200: "rgb(var(--slate-200) / <alpha-value>)",
          300: "rgb(var(--slate-300) / <alpha-value>)",
          400: "rgb(var(--slate-400) / <alpha-value>)",
          500: "rgb(var(--slate-500) / <alpha-value>)",
          600: "rgb(var(--slate-600) / <alpha-value>)",
          700: "rgb(var(--slate-700) / <alpha-value>)",
          800: "rgb(var(--slate-800) / <alpha-value>)",
          900: "rgb(var(--slate-900) / <alpha-value>)",
          950: "rgb(var(--slate-950) / <alpha-value>)",
        },
      },
      fontFamily: {
        sans: ["Inter", "ui-sans-serif", "system-ui", "sans-serif"],
      },
    },
  },
  plugins: [],
};

import type { Config } from "tailwindcss";

// Palantir-style monochrome system, driven by CSS variables (see globals.css).
// Dark mode is the exact luminance inversion of the light palette.
const config: Config = {
  darkMode: "class",
  content: ["./app/**/*.{ts,tsx}", "./components/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        // page + surfaces
        ink: "rgb(var(--bg) / <alpha-value>)", // page background
        panel: "rgb(var(--surface) / <alpha-value>)", // cards / frosted bars
        raised: "rgb(var(--surface-2) / <alpha-value>)", // the "Request a Demo" gray box
        edge: "rgb(var(--edge) / <alpha-value>)", // hairline borders
        // text
        fg: "rgb(var(--fg) / <alpha-value>)", // primary text
        muted: "rgb(var(--muted) / <alpha-value>)", // secondary text
        subtle: "rgb(var(--subtle) / <alpha-value>)", // tertiary / hints
        // the high-contrast "inverse" used for primary buttons + highlights
        accent: "rgb(var(--accent) / <alpha-value>)",
        "accent-fg": "rgb(var(--accent-fg) / <alpha-value>)",
      },
      fontFamily: {
        sans: ["var(--font-inter)", "ui-sans-serif", "system-ui", "sans-serif"],
        mono: ["ui-monospace", "SFMono-Regular", "Menlo", "monospace"],
      },
      borderRadius: {
        xl: "0.875rem",
        "2xl": "1.25rem",
      },
      keyframes: {
        "fade-in": {
          "0%": { opacity: "0", transform: "translateY(4px)" },
          "100%": { opacity: "1", transform: "translateY(0)" },
        },
      },
      animation: {
        "fade-in": "fade-in 0.4s ease-out both",
      },
    },
  },
  plugins: [],
};

export default config;

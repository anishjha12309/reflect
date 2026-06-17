"use client";

import { useEffect, useState } from "react";

type Theme = "light" | "dark";

function current(): Theme {
  if (typeof document === "undefined") return "light";
  return document.documentElement.classList.contains("dark") ? "dark" : "light";
}

// Toggles the `dark` class on <html>, persists the choice, and broadcasts a
// `themechange` event so theme-aware canvases (the usage chart) can re-read tokens.
export default function ThemeToggle({ className = "" }: { className?: string }) {
  const [theme, setTheme] = useState<Theme>("light");
  const [mounted, setMounted] = useState(false);

  useEffect(() => {
    setTheme(current());
    setMounted(true);
  }, []);

  const toggle = () => {
    const next: Theme = current() === "dark" ? "light" : "dark";
    document.documentElement.classList.toggle("dark", next === "dark");
    try {
      localStorage.setItem("theme", next);
    } catch {
      /* private mode — ignore */
    }
    setTheme(next);
    window.dispatchEvent(new CustomEvent("themechange", { detail: next }));
  };

  return (
    <button
      type="button"
      onClick={toggle}
      aria-label="Toggle color theme"
      title={mounted ? `Switch to ${theme === "dark" ? "light" : "dark"} mode` : "Toggle theme"}
      className={`grid h-9 w-9 place-items-center rounded-full border border-edge text-fg transition-colors hover:bg-raised ${className}`}
    >
      {/* Sun in dark mode (click → go light), moon in light mode (click → go dark). */}
      {mounted && theme === "dark" ? <SunIcon /> : <MoonIcon />}
    </button>
  );
}

function MoonIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
      <path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z" />
    </svg>
  );
}

function SunIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
      <circle cx="12" cy="12" r="4" />
      <path d="M12 2v2M12 20v2M4.93 4.93l1.41 1.41M17.66 17.66l1.41 1.41M2 12h2M20 12h2M6.34 17.66l-1.41 1.41M19.07 4.93l-1.41 1.41" />
    </svg>
  );
}

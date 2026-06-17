import { API_URL } from "./stream";
import type { QuotaProvider } from "./types";

export interface CallRecord {
  ts: number;
  provider: string;
  prompt_tokens: number;
  completion_tokens: number;
  success: boolean;
}

export interface Metrics {
  providers: QuotaProvider[];
  series: CallRecord[];
}

export async function fetchMetrics(signal?: AbortSignal): Promise<Metrics> {
  const res = await fetch(`${API_URL}/metrics`, { signal });
  if (!res.ok) throw new Error(`/metrics responded ${res.status}`);
  return res.json();
}

// Each provider maps to a theme CSS variable (defined in globals.css) so the chart
// follows light/dark mode. Fallbacks are used during SSR / before hydration.
const PROVIDER_VARS: Record<string, { var: string; fallback: string }> = {
  cerebras: { var: "--chart-1", fallback: "37 99 235" },
  groq: { var: "--chart-2", fallback: "217 119 6" },
  gemini: { var: "--chart-3", fallback: "124 58 237" },
  openrouter: { var: "--chart-4", fallback: "94 94 94" },
};

// Resolve a CSS custom property ("r g b" triplet) to an rgb() string, browser-side.
export function cssVar(name: string, fallback: string): string {
  if (typeof window === "undefined") return `rgb(${fallback})`;
  const v = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  return `rgb(${v || fallback})`;
}

export function colorFor(provider: string): string {
  const p = PROVIDER_VARS[provider];
  return p ? cssVar(p.var, p.fallback) : cssVar("--muted", "148 163 184");
}

export const PROVIDERS = ["cerebras", "groq", "gemini", "openrouter"] as const;

export type ChartRow = { t: number } & Partial<Record<string, number>>;

/**
 * Merge the raw call log into recharts rows: one row per call, x = seconds since the
 * first call, with every provider's cumulative successful-token total carried forward.
 */
export function toChartRows(series: CallRecord[]): ChartRow[] {
  if (series.length === 0) return [];
  const t0 = series[0].ts;
  const running: Record<string, number> = Object.fromEntries(PROVIDERS.map((p) => [p, 0]));
  return series.map((c) => {
    if (c.success) running[c.provider] = (running[c.provider] ?? 0) + c.prompt_tokens + c.completion_tokens;
    return { t: Math.round(c.ts - t0), ...running };
  });
}

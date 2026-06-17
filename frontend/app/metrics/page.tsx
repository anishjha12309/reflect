"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";
import dynamic from "next/dynamic";
import { fetchMetrics, type Metrics } from "@/lib/metrics";
import QuotaStrip from "@/components/QuotaStrip";

// recharts touches browser APIs — load it client-only (no server prerender).
const UsageChart = dynamic(() => import("@/components/UsageChart"), {
  ssr: false,
  loading: () => <p className="text-sm text-subtle">Loading chart…</p>,
});

const POLL_MS = 5000;

export default function MetricsPage() {
  const [metrics, setMetrics] = useState<Metrics | null>(null);
  const [error, setError] = useState<string | null>(null);
  const abortRef = useRef<AbortController | null>(null);

  const load = useCallback(async () => {
    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;
    try {
      setMetrics(await fetchMetrics(controller.signal));
      setError(null);
    } catch (e) {
      if (!controller.signal.aborted) setError(e instanceof Error ? e.message : "Failed to load metrics");
    }
  }, []);

  useEffect(() => {
    load();
    const id = setInterval(load, POLL_MS); // live-refresh while a run is in flight
    return () => {
      clearInterval(id);
      abortRef.current?.abort();
    };
  }, [load]);

  const totals = metrics
    ? metrics.providers.reduce((acc, p) => acc + p.tokens_used, 0)
    : 0;

  return (
    <main className="mx-auto max-w-6xl px-4 py-12 sm:py-16">
      <header className="mb-6 flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <h1 className="text-3xl font-medium text-fg display">Provider Quota Dashboard</h1>
          <p className="mt-2 text-sm text-muted">
            Live token &amp; request usage per provider — the cost-engineering story, on $0 tiers.
          </p>
        </div>
        <Link href="/" className="self-start rounded-full border border-edge px-4 py-2 text-sm text-fg transition-colors hover:bg-raised">
          ← Back to research
        </Link>
      </header>

      {error && (
        <div className="mb-4 rounded-xl border border-red-300 bg-red-50 px-4 py-3 text-sm text-red-700 dark:border-red-900 dark:bg-red-950/40 dark:text-red-300">
          {error} — is the backend running?
        </div>
      )}

      {metrics && (
        <>
          <div className="mb-6">
            <QuotaStrip quota={metrics.providers} />
          </div>

          <section className="rounded-xl border border-edge bg-panel p-5">
            <div className="mb-4 flex flex-col gap-1 sm:flex-row sm:items-center sm:justify-between">
              <h2 className="text-xs font-semibold uppercase tracking-wider text-subtle">
                Cumulative tokens over time
              </h2>
              <span className="text-xs text-subtle">
                {totals.toLocaleString()} tokens · {metrics.series.length} calls today
              </span>
            </div>
            <UsageChart series={metrics.series} />
          </section>
        </>
      )}

      {!metrics && !error && <p className="text-sm text-subtle">Loading metrics…</p>}
    </main>
  );
}

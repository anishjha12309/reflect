import type { QuotaProvider } from "@/lib/types";

// Per-provider token/request telemetry — the "cost-engineering" story made visible.
export default function QuotaStrip({ quota }: { quota: QuotaProvider[] }) {
  return (
    <section className="flex flex-wrap gap-2 no-print">
      {quota.map((p) => (
        <div
          key={p.provider}
          className={`rounded-lg border px-3 py-2 text-xs ${
            p.exhausted
              ? "border-red-300 bg-red-50 text-red-700 dark:border-red-800 dark:bg-red-950/40 dark:text-red-300"
              : "border-edge bg-panel text-muted"
          }`}
        >
          <div className="font-semibold capitalize text-fg">
            {p.provider}
            {p.exhausted && (
              <span className="ml-2 rounded bg-red-200 px-1.5 py-0.5 text-[10px] text-red-800 dark:bg-red-900 dark:text-red-200">
                tapped out
              </span>
            )}
          </div>
          <div className="text-muted">
            {p.tokens_used.toLocaleString()} tok
            {p.tokens_limit ? ` / ${p.tokens_limit.toLocaleString()}` : ""} · {p.requests_used} req
            {p.requests_limit ? ` / ${p.requests_limit}` : ""}
          </div>
        </div>
      ))}
    </section>
  );
}

import type { Contradiction } from "@/lib/types";

// Cross-source contradictions detected by the critic, surfaced inline.
export default function ContradictionFlags({ contradictions }: { contradictions: Contradiction[] }) {
  if (contradictions.length === 0) return null;
  return (
    <section className="mb-4 rounded-xl border border-amber-300 bg-amber-50 p-4 no-print dark:border-amber-900 dark:bg-amber-950/30">
      <h2 className="mb-2 text-sm font-semibold text-amber-700 dark:text-amber-300">
        ⚠ Source contradictions ({contradictions.length})
      </h2>
      <ul className="space-y-2 text-sm text-amber-900/90 dark:text-amber-100/90">
        {contradictions.map((c, i) => (
          <li key={i} className="rounded-lg border border-amber-200 bg-ink/60 p-2 dark:border-amber-900/60 dark:bg-ink/30">
            <div>
              <span className="font-medium text-amber-700 dark:text-amber-300">A:</span> {c.claim_a}
            </div>
            <div>
              <span className="font-medium text-amber-700 dark:text-amber-300">B:</span> {c.claim_b}
            </div>
            {c.explanation && (
              <div className="mt-1 text-xs text-amber-800/70 dark:text-amber-200/70">{c.explanation}</div>
            )}
          </li>
        ))}
      </ul>
    </section>
  );
}

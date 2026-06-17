import Link from "next/link";

// The differentiators (CLAUDE.md §1). Every card links somewhere real — a page
// that proves the claim — so nothing here is decorative.
const CAPABILITIES: { title: string; body: string; href: string; cta: string }[] = [
  {
    title: "Rate-limit-aware LLM gateway",
    body: "Every call is routed by task type, required context window, and live remaining quota — failing over on 429/5xx with exponential backoff and a per-provider circuit breaker.",
    href: "/metrics",
    cta: "See live quota",
  },
  {
    title: "True parallel DAG orchestration",
    body: "The planner emits a dependency graph; independent sub-queries run concurrently via asyncio.gather behind a bounded semaphore. Concurrency engineering, not a for-loop.",
    href: "/architecture",
    cta: "View architecture",
  },
  {
    title: "Reflection & self-correction",
    body: "A critic agent reviews each draft for coverage gaps and cross-source contradictions, and can trigger a bounded second research round before you ever see the report.",
    href: "/#how-it-works",
    cta: "See the loop",
  },
  {
    title: "Semantic dedup cache",
    body: "Near-duplicate sub-queries and sources are caught before they ever hit a provider, conserving scarce free-tier quota across a run.",
    href: "/architecture",
    cta: "View architecture",
  },
  {
    title: "Live quota telemetry",
    body: "Per-provider token and request usage is metered and charted in real time — the cost-engineering story, made visible on $0 tiers.",
    href: "/metrics",
    cta: "Open dashboard",
  },
  {
    title: "Citation-grade attribution",
    body: "Every claim maps to a numbered source with clickable [n] citations, and the critic surfaces contradictions inline rather than hiding them.",
    href: "/#start",
    cta: "Try a run",
  },
];

export default function CapabilitiesSection() {
  return (
    <section id="capabilities" className="scroll-mt-24 border-t border-edge py-16 sm:py-24">
      <div className="mx-auto max-w-6xl px-4">
        <p className="text-xs font-semibold uppercase tracking-wider text-subtle">Capabilities</p>
        <h2 className="display mt-3 max-w-3xl text-3xl font-medium text-fg sm:text-4xl">
          The engineering a tutorial clone can&apos;t give you.
        </h2>
        <div className="mt-12 grid grid-cols-1 gap-5 md:grid-cols-2 lg:grid-cols-3">
          {CAPABILITIES.map((c) => (
            <Link
              key={c.title}
              href={c.href}
              className="group flex flex-col rounded-2xl border border-edge bg-panel p-6 transition-colors hover:border-fg/30"
            >
              <h3 className="text-lg font-semibold text-fg">{c.title}</h3>
              <p className="mt-3 flex-1 text-sm leading-relaxed text-muted">{c.body}</p>
              <span className="mt-5 inline-flex items-center gap-1.5 text-sm font-medium text-fg">
                {c.cta}
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden className="transition-transform group-hover:translate-x-0.5">
                  <path d="M5 12h14M13 6l6 6-6 6" />
                </svg>
              </span>
            </Link>
          ))}
        </div>
      </div>
    </section>
  );
}

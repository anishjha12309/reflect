import type { Metadata } from "next";
import Link from "next/link";

export const metadata: Metadata = {
  title: "Architecture — Reflect",
  description: "How Reflect's multi-agent research pipeline is wired, layer by layer.",
};

// A flow row in the layered diagram.
function Layer({
  label,
  title,
  children,
}: {
  label: string;
  title: string;
  children?: React.ReactNode;
}) {
  return (
    <div className="rounded-2xl border border-edge bg-panel p-6">
      <p className="text-xs font-semibold uppercase tracking-wider text-subtle">{label}</p>
      <h3 className="display mt-1 text-xl font-medium text-fg">{title}</h3>
      {children && <div className="mt-4">{children}</div>}
    </div>
  );
}

function Node({ title, sub }: { title: string; sub: string }) {
  return (
    <div className="rounded-xl border border-edge bg-ink p-4">
      <p className="font-medium text-fg">{title}</p>
      <p className="mt-1 text-sm text-muted">{sub}</p>
    </div>
  );
}

function Arrow() {
  return (
    <div className="flex justify-center py-2 text-subtle" aria-hidden>
      <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
        <path d="M12 5v14M6 13l6 6 6-6" />
      </svg>
    </div>
  );
}

const AGENTS = [
  ["Planner", "Topic → sub-question DAG + HITL approval"],
  ["Search", "OpenAlex (scholarly abstracts) → Tavily → Serper → SearXNG fallback chain"],
  ["Reader", "Fetch + clean (trafilatura) + dedup cache; uses OpenAlex abstract directly when present"],
  ["Summarizer", "Per-source structured notes"],
  ["Synthesizer", "Long-context, cited report"],
  ["Critic", "Gap + contradiction detection → re-search; surfaces honest 'unavailable' state when reasoning providers are exhausted"],
];

const PROVIDERS = [
  ["Cerebras", "Short, high-volume tasks · 8K context cap"],
  ["Groq", "Mid reasoning · planner & critic"],
  ["Gemini", "Final long-context synthesis · up to 1M"],
  ["SambaNova", "Reliable-JSON fallback (summarize / reasoning)"],
  ["Mistral", "Reasoning / overflow fallback"],
];

export default function ArchitecturePage() {
  return (
    <main className="mx-auto max-w-5xl px-4 py-16 sm:py-24">
      <header className="max-w-3xl">
        <p className="text-xs font-semibold uppercase tracking-wider text-subtle">Architecture</p>
        <h1 className="display mt-3 text-4xl font-medium text-fg sm:text-5xl">
          Open-web research as a planned, parallel, self-correcting graph.
        </h1>
        <p className="mt-6 text-lg text-muted">
          Every external call flows through one rate-limit-aware gateway. Agents never touch a
          provider SDK directly — they ask the router, which picks a provider by task, context
          window, and live remaining quota, and fails over on rate limits.
        </p>
      </header>

      <div className="mt-14 space-y-1">
        <Layer label="Client" title="Next.js frontend (Vercel)">
          <p className="text-sm text-muted">
            Streams the run over SSE via <code className="rounded bg-raised px-1 py-0.5 font-mono text-xs text-fg">fetch</code> +{" "}
            <code className="rounded bg-raised px-1 py-0.5 font-mono text-xs text-fg">ReadableStream</code>: plan, live activity,
            the report with clickable <code className="rounded bg-raised px-1 py-0.5 font-mono text-xs text-fg">[n]</code> citations, and the{" "}
            <Link href="/metrics" className="text-fg underline decoration-edge underline-offset-2 hover:decoration-fg">quota dashboard</Link>.
          </p>
        </Layer>

        <Arrow />

        <Layer label="Orchestrator" title="FastAPI + LangGraph StateGraph">
          <p className="text-sm text-muted">
            A serializable <code className="rounded bg-raised px-1 py-0.5 font-mono text-xs text-fg">ResearchState</code> is the single
            source of truth — topic, plan, tasks, sources, notes, draft, critic feedback, round, and the
            quota ledger. Conditional edges drive the critic re-search loop and human-in-the-loop approval.
          </p>
        </Layer>

        <Arrow />

        <Layer label="Agents" title="Six specialised workers, bounded parallelism">
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
            {AGENTS.map(([t, s]) => (
              <Node key={t} title={t} sub={s} />
            ))}
          </div>
          <p className="mt-4 text-sm text-muted">
            Independent sub-queries run concurrently via <code className="rounded bg-raised px-1 py-0.5 font-mono text-xs text-fg">asyncio.gather</code>{" "}
            behind a bounded semaphore — never unbounded fan-out into rate-limited providers.
          </p>
        </Layer>

        <Arrow />

        <Layer label="Gateway" title="core/llm_router.py">
          <p className="text-sm text-muted">
            Provider selection by (task type, needed context, remaining quota), 429/5xx failover with
            exponential backoff, a per-provider circuit breaker with half-open probes, token-per-minute
            (TPM)-aware failover that skips providers lacking token headroom (e.g. Groq&apos;s 6K TPM) before
            a 429 is ever triggered, and a token/quota ledger persisted to SQLite. A pre-flight token count
            keeps oversize prompts off Cerebras&apos; 8K cap.
          </p>
        </Layer>

        <Arrow />

        <Layer label="Providers" title="Five no-card free LLM tiers · four search sources">
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-5">
            {PROVIDERS.map(([t, s]) => (
              <Node key={t} title={t} sub={s} />
            ))}
          </div>
          <p className="mt-4 text-sm text-muted">
            Search: <span className="text-fg">OpenAlex</span> (scholarly primary — peer-reviewed abstracts, no key, paywall-dodging) →{" "}
            <span className="text-fg">Tavily</span> → <span className="text-fg">Serper</span> → <span className="text-fg">SearXNG</span>{" "}
            (unlimited self-hosted fallback). All no-card, $0.
          </p>
        </Layer>
      </div>

      <div className="mt-12 flex flex-wrap gap-3">
        <Link href="/#start" className="rounded-full bg-accent px-6 py-3 text-sm font-medium text-accent-fg transition-opacity hover:opacity-90">
          Start a research run
        </Link>
        <Link href="/#how-it-works" className="rounded-full border border-edge px-6 py-3 text-sm font-medium text-fg transition-colors hover:bg-raised">
          See the workflow
        </Link>
      </div>
    </main>
  );
}

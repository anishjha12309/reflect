// The agent pipeline (CLAUDE.md §3). Anchored at #how-it-works so the navbar,
// footer, and the "See how it works" CTA all land here.
const STEPS: { n: string; agent: string; role: string }[] = [
  {
    n: "01",
    agent: "Planner",
    role: "Decomposes your topic into 3–7 atomic sub-questions and a dependency DAG, pausing for optional human approval.",
  },
  {
    n: "02",
    agent: "Search",
    role: "Queries each leaf sub-question across a Tavily → Serper → SearXNG fallback chain for ranked sources.",
  },
  {
    n: "03",
    agent: "Reader",
    role: "Fetches and cleans page text with trafilatura, deduping against a semantic cache to conserve free-tier quota.",
  },
  {
    n: "04",
    agent: "Summarizer",
    role: "Distills each source into structured notes — a claim, its evidence, and the source id it came from.",
  },
  {
    n: "05",
    agent: "Synthesizer",
    role: "Merges the notes into a sectioned, long-context report with inline [n] citations mapped to a sources table.",
  },
  {
    n: "06",
    agent: "Critic",
    role: "Scores coverage and flags cross-source contradictions, triggering a bounded second research round when needed.",
  },
];

export default function WorkflowSection() {
  return (
    <section id="how-it-works" className="scroll-mt-24 border-t border-edge py-16 sm:py-24">
      <div className="mx-auto max-w-6xl px-4">
        <p className="text-xs font-semibold uppercase tracking-wider text-subtle">How it works</p>
        <h2 className="display mt-3 max-w-3xl text-3xl font-medium text-fg sm:text-4xl">
          Six specialised agents, run as a parallel graph — not a linear chain.
        </h2>
        <div className="mt-12 grid grid-cols-1 gap-px overflow-hidden rounded-2xl border border-edge bg-edge sm:grid-cols-2 lg:grid-cols-3">
          {STEPS.map((s) => (
            <div key={s.n} className="bg-ink p-6">
              <div className="flex items-baseline gap-3">
                <span className="font-mono text-sm text-subtle">{s.n}</span>
                <h3 className="text-lg font-semibold text-fg">{s.agent}</h3>
              </div>
              <p className="mt-3 text-sm leading-relaxed text-muted">{s.role}</p>
            </div>
          ))}
        </div>
      </div>
    </section>
  );
}

import type { SubQuestion } from "@/lib/types";

// The research plan with a human Approve / Edit gate (CLAUDE.md §3 HITL).
// NOTE: with the backend's require_approval off, the stream runs straight through,
// so "Approve" here acknowledges the plan and "Edit" returns to the topic box.
// Full pause-and-resume needs the backend interrupt + a /research/resume endpoint.
export default function PlanPanel({ plan, onEditTopic }: { plan: SubQuestion[]; onEditTopic: () => void }) {
  return (
    <section className="rounded-xl border border-edge bg-panel p-4">
      <h2 className="mb-3 text-xs font-semibold uppercase tracking-wider text-subtle">Research plan</h2>
      <ol className="space-y-2">
        {plan.map((q) => (
          <li key={q.id} className="rounded-lg border border-edge bg-ink p-2.5 text-sm text-fg">
            <span className="mr-2 font-mono text-xs text-muted">{q.id}</span>
            {q.question}
            {q.depends_on.length > 0 && (
              <span className="ml-2 text-xs text-subtle">after {q.depends_on.join(", ")}</span>
            )}
          </li>
        ))}
      </ol>
      <div className="mt-3 flex gap-2">
        <button className="rounded-full border border-edge px-3 py-1 text-xs text-muted transition-colors hover:bg-raised" onClick={onEditTopic}>
          Edit topic
        </button>
      </div>
    </section>
  );
}

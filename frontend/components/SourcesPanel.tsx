import type { Source } from "@/lib/types";

// Citation-grade source attribution. Each entry has id="source-n" so the inline
// [n] links in the report scroll here.
export default function SourcesPanel({ sources }: { sources: Source[] }) {
  return (
    <div className="mt-6 border-t border-edge pt-4">
      <h3 className="mb-2 text-xs font-semibold uppercase tracking-wider text-subtle">Sources</h3>
      <ol className="space-y-1.5 text-sm">
        {sources.map((s) => (
          <li key={s.n} id={`source-${s.n}`} className="rounded px-1 target:bg-raised">
            <span className="mr-2 font-mono text-xs text-muted">[{s.n}]</span>
            <a
              href={s.url}
              target="_blank"
              rel="noopener noreferrer"
              className="text-fg underline decoration-edge underline-offset-2 hover:decoration-fg"
            >
              {s.title}
            </a>
            <span className="ml-2 text-xs text-subtle">{hostOf(s.url)}</span>
          </li>
        ))}
      </ol>
    </div>
  );
}

function hostOf(url: string): string {
  try {
    return new URL(url).hostname;
  } catch {
    return "";
  }
}

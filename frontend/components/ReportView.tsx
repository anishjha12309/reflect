"use client";

import { useMemo } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { downloadMarkdown, parseReport } from "@/lib/report";
import SourcesPanel from "./SourcesPanel";

interface Props {
  report: string;
  topic: string;
  partial: boolean;
  streaming: boolean;
}

export default function ReportView({ report, topic, partial, streaming }: Props) {
  const { body, sources } = useMemo(() => parseReport(report), [report]);
  const hasContent = report.trim().length > 0;

  return (
    <section className="print-root rounded-xl border border-edge bg-panel p-5">
      <div className="mb-4 flex items-center justify-between no-print">
        <h2 className="text-xs font-semibold uppercase tracking-wider text-subtle">
          Report {streaming && <span className="ml-2 animate-pulse text-fg">streaming…</span>}
        </h2>
        {hasContent && (
          <div className="flex gap-2">
            <button
              className="rounded-full border border-edge px-3 py-1 text-xs text-muted transition-colors hover:bg-raised"
              onClick={() => downloadMarkdown(report, topic)}
            >
              Download .md
            </button>
            <button
              className="rounded-full border border-edge px-3 py-1 text-xs text-muted transition-colors hover:bg-raised"
              onClick={() => window.print()}
            >
              Download PDF
            </button>
          </div>
        )}
      </div>

      {partial && (
        <div className="mb-4 rounded-lg border border-amber-300 bg-amber-50 px-3 py-2 text-sm text-amber-700 dark:border-amber-900 dark:bg-amber-950/30 dark:text-amber-300">
          ⚠ This report is <strong>incomplete</strong> — some providers or sources were unavailable.
        </div>
      )}

      {!hasContent ? (
        <p className="text-sm text-subtle no-print">The report will appear here as it is synthesized.</p>
      ) : (
        <div className="report max-w-none">
          <ReactMarkdown remarkPlugins={[remarkGfm]}>{body}</ReactMarkdown>
        </div>
      )}

      {sources.length > 0 && <SourcesPanel sources={sources} />}
    </section>
  );
}

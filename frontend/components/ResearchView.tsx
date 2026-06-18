"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { pingHealth, streamResearch } from "@/lib/stream";
import type {
  Contradiction,
  QuotaProvider,
  ServerEvent,
  StreamStatus,
  SubQuestion,
} from "@/lib/types";
import PlanPanel from "./PlanPanel";
import ActivityLog, { Activity } from "./ActivityLog";
import ReportView from "./ReportView";
import QuotaStrip from "./QuotaStrip";
import ContradictionFlags from "./ContradictionFlags";

export default function ResearchView() {
  const [topic, setTopic] = useState("");
  const [status, setStatus] = useState<StreamStatus>("idle");
  const [error, setError] = useState<string | null>(null);

  const [plan, setPlan] = useState<SubQuestion[]>([]);
  const [activity, setActivity] = useState<Activity[]>([]);
  const [report, setReport] = useState("");
  const [quota, setQuota] = useState<QuotaProvider[]>([]);
  const [contradictions, setContradictions] = useState<Contradiction[]>([]);
  const [partial, setPartial] = useState(false);

  const abortRef = useRef<AbortController | null>(null);
  const lastTopicRef = useRef("");

  // Abort any in-flight stream if the user navigates away.
  useEffect(() => () => abortRef.current?.abort(), []);

  const pushActivity = useCallback((message: string, tone: Activity["tone"] = "info") => {
    setActivity((prev) => [...prev, { message, tone, ts: Date.now() }]);
  }, []);

  const handleEvent = useCallback(
    (ev: ServerEvent) => {
      if (status !== "streaming") setStatus("streaming");
      switch (ev.event) {
        case "plan_ready":
          setPlan(ev.data.sub_questions);
          pushActivity(`Planned ${ev.data.tasks} sub-question(s).`);
          break;
        case "task_done":
          pushActivity(`Researched ${ev.data.id} — found ${ev.data.sources} source(s).`);
          break;
        case "task_empty":
          pushActivity(`No usable sources for ${ev.data.id} (${ev.data.reason}).`, "warn");
          break;
        case "notes_ready":
          pushActivity(`Extracted notes — ${ev.data.total} total.`);
          break;
        case "round_started":
          pushActivity(`Critic requested more — starting round ${ev.data.round}.`, "warn");
          break;
        case "draft_ready":
          pushActivity(ev.data.partial ? "Draft assembled (partial)." : "Draft assembled.");
          break;
        case "critic_verdict":
          pushActivity(
            `Critic: coverage ${(ev.data.coverage * 100).toFixed(0)}% — ${ev.data.approved ? "approved" : "needs more"}.`,
            ev.data.approved ? "ok" : "warn",
          );
          if (ev.data.contradictions?.length) setContradictions(ev.data.contradictions);
          break;
        case "critic_unavailable":
          pushActivity("Critic unavailable — all reasoning providers were rate-limited; report finished without a quality check.", "warn");
          break;
        case "critic_skipped":
          pushActivity("Critic skipped (partial report).", "warn");
          break;
        case "quota_update":
          setQuota(ev.data);
          break;
        case "report_chunk":
          setReport((prev) => prev + ev.data.text);
          break;
        case "done":
          setPartial(ev.data.partial);
          setStatus("done");
          pushActivity(ev.data.partial ? "Done — partial report." : "Done.", ev.data.partial ? "warn" : "ok");
          break;
        case "error":
          setError(ev.data.message);
          setStatus("error");
          break;
        default:
          break;
      }
    },
    [pushActivity, status],
  );

  const start = useCallback(
    async (t: string) => {
      const trimmed = t.trim();
      if (!trimmed) return;
      lastTopicRef.current = trimmed;

      // reset
      abortRef.current?.abort();
      const controller = new AbortController();
      abortRef.current = controller;
      setError(null);
      setPlan([]);
      setActivity([]);
      setReport("");
      setQuota([]);
      setContradictions([]);
      setPartial(false);
      setStatus("warming");

      // Cold-start handling: the HF Space may be asleep.
      const awake = await pingHealth(controller.signal);
      if (!awake) pushActivity("Backend is waking up (cold start)…", "warn");

      try {
        await streamResearch(trimmed, { onEvent: handleEvent, signal: controller.signal });
        setStatus((s) => (s === "error" ? s : "done"));
      } catch (e) {
        if (controller.signal.aborted) return; // user navigated/cancelled — not an error
        setError(e instanceof Error ? e.message : "Stream failed");
        setStatus("error");
      }
    },
    [handleEvent, pushActivity],
  );

  const busy = status === "warming" || status === "streaming";

  return (
    <div className="space-y-6">
      <form
        className="flex flex-col gap-2 no-print sm:flex-row"
        onSubmit={(e) => {
          e.preventDefault();
          start(topic);
        }}
      >
        <input
          className="w-full min-w-0 rounded-full border border-edge bg-panel px-5 py-3 text-fg placeholder:text-subtle outline-none transition-colors focus:border-accent sm:flex-1"
          placeholder="Enter a research topic…"
          value={topic}
          onChange={(e) => setTopic(e.target.value)}
          disabled={busy}
        />
        {busy ? (
          <button
            type="button"
            className="w-full shrink-0 rounded-full border border-edge px-6 py-3 text-fg transition-colors hover:bg-raised sm:w-auto"
            onClick={() => {
              abortRef.current?.abort();
              setStatus("idle");
              pushActivity("Cancelled.", "warn");
            }}
          >
            Stop
          </button>
        ) : (
          <button
            type="submit"
            className="w-full shrink-0 rounded-full bg-accent px-6 py-3 font-medium text-accent-fg transition-opacity hover:opacity-90 sm:w-auto"
          >
            Research
          </button>
        )}
      </form>

      {status === "warming" && (
        <div className="rounded-xl border border-edge bg-panel px-4 py-3 text-sm text-muted no-print">
          Warming up the backend…
        </div>
      )}

      {status === "error" && (
        <div className="flex items-center justify-between rounded-xl border border-red-300 bg-red-50 px-4 py-3 text-sm text-red-700 no-print dark:border-red-900 dark:bg-red-950/40 dark:text-red-300">
          <span>Stream error: {error}</span>
          <button
            className="rounded-full border border-red-300 px-3 py-1 hover:bg-red-100 dark:border-red-800 dark:hover:bg-red-900/40"
            onClick={() => start(lastTopicRef.current)}
          >
            Retry
          </button>
        </div>
      )}

      {quota.length > 0 && <QuotaStrip quota={quota} />}

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-3">
        <div className="space-y-6 lg:col-span-1 no-print">
          {plan.length > 0 && <PlanPanel plan={plan} onEditTopic={() => setStatus("idle")} />}
          <ActivityLog activity={activity} />
        </div>
        <div className="lg:col-span-2">
          <ContradictionFlags contradictions={contradictions} />
          <ReportView report={report} topic={lastTopicRef.current} partial={partial} streaming={status === "streaming"} />
        </div>
      </div>
    </div>
  );
}

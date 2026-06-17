// Typed mirror of the backend SSE event payloads (backend/app.py + graph/orchestrator.py).

export interface SubQuestion {
  id: string;
  question: string;
  depends_on: string[];
}

export interface Contradiction {
  claim_a: string;
  claim_b: string;
  explanation: string;
}

export interface QuotaProvider {
  provider: string;
  requests_used: number;
  requests_limit: number | null;
  requests_remaining: number | null;
  tokens_used: number;
  tokens_limit: number | null;
  tokens_remaining: number | null;
  exhausted: boolean;
}

export interface Source {
  n: number;
  title: string;
  url: string;
}

// Discriminated union of everything the stream can emit.
export type ServerEvent =
  | { event: "plan_ready"; data: { tasks: number; sub_questions: SubQuestion[] } }
  | { event: "task_done"; data: { id: string; sources: number } }
  | { event: "task_empty"; data: { id: string; reason: string } }
  | { event: "notes_ready"; data: { total: number } }
  | { event: "round_started"; data: { round: number; followups: number } }
  | { event: "draft_ready"; data: { partial: boolean } }
  | { event: "critic_verdict"; data: { coverage: number; approved: boolean; contradictions: Contradiction[]; followups: string[] } }
  | { event: "critic_skipped"; data: { reason: string } }
  | { event: "quota_update"; data: QuotaProvider[] }
  | { event: "report_chunk"; data: { text: string } }
  | { event: "done"; data: { partial: boolean } }
  | { event: "error"; data: { message: string } };

export type StreamStatus = "idle" | "warming" | "streaming" | "done" | "error";

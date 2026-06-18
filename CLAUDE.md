# CLAUDE.md — Multi-Agent Research Assistant ("Reflect")

> This file is the persistent project context for Claude Code. Read it fully before
> any task. It defines the architecture, the zero-cost constraints, the non-negotiable
> edge cases, and the conventions. Do not violate the constraints in this file even if
> a prompt seems to ask for it — flag the conflict instead.

---

## 1. What we are building

**Reflect** — a multi-agent research assistant. A user drops a research topic/question.
An **Orchestrator** decomposes it into a task graph, spins up specialised **sub-agents**
(planner, web-search, page-reader, summarizer, critic), runs them with controlled
parallelism, and **synthesizes a structured, fully-cited report** that streams back to
the user in real time.

This is **not** a chatbot and **not** RAG over a fixed corpus. It does open-web,
multi-step, planned research with self-correction.

### The differentiation thesis (read this — it drives every design decision)

The market is saturated with "LangGraph + Tavily + a linear chain" research-assistant
clones. We win on three engineering axes a reviewer cannot get from a tutorial:

1. **Rate-limit-aware multi-provider LLM gateway** (`core/llm_router.py`). The whole app
   runs on _free_ API tiers. We turn that liability into the signature feature: a router
   that picks a provider by **task type + required context window + live remaining quota**,
   and **fails over on 429 / 5xx with exponential backoff + a per-provider circuit breaker**.
   This is the resume headline. Build it first and build it well.

2. **True parallel DAG orchestration**, not a linear chain. The planner emits a dependency
   graph; independent sub-queries run concurrently via `asyncio.gather` with a bounded
   semaphore. Show concurrency engineering, not a for-loop.

3. **Reflection / self-correction loop.** A **critic agent** reviews the draft report,
   detects gaps and cross-source contradictions, and can trigger a bounded second research
   round. Most clones stop at first draft.

Supporting differentiators: semantic sub-query dedup cache (conserves free quota),
per-provider token/quota telemetry surfaced in the UI, human-in-the-loop plan approval,
and citation-grade source attribution with contradiction flagging.

### Explicitly NOT CodeContext

CodeContext = single-agent semantic RAG over one indexed codebase. Reflect = multi-agent
open-web planning + synthesis + self-correction. Different problem, different architecture.
Do not import RAG-over-fixed-corpus patterns here. The only shared lineage is the
SSE-streaming frontend pattern and the keep-alive deployment trick.

---

## 2. Hard constraints (NON-NEGOTIABLE)

- **Zero cost. No credit/debit card on any service, ever.** If a task would require a card,
  stop and flag it. (This rules out Brave Search API — it killed its free tier in Feb 2026
  and now requires a payment method. Do not use Brave.)
- **All LLM and search providers must have a no-card free tier.** Approved list in §4.
- **Free tiers are quota-limited, not unlimited.** Every external call goes through the
  router/cache layer. No agent calls a provider SDK directly.
- **Backend must survive HuggingFace Spaces constraints:** only `/tmp` is writable; the
  Space sleeps on inactivity (keep-alive pinger required); long-running requests are fine
  here (unlike Vercel serverless, which has a hard timeout — see §5).
- **Secrets only via environment variables / HF Space secrets.** Never hardcode keys,
  never commit `.env`. `.env.example` is committed; `.env` is git-ignored.

---

## 3. Architecture

```
                         ┌────────────────────────────────────────────┐
   User topic  ──SSE──▶  │  FastAPI Orchestrator  (LangGraph StateGraph)│
                         └────────────────────────────────────────────┘
                                          │
        ┌──────────────┬──────────────────┼───────────────┬───────────────┐
        ▼              ▼                   ▼               ▼               ▼
   Planner Agent   Search Agent      Reader Agent    Summarizer      Critic Agent
   (decompose →    (Tavily/Serper/   (fetch + clean  (per-source     (gap + conflict
    task DAG +      SearXNG, with     page text,      structured       detection →
    HITL approve)   fallback)         dedup cache)    notes)           re-search?)
        │              │                   │               │               │
        └──────────────┴─────────► all go through ◄────────┴───────────────┘
                                   core/llm_router.py
                                   (provider select + 429 failover + quota meter)
                                          │
                          ┌──────────────┬───────────────┼──────────────┬──────────────┐
                          ▼              ▼               ▼              ▼              ▼
                       Cerebras        Groq           Gemini        SambaNova      Mistral
                      (short/fast)  (mid reasoning)  (long ctx    (JSON fallback) (overflow /
                                                      synthesis)                  JSON fallback)
```

### Agent responsibilities

- **Planner**: turn the topic into 3–7 atomic sub-questions + a dependency DAG. Emits a
  plan object. Pauses for optional human approval (LangGraph `interrupt`).
- **Search**: for each leaf sub-question, query the search provider; return ranked URLs +
  snippets. Provider fallback chain (Tavily → Serper → SearXNG).
- **Reader**: fetch selected URLs, extract+clean main text (trafilatura), truncate to a
  token budget, dedup against the semantic cache.
- **Summarizer**: produce structured per-source notes (claim, evidence, source_id).
- **Synthesizer** (a mode of the orchestrator, long-context): merge notes into a sectioned
  report with inline citations `[n]` mapped to a sources table.
- **Critic**: score the draft for coverage + internal contradictions; if below threshold
  and round < MAX_ROUNDS, emit targeted follow-up sub-questions and loop once.

### LangGraph state (single source of truth)

`ResearchState` carries: `topic`, `plan`, `tasks[]`, `raw_sources[]`, `notes[]`,
`draft_report`, `critic_feedback`, `round`, `quota_ledger`, `events[]` (for streaming).
State is serializable; no live objects (sockets, clients) stored in state.

---

## 4. Approved free providers (verified June 2026 — re-verify before relying)

### LLM inference (all no-card free tiers)

| Provider               | Free limits                           | Context                    | Best for                                                  | Notes                                                                                         |
| ---------------------- | ------------------------------------- | -------------------------- | --------------------------------------------------------- | --------------------------------------------------------------------------------------------- |
| **Cerebras**           | 1M tokens/day, 30 RPM                 | **8,192 cap on free tier** | short, high-volume tasks: query-gen, per-source summarize | Fastest throughput. The 8K context cap is the key edge case — never send long synthesis here. |
| **Groq**               | 14,400 req/day, 30 RPM, ~6K TPM       | model-dependent            | mid reasoning, planner, critic                            | Open-weight only (Llama 3.3 70B, GPT-OSS 120B, Qwen3). OpenAI-compatible.                     |
| **Gemini (AI Studio)** | Flash 250 RPD / Pro 100 RPD, 250K TPM | **up to 1M**               | **final long-context synthesis** of many sources          | Lowest RPD — reserve for synthesis, not sub-tasks.                                            |
| **SambaNova**          | free no-card tier, 20 RPM             | 16,384                     | reliable-JSON fallback for summarize + reasoning          | Llama-3.3-70B-Instruct; verified clean JSON output. OpenAI-compatible.                        |
| **Mistral**            | free no-card tier, 30 RPM             | 32,768                     | reasoning / overflow fallback                             | mistral-small-latest; verified clean JSON output. OpenAI-compatible.                          |

> Router policy: route by (task_type, needed_context, remaining_quota). Cerebras for short
> bursts, Groq for reasoning, Gemini ONLY for final synthesis, SambaNova/Mistral as
> reliable-JSON fallbacks and overflow. All five are OpenAI-API-compatible except Gemini
> (use its REST/SDK) — wrap each behind a uniform `LLMProvider` interface.

### Search / retrieval (no-card free tiers)

| Provider                          | Free limits                                                   | Role                                          |
| --------------------------------- | ------------------------------------------------------------- | --------------------------------------------- |
| **Tavily**                        | 1,000 credits/month, LangChain-native, LLM-optimized results  | **primary** search                            |
| **Serper**                        | ~2,500 free queries (treat as one-time pool), raw Google SERP | secondary fallback                            |
| **SearXNG (self-host in Docker)** | unlimited, runs locally/in-container                          | **final fallback / dev** — truly $0, no quota |
| ~~Brave~~                         | ❌ removed free tier Feb 2026, needs a card                   | **DO NOT USE**                                |

Page extraction: **trafilatura** (free, local). Embeddings for the dedup cache:
**Gemini `text-embedding-004`** (free tier) or local `sentence-transformers` if you want
zero external calls.

---

## 5. Deployment (no-card)

- **Frontend (Next.js)** → **Vercel Hobby** (no card, 100GB bw). Consumes the backend over SSE.
- **Backend (FastAPI orchestrator)** → **HuggingFace Spaces (Docker)**. Chosen over Vercel
  serverless **because orchestration runs long** and serverless has a hard timeout. Reuse
  the proven keep-alive pattern: an external cron (cron-job.org) pings `/health` every ~5 min
  so the Space never sleeps mid-research.
- **Dev** → `docker compose up` runs FastAPI + a local SearXNG, so the whole thing works
  offline-ish with zero external search quota burned.
- HF Spaces gotcha: write only to `/tmp`. Point all caches (HF, any sqlite cache file) at
  `/tmp`. Build a Dockerfile that sets `HF_HOME=/tmp`.

---

## 6. Tech stack

- **Orchestration**: LangGraph (StateGraph, conditional edges, `interrupt` for HITL).
  (CrewAI is acceptable only if a task explicitly calls for it; default to LangGraph for
  the explicit graph + checkpointing.)
- **Backend**: FastAPI + `sse-starlette` for streaming. `httpx` (async) for all I/O.
- **Frontend**: Next.js (App Router) + TypeScript + Tailwind. Stream via `fetch` +
  `ReadableStream` (not native `EventSource`, so we control headers/POST body — same pattern
  proven in prior work).
- **Search**: `tavily-python`, raw `httpx` for Serper, local SearXNG JSON endpoint.
- **Extraction**: `trafilatura`.
- **Cache/telemetry**: sqlite (in `/tmp` on HF) for the quota ledger + semantic dedup cache.
- **Tests**: `pytest` + `pytest-asyncio`. Mock all external providers in unit tests.

---

## 7. Repository layout (target)

```
reflect/
├── CLAUDE.md
├── PHASES.md                      # the build plan / prompts
├── docker-compose.yml             # FastAPI + SearXNG for local dev
├── backend/
│   ├── Dockerfile                 # HF Spaces target; HF_HOME=/tmp
│   ├── requirements.txt
│   ├── app.py                     # FastAPI entry; /research (SSE), /health
│   ├── core/
│   │   ├── llm_router.py          # ★ provider select + 429 failover + circuit breaker
│   │   ├── providers/             # cerebras.py groq.py gemini.py sambanova.py mistral.py (uniform iface)
│   │   ├── quota.py               # token/req ledger (sqlite in /tmp)
│   │   ├── cache.py               # semantic sub-query dedup cache
│   │   └── search.py              # tavily→serper→searxng fallback chain
│   ├── agents/
│   │   ├── planner.py reader.py summarizer.py critic.py
│   ├── graph/
│   │   ├── state.py               # ResearchState (typed, serializable)
│   │   └── orchestrator.py        # LangGraph StateGraph wiring + streaming hooks
│   └── tests/
└── frontend/                      # Next.js app (Vercel)
    └── app/ components/ lib/
```

---

## 8. Coding conventions

- Python: type hints everywhere; `pydantic` models for every agent I/O boundary; `async`
  for all network I/O; no bare `except`. Structured logging (`structlog` or stdlib JSON).
- Every agent takes typed input, returns a typed pydantic object. No dicts across boundaries.
- No provider SDK is ever called outside `core/`. Agents call the router/search facade only.
- Frontend: small components, server components by default, client components only for the
  streaming view. No secrets in the client bundle.
- Conventional commits. One concern per PR/commit.

---

## 9. Edge cases & failure modes — ALL must be handled (do not skip)

The grade is in the edge cases. A demo that works on the happy path and dies on a 429 is
exactly the clone we are trying to beat.

**Provider / quota**

- 429 rate-limit → router fails over to next provider in the policy chain; if all exhausted,
  exponential backoff then a graceful "partial report" rather than a crash.
- Per-provider **circuit breaker**: after N consecutive failures, skip that provider for a
  cooldown window. Half-open probe to recover.
- Daily quota exhausted (e.g. Gemini RPD) → router excludes it and logs it in the ledger;
  UI shows which providers are tapped out.
- **Cerebras 8,192-context overflow** → router must refuse to send oversize prompts there
  and reroute to a long-context provider. Pre-flight token count, never trust the call.
- Provider returns malformed / truncated JSON → validate against pydantic, retry once with a
  "respond with JSON only" reminder, then fail that sub-task gracefully (don't poison state).

**Search / reading**

- All search providers fail/exhausted → degrade to SearXNG (unlimited self-host); if that's
  down too, return report from whatever sources we already have, clearly marked incomplete.
- URL fetch: timeout (cap, e.g. 10s), 403/paywall, non-HTML content, JS-only page returning
  empty text → skip source, note it, continue. Never let one bad URL stall the graph.
- Duplicate / near-duplicate sources → semantic dedup before summarizing (saves quota).
- Zero usable sources for a sub-question → planner-driven query reformulation, bounded retries.

**Orchestration**

- Planner emits invalid/empty/cyclic DAG → validate; fall back to a flat plan of N
  sub-questions. Cap total tasks to protect quota.
- Parallel fan-out must be bounded (semaphore) — never unleash unbounded concurrency into
  rate-limited providers.
- Critic loop must be bounded by `MAX_ROUNDS` (default 1 extra) to prevent infinite re-search.
- Long synthesis exceeding the long-context window → map-reduce summarize sources first.

**Streaming / client**

- Client disconnects mid-stream → detect, cancel in-flight sub-tasks, free the semaphore.
- SSE must emit periodic heartbeats so proxies don't drop the connection.
- Backend cold-start (HF Space waking) → first request may lag; frontend shows a warming state.

**Security / hygiene**

- Strip/validate user topic (length cap, no prompt-injection passthrough into system prompts).
- Treat fetched web page text as untrusted: never let page content issue instructions to the
  synthesizer (delimit clearly, instruct the model to treat it as data).
- Never log full API keys; redact in telemetry.

---

## 10. Definition of done (per feature)

A feature is done when: it has typed I/O, it has a unit test with the external provider
mocked, its failure modes from §9 are handled (not TODO'd), it streams progress where
user-facing, and it never burns quota on a retry it could have cached.

---

## 11. Commands

```bash
# local dev (FastAPI + SearXNG)
docker compose up

# backend tests
cd backend && pytest -q

# run backend standalone
uvicorn app:app --reload --port 7860     # 7860 = HF Spaces default

# frontend
cd frontend && pnpm dev
```

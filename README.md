# Reflect 🔭

A **multi-agent research assistant**. Drop in a topic; an orchestrator decomposes it into a
dependency graph of sub-questions, runs specialised sub-agents (planner, search, reader,
summarizer, critic) with bounded parallelism, and **synthesizes a structured, fully-cited
report that streams back in real time** — with a critic-driven self-correction loop.

Runs on a **fully zero-cost, no-credit-card** stack. The engineering headline is a
**rate-limit-aware multi-provider LLM gateway** that turns free-tier quota limits into the
signature feature.

> 🎬 **30-second demo**
>
> ![Reflect demo](docs/demo.gif)
>
> _(Drop a screen recording at `docs/demo.gif`.)_

---

## Why this isn't another LangGraph+Tavily clone

Three engineering axes a reviewer can't get from a tutorial:

1. **Rate-limit-aware multi-provider LLM gateway** ([`backend/core/llm_router.py`](backend/core/llm_router.py)) —
   picks a provider by _task type + required context window + live remaining quota_, and
   **fails over on 429/5xx with exponential backoff + a per-provider circuit breaker**.
   ([Read this first](#how-the-router-fails-over).)
2. **True parallel DAG orchestration**, not a linear chain — the planner emits a dependency
   graph; independent sub-queries run concurrently behind a bounded semaphore.
3. **Reflection / self-correction loop** — a critic agent reviews the draft, detects gaps
   and cross-source contradictions, and triggers a bounded second research round.

Supporting: semantic sub-query dedup cache (conserves quota), per-provider quota telemetry
in the UI, citation-grade attribution with contradiction flagging.

---

## Architecture

```
                          ┌────────────────────────────────────────────┐
   User topic  ──SSE──▶   │  FastAPI Orchestrator  (LangGraph StateGraph)│
   (POST /research)       └────────────────────────────────────────────┘
                                           │
        ┌──────────────┬──────────────────┼───────────────┬───────────────┐
        ▼              ▼                   ▼               ▼               ▼
   Planner Agent   Search Agent      Reader Agent    Summarizer      Critic Agent
   (topic → DAG    (Tavily→Serper→   (fetch + clean  (per-source     (gap + conflict
    of sub-qs)      SearXNG)          page text)      notes)          detection → loop?)
        │              │                   │               │               │
        └──────────────┴────────► all LLM calls go through ◄───────────────┘
                                  core/llm_router.py
                          (provider select · 429 failover · circuit breaker · quota meter)
                                           │
                          ┌────────────────┼────────────────┬───────────────┐
                          ▼                ▼                ▼               ▼
                       Cerebras          Groq            Gemini        OpenRouter
                      (short/fast)    (reasoning)     (long synth)     (overflow)
```

Frontend (Next.js on Vercel) consumes `POST /research` over SSE via `fetch` +
`ReadableStream`, rendering the plan, a live activity log, the streamed report with
clickable `[n]` citations, a provider/quota strip, and inline contradiction flags.

---

## How the router fails over

> This is the part to read first — it's what makes Reflect different.

Every agent calls **only** `LLMRouter.complete(...)`; no agent ever touches a provider SDK.
For each call:

1. **Pre-flight token count.** The prompt is measured locally — we never trust the API to
   tell us we overflowed. Providers whose `max_context` is too small are excluded up front
   (e.g. a long synthesis is never sent to a short-context tier).
2. **Policy-ordered chain.** `pick(task_type, needed_context, ledger)` returns an ordered
   fallback chain by task type:

   | task_type        | chain (first viable wins)        |
   | ---------------- | -------------------------------- |
   | `short`          | Cerebras → Groq → OpenRouter      |
   | `reasoning`      | Groq → OpenRouter → Gemini        |
   | `long_synthesis` | Gemini → OpenRouter               |
   | `overflow`       | OpenRouter → Groq                 |

   …after removing providers that are context-too-small, **circuit-open**, or **out of
   daily quota** (per the sqlite ledger).
3. **Failover on `429`/`5xx`** → next provider in the chain.
4. **Per-provider circuit breaker.** After _N_ consecutive failures the breaker **opens**
   for a cooldown; a **half-open** probe lets it recover. Open breakers are skipped.
5. **Whole chain exhausted** → exponential backoff **with jitter**, capped retries, then a
   typed `AllProvidersExhausted` — callers degrade to a clearly-marked **partial report**
   instead of crashing.
6. **JSON mode** is validated against a pydantic schema; on a parse failure the router
   retries once with a "return JSON only" reminder, then fails over.
7. **Telemetry.** Every call records `(provider, task_type, prompt/completion tokens,
   success)` into the quota ledger; remaining-quota estimates are surfaced in the UI.

See the tests in [`backend/tests/test_router.py`](backend/tests/test_router.py) — happy
path, 429 failover, all-exhausted backoff, context-overflow exclusion, malformed-JSON
retry, and circuit-breaker open/half-open transitions.

---

## Free-tier providers (no card)

| Provider              | Model (current)              | Free limits                  | Context     | Role in router        |
| --------------------- | ---------------------------- | ---------------------------- | ----------- | --------------------- |
| **Cerebras**          | `gpt-oss-120b`               | 1M tokens/day, 30 RPM        | 65,536      | short / high-volume   |
| **Groq**              | `llama-3.3-70b-versatile`    | 14,400 RPD, 30 RPM, ~6K TPM  | 32,768      | reasoning             |
| **Gemini (AI Studio)**| `gemini-2.5-flash`           | 250 RPD, 250K TPM            | 1,000,000   | long-context synthesis|
| **OpenRouter**        | `openrouter/free`            | ~20 RPM, ~50 RPD             | 131,072     | overflow / last resort|

Search: **Tavily** (primary) → **Serper** (fallback) → **SearXNG** (self-host, unlimited).
Embeddings for the dedup cache: **Gemini `gemini-embedding-2`** (falls back to local
`sentence-transformers` if no Gemini key). _Re-verify limits before relying — free tiers move._

---

## Local development

```bash
# 0. Create your env file first (docker compose reads it)
cp .env.example .env      # then fill in whatever free-tier keys you have

# Backend + a local SearXNG (zero external search quota)
docker compose up

# Backend standalone
cd backend && pip install -r requirements.txt
uvicorn app:app --reload --port 7860      # 7860 = HF Spaces default

# Backend tests (all external providers mocked — no network)
cd backend && pytest -q

# Frontend
cd frontend && pnpm install && pnpm dev
```

Copy `.env.example` → `.env` and fill in whatever free-tier keys you have (the app routes
around the missing ones). **No keys ever live in the frontend bundle** — the browser only
ever sees `NEXT_PUBLIC_API_URL`.

---

## Deploy (no card)

### 1. Backend → HuggingFace Spaces (Docker SDK)

Push `backend/` to a Docker Space and set the secrets. Full instructions and the exact
secret names are in [`backend/README.md`](backend/README.md). The Dockerfile forces all
writes to `/tmp` (HF constraint).

### 2. Keep-alive → cron-job.org

Spaces sleep on inactivity. Create a free [cron-job.org](https://cron-job.org) job:

- **URL**: `https://<your-space>.hf.space/health`
- **Method**: `GET`
- **Schedule**: every **5 minutes**

This pings `/health` so the Space never sleeps mid-research.

### 3. Frontend → Vercel (Hobby, no card)

Import the repo on Vercel and set **Root Directory** = `frontend` (Next.js is
auto-detected). Add one environment variable:

- `NEXT_PUBLIC_API_URL` = `https://<your-space>.hf.space`

Then set `ALLOWED_ORIGINS` on the HF Space to your Vercel URL so CORS allows the browser
through. See [`frontend/README.md`](frontend/README.md).

---

## Repository layout

```
backend/      FastAPI orchestrator (LangGraph), the LLM router, agents, search, cache
  core/       llm_router.py · providers/ · quota.py · cache.py · search.py
  agents/     planner · reader · summarizer · critic
  graph/      state.py (ResearchState) · orchestrator.py (StateGraph + fan-out)
  tests/      pytest-asyncio, all providers mocked
frontend/     Next.js (App Router, TS, Tailwind) — streaming research UI
docker-compose.yml   FastAPI + SearXNG for local dev
CLAUDE.md     persistent project context / constraints
PHASES.md     the build plan
```

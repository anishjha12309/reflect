---
title: Reflect Backend
emoji: 🔭
colorFrom: indigo
colorTo: green
sdk: docker
app_port: 7860
pinned: false
---

# Reflect — Backend (HuggingFace Space, Docker SDK)

FastAPI orchestrator for the Reflect multi-agent research assistant. Deployed as a
**Docker SDK Space** because orchestration runs long and serverless platforms time out
(see root `README.md` and `CLAUDE.md` §5).

## Endpoints

| Method | Path        | Purpose                                                        |
| ------ | ----------- | -------------------------------------------------------------- |
| `GET`  | `/health`   | Liveness probe (target of the keep-alive cron).               |
| `POST` | `/research` | Runs the orchestrator and streams typed SSE events live.      |

## Deploy

1. Create a new Space → **Docker** → blank.
2. Push the contents of `backend/` to the Space repo root (this `README.md` with the
   YAML header above must sit at the repo root so HF builds the Dockerfile and exposes
   port 7860).
3. Add the secrets/variables below under **Settings → Variables and secrets**.

### Secrets (sensitive — "New secret")

You need **at least one LLM key**; the router uses whatever is present and routes around
the rest. Add a search key too, or rely on the SearXNG fallback.

| Secret name          | Provider    | Notes                                  |
| -------------------- | ----------- | -------------------------------------- |
| `CEREBRAS_API_KEY`   | Cerebras    | short / high-volume tasks              |
| `GROQ_API_KEY`       | Groq        | reasoning (planner, critic)            |
| `GEMINI_API_KEY`     | Gemini      | long-context synthesis + embeddings    |
| `SAMBANOVA_API_KEY`  | SambaNova   | reliable-JSON fallback (summarize/reasoning) |
| `MISTRAL_API_KEY`    | Mistral     | reasoning / overflow fallback          |
| `TAVILY_API_KEY`     | Tavily      | primary search                         |
| `SERPER_API_KEY`     | Serper      | secondary search fallback              |

### Variables (non-sensitive — "New variable")

| Variable name     | Example                                   | Notes                                          |
| ----------------- | ----------------------------------------- | ---------------------------------------------- |
| `ALLOWED_ORIGINS` | `https://reflect.vercel.app`            | CORS — your Vercel frontend origin(s), CSV.    |
| `SEARXNG_URL`     | `https://your-searxng.example`            | Optional final-fallback search; omit if none.  |

## `/tmp`-only writes (HF constraint)

The Space filesystem is read-only except `/tmp`. The `Dockerfile` forces **every** writable
path there — `HOME`, `TMPDIR`, `HF_HOME`, `XDG_CACHE_HOME`, and both sqlite ledgers
(`QUOTA_DB_PATH`, `CACHE_DB_PATH`). Nothing is written outside `/tmp`. Verify after deploy:

```bash
# in the Space "Logs" / a debug shell — these are the only app-written paths:
ls -la /tmp/reflect_quota.sqlite /tmp/reflect_cache.sqlite
```

## Keep-alive

Spaces sleep on inactivity. A `cron-job.org` job hits `GET /health` every 5 minutes so the
Space never sleeps mid-research. See root `README.md` → Deploy.

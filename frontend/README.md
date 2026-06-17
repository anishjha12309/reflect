# Reflect — Frontend (Next.js, Vercel)

Streaming research UI. Consumes the backend's `POST /research` over SSE via `fetch` +
`ReadableStream` (not `EventSource`, so we POST a body and set headers). Renders the plan,
a live activity log, the streamed report with clickable `[n]` citations → sources panel, a
provider/quota strip, and inline critic contradiction flags.

**No API keys live here.** The only configuration is the public backend URL; the browser
talks exclusively to our backend.

## Local dev

```bash
pnpm install
cp .env.example .env.local        # set NEXT_PUBLIC_API_URL=http://localhost:7860
pnpm dev                          # http://localhost:3000
```

## Deploy → Vercel (Hobby tier, no card)

1. Import the repository on [vercel.com](https://vercel.com).
2. **Root Directory** → `frontend` (Next.js is auto-detected; no build config needed).
3. **Environment Variables**:

   | Name                  | Value                            |
   | --------------------- | -------------------------------- |
   | `NEXT_PUBLIC_API_URL` | `https://<your-space>.hf.space`  |

4. Deploy. Then set `ALLOWED_ORIGINS` on the HF Space to your Vercel URL (e.g.
   `https://reflect.vercel.app`) so CORS lets the browser through.

## Export

The report can be downloaded as **Markdown** (Blob) or **PDF** (client-side browser print
with a print stylesheet that shows only the report).

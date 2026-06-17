# Reflect backend — HuggingFace Spaces (Docker) target.
# HF Spaces gotcha: only /tmp is writable. Point all caches there (CLAUDE.md §5).
FROM python:3.12-slim

# HF Spaces gotcha: only /tmp is writable at runtime. Force EVERY writable path there:
# HOME + TMPDIR (sqlite via tempfile, any stray home writes), HF/XDG caches, and the
# app's own sqlite ledgers (quota + dedup cache). Nothing outside /tmp is written.
ENV HF_HOME=/tmp
ENV XDG_CACHE_HOME=/tmp
ENV HOME=/tmp
ENV TMPDIR=/tmp
ENV QUOTA_DB_PATH=/tmp/reflect_quota.sqlite
ENV CACHE_DB_PATH=/tmp/reflect_cache.sqlite
ENV PIP_NO_CACHE_DIR=1
ENV PYTHONUNBUFFERED=1
ENV PORT=7860

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 7860

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "7860"]

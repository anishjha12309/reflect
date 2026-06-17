"""Token/request telemetry ledger (CLAUDE.md §4 router policy, §9 quota gating).

Backed by sqlite. On HuggingFace Spaces only /tmp is writable, so the default DB
path lives under the system temp dir (which is /tmp on Linux). Tests pass
":memory:" for an isolated in-process DB.
"""
from __future__ import annotations

import os
import sqlite3
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

from .providers.base import ProviderCapabilities


def _default_db_path() -> str:
    env = os.environ.get("QUOTA_DB_PATH")
    if env:
        return env
    return str(Path(tempfile.gettempdir()) / "reflect_quota.sqlite")


def _today() -> str:
    return datetime.now(timezone.utc).date().isoformat()


class QuotaLedger:
    """Records every provider call and estimates remaining daily quota."""

    def __init__(self, db_path: str | None = None) -> None:
        self._conn = sqlite3.connect(db_path or _default_db_path(), check_same_thread=False)
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS calls (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                day TEXT NOT NULL,
                ts REAL NOT NULL,
                provider TEXT NOT NULL,
                task_type TEXT NOT NULL,
                prompt_tokens INTEGER NOT NULL,
                completion_tokens INTEGER NOT NULL,
                success INTEGER NOT NULL
            )
            """
        )
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_calls_provider_day ON calls(provider, day)"
        )
        self._conn.commit()

    def record(
        self,
        provider: str,
        task_type: str,
        prompt_tokens: int,
        completion_tokens: int,
        *,
        success: bool,
    ) -> None:
        self._conn.execute(
            "INSERT INTO calls (day, ts, provider, task_type, prompt_tokens, "
            "completion_tokens, success) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                _today(),
                time.time(),
                provider,
                task_type,
                prompt_tokens,
                completion_tokens,
                int(success),
            ),
        )
        self._conn.commit()

    def usage_today(self, provider: str) -> dict[str, int]:
        """Requests count all attempts (a 429 still consumes a request); tokens
        count only successful calls (failed calls produced no tokens)."""
        row = self._conn.execute(
            "SELECT COUNT(*), "
            "COALESCE(SUM(CASE WHEN success=1 THEN prompt_tokens + completion_tokens "
            "ELSE 0 END), 0) "
            "FROM calls WHERE provider = ? AND day = ?",
            (provider, _today()),
        ).fetchone()
        return {"requests": row[0], "tokens": row[1]}

    def is_exhausted(self, cap: ProviderCapabilities) -> bool:
        used = self.usage_today(cap.name)
        if cap.rpd is not None and used["requests"] >= cap.rpd:
            return True
        if cap.tpd is not None and used["tokens"] >= cap.tpd:
            return True
        return False

    def remaining(self, cap: ProviderCapabilities) -> dict[str, int | str | None]:
        used = self.usage_today(cap.name)
        return {
            "provider": cap.name,
            "requests_used": used["requests"],
            "requests_limit": cap.rpd,
            "requests_remaining": None if cap.rpd is None else max(0, cap.rpd - used["requests"]),
            "tokens_used": used["tokens"],
            "tokens_limit": cap.tpd,
            "tokens_remaining": None if cap.tpd is None else max(0, cap.tpd - used["tokens"]),
            "exhausted": self.is_exhausted(cap),
        }

    def recent_calls(self, limit: int = 2000) -> list[dict[str, object]]:
        """Today's calls in chronological order — the time series for the dashboard."""
        rows = self._conn.execute(
            "SELECT ts, provider, prompt_tokens, completion_tokens, success "
            "FROM calls WHERE day = ? ORDER BY ts DESC LIMIT ?",
            (_today(), limit),
        ).fetchall()
        return [
            {
                "ts": r[0],
                "provider": r[1],
                "prompt_tokens": r[2],
                "completion_tokens": r[3],
                "success": bool(r[4]),
            }
            for r in reversed(rows)
        ]

    def close(self) -> None:
        self._conn.close()

"""Small runtime config helpers (env-driven, no secrets here)."""
from __future__ import annotations

import os

_DEFAULT_ORIGINS = "http://localhost:3000"


def allowed_origins() -> list[str]:
    """CORS origins for the Vercel frontend (CLAUDE.md §5).

    Set ALLOWED_ORIGINS to a comma-separated list in the HF Space; defaults to the
    local Next.js dev origin.
    """
    raw = os.environ.get("ALLOWED_ORIGINS", _DEFAULT_ORIGINS)
    return [o.strip() for o in raw.split(",") if o.strip()]

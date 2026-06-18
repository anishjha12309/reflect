"""Critic agent (CLAUDE.md §3, §9): review a draft for coverage + contradictions.

Routes as task_type="reasoning". Decides whether a bounded second research round is
warranted: if coverage_score < threshold AND the current round is still below
MAX_ROUNDS, it emits targeted follow-up questions; otherwise it approves. This is the
gate that keeps the self-correction loop bounded (no infinite re-search).
"""
from __future__ import annotations

import structlog
from pydantic import BaseModel, Field, ValidationError

from agents.summarizer import Note
from core.llm_router import LLMRouter
from core.providers.base import AllProvidersExhausted, Message

log = structlog.get_logger(__name__)

DEFAULT_COVERAGE_THRESHOLD = 0.7
MAX_ROUNDS = 1  # one extra research round beyond the initial pass
# Cap the number of notes fed to the critic to bound prompt size and protect Groq's
# ~6K TPM free limit from being consumed by an oversized critic call.
CRITIC_MAX_NOTES = 60

_SYSTEM = (
    "You are a rigorous research critic. Given a DRAFT report and the underlying NOTES, "
    "assess how completely the draft answers the topic and find cross-source "
    "contradictions. Score coverage from 0.0 (poor) to 1.0 (complete). If coverage is "
    "weak, propose specific follow-up sub-questions that would close the gaps.\n"
    'Return ONLY JSON: {"coverage_score": 0.0, "contradictions": '
    '[{"claim_a": "...", "claim_b": "...", "explanation": "..."}], '
    '"followup_questions": ["..."]}.'
)


class Contradiction(BaseModel):
    claim_a: str
    claim_b: str
    explanation: str = ""


class CriticVerdict(BaseModel):
    coverage_score: float
    contradictions: list[Contradiction]
    followup_questions: list[str]
    approved: bool
    # False when the critic couldn't run at all (e.g. AllProvidersExhausted); distinguishes
    # "critic ran and scored 0%" from "critic never ran" so the UI can be honest.
    available: bool = True


class _CriticLLMOutput(BaseModel):
    coverage_score: float
    contradictions: list[Contradiction] = Field(default_factory=list)
    followup_questions: list[str] = Field(default_factory=list)


class Critic:
    task_type = "reasoning"

    def __init__(
        self,
        router: LLMRouter,
        *,
        coverage_threshold: float = DEFAULT_COVERAGE_THRESHOLD,
        max_rounds: int = MAX_ROUNDS,
    ) -> None:
        self._router = router
        self._threshold = coverage_threshold
        self._max_rounds = max_rounds

    async def review(self, draft_report: str, notes: list[Note], *, round: int) -> CriticVerdict:
        # Slice to CRITIC_MAX_NOTES before building the prompt to keep the token count
        # manageable and protect Groq's free-tier TPM cap.
        notes_block = "\n".join(f"- ({n.source_id}) {n.claim}" for n in notes[:CRITIC_MAX_NOTES])
        messages = [
            Message(role="system", content=_SYSTEM),
            Message(
                role="user",
                content=f"DRAFT REPORT:\n{draft_report}\n\nNOTES:\n{notes_block}",
            ),
        ]
        try:
            result = await self._router.complete(
                messages,
                task_type=self.task_type,
                max_tokens=1024,
                json_mode=True,
                schema=_CriticLLMOutput,
            )
            parsed = _CriticLLMOutput.model_validate_json(result.text)
        except (AllProvidersExhausted, ValidationError) as exc:
            # If the critic itself can't run, approve so the pipeline ends cleanly.
            # available=False signals the UI that the 0.0 score is an absence, not a result.
            log.warning("critic_failed_approving", error=str(exc))
            return CriticVerdict(
                coverage_score=0.0, contradictions=[], followup_questions=[], approved=True, available=False
            )

        coverage = max(0.0, min(1.0, parsed.coverage_score))
        needs_more = coverage < self._threshold and round < self._max_rounds
        return CriticVerdict(
            coverage_score=coverage,
            contradictions=parsed.contradictions,
            followup_questions=parsed.followup_questions if needs_more else [],
            approved=not needs_more,
        )

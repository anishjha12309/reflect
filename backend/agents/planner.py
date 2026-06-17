"""Planner agent (CLAUDE.md §3): topic → a validated research DAG.

Decomposes a topic into 3–7 atomic sub-questions with dependencies. Routes as
task_type="reasoning" (Groq tier). Defends quota and the graph:
  - caps total tasks (default 7),
  - validates the DAG (rejects empty / cyclic / dangling deps),
  - on any invalid output falls back to a FLAT list of sub-questions instead of crashing.
"""
from __future__ import annotations

import structlog
from pydantic import BaseModel, Field, ValidationError

from core.llm_router import LLMRouter
from core.providers.base import AllProvidersExhausted, Message

log = structlog.get_logger(__name__)

MAX_TASKS = 7

_SYSTEM = (
    "You are a research planner. Decompose the user's topic into 3 to 7 ATOMIC "
    "sub-questions that together fully cover it. Express dependencies: a sub-question "
    "that needs another's answer first lists that question's id in depends_on. "
    'Return ONLY JSON of the form {"sub_questions": [{"id": "q1", "question": "...", '
    '"depends_on": []}]}. Use short ids like q1, q2. No prose.'
)


class SubQuestion(BaseModel):
    id: str
    question: str
    depends_on: list[str] = Field(default_factory=list)


class ResearchPlan(BaseModel):
    topic: str
    sub_questions: list[SubQuestion]


class _PlannedQuestion(BaseModel):
    id: str
    question: str
    depends_on: list[str] = Field(default_factory=list)


class _PlanLLMOutput(BaseModel):
    sub_questions: list[_PlannedQuestion]


class Planner:
    task_type = "reasoning"

    def __init__(self, router: LLMRouter, *, max_tasks: int = MAX_TASKS) -> None:
        self._router = router
        self._max_tasks = max_tasks

    async def plan(self, topic: str) -> ResearchPlan:
        messages = [
            Message(role="system", content=_SYSTEM),
            Message(role="user", content=f"Topic: {topic}"),
        ]
        try:
            result = await self._router.complete(
                messages,
                task_type=self.task_type,
                max_tokens=1024,
                json_mode=True,
                schema=_PlanLLMOutput,
            )
            raw = _PlanLLMOutput.model_validate_json(result.text)
        except (AllProvidersExhausted, ValidationError) as exc:
            log.warning("planner_llm_failed_using_fallback", error=str(exc))
            return self._fallback(topic, [])

        # Cap first (protect quota), then validate the resulting graph.
        capped = raw.sub_questions[: self._max_tasks]
        if not capped:
            return self._fallback(topic, [])

        subs = [
            SubQuestion(id=q.id, question=q.question, depends_on=list(q.depends_on))
            for q in capped
        ]
        if not _dag_is_valid(subs):
            log.warning("planner_invalid_dag_flattening", count=len(subs))
            subs = [SubQuestion(id=s.id, question=s.question, depends_on=[]) for s in subs]
        return ResearchPlan(topic=topic, sub_questions=subs)

    def _fallback(self, topic: str, questions: list[SubQuestion]) -> ResearchPlan:
        """Flat plan: keep any questions we have (no deps), else one question = the topic."""
        if questions:
            flat = [
                SubQuestion(id=q.id, question=q.question, depends_on=[])
                for q in questions[: self._max_tasks]
            ]
        else:
            flat = [SubQuestion(id="q1", question=topic, depends_on=[])]
        return ResearchPlan(topic=topic, sub_questions=flat)


def _dag_is_valid(subs: list[SubQuestion]) -> bool:
    ids = [s.id for s in subs]
    if not ids or len(set(ids)) != len(ids):
        return False
    id_set = set(ids)
    graph = {s.id: s.depends_on for s in subs}
    if any(dep not in id_set for s in subs for dep in s.depends_on):
        return False  # dangling dependency

    # Cycle detection via DFS coloring.
    WHITE, GRAY, BLACK = 0, 1, 2
    color = {i: WHITE for i in ids}

    def visit(node: str) -> bool:
        color[node] = GRAY
        for nb in graph[node]:
            if color[nb] == GRAY:
                return False  # back-edge → cycle
            if color[nb] == WHITE and not visit(nb):
                return False
        color[node] = BLACK
        return True

    return all(visit(i) for i in ids if color[i] == WHITE)

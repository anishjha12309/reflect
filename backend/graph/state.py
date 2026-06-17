"""ResearchState — the single, serializable source of truth (CLAUDE.md §3).

Carries ONLY data: topic, plan, tasks, sources, notes, draft/final report, critic
feedback, round counter, partial-degradation flags, and the streamed event log. No
live objects (router, http clients, semaphores) are ever stored here — those live on
the Orchestrator so the state stays JSON-serializable for checkpointing.
"""
from __future__ import annotations

import time
from typing import Any, Literal

from pydantic import BaseModel, Field

from agents.critic import CriticVerdict
from agents.planner import ResearchPlan
from agents.summarizer import Note

TaskStatus = Literal["pending", "done", "empty"]


class TaskState(BaseModel):
    """One sub-question and its execution status within the DAG."""

    id: str
    question: str
    depends_on: list[str] = Field(default_factory=list)
    status: TaskStatus = "pending"
    attempts: int = 0  # search reformulation attempts so far
    round: int = 0  # which research round created this task


class RawSource(BaseModel):
    """A fetched source: working text plus the [n] number used for citation."""

    n: int
    url: str
    title: str | None = None
    text: str
    tokens: int = 0
    task_id: str


class Event(BaseModel):
    """A streamable progress event (consumed by the SSE endpoint in Phase 6)."""

    type: str
    data: dict[str, Any] = Field(default_factory=dict)
    ts: float = Field(default_factory=time.time)


class ResearchState(BaseModel):
    topic: str
    plan: ResearchPlan | None = None
    tasks: list[TaskState] = Field(default_factory=list)

    raw_sources: list[RawSource] = Field(default_factory=list)
    notes: list[Note] = Field(default_factory=list)

    draft_report: str = ""
    final_report: str = ""

    critic_feedback: CriticVerdict | None = None
    round: int = 0

    partial: bool = False
    partial_reasons: list[str] = Field(default_factory=list)

    quota_ledger: list[dict[str, Any]] = Field(default_factory=list)
    events: list[Event] = Field(default_factory=list)

    approved: bool = True  # HITL plan-approval gate (auto-approve unless required)

    def event(self, type: str, **data: Any) -> Event:
        return Event(type=type, data=data)

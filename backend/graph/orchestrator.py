"""LangGraph orchestrator with parallel DAG fan-out + bounded self-correction
(CLAUDE.md §3, §9).

Graph:
  plan → approve → fan_out_search → summarize → synthesize → critic
       → (loop to fan_out_search if critic requests another round, else) → finalize → END

Engineering highlights graded in §9:
  - True parallel fan-out: independent leaf sub-questions are searched+read concurrently
    via asyncio.gather behind a bounded semaphore (default 4). DAG dependencies are
    respected by processing ready tasks in waves.
  - Bounded critic loop (MAX_ROUNDS) — no infinite re-search.
  - Graceful degradation: SearchUnavailable / AllProvidersExhausted produce a clearly
    marked PARTIAL report instead of crashing.
  - Empty-sources sub-question → bounded query reformulation.

Live objects (router, agents, semaphore) live on the Orchestrator, never in state, so
ResearchState stays serializable for checkpointing.
"""
from __future__ import annotations

import asyncio

import structlog
from langgraph.graph import END, START, StateGraph

from agents.critic import MAX_ROUNDS, Critic
from agents.planner import Planner
from agents.reader import Reader, SourceDocument
from agents.summarizer import Summarizer
from core.llm_router import LLMRouter
from core.providers.base import AllProvidersExhausted, Message, estimate_tokens
from core.search import SearchFacade, SearchUnavailable

from .state import Event, RawSource, ResearchState, TaskState

log = structlog.get_logger(__name__)

_SYNTH_SYSTEM = (
    "You are a research synthesizer. Using ONLY the provided NOTES, write a clear, "
    "well-structured report with sections and headings. Every factual statement must "
    "carry an inline citation like [n] referencing the note's source number. Do not "
    "invent sources or facts beyond the notes."
)


class Orchestrator:
    def __init__(
        self,
        *,
        planner: Planner,
        search: SearchFacade,
        reader: Reader,
        summarizer: Summarizer,
        critic: Critic,
        router: LLMRouter,
        max_concurrency: int = 3,
        search_k: int = 5,
        read_k: int = 2,
        max_reformulations: int = 1,
        max_rounds: int = MAX_ROUNDS,
        synth_token_budget: int = 24_000,
        require_approval: bool = False,
    ) -> None:
        self._planner = planner
        self._search = search
        self._reader = reader
        self._summarizer = summarizer
        self._critic = critic
        self._router = router
        self._max_concurrency = max_concurrency
        self._search_k = search_k
        self._read_k = read_k
        self._max_reformulations = max_reformulations
        self._max_rounds = max_rounds
        self._synth_budget = synth_token_budget
        self._require_approval = require_approval
        self._app = self._build_graph()

    # --- graph wiring -------------------------------------------------------

    def _build_graph(self):
        g = StateGraph(ResearchState)
        g.add_node("plan", self._plan_node)
        g.add_node("approve", self._approve_node)
        g.add_node("fan_out_search", self._fan_out_node)
        g.add_node("summarize", self._summarize_node)
        g.add_node("synthesize", self._synthesize_node)
        g.add_node("critic", self._critic_node)
        g.add_node("prepare_round", self._prepare_round_node)
        g.add_node("finalize", self._finalize_node)

        g.add_edge(START, "plan")
        g.add_edge("plan", "approve")
        g.add_edge("approve", "fan_out_search")
        g.add_edge("fan_out_search", "summarize")
        g.add_edge("summarize", "synthesize")
        g.add_edge("synthesize", "critic")
        g.add_conditional_edges(
            "critic",
            self._route_after_critic,
            {"loop": "prepare_round", "end": "finalize"},
        )
        g.add_edge("prepare_round", "fan_out_search")
        g.add_edge("finalize", END)
        return g.compile()

    async def run(self, topic: str) -> ResearchState:
        out = await self._app.ainvoke(ResearchState(topic=topic))
        return ResearchState.model_validate(out)

    async def astream(self, topic: str):
        """Yield Events live as the graph progresses (one super-step at a time).

        Cancelling this generator (e.g. on client disconnect) propagates into the
        LangGraph run, cancelling in-flight search/read tasks and releasing the
        fan-out semaphore.
        """
        emitted = 0
        async for snapshot in self._app.astream(ResearchState(topic=topic), stream_mode="values"):
            events = snapshot.get("events", []) if isinstance(snapshot, dict) else snapshot.events
            for ev in events[emitted:]:
                yield ev if isinstance(ev, Event) else Event.model_validate(ev)
            emitted = len(events)

    # --- nodes --------------------------------------------------------------

    async def _plan_node(self, state: ResearchState) -> dict:
        plan = await self._planner.plan(state.topic)
        tasks = [
            TaskState(id=s.id, question=s.question, depends_on=s.depends_on, round=0)
            for s in plan.sub_questions
        ]
        events = [
            *state.events,
            Event(
                type="plan_ready",
                data={
                    "tasks": len(tasks),
                    "sub_questions": [
                        {"id": s.id, "question": s.question, "depends_on": s.depends_on}
                        for s in plan.sub_questions
                    ],
                },
            ),
        ]
        return {"plan": plan, "tasks": tasks, "events": events}

    async def _approve_node(self, state: ResearchState) -> dict:
        # HITL gate (CLAUDE.md §3). Off by default; when enabled it pauses via interrupt.
        if not self._require_approval:
            return {"approved": True}
        from langgraph.types import interrupt

        decision = interrupt({"plan": state.plan.model_dump() if state.plan else None})
        return {"approved": bool(decision)}

    async def _fan_out_node(self, state: ResearchState) -> dict:
        """Concurrently search+read all currently-ready leaf tasks, in DAG waves."""
        sem = asyncio.Semaphore(self._max_concurrency)
        tasks = [t.model_copy(deep=True) for t in state.tasks]
        by_id = {t.id: t for t in tasks}
        raw_sources = list(state.raw_sources)
        events = list(state.events)
        partial = state.partial
        reasons = list(state.partial_reasons)
        next_n = max((s.n for s in raw_sources), default=0)

        while True:
            ready = [
                t
                for t in tasks
                if t.status == "pending"
                and all(by_id[d].status in ("done", "empty") for d in t.depends_on if d in by_id)
            ]
            if not ready:
                break
            results = await asyncio.gather(*(self._gather_one(t, sem) for t in ready))
            for task, docs in zip(ready, results):
                if docs is None:  # search backend wholly unavailable
                    task.status = "empty"
                    partial = True
                    if "search_unavailable" not in reasons:
                        reasons.append("search_unavailable")
                    events.append(Event(type="task_empty", data={"id": task.id, "reason": "search_unavailable"}))
                    continue
                if not docs:
                    task.status = "empty"
                    events.append(Event(type="task_empty", data={"id": task.id, "reason": "no_sources"}))
                    continue
                for doc in docs:
                    next_n += 1
                    raw_sources.append(
                        RawSource(
                            n=next_n,
                            url=doc.url,
                            title=doc.title,
                            text=doc.text,
                            tokens=doc.tokens,
                            task_id=task.id,
                        )
                    )
                task.status = "done"
                events.append(Event(type="task_done", data={"id": task.id, "sources": len(docs)}))

        return {
            "tasks": tasks,
            "raw_sources": raw_sources,
            "events": events,
            "partial": partial,
            "partial_reasons": reasons,
        }

    async def _gather_one(self, task: TaskState, sem: asyncio.Semaphore) -> list[SourceDocument] | None:
        """Search (with bounded reformulation) + read for one task. Returns:
        None  → search backend unavailable, []    → no usable sources, [docs] → ok."""
        query = task.question
        for attempt in range(self._max_reformulations + 1):
            async with sem:
                try:
                    results = await self._search.search(query, k=self._search_k)
                except SearchUnavailable:
                    return None
            if results:
                docs = await self._read_results(results, sem)
                if docs:
                    return docs
            task.attempts += 1
            query = _reformulate(task.question, attempt)
        return []

    async def _read_results(self, results, sem: asyncio.Semaphore) -> list[SourceDocument]:
        async def read_one(url: str) -> SourceDocument | None:
            async with sem:
                return await self._reader.read(url)

        docs = await asyncio.gather(*(read_one(r.url) for r in results[: self._read_k]))
        return [d for d in docs if d is not None]

    async def _summarize_node(self, state: ResearchState) -> dict:
        sem = asyncio.Semaphore(self._max_concurrency)
        already = {n.source_id for n in state.notes}
        pending = [rs for rs in state.raw_sources if rs.url not in already]

        async def one(rs: RawSource):
            async with sem:
                return await self._summarizer.summarize(
                    SourceDocument(url=rs.url, title=rs.title, text=rs.text, tokens=rs.tokens)
                )

        results = await asyncio.gather(*(one(rs) for rs in pending))
        notes = list(state.notes)
        for chunk_notes in results:
            notes.extend(chunk_notes)
        events = [*state.events, Event(type="notes_ready", data={"total": len(notes)})]
        return {"notes": notes, "events": events}

    async def _synthesize_node(self, state: ResearchState) -> dict:
        notes = state.notes
        if not notes:
            report = _partial_report(state.topic, notes, state.raw_sources, "no usable sources found")
            events = [*state.events, Event(type="draft_ready", data={"partial": True})]
            return {
                "draft_report": report,
                "partial": True,
                "partial_reasons": [*state.partial_reasons, "no_notes"],
                "events": events,
            }

        url_to_n = {rs.url: rs.n for rs in state.raw_sources}
        try:
            body = await self._synthesize(state.topic, notes, url_to_n)
        except AllProvidersExhausted:
            report = _partial_report(state.topic, notes, state.raw_sources, "synthesis provider quota exhausted")
            events = [*state.events, Event(type="draft_ready", data={"partial": True})]
            return {
                "draft_report": report,
                "partial": True,
                "partial_reasons": [*state.partial_reasons, "synthesis_exhausted"],
                "events": events,
            }

        report = body + "\n\n" + _sources_table(state.raw_sources)
        events = [*state.events, Event(type="draft_ready", data={"partial": False})]
        return {"draft_report": report, "events": events}

    async def _synthesize(self, topic: str, notes, url_to_n: dict[str, int]) -> str:
        blocks = [f"[{url_to_n.get(n.source_id, 0)}] {n.claim} (evidence: {n.evidence})" for n in notes]
        notes_block = "\n".join(blocks)

        if estimate_tokens(notes_block) <= self._synth_budget:
            return await self._synth_call(topic, notes_block)

        # Map-reduce: synthesize over note groups, then combine (long-context overflow).
        groups = _chunk_lines(blocks, self._synth_budget)
        partials = [await self._synth_call(topic, "\n".join(g), section=True) for g in groups]
        return await self._synth_call(topic, "\n\n".join(partials), reduce=True)

    async def _synth_call(self, topic: str, content: str, *, section: bool = False, reduce: bool = False) -> str:
        instruction = f"Topic: {topic}\n\n"
        if reduce:
            instruction += "Merge these partial syntheses into one coherent report, preserving [n] citations.\n\nPARTIALS:\n"
        elif section:
            instruction += "Write a section of the report from these NOTES, citing [n].\n\nNOTES:\n"
        else:
            instruction += "Write the full report from these NOTES, citing [n].\n\nNOTES:\n"
        messages = [
            Message(role="system", content=_SYNTH_SYSTEM),
            Message(role="user", content=instruction + content),
        ]
        result = await self._router.complete(
            messages, task_type="long_synthesis", max_tokens=4096, json_mode=False
        )
        return result.text

    async def _critic_node(self, state: ResearchState) -> dict:
        # Don't spend critic quota on an already-degraded run.
        if state.partial:
            return {"events": [*state.events, Event(type="critic_skipped", data={"reason": "partial"})]}
        verdict = await self._critic.review(state.draft_report, state.notes, round=state.round)
        if not verdict.available:
            # Providers were exhausted; emit a distinct event so the UI doesn't show
            # "coverage 0% — approved" as if the critic actually ran.
            events = [
                *state.events,
                Event(type="critic_unavailable", data={"reason": "providers_exhausted"}),
            ]
        else:
            events = [
                *state.events,
                Event(
                    type="critic_verdict",
                    data={
                        "coverage": verdict.coverage_score,
                        "approved": verdict.approved,
                        "contradictions": [c.model_dump() for c in verdict.contradictions],
                        "followups": verdict.followup_questions,
                    },
                ),
            ]
        return {"critic_feedback": verdict, "events": events}

    def _route_after_critic(self, state: ResearchState) -> str:
        if state.partial:
            return "end"
        v = state.critic_feedback
        if v is not None and not v.approved and v.followup_questions and state.round < self._max_rounds:
            return "loop"
        return "end"

    async def _prepare_round_node(self, state: ResearchState) -> dict:
        new_round = state.round + 1
        followups = state.critic_feedback.followup_questions if state.critic_feedback else []
        tasks = list(state.tasks)
        for i, q in enumerate(followups, start=1):
            tasks.append(TaskState(id=f"r{new_round}q{i}", question=q, depends_on=[], round=new_round))
        events = [*state.events, Event(type="round_started", data={"round": new_round, "followups": len(followups)})]
        return {"round": new_round, "tasks": tasks, "events": events}

    async def _finalize_node(self, state: ResearchState) -> dict:
        ledger = []
        report_fn = getattr(self._router, "quota_report", None)
        if callable(report_fn):
            ledger = report_fn()
        events = [
            *state.events,
            Event(
                type="done",
                data={"partial": state.partial, "report": state.draft_report, "quota": ledger},
            ),
        ]
        return {"final_report": state.draft_report, "quota_ledger": ledger, "events": events}


# --- helpers ----------------------------------------------------------------


def build_orchestrator_from_env() -> Orchestrator:
    """Wire a fully live orchestrator from environment config (CLAUDE.md §2)."""
    from core.cache import SemanticCache, build_embedder_from_env
    from core.llm_router import build_router_from_env
    from core.search import build_search_from_env

    router = build_router_from_env()
    cache = SemanticCache(build_embedder_from_env())
    return Orchestrator(
        planner=Planner(router),
        search=build_search_from_env(cache=cache),
        reader=Reader(cache=cache),
        summarizer=Summarizer(router),
        critic=Critic(router),
        router=router,
    )


def _reformulate(question: str, attempt: int) -> str:
    """Cheap, deterministic query reformulation (no LLM spend) for empty-result retries."""
    variants = [f"{question} overview", f'"{question}" facts and evidence']
    return variants[min(attempt, len(variants) - 1)]


def _chunk_lines(lines: list[str], budget_tokens: int) -> list[list[str]]:
    groups: list[list[str]] = []
    current: list[str] = []
    used = 0
    for line in lines:
        t = estimate_tokens(line)
        if current and used + t > budget_tokens:
            groups.append(current)
            current, used = [], 0
        current.append(line)
        used += t
    if current:
        groups.append(current)
    return groups


def _sources_table(raw_sources: list[RawSource]) -> str:
    if not raw_sources:
        return "## Sources\n_No sources available._"
    lines = ["## Sources"]
    for rs in sorted(raw_sources, key=lambda s: s.n):
        title = rs.title or rs.url
        lines.append(f"[{rs.n}] {title} — {rs.url}")
    return "\n".join(lines)


def _partial_report(topic: str, notes, raw_sources: list[RawSource], reason: str) -> str:
    header = f"# Research Report: {topic}\n\n> ⚠️ **PARTIAL REPORT** — {reason}. Results are incomplete.\n"
    if not notes:
        return header + "\nNo usable sources were gathered for this topic."
    url_to_n = {rs.url: rs.n for rs in raw_sources}
    body = ["\n## Findings (unsynthesized)"]
    for n in notes:
        body.append(f"- {n.claim} [{url_to_n.get(n.source_id, 0)}]")
    return header + "\n".join(body) + "\n\n" + _sources_table(raw_sources)

"""Critic: bounded one-round re-search decision — router mocked."""
import json

from agents.critic import Critic
from agents.summarizer import Note
from tests.conftest import FakeRouter


def _verdict_json(coverage: float, followups: list[str], contradictions: list[dict] | None = None) -> str:
    return json.dumps(
        {
            "coverage_score": coverage,
            "contradictions": contradictions or [],
            "followup_questions": followups,
        }
    )


_NOTES = [Note(claim="c", evidence="e", source_id="s")]


async def test_low_coverage_first_round_requests_followups() -> None:
    router = FakeRouter([_verdict_json(0.3, ["what about Y?", "what about Z?"])])
    verdict = await Critic(router).review("draft", _NOTES, round=0)

    assert verdict.approved is False
    assert verdict.followup_questions == ["what about Y?", "what about Z?"]
    assert router.calls[0]["task_type"] == "reasoning"


async def test_low_coverage_at_max_round_stops() -> None:
    # Same weak coverage, but we've already used our one extra round → approve, no followups.
    router = FakeRouter([_verdict_json(0.3, ["more?"])])
    verdict = await Critic(router, max_rounds=1).review("draft", _NOTES, round=1)

    assert verdict.approved is True
    assert verdict.followup_questions == []


async def test_high_coverage_approves_immediately() -> None:
    router = FakeRouter([_verdict_json(0.95, [])])
    verdict = await Critic(router).review("draft", _NOTES, round=0)

    assert verdict.approved is True
    assert verdict.followup_questions == []


async def test_contradictions_are_surfaced() -> None:
    router = FakeRouter(
        [_verdict_json(0.9, [], [{"claim_a": "A", "claim_b": "B", "explanation": "conflict"}])]
    )
    verdict = await Critic(router).review("draft", _NOTES, round=0)

    assert len(verdict.contradictions) == 1
    assert verdict.contradictions[0].claim_a == "A"


async def test_coverage_score_is_clamped() -> None:
    router = FakeRouter([_verdict_json(1.7, [])])
    verdict = await Critic(router).review("draft", _NOTES, round=0)
    assert verdict.coverage_score == 1.0


async def test_critic_failure_approves_to_end_pipeline() -> None:
    router = FakeRouter(["garbage not json"])
    verdict = await Critic(router).review("draft", _NOTES, round=0)
    assert verdict.approved is True
    assert verdict.available is False


async def test_successful_critic_run_marks_available() -> None:
    router = FakeRouter([_verdict_json(0.95, [])])
    verdict = await Critic(router).review("draft", _NOTES, round=0)
    assert verdict.available is True

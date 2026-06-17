"""Planner: DAG validation, cyclic fallback, task cap, task_type — router mocked."""
import json

from agents.planner import Planner, _dag_is_valid, SubQuestion
from tests.conftest import FakeRouter


def _plan_json(questions: list[dict]) -> str:
    return json.dumps({"sub_questions": questions})


async def test_valid_dag_is_preserved() -> None:
    router = FakeRouter(
        [
            _plan_json(
                [
                    {"id": "q1", "question": "what is X", "depends_on": []},
                    {"id": "q2", "question": "how does X compare", "depends_on": ["q1"]},
                ]
            )
        ]
    )
    plan = await Planner(router).plan("X")

    assert [s.id for s in plan.sub_questions] == ["q1", "q2"]
    assert plan.sub_questions[1].depends_on == ["q1"]
    assert router.calls[0]["task_type"] == "reasoning"


async def test_cyclic_dag_falls_back_to_flat_list() -> None:
    router = FakeRouter(
        [
            _plan_json(
                [
                    {"id": "q1", "question": "a", "depends_on": ["q2"]},
                    {"id": "q2", "question": "b", "depends_on": ["q1"]},  # cycle
                ]
            )
        ]
    )
    plan = await Planner(router).plan("topic")

    # questions kept, but the (cyclic) dependencies are dropped → flat
    assert len(plan.sub_questions) == 2
    assert all(s.depends_on == [] for s in plan.sub_questions)


async def test_task_count_is_capped() -> None:
    many = [{"id": f"q{i}", "question": f"q{i}?", "depends_on": []} for i in range(12)]
    router = FakeRouter([_plan_json(many)])
    plan = await Planner(router, max_tasks=7).plan("big topic")

    assert len(plan.sub_questions) == 7


async def test_malformed_output_falls_back_to_topic() -> None:
    router = FakeRouter(["this is not json at all"])
    plan = await Planner(router).plan("quantum computing")

    assert len(plan.sub_questions) == 1
    assert plan.sub_questions[0].question == "quantum computing"


async def test_empty_sub_questions_falls_back() -> None:
    router = FakeRouter([_plan_json([])])
    plan = await Planner(router).plan("my topic")
    assert plan.sub_questions[0].question == "my topic"


def test_dag_validator_rejects_dangling_dependency() -> None:
    subs = [SubQuestion(id="q1", question="a", depends_on=["q9"])]
    assert _dag_is_valid(subs) is False

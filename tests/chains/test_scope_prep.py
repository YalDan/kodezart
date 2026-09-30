"""The scope run's prep: the criteria it grades are the ones the board lists."""

from typing import cast

from kodezart.chains.scope_stages import ScopeStages
from kodezart.core.prompt_namespaces import bindings_for
from kodezart.types.domain.agent import ResultEvent
from kodezart.types.domain.branch import trunk_base
from kodezart.types.domain.criteria import (
    CriterionId,
    TrackerCriterion,
    TrackerCriterionSet,
)
from kodezart.types.domain.fire_spec import TrackerSpec
from kodezart.types.domain.scope import ScopeKind, ScopeRef
from kodezart.types.domain.session import PermissionMode
from kodezart.types.domain.workflow import ExecutionContext, WorkflowState
from tests.fakes import SUPPRESS_ALL_SKILLS, FakeAgentRunner
from tests.prompts.test_prompt_wiring import load_registry
from tests.services.test_prompt_pass import example_config


def _item(key: str, text: str, *, criterion: bool = True) -> dict[str, object]:
    return {"key": key, "criterion": criterion, "text": text, "done": False}


async def test_a_criterion_key_listed_twice_is_held_once_where_it_first_stood() -> None:
    """The scope-done answer lists one key twice: one criterion, last text.

    The roster is shaped through the one native set builder, which takes a
    mapping, so the key keeps its first position and the text the
    answer gave it last. The set has no uniqueness check of its own; this is
    the outcome prep chooses.
    """
    answer: dict[str, object] = {
        "items": [
            _item("c/one", "first text"),
            _item("c/two", "second criterion"),
            _item("i/plain", "an issue that is no criterion", criterion=False),
            _item("c/one", "restated text"),
        ],
        "reason": "Two criteria are open.",
    }
    runner = FakeAgentRunner(
        [
            ResultEvent(
                subtype="success",
                duration_ms=1,
                duration_api_ms=1,
                is_error=False,
                num_turns=1,
                session_id="scope-done",
                structured_output=answer,
            )
        ]
    )
    stages = ScopeStages(
        runner=runner,
        prompts=load_registry(bindings=dict(bindings_for(example_config()))),
        skills=SUPPRESS_ALL_SKILLS,
        working_dir="/work",
    )
    execution = ExecutionContext(
        prompt="Deliver the parent.",
        repo_path="/checkout",
        repo_url="https://github.com/example/project",
        cache_key="cache",
        base_spec=trunk_base("main"),
        permission_mode=PermissionMode.ACCEPT_EDITS,
        allowed_tools=["Read"],
        scope=ScopeRef(kind=ScopeKind.ISSUE, key="fire/parent"),
    )

    # Prep reads nothing off the graph state; the run's address is the config.
    empty = cast(WorkflowState, {})

    update = await stages.prep(empty, {"configurable": execution.model_dump()})

    assert update["criterion_set"] == TrackerCriterionSet(
        criteria=[
            TrackerCriterion(id=CriterionId("c/one"), text="restated text"),
            TrackerCriterion(id=CriterionId("c/two"), text="second criterion"),
        ]
    )
    spec = update["fire_spec"]
    assert isinstance(spec, TrackerSpec)
    assert spec.criteria == ("c/one", "c/two")

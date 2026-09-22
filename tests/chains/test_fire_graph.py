"""The actual compiled fire excludes every delivery node and route."""

import inspect
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import Enum
from itertools import product
from pathlib import Path
from types import UnionType
from typing import Literal, Union, get_args, get_origin

from pydantic import BaseModel

from kodezart.chains.ralph_workflow import RalphWorkflowEngine
from kodezart.handlers.agent_handler import (
    _queued_event_payload,
    _streamed_event_payload,
)
from kodezart.types.domain.agent import AgentEvent, WorkflowCompleteEvent
from kodezart.types.domain.criteria import (
    ConjunctionVerdict,
    Contradiction,
    CostMeasurement,
    CriteriaValidation,
    CriterionFeasibility,
    CriterionFlag,
    CriterionVerdict,
    ForbiddenCriterionClass,
)
from kodezart.types.domain.delivery import LaneDelivery
from kodezart.types.domain.outcome import WorkflowOutcome
from kodezart.types.domain.trajectory import IterationRecord, LoopTrajectory
from kodezart.types.domain.workflow import WorkflowState
from tests.chains.test_fire_extraction import DELIVERY_FIELDS, fire

#: Every field the fire's terminal event DECLARES, and every key its state
#: declares, as both stood when the hand-off was pinned.  Closed sets rather
#: than a screen for the five legacy delivery spellings in
#: ``DELIVERY_FIELDS``: "the hand-off adds no field" is a claim about ANY
#: field, and a screen answers only about the names somebody thought to
#: write down — ``delivery_pr_url`` would walk straight past it.  A field the
#: fire genuinely grows is added here in the commit that grows it, which is
#: the review this pin exists to force.
TERMINAL_FIELDS = {
    "accepted",
    "criteria_validation",
    "feature_branch",
    "final_commit_sha",
    "merge_error",
    "merged",
    "outcome",
    "ralph_branch",
    "total_iterations",
    "trajectory",
    "type",
}
STATE_KEYS = {
    "accept_verdict",
    "acceptance_criteria",
    "best_iteration_sha",
    "criteria_infeasible",
    "criteria_regeneration_rounds",
    "criteria_validation",
    "criterion_set",
    "feature_branch",
    "feature_tip_sha",
    "fire_spec",
    "flagged_items",
    "issue_key",
    "lane_entry",
    "merge_error",
    "merged",
    "ralph_branch",
    "remediation_entry",
    "remediation_rounds_used",
    "remediation_ticket",
    "repo_url",
    "repo_visibility",
    "review_base_sha",
    "review_feedback",
    "review_head_sha",
    "review_passed",
    "ruling_unrecorded",
    "total_iterations",
    "trajectory",
    "work_base_ref",
}
#: Every field each model NESTED in the terminal declares, reached through
#: the terminal's own annotations, as they stood when the hand-off was
#: pinned.  The renderings below compare what a nested value sends against
#: that value's own model, which is exactly as closed as the model is: a
#: delivery fact declared on a nested model — ``pr_url`` on the trajectory —
#: is on that model and on the wire together and agrees with itself.  So the
#: nested models are held to a roster the way the terminal is, and a field
#: they grow is added here in the commit that grows it.
NESTED_FIELDS: dict[type[BaseModel], set[str]] = {
    LoopTrajectory: {
        "best_commit_sha",
        "best_iteration",
        "best_passed_count",
        "never_passed_ids",
        "plateaued",
        "records",
    },
    IterationRecord: {
        "commit_sha",
        "failing_criterion_ids",
        "iteration",
        "passed_count",
    },
    CriteriaValidation: {"conjunction", "verdicts"},
    ConjunctionVerdict: {"contradictions", "satisfiable"},
    Contradiction: {"criterion_ids", "explanation"},
    CriterionFeasibility: {
        "cost_measurement",
        "criterion_id",
        "flags",
        "forbidden_class",
        "missing_resource",
        "refutation",
        "undeclared_switch_arms",
        "verdict",
    },
    CostMeasurement: {"affordable", "observed"},
}
#: What the lane's delivery record and the fire's two surfaces already share,
#: measured at the same commit as the rosters above.  These two names are the
#: fire's own facts that the record repeats back, not delivery facts that
#: migrated in; every other name on ``LaneDelivery`` — the pull request, the
#: observation, the red class, the stall and remediation flags — belongs to
#: the delivery alone, and the empty intersection with the state says so.
SHARED_WITH_TERMINAL = {"final_commit_sha", "outcome"}
SHARED_WITH_STATE: set[str] = set()


def test_compiled_fire_has_no_delivery_nodes_routes_or_capabilities():
    engine = fire()
    graph = engine.graph.get_graph()
    forbidden = {"open_pr", "monitor_ci", "comment_failure", "open_stalled_pr"}
    assert not forbidden.intersection(graph.nodes)
    assert not any(
        edge.source in forbidden or edge.target in forbidden for edge in graph.edges
    )
    assert not DELIVERY_FIELDS.intersection(WorkflowState.__annotations__)
    assert not DELIVERY_FIELDS.intersection(WorkflowCompleteEvent.model_fields)
    parameters = inspect.signature(RalphWorkflowEngine).parameters
    assert "pr_creator" not in parameters and "ci_monitor" not in parameters
    source = Path(inspect.getfile(RalphWorkflowEngine)).read_text()
    for name in (
        "_open_pr_node",
        "_monitor_ci_node",
        "_comment_failure_node",
        "_route_after_pr",
        "_route_after_ci",
    ):
        assert name not in source


@dataclass(frozen=True)
class Rendering:
    """One function production puts the terminal on a wire with.

    ``send`` is production's own function, called as production calls it;
    this module never renders the event itself.  The two flags are what the
    wire is EXPECTED to do with a field — carry it under its alias, and leave
    it off when it holds ``None`` — and the keys each rendering must carry
    are derived from them and from the model, per instance.
    """

    send: Callable[[AgentEvent], Mapping[str, object]]
    aliased: bool
    drops_none: bool


#: Every function the handler sends an event through: the queued job's
#: frames, and the live stream's, which the error frame shares.  A terminal
#: is neither scope envelope, so the queued rendering drops its ``None``s.
PRODUCTION_RENDERINGS: dict[str, Rendering] = {
    "queued": Rendering(send=_queued_event_payload, aliased=True, drops_none=True),
    "streamed": Rendering(send=_streamed_event_payload, aliased=True, drops_none=True),
}
#: The required facts of the terminal that are not a choice among enumerated
#: values.  Checked against the model below, so a required field it grows
#: arrives with no value here and reds.
REQUIRED = {
    "feature_branch": "feature/pinned",
    "ralph_branch": "ralph/pinned",
    "total_iterations": 1,
    "accepted": True,
}


def carried() -> dict[str, object]:
    """A value for EVERY optional field of the terminal, each holding something.

    The nested values carry every optional field of their own as well, so a
    key a nested model sends only when it holds something is on the wire in
    this half.  Checked against the model below, so an optional field the
    terminal grows arrives with no value here and reds.
    """
    return {
        "merged": True,
        "final_commit_sha": "c" * 40,
        "merge_error": "a consolidation refusal",
        "trajectory": LoopTrajectory(
            records=[
                IterationRecord(
                    iteration=1,
                    passed_count=0,
                    failing_criterion_ids=["AC-1"],
                    commit_sha="d" * 40,
                )
            ],
            never_passed_ids=["AC-1"],
            best_passed_count=0,
            best_iteration=1,
            best_commit_sha="d" * 40,
            plateaued=True,
        ),
        "criteria_validation": CriteriaValidation(
            verdicts=[
                CriterionFeasibility(
                    criterion_id="AC-1",
                    verdict=CriterionVerdict.infeasible,
                    refutation="a refutation",
                    missing_resource="a resource",
                    cost_measurement=CostMeasurement(
                        observed="an observation", affordable=False
                    ),
                    flags=[next(iter(CriterionFlag))],
                    forbidden_class=next(iter(ForbiddenCriterionClass)),
                    undeclared_switch_arms=["an arm"],
                )
            ],
            conjunction=ConjunctionVerdict(
                satisfiable=False,
                contradictions=[
                    Contradiction(
                        criterion_ids=["AC-1", "AC-2"], explanation="they collide"
                    )
                ],
            ),
        ),
    }


def enumerated(model: type[BaseModel]) -> dict[str, tuple[object, ...]]:
    """Every field of *model* typed by an enum or a ``Literal``, with its values.

    Read off the annotations, through a union's members, so a field typed
    ``SomeEnum | None`` is enumerated too; ``None`` itself is the unset half
    and is not one of the values.
    """
    choices: dict[str, tuple[object, ...]] = {}
    for name, field in model.model_fields.items():
        annotation = field.annotation
        members = (
            get_args(annotation)
            if get_origin(annotation) in (Union, UnionType)
            else (annotation,)
        )
        values: list[object] = []
        for member in members:
            if get_origin(member) is Literal:
                values.extend(get_args(member))
            elif isinstance(member, type) and issubclass(member, Enum):
                values.extend(member)
        if values:
            choices[name] = tuple(values)
    return choices


def terminals() -> list[tuple[str, WorkflowCompleteEvent]]:
    """One terminal per value of every enumerated field, twice over.

    Once with every optional field unset and once with every one of them
    set: a key sent only for one outcome, or only when a field holds
    something, is on the wire in exactly one of these.  The values are the
    product over the enumerated fields, which is bounded by their members.
    """
    choices = enumerated(WorkflowCompleteEvent)
    built = []
    for values in product(*choices.values()):
        chosen = dict(zip(choices, values, strict=True))
        label = ", ".join(f"{name}={value}" for name, value in chosen.items())
        built.append((f"{label}, unset", WorkflowCompleteEvent(**REQUIRED, **chosen)))
        built.append(
            (
                f"{label}, set",
                WorkflowCompleteEvent(**REQUIRED, **chosen, **carried()),
            )
        )
    return built


def expected_keys(value: BaseModel, rendering: Rendering) -> dict[str, str]:
    """The key each field of *value* is sent under, by *rendering*, and no other.

    Derived from the model: every declared field, under its alias when the
    rendering uses aliases, and — for a rendering that drops ``None`` — only
    the fields that hold something on this instance.
    """
    return {
        (
            (field.serialization_alias or field.alias or name)
            if rendering.aliased
            else name
        ): name
        for name, field in type(value).model_fields.items()
        if not (rendering.drops_none and getattr(value, name) is None)
    }


def assert_sends_its_fields(
    value: BaseModel, sent: object, rendering: Rendering, where: str
) -> None:
    """*sent* carries exactly *value*'s own fields, and so does every model in it.

    An equality per instance, not a union over several: a key that one
    rendering or one instance adds is a key that one wire carries.  Recurses
    into every nested model value, directly held or held in a list, and
    compares it against that nested model's own fields; the walk is bounded
    by the value's own depth.
    """
    assert isinstance(sent, Mapping), where
    keys = expected_keys(value, rendering)
    assert set(sent) == set(keys), where
    for key, name in keys.items():
        held = getattr(value, name)
        inside = f"{where} / {key}"
        if isinstance(held, BaseModel):
            assert_sends_its_fields(held, sent[key], rendering, inside)
        elif isinstance(held, list | tuple):
            items = sent[key]
            assert isinstance(items, list | tuple), inside
            assert len(items) == len(held), inside
            for index, item in enumerate(held):
                if isinstance(item, BaseModel):
                    assert_sends_its_fields(
                        item, items[index], rendering, f"{inside}[{index}]"
                    )


def models_under(model: type[BaseModel]) -> dict[type[BaseModel], set[str]]:
    """*model* and every model its annotations reach, each with its fields.

    Bounded: each model is visited once, and each annotation is a finite
    tree of arguments.
    """
    found: dict[type[BaseModel], set[str]] = {}
    pending = [model]
    while pending:
        current = pending.pop()
        if current in found:
            continue
        found[current] = set(current.model_fields)
        for field in current.model_fields.values():
            annotations: list[object] = [field.annotation]
            while annotations:
                annotation = annotations.pop()
                if isinstance(annotation, type) and issubclass(annotation, BaseModel):
                    pending.append(annotation)
                annotations.extend(get_args(annotation))
    return found


def test_the_fire_terminal_and_state_grow_no_field_of_the_lane_s_delivery():
    """The other half of the hand-off claim: neither surface grew a field.

    ``DELIVERY_FIELDS`` above screens five legacy spellings, so it answers
    about the names it lists and about nothing else.  Two derived pins close
    that: each surface's whole roster, so a field arriving under any spelling
    reds; and the delivery record intersected with each surface, so a
    delivery fact moving onto the fire is named by the intersection it joins
    even if the rosters are updated in the same breath.

    The terminal's roster reaches into the models it nests, which the wire
    check below compares only against themselves, and no model on it may
    declare a computed field: a computed field is absent from every roster
    and present on the wire, and one spelled as an existing field's alias —
    ``mergeError`` — would overwrite that key and leave the key set
    unchanged.
    """
    assert set(WorkflowCompleteEvent.model_fields) == TERMINAL_FIELDS
    assert models_under(WorkflowCompleteEvent) == {
        WorkflowCompleteEvent: TERMINAL_FIELDS,
        **NESTED_FIELDS,
    }
    assert WorkflowCompleteEvent.model_computed_fields == {}
    assert {
        model: model.model_computed_fields
        for model in models_under(WorkflowCompleteEvent)
        if model.model_computed_fields
    } == {}
    assert set(WorkflowState.__annotations__) == STATE_KEYS

    delivery = set(LaneDelivery.model_fields)
    assert delivery & set(WorkflowCompleteEvent.model_fields) == SHARED_WITH_TERMINAL
    assert delivery & set(WorkflowState.__annotations__) == SHARED_WITH_STATE


def test_what_production_sends_for_the_terminal_is_its_fields_and_no_more():
    """Every rendering production sends the terminal through, per instance.

    What is compared is what PRODUCTION sends — the handler's own functions,
    called on the event — and not a rendering this module chose: a key only
    the json mode, the aliases, or the dropping of ``None`` puts on the wire
    is on it here exactly when it is on it in production.  Each rendering is
    compared per instance, with an equality, against the keys the model
    derives for that instance, and so is every model nested in it; the
    instances are one per value of every enumerated field, each once with
    every optional field unset and once with every one of them set, so a key
    sent for one outcome, or only when a field holds something, reds.
    """
    optional = {
        name
        for name, field in WorkflowCompleteEvent.model_fields.items()
        if not field.is_required()
    }
    required = set(WorkflowCompleteEvent.model_fields) - optional
    choices = enumerated(WorkflowCompleteEvent)
    assert set(REQUIRED) == required - set(choices)
    assert set(carried()) == optional - set(choices)
    assert set(choices["outcome"]) == set(WorkflowOutcome)

    built = terminals()
    assert len(built) == 2 * len(WorkflowOutcome)
    for rendering_name, rendering in PRODUCTION_RENDERINGS.items():
        for instance_name, terminal in built:
            assert_sends_its_fields(
                terminal,
                rendering.send(terminal),
                rendering,
                f"{rendering_name}: {instance_name}",
            )

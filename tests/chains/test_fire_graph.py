"""The fire's hand-off adds no field to its state or to its terminal event.

The reach of this module's pins, stated once:

- the fire's state keys, by object;
- the terminal's machinery, by digest: the terminal event model and every
  model it reaches, with their serialisation and validation machinery and
  their ``extra`` setting;
- the tracker-native completion node, driven over every outcome the
  classifier names under every entry kind the ``LaneEntry`` union names,
  both work bases and every visibility, on native states the snapshot
  check admits;
- the node's construction and binding, by object: its source and the
  source of every function its event construction calls, by digest, and
  the compiled native graph's ``complete`` node bound to that method;
- the relays and the queue up to the handler's rendering: the terminal
  passes through the origin-routed engine's run and the fire engine's
  run, the job queue's buffer — which holds event objects, not bytes,
  until the handler renders them — the handler's rendering and the SSE
  framing, all observed and pinned by digest as the egress path;
- the coordinator's public surface, pinned beside the lane's delivery
  tests.

Outside the reach: only what follows the handler's rendering, meaning the
bytes the SSE frame becomes, the ASGI messages that carry them and any
middleware over them.  None of these is the hand-off; they carry whatever
bytes they are given, and that limit is held by a negative control below.
The route table, the stream column and the egress path are pinned below
as they stand, and each of those pins states what it holds; none claims
to cover every route to the wire.
"""

import ast
import asyncio
import hashlib
import inspect
import json
import re
import sys
import textwrap
from collections.abc import AsyncIterator, Awaitable, Callable, Iterable, Mapping
from enum import Enum
from itertools import product
from math import prod
from pathlib import Path
from types import (
    CodeType,
    FrameType,
    FunctionType,
    MethodType,
    ModuleType,
    UnionType,
)
from typing import Annotated, Literal, Union, get_args, get_origin

import pytest
from fastapi import FastAPI
from langchain_core.runnables import RunnableConfig
from pydantic import BaseModel
from pydantic_core import SchemaSerializer
from starlette.types import Message

import kodezart
from kodezart.adapters import asyncio_job_queue
from kodezart.api.v1.endpoints import agent as agent_routes
from kodezart.api.v1.endpoints import jobs as job_routes
from kodezart.chains import ralph_workflow
from kodezart.chains.fire_consolidation import FireConsolidation
from kodezart.chains.ralph_workflow import (
    FireGraph,
    RalphWorkflowEngine,
    fire_terminal,
)
from kodezart.composition.engine import OriginRoutedWorkflowEngine
from kodezart.composition.jobs import build_job_service
from kodezart.core.protocols import FireCriteriaSource
from kodezart.domain.accept_gate import gate_cleared
from kodezart.domain.outcome import classify_outcome
from kodezart.domain.thread_id import workflow_thread_id
from kodezart.handlers import agent_handler
from kodezart.main import create_app
from kodezart.services.agent_service import AgentService
from kodezart.types.domain.accept import AcceptVerdict
from kodezart.types.domain.agent import (
    AgentEvent,
    AuthoredWorkflowCompleteEvent,
    WorkflowCompleteEvent,
)
from kodezart.types.domain.criteria import (
    ConjunctionVerdict,
    Contradiction,
    CostMeasurement,
    CriteriaValidation,
    CriterionFeasibility,
    CriterionFlag,
    CriterionId,
    CriterionVerdict,
    ForbiddenCriterionClass,
    TrackerCriterion,
    TrackerCriterionSet,
)
from kodezart.types.domain.delivery import LaneDelivery
from kodezart.types.domain.fire_spec import CriterionRef, IssueRef, TrackerSpec
from kodezart.types.domain.gating import RepoVisibility
from kodezart.types.domain.job import JobState
from kodezart.types.domain.lane_entry import LaneEntry
from kodezart.types.domain.outcome import WorkflowOutcome
from kodezart.types.domain.tracker import TrackerIssue
from kodezart.types.domain.trajectory import IterationRecord, LoopTrajectory
from kodezart.types.domain.workflow import WorkflowState
from kodezart.utils.sse import format_sse
from tests.chains.test_fire_extraction import DELIVERY_FIELDS, fire
from tests.domain.test_outcome import _state
from tests.fakes import (
    SUPPRESS_ALL_SKILLS,
    FakeAgentExecutor,
    FakeWorkspaceProvider,
    attached_job_queue,
)

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
    "best_iteration_branch",
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
#: pinned.  The egress check below compares what a nested value sends against
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


#: The required facts of the terminal that are not a choice among enumerated
#: values.  Checked against the model below, so a required field it grows
#: arrives with no value here and reds.  ``accepted``, ``outcome`` and every
#: other enumerated field take their values from the enumeration instead.
REQUIRED = {
    "feature_branch": "feature/pinned",
    "ralph_branch": "ralph/pinned",
    "total_iterations": 1,
}


def carried() -> dict[str, object]:
    """A value for every optional, non-enumerated field of the terminal.

    The nested values carry every optional field of their own as well, so
    this is the template every instance below is cut from: each model the
    terminal's annotations reach is held here (checked by the test), and a
    field of it is set or left unset per instance.  The enumerated fields
    inside the nested values are placeholders the instances overwrite.
    Checked against the model below, so an optional field the terminal grows
    arrives with no value here and reds.
    """
    return {
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


def choices(annotation: object) -> tuple[object, ...]:
    """Every value a field typed *annotation* can take, when it is enumerable.

    A ``bool``, a ``Literal`` and an ``Enum`` are enumerable, read through a
    union's members (``None`` is the unset half, not a value) and through
    ``Annotated``; a list of an enumerable type takes each value as its one
    item.  Anything else answers the empty tuple.  Bounded by the
    annotation's own finite tree of arguments.
    """
    origin = get_origin(annotation)
    if origin in (Union, UnionType):
        return tuple(
            value for member in get_args(annotation) for value in choices(member)
        )
    if origin is Annotated:
        return choices(get_args(annotation)[0])
    if origin is Literal:
        return get_args(annotation)
    if origin is list:
        return tuple([value] for value in choices(get_args(annotation)[0]))
    if annotation is bool:
        return (False, True)
    if isinstance(annotation, type) and issubclass(annotation, Enum):
        return tuple(annotation)
    return ()


#: Where a field sits inside the terminal: field names, with a list index
#: wherever the value on the way is a list.
Site = tuple[str | int, ...]


def sites(held: BaseModel, at: Site = ()) -> list[tuple[Site, tuple[object, ...]]]:
    """Every enumerable field of *held* and of every model inside it.

    At ANY depth: the terminal's own fields and those of every model its
    values hold, directly or in a list, each with the values it can take.
    Bounded by the depth of the value.
    """
    found: list[tuple[Site, tuple[object, ...]]] = []
    for name, field in type(held).model_fields.items():
        values = choices(field.annotation)
        if values:
            found.append(((*at, name), values))
        value = getattr(held, name)
        if isinstance(value, BaseModel):
            found.extend(sites(value, (*at, name)))
        elif isinstance(value, list):
            for index, item in enumerate(value):
                if isinstance(item, BaseModel):
                    found.extend(sites(item, (*at, name, index)))
    return found


def models_held(held: BaseModel) -> set[type[BaseModel]]:
    """Every model class *held* is or holds, directly or in a list."""
    found = {type(held)}
    for name in type(held).model_fields:
        value = getattr(held, name)
        items = value if isinstance(value, list) else [value]
        for item in items:
            if isinstance(item, BaseModel):
                found |= models_held(item)
    return found


#: A value per site: the choices one instance is cut with.
Chosen = Mapping[Site, object]


def below(chosen: Chosen, step: str | int) -> dict[Site, object]:
    """The choices of *chosen* under *step*, each with *step* taken off."""
    return {path[1:]: value for path, value in chosen.items() if path[0] == step}


def cut(template: BaseModel, *, filled: bool, chosen: Chosen) -> BaseModel:
    """A copy of *template* with the field at each chosen site set to its value.

    Every enumerated field no site chooses keeps the template's value.
    With *filled*, every optional field holds the template's value; without
    it, every optional field is left unset, except the ones on the way to a
    chosen site, which have to be there for the field to be.  Built through
    the model, so every instance is one the model accepts.
    """
    fields: dict[str, object] = {}
    for name, field in type(template).model_fields.items():
        if (name,) in chosen:
            fields[name] = chosen[(name,)]
            continue
        under = below(chosen, name)
        if not (under or filled or field.is_required()):
            continue
        fields[name] = cut_value(getattr(template, name), filled=filled, chosen=under)
    return type(template)(**fields)


def cut_value(held: object, *, filled: bool, chosen: Chosen) -> object:
    """*held* rebuilt by :func:`cut` when it is a model or a list of models."""
    if isinstance(held, BaseModel):
        return cut(held, filled=filled, chosen=chosen)
    if isinstance(held, list):
        return [
            cut_value(item, filled=filled, chosen=below(chosen, index))
            for index, item in enumerate(held)
        ]
    return held


def template() -> WorkflowCompleteEvent:
    """The terminal with every field, at every depth, holding something."""
    return WorkflowCompleteEvent(
        **REQUIRED,
        **{
            name: values[0]
            for name, values in enumerated(WorkflowCompleteEvent).items()
        },
        **carried(),
    )


def unfilled(held: BaseModel, at: str = "") -> list[str]:
    """Every field of *held*, at any depth, that holds ``None`` or nothing."""
    found: list[str] = []
    for name in type(held).model_fields:
        value = getattr(held, name)
        where = f"{at}.{name}" if at else name
        if value is None or value == []:
            found.append(where)
        for item in value if isinstance(value, list) else [value]:
            if isinstance(item, BaseModel):
                found.extend(unfilled(item, where))
    return found


def handed_off() -> dict[Site, object]:
    """The terminal's own enumerated values on the fire's hand-off.

    Built from real inputs the way the fire's completion node builds the
    terminal: a state whose loop was accepted, whose merge landed and whose
    review passed, read through the shipped gate and classified by the
    shipped classifier.  The lane's delivery fields are taken off the state
    first, because the fire's own state has none.
    """
    legacy = _state(verdict=AcceptVerdict.accepted, merged=True, review_passed=True)
    state = WorkflowState(
        **{key: value for key, value in legacy.items() if key not in DELIVERY_FIELDS}
    )
    return {
        ("accepted",): gate_cleared(state["accept_verdict"]),
        ("merged",): state["merged"],
        ("outcome",): classify_outcome(state),
    }


def own_sites(full: WorkflowCompleteEvent) -> list[tuple[Site, tuple[object, ...]]]:
    """The terminal's own enumerated fields, each with the values it can take."""
    return [(path, values) for path, values in sites(full) if len(path) == 1]


def combinations(full: WorkflowCompleteEvent) -> list[dict[Site, object]]:
    """Every combination of the terminal's own enumerated values.

    The full cross product — ``type``, ``accepted``, ``outcome``, ``merged``
    as the terminal declares them — so no pair of values is left out,
    whichever two a serialiser keys on.
    """
    own = own_sites(full)
    return [
        dict(zip([path for path, _ in own], values, strict=True))
        for values in product(*(values for _, values in own))
    ]


def variations(full: WorkflowCompleteEvent) -> list[dict[Site, object]]:
    """Every value of every NESTED enumerated field, one at a time, from two bases.

    The first base holds the first value of each of the terminal's own
    enumerated fields; the second is the hand-off, as :func:`handed_off`
    derives it.  Each variation holds its base's own values and moves one
    nested field — a ``bool``, ``Literal`` or ``Enum`` of any model the
    terminal nests, at any depth — to one of its values; every other nested
    field keeps its first value.  The count is the sum of the nested values,
    once per base, not their product.
    """
    first = {path: values[0] for path, values in own_sites(full)}
    nested = [(path, values) for path, values in sites(full) if len(path) > 1]
    return [
        {**base, path: value}
        for base in (first, {**first, **handed_off()})
        for path, values in nested
        for value in values
    ]


def terminals(full: WorkflowCompleteEvent) -> list[tuple[str, WorkflowCompleteEvent]]:
    """Each combination and each variation, built with the optionals unset and set.

    Cut from *full* with every enumerated field at every depth at its first
    value, so no instance inherits a choice somebody wrote into the
    template.  Each is built twice, once with every optional field unset and
    once with every one of them set, so a key sent only for some values, or
    only when a field holds something, is on the wire in one of these.
    """
    firsts = cut(
        full, filled=True, chosen={path: values[0] for path, values in sites(full)}
    )
    built = []
    for chosen in [*combinations(full), *variations(full)]:
        where = ", ".join(
            ".".join(str(step) for step in path) + f"={value}"
            for path, value in chosen.items()
        )
        for filled in (False, True):
            instance = cut(firsts, filled=filled, chosen=chosen)
            assert isinstance(instance, WorkflowCompleteEvent)
            built.append((f"{where}, {'set' if filled else 'unset'}", instance))
    return built


def enumerated(model: type[BaseModel]) -> dict[str, tuple[object, ...]]:
    """Every enumerable field of *model* itself, with the values it can take."""
    return {
        name: values
        for name, field in model.model_fields.items()
        if (values := choices(field.annotation))
    }


def expected_keys(value: BaseModel) -> dict[str, str]:
    """The key each field of *value* is sent under, and no other.

    The terminal goes out the same way on every path the handler emits it
    on: under its aliases, with every field that holds ``None`` left off —
    it is neither of the scope envelopes, the only events that keep their
    nulls.  So the keys are every declared field, under its alias, that
    holds something on this instance.
    """
    return {
        field.serialization_alias or field.alias or name: name
        for name, field in type(value).model_fields.items()
        if getattr(value, name) is not None
    }


#: A value the wire renders as a scalar: what JSON decodes a string, a
#: number or a boolean to.
SCALAR = str | int | float | bool


def declared_shape(annotation: object) -> object:
    """The shape a field typed *annotation* is sent in.

    A model is sent as a mapping, a list as a list of its items' shape,
    anything else as a scalar.  Read through ``Optional`` and ``Annotated``;
    a union of several shapes would need its own rule, so it reds here.
    """
    origin = get_origin(annotation)
    if origin in (Union, UnionType):
        shapes = {
            declared_shape(member)
            for member in get_args(annotation)
            if member is not type(None)
        }
        assert len(shapes) == 1, annotation
        return shapes.pop()
    if origin is Annotated:
        return declared_shape(get_args(annotation)[0])
    if origin is list:
        return ("list", declared_shape(get_args(annotation)[0]))
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        return annotation
    return SCALAR


def assert_sent_in_shape(held: object, sent: object, shape: object, where: str) -> None:
    """*sent* has the *shape* its field declares, recursively."""
    if shape is SCALAR:
        assert isinstance(sent, SCALAR), where
    elif isinstance(shape, tuple):
        assert isinstance(sent, list), where
        assert isinstance(held, list), where
        assert len(sent) == len(held), where
        for index, (item, sent_item) in enumerate(zip(held, sent, strict=True)):
            assert_sent_in_shape(item, sent_item, shape[1], f"{where}[{index}]")
    else:
        assert isinstance(held, BaseModel), where
        assert_sends_its_fields(held, sent, where)


def assert_sends_its_fields(value: BaseModel, sent: object, where: str) -> None:
    """*sent* carries exactly *value*'s own fields, each in its declared shape.

    An equality per instance: a key that one instance adds is a key that
    one wire carries.  Every value is then held to the shape its field
    declares — a scalar field is a scalar on the wire, never a mapping or a
    list; a model field is a mapping checked the same way; a list of models
    is a list whose every item is.  Bounded by the value's own depth.
    """
    assert isinstance(sent, Mapping), where
    keys = expected_keys(value)
    assert set(sent) == set(keys), where
    for key, name in keys.items():
        assert_sent_in_shape(
            getattr(value, name),
            sent[key],
            declared_shape(type(value).model_fields[name].annotation),
            f"{where} / {key}",
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


def state_keys(state: type) -> set[str]:
    """Every key the TypedDict *state* declares or merges from a base, by object.

    Read off the two registers the TypedDict machinery fills for the class
    — its required keys and its optional keys — and held equal to its
    merged annotations, so a key arriving through a base is the state's own
    here, and the two readings cannot drift apart.
    """
    keys = set(state.__required_keys__) | set(state.__optional_keys__)
    assert keys == set(state.__annotations__)
    return keys


def test_the_fire_s_state_keys_are_pinned_whole():
    """The fire's state type, as an exact key set read by object.

    Every key of ``WorkflowState``, declared on it or merged from a base,
    read from the TypedDict's own key registers rather than from its
    source, and held equal to :data:`STATE_KEYS`: a delivery key added to
    the state under any spelling reds here.  The one optional key is pinned
    as such too, so a key made optional is a change here as well.  The
    control merges a delivery key into the state through a base and shows
    the reading sees it.  The coordinator's public surface — ``deliver``
    and its signature — is pinned beside the lane's delivery tests.
    """
    assert state_keys(WorkflowState) == STATE_KEYS
    assert set(WorkflowState.__optional_keys__) == {"ruling_unrecorded"}

    class Delivered(WorkflowState):
        pr_url: str | None

    assert state_keys(Delivered) - STATE_KEYS == {"pr_url"}
    assert DELIVERY_FIELDS & state_keys(Delivered) == {"pr_url"}


def delivery_keys() -> set[str]:
    """Every key a delivery fact goes out under, by field name and by alias.

    Derived from the models: the fields the authored terminal declares
    beyond the fire's — the pull request's url and number, the CI status —
    and every field of the lane's delivery record but the fire's own facts
    the record repeats (:data:`SHARED_WITH_TERMINAL`), each under its field
    name and under the alias it is sent by.
    """
    authored = {
        name: field
        for name, field in AuthoredWorkflowCompleteEvent.model_fields.items()
        if name not in WorkflowCompleteEvent.model_fields
    }
    lane = {
        name: field
        for name, field in LaneDelivery.model_fields.items()
        if name not in SHARED_WITH_TERMINAL
    }
    return {
        key
        for fields in (authored, lane)
        for name, field in fields.items()
        for key in (name, field.serialization_alias or field.alias or name)
    }


def classified_outcomes() -> set[WorkflowOutcome]:
    """Every outcome the shipped classifier names, read off its source.

    The completion node classifies through :func:`classify_outcome`, so
    the outcomes it can emit are the members that function names; the
    rest of the enumeration is assigned at the queue boundary or by the
    delivery and scope arms, never by the fire's graph.  Bounded by the
    function's syntax tree.
    """
    tree = ast.parse(textwrap.dedent(inspect.getsource(classify_outcome)))
    return {
        WorkflowOutcome[node.attr]
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and node.value.id == "WorkflowOutcome"
    }


def trajectory(*, plateaued: bool, never_passed: list[str]) -> LoopTrajectory:
    """A one-record trajectory at the plateau flag and never-passed set asked."""
    return LoopTrajectory(
        records=[
            IterationRecord(
                iteration=1,
                passed_count=0,
                failing_criterion_ids=never_passed,
                commit_sha="d" * 40,
            )
        ],
        never_passed_ids=never_passed,
        best_passed_count=0,
        best_iteration=1,
        best_commit_sha="d" * 40,
        plateaued=plateaued,
    )


#: The subject a native state is addressed to, and the one criterion it is
#: graded against: the spec the state carries and the roster the engine's
#: reader answers name the same key, so the snapshot check the completion
#: node runs first admits the state.
SUBJECT = "KOD-313"
CRITERION = "KOD-314"


def native_spec() -> TrackerSpec:
    """The tracker spec a native state carries: the subject and its criterion."""
    return TrackerSpec(
        subject=IssueRef(SUBJECT),
        body="the subject's own text",
        criteria=(CriterionRef(CRITERION),),
        read_at_version="1",
    )


def native_roster() -> TrackerCriterionSet:
    """The roster the state records and the reader answers: the spec's criterion."""
    return TrackerCriterionSet(
        criteria=[TrackerCriterion(id=CriterionId(CRITERION), text="a check holds")]
    )


class HeldCriteria:
    """A criteria source that answers one spec and one roster, whatever is asked.

    What an engine needs to compile its tracker-native graph, and what the
    completion node's snapshot check reads: the roster it answers is the
    one every drive state records, so the check admits each of them, and
    every spec it was asked for is kept, so the drive can show the check
    ran on every state.
    """

    def __init__(self) -> None:
        self.spec = native_spec()
        self.roster = native_roster()
        self.asked: list[TrackerSpec] = []

    async def read_entry(
        self, *, issue_key: str, delivering: bool = False
    ) -> tuple[TrackerSpec, TrackerCriterionSet]:
        return self.spec, self.roster

    async def read_current(
        self, *, spec: TrackerSpec, held: TrackerCriterionSet | None = None
    ) -> TrackerCriterionSet:
        self.asked.append(spec)
        return self.roster

    async def owed_from(
        self,
        *,
        spec: TrackerSpec,
        criteria: Mapping[str, TrackerIssue],
        held: TrackerCriterionSet,
    ) -> TrackerCriterionSet:
        self.asked.append(spec)
        return self.roster


def fire_state(
    *,
    feature_tip_sha: str | None = None,
    criteria_validation: CriteriaValidation | None = None,
    unconfirmed: bool = False,
    **classified: object,
) -> WorkflowState:
    """A native state as the completion node reads it, with exactly the fire's keys.

    The classifier tests' neutral state with *classified* set on it, the
    lane's delivery keys taken off, and the keys the fire's prepare step
    writes that the neutral state does not carry added at the values
    prepare gives a fresh run against trunk; the terminal's other inputs
    are set as asked.  Addressed to the native subject — its issue key,
    the tracker spec and the roster the engine's reader answers — so the
    snapshot check the node runs first admits it.  The entry facts the
    drive crosses it with (the lane entry, the work base, the visibility)
    hold their fresh-run values here and are set by :func:`native_axes`.
    Held to the pinned key set, so the drive runs on the fire's own shape.
    """
    legacy = _state(**classified)
    state = WorkflowState(
        **{key: value for key, value in legacy.items() if key not in DELIVERY_FIELDS},
        lane_entry=None,
        work_base_ref="main",
        repo_visibility=RepoVisibility.UNKNOWN,
        best_iteration_branch=None,
    )
    state["issue_key"] = SUBJECT
    state["fire_spec"] = native_spec()
    state["criterion_set"] = native_roster()
    state["feature_tip_sha"] = feature_tip_sha
    state["criteria_validation"] = criteria_validation
    if unconfirmed:
        state["ruling_unrecorded"] = True
    assert set(WorkflowState.__required_keys__) <= set(state) <= STATE_KEYS
    return state


def lane_entry_kinds() -> tuple[type[BaseModel], ...]:
    """Every kind of lane entry, read off the ``LaneEntry`` union by object.

    The alias holds an ``Annotated`` union; its members are the kinds.
    """
    union, _ = get_args(LaneEntry.__value__)
    kinds = get_args(union)
    assert kinds != ()
    assert all(isinstance(kind, type) and issubclass(kind, BaseModel) for kind in kinds)
    return kinds


def entered(kind: type[BaseModel]) -> BaseModel:
    """One entry of *kind*, each required field filled by what its annotation admits.

    A string field holds a name; a field that admits ``None`` holds it; a
    ``bool`` field holds ``False``, which is what ``base_stale`` answers for
    a record that carries no dispatch base (KOD-888).  Any other required
    field reds here rather than being guessed at.
    """
    fields: dict[str, object] = {}
    for name, field in kind.model_fields.items():
        if not field.is_required():
            continue
        if field.annotation is str:
            fields[name] = f"{name}-pinned"
        elif field.annotation is bool:
            fields[name] = False
        else:
            assert type(None) in get_args(field.annotation), (kind, name)
            fields[name] = None
    return kind(**fields)


#: The loop branch every drive state names, which a recorded lane continues
#: as its work base: the neutral state's own.
LOOP_BRANCH = _state()["ralph_branch"]

#: The refs a run's next loop cuts from: trunk, as a fresh run holds, and
#: the loop branch a recorded lane continues.
WORK_BASES = ("main", LOOP_BRANCH)


def native_axes() -> dict[str, dict[str, object]]:
    """Every combination of the entry facts a native state carries, by name.

    Every lane entry — none, and one of each kind the ``LaneEntry`` union
    names, derived from the union by object — crossed with each work base
    and with every member of ``RepoVisibility``.  Bounded by the product
    of the three.
    """
    kinds = lane_entry_kinds()
    entries: list[BaseModel | None] = [None, *(entered(kind) for kind in kinds)]
    assert {type(entry) for entry in entries if entry is not None} == set(kinds)
    return {
        (
            f"{'no entry' if entry is None else type(entry).__name__}, "
            f"{'trunk' if base == 'main' else 'loop branch'}, {visibility.value}"
        ): {
            "lane_entry": entry,
            "work_base_ref": base,
            "repo_visibility": visibility,
        }
        for entry in entries
        for base in WORK_BASES
        for visibility in RepoVisibility
    }


def node_binding(graph: FireGraph, name: str) -> MethodType:
    """The bound method the compiled *graph* runs as its node *name*, by object.

    Read off the compiled graph's own node: the callable it wraps, which
    for a coroutine method is held as the node's async function.  A node
    bound to anything but a bound method reds here.
    """
    runnable = graph.nodes[name].bound
    bound = runnable.afunc if runnable.func is None else runnable.func
    assert isinstance(bound, MethodType), name
    return bound


#: The commit a merged hand-off's terminal carries.
LANDED = "m" * 40


def completions() -> dict[str, tuple[WorkflowOutcome, WorkflowState]]:
    """One state per outcome the classifier names, and the outcome it names.

    The loop exits are run with the trajectory's plateau flag set and, under
    the same exit, unset; the merged hand-off carries its commit and no
    merge error; the clean run's trajectory has nothing that never passed.
    """
    accepted: dict[str, object] = {
        "verdict": AcceptVerdict.accepted,
        "merged": True,
        "review_passed": True,
        "feature_tip_sha": LANDED,
    }
    return {
        "a merge that diverged": (
            WorkflowOutcome.merge_divergent,
            fire_state(merge_error="the branches diverged"),
        ),
        "a fix whose consolidation failed": (
            WorkflowOutcome.fix_consolidation_failed,
            fire_state(merge_error="the branches diverged", remediation_rounds_used=1),
        ),
        "a loop that plateaued": (
            WorkflowOutcome.loop_plateaued,
            fire_state(trajectory=trajectory(plateaued=True, never_passed=["AC-1"])),
        ),
        "a loop that did not plateau": (
            WorkflowOutcome.loop_not_accepted,
            fire_state(trajectory=trajectory(plateaued=False, never_passed=["AC-1"])),
        ),
        "a loop with no trajectory": (
            WorkflowOutcome.loop_not_accepted,
            fire_state(),
        ),
        "a loop that committed nothing": (
            WorkflowOutcome.zero_commit_no_pr,
            fire_state(
                best_iteration_sha=None,
                trajectory=trajectory(plateaued=True, never_passed=["AC-1"]),
            ),
        ),
        "a remediation budget spent": (
            WorkflowOutcome.remediation_budget_exhausted,
            fire_state(remediation_rounds_used=1),
        ),
        "an infeasible criteria sweep": (
            WorkflowOutcome.criteria_infeasible,
            fire_state(
                criteria_infeasible=True,
                criteria_validation=carried()["criteria_validation"],
            ),
        ),
        "an unconfirmed pin": (
            WorkflowOutcome.ruling_unrecorded,
            fire_state(unconfirmed=True),
        ),
        "a merged hand-off": (
            WorkflowOutcome.handed_off_for_delivery,
            fire_state(**accepted),
        ),
        "a clean run": (
            WorkflowOutcome.handed_off_for_delivery,
            fire_state(
                **accepted, trajectory=trajectory(plateaued=False, never_passed=[])
            ),
        ),
        "a review that failed": (
            WorkflowOutcome.review_failed_fix_budget_exhausted,
            fire_state(**{**accepted, "review_passed": False}),
        ),
    }


def declared_keys(
    value: BaseModel, *, by_alias: bool, exclude_none: bool
) -> dict[str, str]:
    """The key each field of *value* is dumped under, in the rendering asked for."""
    return {
        (field.serialization_alias or field.alias or name) if by_alias else name: name
        for name, field in type(value).model_fields.items()
        if not (exclude_none and getattr(value, name) is None)
    }


def assert_dumped_as_declared(
    value: BaseModel,
    dump: object,
    *,
    by_alias: bool,
    exclude_none: bool,
    where: str,
) -> None:
    """*dump* has exactly the keys *value*'s schema declares, at every depth."""
    assert isinstance(dump, Mapping), where
    keys = declared_keys(value, by_alias=by_alias, exclude_none=exclude_none)
    assert set(dump) == set(keys), where
    for key, name in keys.items():
        held = getattr(value, name)
        sent = dump[key]
        if isinstance(held, list):
            assert isinstance(sent, list), f"{where} / {key}"
            pairs = list(zip(held, sent, strict=True))
        else:
            pairs = [(held, sent)]
        for item, sent_item in pairs:
            if isinstance(item, BaseModel):
                assert_dumped_as_declared(
                    item,
                    sent_item,
                    by_alias=by_alias,
                    exclude_none=exclude_none,
                    where=f"{where} / {key}",
                )


def keys_in(dump: object) -> set[str]:
    """Every key of every mapping in *dump*, at any depth."""
    if isinstance(dump, Mapping):
        return {str(key) for key in dump} | {
            key for value in dump.values() for key in keys_in(value)
        }
    if isinstance(dump, list):
        return {key for item in dump for key in keys_in(item)}
    return set()


async def test_the_completion_node_emits_exactly_the_terminal_for_every_native_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The tracker-native completion node, run over every outcome under every entry.

    The fire's terminal is built in one place, the engine's completion
    node, which the egress drive replaces with a held composition and the
    machinery pins read as a class rather than run.  It is run here as the
    compiled tracker-native graph binds it — the engine is built with a
    criteria source, so that graph is compiled, and the node is read off
    it — with the stream writer a graph run would hand it and a config
    carrying a thread id, over tracker-native states: each carries the
    subject's issue key, a tracker spec and the roster the engine's reader
    answers, and the reader is shown never asked: the node reads nothing
    before it emits.  The states are one per outcome
    the shipped classifier names — derived from the classifier's source, so
    a member it starts producing is a run missing here; among them the
    plateau with the trajectory's flag set and, under the same loop exit,
    unset; the merged hand-off with its commit present and no merge error;
    and a clean run whose trajectory has nothing that never passed — each
    crossed with every entry fact the node could branch on: no lane entry
    and one of each kind the ``LaneEntry`` union names, trunk and the loop
    branch as the work base, and every repository visibility.  For each
    run: the event is exactly ``WorkflowCompleteEvent`` and not a subclass;
    its dump in python and in JSON mode, with and without aliases, with and
    without its ``None`` fields, has exactly the keys the schema declares at
    every depth; and no delivery key, under its field name or its alias, is
    anywhere in any of them.

    That is the reach: the node, over these states.  The authored arm
    legitimately emits ``AuthoredWorkflowCompleteEvent``, with its delivery
    fields, from its own graph, and is not driven here.
    """
    runs = completions()
    assert {outcome for outcome, _ in runs.values()} == classified_outcomes()
    assert classified_outcomes() != set()
    assert {
        state["trajectory"].plateaued
        for _, state in runs.values()
        if state["trajectory"] is not None
    } == {False, True}
    _, handed = runs["a merged hand-off"]
    assert handed["feature_tip_sha"] == LANDED and handed["merge_error"] is None
    _, clean = runs["a clean run"]
    assert clean["trajectory"] is not None
    assert clean["trajectory"].never_passed_ids == []
    for name, (_, state) in runs.items():
        assert state["issue_key"] == SUBJECT, name
        assert isinstance(state["fire_spec"], TrackerSpec), name
        assert state["ralph_branch"] == LOOP_BRANCH, name
    forbidden = delivery_keys()
    assert {"prUrl", "prNumber", "ciStatus"} <= forbidden
    assert forbidden & set(WorkflowCompleteEvent.model_fields) == set()
    axes = native_axes()
    kinds = lane_entry_kinds()
    assert len(axes) == (1 + len(kinds)) * len(WORK_BASES) * len(RepoVisibility)
    assert {facts["repo_visibility"] for facts in axes.values()} == set(RepoVisibility)
    assert {type(facts["lane_entry"]) for facts in axes.values()} == {
        type(None),
        *kinds,
    }

    criteria = HeldCriteria()
    assert isinstance(criteria, FireCriteriaSource)
    engine = fire(criteria=criteria)
    assert engine.native_graph is not None
    complete = node_binding(engine.native_graph, "complete")
    assert complete.__self__ is engine
    config = RunnableConfig(configurable={"thread_id": workflow_thread_id("pinned")})
    written: list[AgentEvent] = []
    monkeypatch.setattr(ralph_workflow, "get_stream_writer", lambda: written.append)
    driven = 0
    for name, (outcome, state) in runs.items():
        for entry, facts in axes.items():
            where = f"{name} ({entry})"
            run = WorkflowState(**{**state, **facts})
            assert set(run) == set(state), where
            del written[:]
            assert await complete(run, config) == {}, where
            driven += 1
            (event,) = written
            assert type(event) is WorkflowCompleteEvent, where
            assert event.outcome is outcome, where
            renderings = {
                (mode, by_alias, exclude_none): event.model_dump(
                    mode=mode, by_alias=by_alias, exclude_none=exclude_none
                )
                for mode in ("python", "json")
                for by_alias in (False, True)
                for exclude_none in (False, True)
            } | {
                ("json text", by_alias, exclude_none): json.loads(
                    event.model_dump_json(by_alias=by_alias, exclude_none=exclude_none)
                )
                for by_alias in (False, True)
                for exclude_none in (False, True)
            }
            assert len(renderings) == 12, where
            for (mode, by_alias, exclude_none), dump in renderings.items():
                rendering = f"{where}: {mode}, {'aliased' if by_alias else 'named'}"
                rendering += ", nones off" if exclude_none else ", nones on"
                assert_dumped_as_declared(
                    event,
                    dump,
                    by_alias=by_alias,
                    exclude_none=exclude_none,
                    where=rendering,
                )
                assert keys_in(dump) & forbidden == set(), rendering
    assert driven == len(runs) * len(axes)
    assert criteria.asked == []


#: The completion node, the builder it hands the writer's event through,
#: and every function the event's construction calls, each with the sha256
#: of its source text, as they stood when the site was closed — the way
#: :data:`EGRESS_PATH` pins the path.  The drive above runs the node over
#: the states it builds; a branch keyed on a state no drive builds moves a
#: digest here instead, and is made in the commit that updates the table,
#: which is the review this pin exists to force.  Held equal to the set
#: derived from the node's own source and its builder's.
CONSTRUCTION_SITE: dict[Callable[..., object], str] = {
    RalphWorkflowEngine._complete_node: (
        "48eac8e20f09d78cf877fb7757c8358241bde4e48c8ab450ba8dd0bc4ba64e7e"
    ),
    fire_terminal: "266b5d0a53e674c24e69768ca423520c429d26bdc93cdb06813cb2bff99b3285",
    gate_cleared: "d946d1754e3e827be61ef888723ad0fa88ef5ef5845e4c48409c76ac905426f3",
    classify_outcome: (
        "d6bb8236eee686089a804e475ec56791712d7242887343223a6b0656aedb0f35"
    ),
}


def event_builders(node: Callable[..., object]) -> set[Callable[..., object]]:
    """Every function *node*'s source calls to build the event it hands the writer.

    By object: the one call handed to the writer is held to be either a
    call of the name ``WorkflowCompleteEvent`` or a call of a bare name
    resolved in the node's module after import to a function, the builder.
    A builder's own source holds exactly one ``return``, and its value is a
    call of the name ``WorkflowCompleteEvent``.  Whichever call constructs
    the event is bound by that name, in its own module, to
    :class:`WorkflowCompleteEvent`; it and the call handed to the writer
    hold no conditional expression and no other callable: every call
    inside their arguments is of a bare name, resolved in its own module
    after import to a function.  The builder and those functions are
    returned.  Bounded by the node's syntax tree and the builder's.
    """
    module = inspect.getmodule(node)
    assert module is not None
    tree = ast.parse(textwrap.dedent(inspect.getsource(node)))
    handed = [
        call.args
        for call in ast.walk(tree)
        if isinstance(call, ast.Call)
        and isinstance(call.func, ast.Name)
        and call.func.id == "writer"
    ]
    assert len(handed) == 1
    (event,) = handed[0]
    assert isinstance(event, ast.Call)
    assert isinstance(event.func, ast.Name)
    sites: list[tuple[ModuleType, ast.Call]] = [(module, event)]
    found: set[Callable[..., object]] = set()
    if event.func.id != "WorkflowCompleteEvent":
        builder = vars(module)[event.func.id]
        assert inspect.isfunction(builder)
        found.add(builder)
        module = inspect.getmodule(builder)
        assert module is not None
        body = ast.parse(textwrap.dedent(inspect.getsource(builder)))
        returns = [part for part in ast.walk(body) if isinstance(part, ast.Return)]
        assert len(returns) == 1
        construction = returns[0].value
        assert isinstance(construction, ast.Call)
        assert isinstance(construction.func, ast.Name)
        assert construction.func.id == "WorkflowCompleteEvent"
        sites.append((module, construction))
    assert vars(module)["WorkflowCompleteEvent"] is WorkflowCompleteEvent
    for where, site in sites:
        for part in ast.walk(site):
            assert not isinstance(part, ast.IfExp), ast.dump(part)
            if isinstance(part, ast.Call) and part is not site:
                assert isinstance(part.func, ast.Name), ast.dump(part.func)
                found.add(vars(where)[part.func.id])
    assert all(inspect.isfunction(builder) for builder in found)
    return found


def test_the_completion_node_s_construction_site_is_closed_by_object() -> None:
    """The node's source, its builders' source and the graph's binding, by object.

    The drive above runs the node over the states it builds.  A branch on
    a state no drive builds is caught here instead, by pinning the text:
    the node's own source, the builder it hands the writer's event
    through, and the source of every function the event's construction
    calls — derived from the node's syntax tree and the builder's, and
    resolved in their modules — are held to their digests, so ANY change to
    them is made in the commit that updates the table.  The event handed
    to the writer is one call, of ``WorkflowCompleteEvent`` by name or of a
    builder whose one ``return`` is that call, with no conditional
    expression.  And the compiled tracker-native graph's
    ``complete`` node is bound to that very method, the engine's own bound
    ``RalphWorkflowEngine._complete_node``, so a graph pointed at another
    callable reds here.
    """
    node = RalphWorkflowEngine._complete_node
    assert CONSTRUCTION_SITE != {}
    assert {node, *event_builders(node)} == set(CONSTRUCTION_SITE)
    assert {site: source_digest(site) for site in CONSTRUCTION_SITE} == (
        CONSTRUCTION_SITE
    )
    engine = fire(criteria=HeldCriteria())
    assert engine.native_graph is not None
    bound = node_binding(engine.native_graph, "complete")
    assert bound.__func__ is node
    assert bound.__self__ is engine


#: What a class may define to be rendered some other way than by its
#: fields: pydantic's own entry points, which ``BaseModel`` defines and a
#: model overrides by defining one of its own, and the two hooks through
#: which a model's serialiser and schema are built.
SERIALISATION_HOOKS = frozenset(
    {
        "__get_pydantic_core_schema__",
        "__getstate__",
        "__iter__",
        "__pydantic_serializer__",
        "dict",
        "json",
        "model_dump",
        "model_dump_json",
    }
)

#: The schema types whose own function decides what is sent: a plain or a
#: wrap function replaces or wraps the default rendering of what it holds.
FUNCTION_SCHEMAS = frozenset({"function-plain", "function-wrap"})


def custom_serialisation(schema: object) -> list[str]:
    """Every place in a core *schema* where a value is not sent by default.

    A ``serialization`` entry of any kind — which is how pydantic records a
    ``model_serializer``, a ``field_serializer`` and a ``PlainSerializer`` or
    ``WrapSerializer`` annotation, however it is spelled — and any schema
    of a function type.  Walked through every mapping and list the schema
    holds, at any depth; bounded by the schema's own finite tree, each node
    taken once.
    """
    found: list[str] = []
    pending: list[tuple[str, object]] = [("schema", schema)]
    seen: set[int] = set()
    while pending:
        where, node = pending.pop()
        if id(node) in seen:
            continue
        seen.add(id(node))
        if isinstance(node, Mapping):
            if "serialization" in node:
                found.append(f"{where}.serialization")
            kind = node.get("type")
            if isinstance(kind, str) and kind in FUNCTION_SCHEMAS:
                found.append(f"{where}: {kind}")
            pending.extend((f"{where}.{key}", value) for key, value in node.items())
        elif isinstance(node, list | tuple):
            pending.extend(
                (f"{where}[{index}]", item) for index, item in enumerate(node)
            )
    return sorted(found)


def own_serialisation_hooks(cls: type) -> set[str]:
    """Each hook of :data:`SERIALISATION_HOOKS` *cls* defines in its own ``vars()``.

    Pydantic sets ``__pydantic_serializer__`` on every model class it
    builds, from that class's own core schema, which the walk above reads.
    That one is pydantic's rendering of the schema and not a hook of the
    class, so it is left out when it is exactly that serialiser: built by
    pydantic-core from the very schema object the class holds.  Any other
    serialiser there is one somebody put there, and is kept.
    """
    own = vars(cls)
    hooks = {name for name in own if name in SERIALISATION_HOOKS}
    serializer = own.get("__pydantic_serializer__")
    if type(serializer) is SchemaSerializer and serializer.__reduce__()[1][
        0
    ] is own.get("__pydantic_core_schema__"):
        hooks.discard("__pydantic_serializer__")
    return hooks


def closure_line() -> list[type[BaseModel]]:
    """Every class the terminal's machinery is made of, in a stable order.

    The terminal, every model its annotations reach (held equal to the
    rosters above), and every class on each one's line but ``object`` and
    pydantic's own ``BaseModel``: a validator or a config setting declared
    on a base is the terminal's as much as one declared on it.  Sorted by
    module and qualified name.
    """
    closure = set(models_under(WorkflowCompleteEvent))
    assert closure == {WorkflowCompleteEvent, *NESTED_FIELDS}
    line = {
        cls
        for model in closure
        for cls in model.__mro__
        if cls is not object and cls is not BaseModel
    }
    assert closure < line
    assert all(issubclass(cls, BaseModel) for cls in line)
    return sorted(line, key=qualified)


def qualified(cls: type) -> str:
    """*cls* by module and qualified name."""
    return f"{cls.__module__}.{cls.__qualname__}"


def test_nothing_on_the_terminal_s_closure_renders_it_but_its_fields():
    """No custom serialisation on the terminal or on any model it holds.

    The egress check below renders instances, and an instance shows a key
    only for the values somebody chose to build: a serialiser keyed on a
    value no instance holds sends a delivery fact for that value alone and
    passes it.  So the places such a key can be added are closed as objects
    rather than searched for with more values.  For the terminal and every
    model its annotations reach, and every class on each one's line but
    pydantic's own ``BaseModel``: the core schema pydantic serialises from,
    walked whole, holds no serialisation of its own; the model has no
    computed field; and the class defines none of pydantic's rendering
    entry points or hooks.  Whatever a model's values, what it sends is
    then its fields, as its schema declares them.
    """
    line = closure_line()
    closure = set(models_under(WorkflowCompleteEvent))
    assert {
        cls.__qualname__: found
        for cls in line
        if (found := custom_serialisation(cls.__pydantic_core_schema__))
    } == {}
    assert {
        model.__qualname__: model.model_computed_fields
        for model in closure
        if model.model_computed_fields
    } == {}
    assert {
        cls.__qualname__: hooks
        for cls in line
        if (hooks := own_serialisation_hooks(cls))
    } == {}


#: A ``ref`` as pydantic writes it into a core schema: the class's module
#: path and qualified name, then the class's address in this process.
ADDRESSED_REF = re.compile(r"^(?P<name>[\w.]+):\d+$")

#: The mark a ``repr`` leaves on a value shown with its address.
ADDRESS = re.compile(r" at 0x[0-9a-f]+")


def rendered(node: object, on_stack: frozenset[int] = frozenset()) -> object:
    """A core schema *node*, rendered so two processes render it alike.

    A mapping keeps its keys and a list or tuple its order.  A class, a
    function and a bound method are named by module and qualified name —
    the method by the class it is bound to as well — rather than shown
    with their address; the ``ref`` strings pydantic builds from a class's
    name and address lose the address; a JSON value is kept; anything else
    is its ``repr``.  Bounded by the schema's own finite tree: a node on
    the way to itself reds here.
    """
    assert id(node) not in on_stack
    below = on_stack | {id(node)}
    if isinstance(node, Mapping):
        return {
            str(key): (
                ADDRESSED_REF.sub(r"\g<name>", value)
                if key in ("ref", "schema_ref") and isinstance(value, str)
                else rendered(value, below)
            )
            for key, value in node.items()
        }
    if isinstance(node, list | tuple):
        return [rendered(item, below) for item in node]
    if node is None or isinstance(node, str | int | float | bool):
        return node
    if isinstance(node, type):
        return f"class {qualified(node)}"
    if isinstance(node, MethodType):
        bound_to = rendered(node.__self__, below)
        return f"method {rendered(node.__func__, below)} of {bound_to}"
    if isinstance(node, FunctionType):
        return f"function {node.__module__}.{node.__qualname__}"
    return repr(node)


def machinery_digest(line: Iterable[type[BaseModel]]) -> str:
    """One sha256 over the rendered core schema of every class in *line*.

    Keyed by qualified name and dumped with sorted keys, so the text is
    the same whichever order the classes come in and whichever process
    renders them; a value still shown with its address reds here rather
    than moving the digest from one run to the next.
    """
    text = json.dumps(
        {qualified(cls): rendered(cls.__pydantic_core_schema__) for cls in line},
        sort_keys=True,
    )
    assert ADDRESS.search(text) is None
    return hashlib.sha256(text.encode()).hexdigest()


#: The sha256 of the core schema of every class on the terminal's line,
#: rendered by :func:`rendered` and digested by :func:`machinery_digest`,
#: as they stood when the hand-off was pinned, under pydantic 2.12.5.  The
#: core schema is what pydantic validates and serialises from, so a field,
#: an alias, a default, a validator of any mode, a serializer of any kind or
#: an ``extra`` setting changed on any class on the line moves this digest,
#: and the change is made in the commit that updates it, which is the
#: review this pin exists to force.  A pydantic upgrade that lays a schema
#: out differently moves it as well, and is reviewed the same way.
TERMINAL_MACHINERY_DIGEST = (
    "ad6d5692fb766a02c3b7072f117a1a9975ccce415c5200d30d0202c89448e664"
)


def test_the_terminal_s_whole_machinery_is_pinned_by_digest():
    """Every class on the terminal's line, whole, in one digest and one control.

    The walk above flags custom serialisation.  A change on the validation
    side — a nested model letting extra keys through and a before-validator
    putting one in — leaves every roster, every hook and every
    serialisation entry as it was and still puts a key on the wire, because
    pydantic sends what ``extra='allow'`` kept.  So the whole core schema of
    every class on the line is pinned at once, rendered without addresses
    and digested, and ANY change to a field, an alias, a validator, a
    serializer or a config setting on any of them moves the digest.  The
    likeliest change is held apart in a readable assertion as well: every
    class on the line forbids extra keys.
    """
    line = closure_line()
    assert line != []
    assert {cls.__qualname__: cls.model_config.get("extra") for cls in line} == {
        cls.__qualname__: "forbid" for cls in line
    }
    assert machinery_digest(line) == TERMINAL_MACHINERY_DIGEST


#: How long the egress drive waits on any one step of the run it holds: the
#: bound the lane's delivery driver uses, so a run that never arrives reds on
#: a TimeoutError instead of hanging the module.
ATTACH_BOUND = 5

#: A route as the app holds it: its class, its methods, its path.
Row = tuple[str, tuple[str, ...], str]

#: Every route the app serves, of any class, each marked by whether it
#: answers with an event stream, and why.  Held equal to the app's routes as
#: they stand, so a route added under any declaration — a handler returning a
#: stream with no ``response_class`` and nothing documented, a starlette
#: ``Route``, a websocket, a mount — is a row nobody has classified, and the
#: pin reds until somebody does.
ROUTES: dict[Row, tuple[bool, str]] = {
    ("starlette.routing.Route", ("GET", "HEAD"), "/openapi.json"): (
        False,
        "the schema document, one JSON body",
    ),
    ("fastapi.routing.APIRoute", ("GET",), "/api/v1/health"): (
        False,
        "one health body",
    ),
    ("fastapi.routing.APIRoute", ("POST",), "/api/v1/agent/query"): (
        True,
        "streams the query's events as the agent run yields them",
    ),
    ("fastapi.routing.APIRoute", ("POST",), "/api/v1/agent/workflow"): (
        True,
        "streams the job's handle, then attaches to the queued run",
    ),
    ("fastapi.routing.APIRoute", ("POST",), "/api/v1/agent/fire"): (
        False,
        "answers the job's handle and nothing else",
    ),
    ("fastapi.routing.APIRoute", ("GET",), "/api/v1/jobs/{job_id}"): (
        False,
        "one job status body",
    ),
    ("fastapi.routing.APIRoute", ("GET",), "/api/v1/jobs/{job_id}/stream"): (
        True,
        "replays the job's buffer, then streams it live",
    ),
}

#: The headers every event-stream response carries, as they stood when the
#: egress was pinned: a fact sent in a header of its own is on the wire too.
STREAM_HEADERS = [("content-type", "text/event-stream; charset=utf-8")]


def route_rows(app: FastAPI) -> list[Row]:
    """Every route of *app*, whatever its class, as a row, sorted.

    A list rather than a set, so a route registered twice is two rows.
    """
    return sorted(
        (
            f"{type(route).__module__}.{type(route).__qualname__}",
            tuple(sorted(getattr(route, "methods", None) or ())),
            getattr(route, "path", repr(route)),
        )
        for route in app.routes
    )


def stream_routes() -> set[tuple[str, str]]:
    """Each method and path the route table marks as an event stream."""
    return {
        (method, path)
        for (_, methods, path), (stream, _) in ROUTES.items()
        if stream
        for method in methods
    }


def test_every_route_the_app_serves_is_classified():
    """The app's whole route table, by equality, each row marked stream or not.

    Read off ``app.routes`` for every route class, not only the ones that
    declare how they answer, so a new route of any kind reds here until it
    is classified.  Read under the configuration the test environment
    builds; the rows another configuration or the lifespan adds are
    measured below.
    """
    assert route_rows(create_app()) == sorted(ROUTES)
    assert stream_routes() != set()


#: The rows ``http.debug`` adds to :data:`ROUTES`: the interactive schema
#: pages, each one HTML body.  Measured with debug on, and driven there.
DEBUG_ROUTES: dict[Row, tuple[bool, str]] = {
    ("starlette.routing.Route", ("GET", "HEAD"), "/docs"): (
        False,
        "the interactive schema page, one HTML body",
    ),
    ("starlette.routing.Route", ("GET", "HEAD"), "/docs/oauth2-redirect"): (
        False,
        "the schema page's sign-in redirect, one HTML body",
    ),
    ("starlette.routing.Route", ("GET", "HEAD"), "/redoc"): (
        False,
        "the rendered schema page, one HTML body",
    ),
}

#: The rows running the app's lifespan adds, under either debug setting, in
#: the test environment's configuration: no tracker and no operation config.
LIFESPAN_ROUTES: dict[Row, tuple[bool, str]] = {}


@pytest.mark.parametrize("debug", [False, True], ids=["debug off", "debug on"])
async def test_every_configuration_serves_the_classified_routes(
    monkeypatch: pytest.MonkeyPatch, debug: bool
) -> None:
    """The route table under both ``debug`` settings, before and in the lifespan.

    The app is built from the test environment with ``http.debug`` set each
    way, its rows read, then read again with its lifespan entered, the way
    a served app is.  Each of those adds exactly the rows measured for it,
    so a route mounted only under ``debug``, or registered while an app of
    this configuration starts, is a row nobody classified and reds here.
    That is the reach: the lifespan is run with no tracker and no operation
    config, so a route registered only when one of those is configured is
    not read here.  The rows debug adds are driven on that app, and none of
    them streams.
    """
    monkeypatch.setenv("KODEZART_HTTP__DEBUG", "true" if debug else "false")
    app = create_app()
    assert app.state.config.http.debug is debug
    assert set(DEBUG_ROUTES) & set(ROUTES) == set()
    assert set(LIFESPAN_ROUTES) & {*ROUTES, *DEBUG_ROUTES} == set()
    table = {**ROUTES, **(DEBUG_ROUTES if debug else {})}
    before = route_rows(app)
    assert before == sorted(table)
    async with app.router.lifespan_context(app):
        within = route_rows(app)
    assert within == sorted({**table, **LIFESPAN_ROUTES})
    if debug:
        assert DEBUG_ROUTES != {}
        answers = {
            (method, path): await exchange(app, method, path)
            for _, methods, path in DEBUG_ROUTES
            for method in methods
        }
        statuses = {route: status for route, (status, _, _) in answers.items()}
        assert statuses == dict.fromkeys(answers, 200)
        assert {
            (method, path): streams(answers[(method, path)][1])
            for _, methods, path in DEBUG_ROUTES
            for method in methods
        } == {
            (method, path): stream
            for (_, methods, path), (stream, _) in DEBUG_ROUTES.items()
            for method in methods
        }


#: What one exchange answered: its status, its headers and, when it is an
#: event stream, its decoded frames.
Answer = tuple[int, list[tuple[str, str]], list[dict[str, object]]]


def streams(headers: list[tuple[str, str]]) -> bool:
    """Whether *headers* announce an event stream, whatever else they carry."""
    return any(
        name == "content-type" and value.startswith("text/event-stream")
        for name, value in headers
    )


def sse_frame(block: str) -> dict[str, object]:
    """One blank-line-separated SSE block, held to exactly what ``format_sse`` sends.

    Two field lines and nothing else: an ``event:`` line naming the data's
    own ``type``, then one ``data:`` line.  An ``id:`` line, a ``retry:``
    line, a comment line, a second data line or an event name the data does
    not carry reds here, so no fact rides in the framing unread.  Held to
    the bare newline line ends ``format_sse`` writes: a block using another
    line end is a block of other lines, and reds too.
    """
    lines = block.split("\n")
    assert len(lines) == 2, block
    event, data = lines
    assert data.startswith("data: "), block
    frame = json.loads(data.removeprefix("data: "))
    assert isinstance(frame, dict), block
    assert event == f"event: {frame['type']}", block
    return frame


async def exchange(
    app: FastAPI,
    method: str,
    path: str,
    *,
    body: Mapping[str, object] | None = None,
    on_frame: Callable[[dict[str, object]], Awaitable[None]] | None = None,
) -> Answer:
    """One request through the app's own ASGI callable, read as it is sent.

    The response's status and headers and, when the headers announce an
    event stream, every SSE frame decoded by :func:`sse_frame` the moment
    its body chunk is sent, handed to *on_frame* before the app may send
    the next.  So the caller acts between two frames the way a client
    reading the stream can, which a transport that collects the whole body
    first cannot.  Any other body is read to its end and not decoded.
    Bounded by ``ATTACH_BOUND`` per frame handler and for the whole
    exchange.
    """
    payload = b"" if body is None else json.dumps(body).encode()
    sent = asyncio.Event()
    asked = False
    started: dict[str, object] = {}
    headers: list[tuple[str, str]] = []
    pending = ""
    frames: list[dict[str, object]] = []

    async def receive() -> Message:
        nonlocal asked
        if not asked:
            asked = True
            return {"type": "http.request", "body": payload, "more_body": False}
        await sent.wait()
        return {"type": "http.disconnect"}

    async def send(message: Message) -> None:
        nonlocal pending
        if message["type"] == "http.response.start":
            started.update(message)
            headers.extend(
                (name.decode(), value.decode()) for name, value in message["headers"]
            )
            return
        if streams(headers):
            pending += bytes(message.get("body", b"")).decode()
            *blocks, pending = pending.split("\n\n")
            for block in blocks:
                frame = sse_frame(block)
                frames.append(frame)
                if on_frame is not None:
                    await asyncio.wait_for(on_frame(frame), timeout=ATTACH_BOUND)
        if not message.get("more_body", False):
            sent.set()

    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": method,
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "root_path": "",
        "headers": [(b"content-type", b"application/json")],
        "server": ("test", 80),
        "client": ("test", 1),
    }
    await asyncio.wait_for(app(scope, receive, send), timeout=ATTACH_BOUND)
    status = started["status"]
    assert isinstance(status, int), path
    assert sent.is_set(), path
    assert pending == "", path
    return status, headers, frames


class HeldComposition:
    """A compiled fire's stand-in: it sends its first event and holds the rest.

    Stands in for the compiled graph behind the fire engine's own ``run``,
    which streams it the way it streams the graph.  After the first event
    it waits on ``released``, so a client can attach while the run is still
    going: what it sent is then in the job's buffer, and what it holds goes
    out live.  The node the graph would run is driven and pinned above;
    what this drive observes is everything from the engine's run outward.
    """

    def __init__(self, events: list[AgentEvent]) -> None:
        self._events = events
        self.led = asyncio.Event()
        self.released = asyncio.Event()

    async def astream(
        self, initial_state: WorkflowState, *, config: RunnableConfig, stream_mode: str
    ) -> AsyncIterator[AgentEvent]:
        assert stream_mode == "custom"
        assert set(initial_state) == set(WorkflowState.__required_keys__)
        first, *held = self._events
        yield first
        self.led.set()
        await asyncio.wait_for(self.released.wait(), timeout=ATTACH_BOUND)
        for event in held:
            yield event


def held_relays(
    events: list[AgentEvent],
) -> tuple[OriginRoutedWorkflowEngine, HeldComposition]:
    """The shipped relays between the node and the queue, over a held composition.

    The origin-routed engine with the fire engine on both of its arms, the
    way composition wires a run's engine, and the fire engine built by the
    same factory the other fire tests use with its compiled graph replaced
    by :class:`HeldComposition` holding *events*.  So a terminal the queue
    receives has passed through ``OriginRoutedWorkflowEngine.run`` and
    ``RalphWorkflowEngine.run`` — and, for a merged hand-off, the
    consolidation's backup cleanup — exactly as a served run's does.
    """
    composition = HeldComposition(events)
    engine = fire()
    engine.graph = composition
    assert engine._composition(None) is composition
    routed = OriginRoutedWorkflowEngine(forge_arm=engine, forge_less_arm=engine)
    return routed, composition


#: What a client posts to start a run or a query; the held engine and the
#: fake executor ignore it.
BODY: dict[str, object] = {"prompt": "fix", "repoPath": "/tmp/fake"}


#: The one route whose run is held so that one attach meets the open job.
WORKFLOW = ("POST", "/api/v1/agent/workflow")


async def emitted(events: list[AgentEvent]) -> dict[tuple[str, str], Answer]:
    """What the app answers for *events* on every method of every route it has.

    Driven through the app's own routes, handler and queue, every method
    of every row of :data:`ROUTES`, whether the row is marked a stream or
    not, so whether a route streams is what it answers rather than what
    the table says: a job's path names the workflow's job, and a ``POST``
    carries :data:`BODY`.  Returns each one's status, headers and decoded
    frames, the workflow's leading handle taken off.  The query stream's
    events are what the agent run yields; the workflow's run and every
    other run the routes queue are the held composition's, relayed by the
    shipped engines (:func:`held_relays`); a later attach to the
    workflow's finished job replays its buffer.  Every job the drive
    queued has finished before the queue is stopped.

    The workflow run is held after its first event until that event reaches
    the client.  The handle frame waits until the first event is in the
    job's buffer, so the attach that follows replays it from an open
    stream; the first event's own frame releases the rest, which the queue
    then fans out to the attached client live.  The job is shown still
    running when that release is made, which is after the attach started.
    """
    app = create_app()
    app.state.skills = SUPPRESS_ALL_SKILLS
    app.state.agent_service = AgentService(
        git_base_url="https://github.com",
        executor=FakeAgentExecutor(events=list(events)),
        workspace=FakeWorkspaceProvider(),
        persister=None,
    )
    relays, held = held_relays(events)
    job: list[str] = []
    open_at_release: list[bool] = []
    async with attached_job_queue(
        app, relays, event_buffer_capacity=len(events)
    ) as queue:
        app.state.job_service = build_job_service(registry=queue, checkpointer=None)

        async def attached(frame: dict[str, object]) -> None:
            if frame["type"] == "job_accepted":
                job.append(str(frame["jobId"]))
                await held.led.wait()
            elif not held.released.is_set():
                state = queue.registry.records[job[0]].state
                open_at_release.append(state is not JobState.TERMINAL)
                held.released.set()

        status, headers, (handle, *run) = await exchange(
            app, *WORKFLOW, body=BODY, on_frame=attached
        )
        assert handle["type"] == "job_accepted"
        assert open_at_release == [True]
        answers = {WORKFLOW: (status, headers, run)}
        for _, methods, path in sorted(ROUTES):
            for method in methods:
                if (method, path) == WORKFLOW:
                    continue
                concrete = path.replace("{job_id}", job[0])
                assert "{" not in concrete, path
                answers[(method, path)] = await exchange(
                    app, method, concrete, body=BODY if method == "POST" else None
                )

        async def settled() -> None:
            while any(
                record.state is not JobState.TERMINAL
                for record in queue.registry.records.values()
            ):
                await asyncio.sleep(0)

        await asyncio.wait_for(settled(), timeout=ATTACH_BOUND)
    assert {
        route: status
        for route, (status, _, _) in answers.items()
        if not 200 <= status < 300
    } == {}
    return answers


def test_every_route_has_a_method_to_drive():
    """Each row of the table names a method, so the drive above reaches it."""
    assert [row for row in ROUTES if not row[1]] == []


async def test_the_stream_column_is_what_each_route_answers():
    """Whether a route streams is observed, not restated.

    Every method of every row is driven once, with a JSON body on a
    ``POST`` and the request headers :func:`exchange` sends, and a route
    streams exactly when it answers with ``text/event-stream``.  The
    table's mark for each row must equal that, so a route that starts
    streaming the terminal for that request under an unchanged class,
    method and path reds here until it is reclassified and driven as a
    stream.  A route that streams only for another request — another
    ``Accept`` header, another body — is not driven as one here.
    """
    full = template()
    answers = await emitted([full, full.model_copy()])
    assert set(answers) == {
        (method, path) for _, methods, path in ROUTES for method in methods
    }
    observed = {
        row: {streams(answers[(method, row[2])][1]) for method in row[1]}
        for row in ROUTES
    }
    assert observed == {row: {stream} for row, (stream, _) in ROUTES.items()}


async def test_what_production_sends_for_the_terminal_is_its_fields_and_no_more():
    """What the app EMITS for the terminal, on every route that streams it.

    Compared at the egress, not at a rendering helper: the frames are read
    off the app's own event-stream routes as they are sent, so a key the
    handler, the queue, a route or the SSE framing adds for one of the
    instances built here is on the wire here.  A key added for a value no
    instance holds is not; that is what the machinery and completion-node
    pins above are for.  Every route is driven, the routes that answer
    with an event stream are held equal to the routes the table above marks
    as streams, and each one's headers to the pinned set.  The workflow
    stream is attached while its run is going, so the queue's replay of an
    open job and its live fan-out both carry terminals here; the later
    attach reads the replay of the finished job.

    Per instance, an equality against the keys the model derives for it,
    and every value in the shape its field declares, recursively.  The
    instances are every combination of the terminal's own enumerated
    values, and one per value of every nested enumerable field at any depth
    from two bases — the first values, and the hand-off the shipped
    classifier gives for an accepted, merged, reviewed run — each once with
    every optional field unset and once with every one of them set.
    """
    optional = {
        name
        for name, field in WorkflowCompleteEvent.model_fields.items()
        if not field.is_required()
    }
    required = set(WorkflowCompleteEvent.model_fields) - optional
    own = enumerated(WorkflowCompleteEvent)
    assert set(REQUIRED) == required - set(own)
    assert set(carried()) == optional - set(own)
    assert set(own["outcome"]) == set(WorkflowOutcome)
    assert set(own["accepted"]) == {False, True}

    full = template()
    assert models_held(full) == set(models_under(WorkflowCompleteEvent))
    assert unfilled(full) == []
    assert handed_off() == {
        ("accepted",): True,
        ("merged",): True,
        ("outcome",): WorkflowOutcome.handed_off_for_delivery,
    }
    assert set(handed_off()) <= {path for path, _ in own_sites(full)}
    assert {path for path, _ in own_sites(full)} == {(name,) for name in own}
    assert len(combinations(full)) == prod(len(values) for values in own.values())
    nested = sum(len(values) for path, values in sites(full) if len(path) > 1)
    assert nested > 0
    assert len(variations(full)) == 2 * nested
    built = terminals(full)
    assert len(built) == 2 * (len(combinations(full)) + len(variations(full)))

    sent = await emitted([terminal for _, terminal in built])
    streamed = {route for route, (_, headers, _) in sent.items() if streams(headers)}
    assert streamed == stream_routes()
    for route in sorted(streamed):
        status, headers, frames = sent[route]
        assert status == 200, route
        assert headers == STREAM_HEADERS, route
        assert len(frames) == len(built), route
        for (name, terminal), frame in zip(built, frames, strict=True):
            assert_sends_its_fields(terminal, frame, f"{route}: {name}")


#: Where the shipped package's source lives: a frame whose code was read
#: from a file under it runs production code.
PACKAGE_ROOT = Path(inspect.getfile(kodezart)).parent

#: Every function of the shipped package the drive below observes holding
#: the terminal, or its rendering, as an argument or a return value on a
#: route that streams it, and every package function on the stack at that
#: moment, each with the sha256 of its source text.  Pinned whole: a change
#: to any of them — or a function joining or leaving the observed set — has
#: to update this table deliberately, which is the review this pin exists to
#: force.  Held equal to the set as the drive observes it.  The path runs
#: from the engine relays — the origin-routed engine's run and the fire
#: engine's run, which stream the terminal from the graph to the queue, and
#: the consolidation's backup cleanup, which is handed it after — through
#: the queue's worker, its publish and its buffer's stream, the handler's
#: rendering, the routes and the SSE framing.  What it pins is these
#: functions' text as they stand.  It does not claim that no other code
#: can put a key on the wire: a function that holds the terminal only in a
#: local between its call and its return is not observed, and transport
#: after the handler's rendering — the frame's bytes, the ASGI messages
#: that carry them, middleware over them — is outside this module's reach.
EGRESS_PATH: dict[Callable[..., object], str] = {
    OriginRoutedWorkflowEngine.run: (
        "08b5380d0ce22388550179f653ba254701899542a01fcf65580f348cddba07d0"
    ),
    RalphWorkflowEngine.run: (
        "324b17ce17550d1bd0859cbc70f8958dffcd8d7694b244a3f6fa7d0a5cf88e08"
    ),
    FireConsolidation.cleanup_backups: (
        "353aed993557d1dd1e9095121da74b4416e6bf5ae87b7f7fdd97b811ab5087ee"
    ),
    asyncio_job_queue.AsyncioJobQueue._worker: (
        "55544aa6ca52e76b284c27b3d9de15540a8f597454659d529fdd9bfaa78a9cf6"
    ),
    asyncio_job_queue.AsyncioJobQueue._run_job: (
        "d5ce295e946726cf3de544d371a971ceee8bf830c9cdebac9956c1504eb07805"
    ),
    asyncio_job_queue.AsyncioJobQueue._publish: (
        "d6a85e1885ad36f4ca75934bd37c541f65f26ad643520400b48411d0270a61f7"
    ),
    asyncio_job_queue._JobStream.publish: (
        "2ce3207a884a56d24921e3fe5f5af3edf47904437a0ca1fb825957d505e5266d"
    ),
    asyncio_job_queue._JobStream.stream: (
        "16713e2573486f8b4b1c70d0054ed76f8cf8648710da53e7f078ea7a78ee0012"
    ),
    AgentService.stream: (
        "df524d52ffd4a599d2b539ec42f7c69988d92a584c731209be308712192eb042"
    ),
    AgentService._run_in_workspace: (
        "77df3f7aa17ae2ead48e9a1e948b9b332ff306d5a4a032945c912f916186834d"
    ),
    agent_handler._queued_event_payload: (
        "0a3e97589e823a8da2cbfe87e9d0d4e439af480bcc27ea7a02bea0fbe6fe2192"
    ),
    agent_handler._streamed_event_payload: (
        "7d0987455078317117be5bf99d57f3e70d72128180a31ccd83ff42109fa9c3a0"
    ),
    agent_handler.AgentHandler.stream_query: (
        "771fa1ef61a6209a2356185c627231826c6f1085cb76d3cec45b19a55c1d1739"
    ),
    agent_handler.AgentHandler.attach_job: (
        "2cb907ec716d4503ee419a14cf4cca0938642daa0e24add04382ea93df8f4359"
    ),
    agent_handler.AgentHandler.stream_workflow: (
        "e9b9e5fbb7cd223451981abaab0eb24bb8af6fd6a7d51035a5f1272121e91256"
    ),
    agent_routes.stream_query: (
        "dcef427f982ff12411c38879358dff9c9275223383329ead6ee73f1c02ed7bed"
    ),
    agent_routes.stream_workflow: (
        "cbba11e3e1f1cc49bf238fbc1d6df536c4951af7a8bf420b893feabff3873ae4"
    ),
    job_routes.stream_job: (
        "de117e987b5560c5e755a6940859aeeceb4854631c87d1fd9b7497eb4e7c4e1b"
    ),
    format_sse: "08c5bddea68439bc1f3a4bfc7f84b2c3dd443c5810a5a34380481ec2c1572f8f",
}


def carries_terminal(value: object) -> bool:
    """Whether *value* is the terminal, or its rendering on the way out.

    The event itself, the mapping the handler renders it to, or the SSE
    frame text that mapping is formatted into.  Bytes, and an ASGI message
    carrying them, are not recognised: transport after the handler's
    rendering is outside this module's reach, and
    :func:`test_transport_after_the_rendering_is_outside_the_reach` holds
    that limit.
    """
    if isinstance(value, WorkflowCompleteEvent):
        return True
    if isinstance(value, dict):
        return value.get("type") == "workflow_complete"
    if isinstance(value, str):
        return '"type": "workflow_complete"' in value
    return False


def test_transport_after_the_rendering_is_outside_the_reach() -> None:
    """The one stated limit, held: what follows the rendering is not observed.

    The terminal, the mapping the handler renders it to and the frame text
    that mapping is formatted into are each recognised — the three forms
    the observed path is derived from.  The frame's bytes, and the ASGI
    body message that carries them, are not: a function that works on
    either is outside the observed set by construction, which is the fact
    the module docstring states.
    """
    terminal = template()
    payload = agent_handler._streamed_event_payload(terminal)
    frame = format_sse(payload)
    assert carries_terminal(terminal)
    assert carries_terminal(payload)
    assert carries_terminal(frame)
    assert not carries_terminal(frame.encode())
    assert not carries_terminal(
        {"type": "http.response.body", "body": frame.encode(), "more_body": True}
    )


def function_of(code: CodeType) -> Callable[..., object]:
    """The module function or method *code* belongs to, by object.

    Read off the code's module and qualified name; a function nested in
    another — a route's ``generate`` — belongs to the one it is nested in,
    whose source holds it.  Bounded by the parts of the name.
    """
    owner: object = inspect.getmodule(code)
    for part in code.co_qualname.split(".<locals>.")[0].split("."):
        member = inspect.getattr_static(owner, part)
        owner = getattr(member, "__func__", member)
    assert inspect.isfunction(owner), code
    return owner


def source_digest(function: Callable[..., object]) -> str:
    """The sha256 of *function*'s source text, as ``inspect`` reads it."""
    return hashlib.sha256(inspect.getsource(function).encode()).hexdigest()


async def egress_path(events: list[AgentEvent]) -> set[Callable[..., object]]:
    """Every production function observed holding the terminal on its way out.

    Observed, not listed: the app is driven over every route it serves
    (:func:`emitted`) with a profile hook on the thread.  Whenever a
    function of the shipped package is entered holding the terminal or its
    rendering among its arguments, or hands one back — a return, or a
    generator's yield — every production function on the stack at that
    moment is in the set: the worker that publishes the event, the queue's
    stream that replays and fans it out, the handler that renders it, the
    route that frames it.  A function that holds the terminal only in a
    local between those two moments, and one that works on bytes, is not
    observed.  Bounded by the calls the drive makes and, per call, by the
    stack's depth.
    """
    path: set[CodeType] = set()
    root = str(PACKAGE_ROOT)

    def observe(frame: FrameType, event: str, arg: object) -> None:
        if event not in ("call", "return"):
            return
        if not frame.f_code.co_filename.startswith(root):
            return
        held = [arg] if event == "return" else list(frame.f_locals.values())
        if not any(map(carries_terminal, held)):
            return
        on_stack: FrameType | None = frame
        while on_stack is not None:
            if on_stack.f_code.co_filename.startswith(root):
                path.add(on_stack.f_code)
            on_stack = on_stack.f_back

    previous = sys.getprofile()
    sys.setprofile(observe)
    try:
        await emitted(events)
    finally:
        sys.setprofile(previous)
    return {function_of(code) for code in path}


async def test_the_egress_path_is_pinned_whole():
    """The functions observed holding the terminal, by object and source.

    The instances the egress check renders can only show a key for the
    values somebody built, so the code observed handling the terminal is
    pinned as well: the set is derived by driving every route the table
    holds and observing which production functions are entered with the
    terminal or hand it back, and that set must equal :data:`EGRESS_PATH`,
    each function's source still hashing to what the table holds.  What
    this pins is that text; the hand-off claim itself rests on the state,
    the terminal's machinery and the completion node, pinned above.
    """
    full = template()
    derived = await egress_path([full, full.model_copy()])
    assert EGRESS_PATH != {}
    assert {
        f"{function.__module__}.{function.__qualname__}" for function in derived
    } == {f"{function.__module__}.{function.__qualname__}" for function in EGRESS_PATH}
    assert derived == set(EGRESS_PATH)
    assert {function: source_digest(function) for function in EGRESS_PATH} == (
        EGRESS_PATH
    )

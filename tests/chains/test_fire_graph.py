"""The actual compiled fire excludes every delivery node and route."""

import asyncio
import hashlib
import inspect
import json
import sys
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from enum import Enum
from itertools import product
from math import prod
from pathlib import Path
from types import CodeType, FrameType, UnionType
from typing import Annotated, Literal, Union, get_args, get_origin

from fastapi import FastAPI
from pydantic import BaseModel
from pydantic_core import SchemaSerializer
from starlette.types import Message

import kodezart
from kodezart.adapters import asyncio_job_queue
from kodezart.api.v1.endpoints import agent as agent_routes
from kodezart.api.v1.endpoints import jobs as job_routes
from kodezart.chains.ralph_workflow import RalphWorkflowEngine
from kodezart.domain.accept_gate import gate_cleared
from kodezart.domain.outcome import classify_outcome
from kodezart.handlers import agent_handler
from kodezart.main import create_app
from kodezart.services.agent_service import AgentService
from kodezart.types.domain.accept import AcceptVerdict
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
from kodezart.types.domain.job import JobState
from kodezart.types.domain.outcome import WorkflowOutcome
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
    declare how they answer, so no spelling of a stream route escapes the
    table: a new route of any kind reds here until it is classified.
    """
    assert route_rows(create_app()) == sorted(ROUTES)
    assert stream_routes() != set()


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
) -> tuple[list[tuple[str, str]], list[dict[str, object]]]:
    """One request through the app's own ASGI callable, read as it is sent.

    The response's headers, and every SSE frame decoded by :func:`sse_frame`
    the moment its body chunk is sent, handed to *on_frame* before the app
    may send the next.  So the caller acts between two frames the way a
    client reading the stream can, which a transport that collects the
    whole body first cannot.  Bounded by ``ATTACH_BOUND`` per frame handler
    and for the whole exchange.
    """
    payload = b"" if body is None else json.dumps(body).encode()
    sent = asyncio.Event()
    asked = False
    started: dict[str, object] = {}
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
            return
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
    assert started["status"] == 200, path
    assert sent.is_set(), path
    assert pending == "", path
    raw = started["headers"]
    assert isinstance(raw, list), path
    headers = [(name.decode(), value.decode()) for name, value in raw]
    return headers, frames


class HeldFire:
    """A workflow engine whose one run sends its first event and holds the rest.

    Stands in for the fire behind the shipped queue.  After the first event
    it waits on ``released``, so a client can attach while the run is still
    going: what it sent is then in the job's buffer, and what it holds goes
    out live.
    """

    def __init__(self, events: list[AgentEvent]) -> None:
        self._events = events
        self.led = asyncio.Event()
        self.released = asyncio.Event()

    async def run(self, **_: object) -> AsyncIterator[AgentEvent]:
        first, *held = self._events
        yield first
        self.led.set()
        await asyncio.wait_for(self.released.wait(), timeout=ATTACH_BOUND)
        for event in held:
            yield event


#: What a client posts to start a run or a query; the held engine and the
#: fake executor ignore it.
BODY: dict[str, object] = {"prompt": "fix", "repoPath": "/tmp/fake"}


async def emitted(
    events: list[AgentEvent],
) -> dict[tuple[str, str], tuple[list[tuple[str, str]], list[dict[str, object]]]]:
    """What the app emits for *events* on every event-stream route it has.

    Driven through the app's own routes, handler and queue: the query
    stream, whose events are what the agent run yields; the workflow
    stream, attached while its run is still going; and a later attach to
    that finished job, which replays its buffer.  Returns each route's
    headers and decoded frames, the workflow's leading handle taken off.

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
    fire = HeldFire(events)
    job: list[str] = []
    open_at_release: list[bool] = []
    sent: dict[
        tuple[str, str], tuple[list[tuple[str, str]], list[dict[str, object]]]
    ] = {}
    async with attached_job_queue(
        app, fire, event_buffer_capacity=len(events)
    ) as queue:

        async def attached(frame: dict[str, object]) -> None:
            if frame["type"] == "job_accepted":
                job.append(str(frame["jobId"]))
                await fire.led.wait()
            elif not fire.released.is_set():
                state = queue.registry.records[job[0]].state
                open_at_release.append(state is not JobState.TERMINAL)
                fire.released.set()

        sent[("POST", "/api/v1/agent/query")] = await exchange(
            app, "POST", "/api/v1/agent/query", body=BODY
        )
        headers, (handle, *run) = await exchange(
            app, "POST", "/api/v1/agent/workflow", body=BODY, on_frame=attached
        )
        assert handle["type"] == "job_accepted"
        assert open_at_release == [True]
        sent[("POST", "/api/v1/agent/workflow")] = (headers, run)
        sent[("GET", "/api/v1/jobs/{job_id}/stream")] = await exchange(
            app, "GET", f"/api/v1/jobs/{job[0]}/stream"
        )
    return sent


async def test_what_production_sends_for_the_terminal_is_its_fields_and_no_more():
    """What the app EMITS for the terminal, on every route that streams it.

    Compared at the egress, not at a rendering helper: the frames are read
    off the app's own event-stream routes as they are sent, so a key the
    handler, the queue, a route or the SSE framing adds after an event is
    rendered is on the wire here exactly when it is in production.  The
    routes driven are held equal to the routes the table above marks as
    streams, and each one's headers to the pinned set.  The workflow stream
    is attached while its run is going, so the queue's replay of an open job
    and its live fan-out both carry terminals here; the later attach reads
    the replay of the finished job.

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
    assert set(sent) == stream_routes()
    for route, (headers, frames) in sent.items():
        assert headers == STREAM_HEADERS, route
        assert len(frames) == len(built), route
        for (name, terminal), frame in zip(built, frames, strict=True):
            assert_sends_its_fields(terminal, frame, f"{route}: {name}")


#: Where the shipped package's source lives: a frame whose code was read
#: from a file under it runs production code.
PACKAGE_ROOT = Path(inspect.getfile(kodezart)).parent

#: Every function between a terminal event and the bytes a client reads, on
#: every route that streams it, each with the sha256 of its source text.
#: A key the handler, the queue or the framing adds for some values of the
#: terminal and not others is added in one of these, whatever values the
#: egress check renders, so the functions are pinned whole: a change to any
#: of them — or a function joining or leaving the path — has to update this
#: table deliberately, which is the review this pin exists to force.  Held
#: equal to the path as the drive below observes it.
EGRESS_PATH: dict[Callable[..., object], str] = {
    asyncio_job_queue.AsyncioJobQueue._worker: (
        "55544aa6ca52e76b284c27b3d9de15540a8f597454659d529fdd9bfaa78a9cf6"
    ),
    asyncio_job_queue.AsyncioJobQueue._run_job: (
        "5763ef3bc64dbad043b3d439170dc9d824aa58ccd25ed51bf38f28a371eee9dc"
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
        "aa935c671ec4e9ea9a61c61bff49b6ceffa2a9888e016aa490699a7be38f4b9e"
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
    frame text that mapping is formatted into.
    """
    if isinstance(value, WorkflowCompleteEvent):
        return True
    if isinstance(value, dict):
        return value.get("type") == "workflow_complete"
    if isinstance(value, str):
        return '"type": "workflow_complete"' in value
    return False


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
    """Every production function the terminal passes through on its way out.

    Observed, not listed: the app is driven over every route that streams
    (:func:`emitted`) with a profile hook on the thread.  Whenever a
    function of the shipped package is entered holding the terminal or its
    rendering, or hands one back — a return, or a generator's yield — every
    production function on the stack at that moment is on the path: the
    worker that publishes the event, the queue's stream that replays and
    fans it out, the handler that renders it, the route that frames it.
    Bounded by the calls the drive makes and, per call, by the stack's
    depth.
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
    """Every function between the terminal and the wire, by object and source.

    The instances the egress check renders can only show a key for the
    values somebody built, so the code that could add one is pinned
    instead: the path is derived by driving every stream route the table
    marks and observing which production functions the terminal passes
    through, and that set must equal :data:`EGRESS_PATH`, each function's
    source still hashing to what the table holds.
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

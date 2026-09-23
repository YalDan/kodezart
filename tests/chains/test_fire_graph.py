"""The actual compiled fire excludes every delivery node and route."""

import inspect
import json
from collections.abc import AsyncIterator, Mapping
from enum import Enum
from pathlib import Path
from types import UnionType
from typing import Annotated, Literal, Union, get_args, get_origin

from fastapi import FastAPI
from fastapi.routing import APIRoute
from httpx import ASGITransport, AsyncClient, Response
from pydantic import BaseModel
from starlette.responses import StreamingResponse

from kodezart.chains.ralph_workflow import RalphWorkflowEngine
from kodezart.main import create_app
from kodezart.services.agent_service import AgentService
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


def cut(template: BaseModel, *, filled: bool, path: Site, value: object) -> BaseModel:
    """A copy of *template* with the field at *path* set to *value*.

    Every other enumerated field takes its first value, so no instance
    inherits a choice somebody wrote into the template.  With *filled*,
    every optional field holds the template's value; without it, every
    optional field is left unset, except the ones on the way to *path*,
    which have to be there for the field to be.  An empty *path* varies
    nothing and is the base instance.  Built through the model, so every
    instance is one the model accepts.
    """
    fields: dict[str, object] = {}
    for name, field in type(template).model_fields.items():
        on_path = bool(path) and path[0] == name
        if on_path and len(path) == 1:
            fields[name] = value
            continue
        values = choices(field.annotation)
        if not (on_path or filled or field.is_required()):
            continue
        if values:
            fields[name] = values[0]
            continue
        rest = path[1:] if on_path else ()
        fields[name] = cut_value(
            getattr(template, name), filled=filled, path=rest, value=value
        )
    return type(template)(**fields)


def cut_value(held: object, *, filled: bool, path: Site, value: object) -> object:
    """*held* rebuilt by :func:`cut` when it is a model or a list of models."""
    if isinstance(held, BaseModel):
        return cut(held, filled=filled, path=path, value=value)
    if isinstance(held, list):
        return [
            cut_value(
                item,
                filled=filled,
                path=path[1:] if path and path[0] == index else (),
                value=value,
            )
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


def terminals(
    template: WorkflowCompleteEvent,
) -> list[tuple[str, WorkflowCompleteEvent]]:
    """One base terminal, and one per value of every enumerable field.

    One field at a time, at any depth: the terminal's own ``accepted``,
    ``outcome``, ``merged``, and every ``bool``, ``Literal`` and ``Enum`` of
    every model it nests.  Each is built twice, once with every optional
    field unset and once with every one of them set, so a key sent only for
    one value, or only when a field holds something, is on the wire in
    exactly one of these.  The count is the sum of the values, not their
    product.
    """
    variations: list[tuple[Site, object]] = [((), None)]
    variations.extend(
        (path, value) for path, values in sites(template) for value in values
    )
    built = []
    for path, value in variations:
        where = ".".join(str(step) for step in path) + f"={value}" if path else "base"
        for filled in (False, True):
            instance = cut(template, filled=filled, path=path, value=value)
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


class ScriptedFire:
    """A workflow engine whose one run emits the given terminals, in order.

    Stands in for the fire behind the shipped queue, so the frames the
    queued path emits are the queue's and the handler's own, from the
    engine's first event to the stream's end.
    """

    def __init__(self, events: list[AgentEvent]) -> None:
        self._events = events

    async def run(self, **_: object) -> AsyncIterator[AgentEvent]:
        for event in self._events:
            yield event


def event_stream_routes(app: FastAPI) -> set[tuple[str, str]]:
    """Every route of *app* that answers with an event stream, with its method.

    Read off the app's own routes, by the response class or the documented
    ``text/event-stream`` content, so a stream route added anywhere is
    counted here without anybody naming it.
    """
    found: set[tuple[str, str]] = set()
    for route in app.routes:
        if not isinstance(route, APIRoute):
            continue
        documented = route.responses.get(200, {}).get("content", {})
        if route.response_class is StreamingResponse or (
            "text/event-stream" in documented
        ):
            found |= {(method, route.path) for method in route.methods}
    return found


async def sse_frames(response: Response) -> list[dict[str, object]]:
    """Every ``data:`` frame of an event stream, decoded as the client reads it."""
    assert response.status_code == 200
    frames: list[dict[str, object]] = []
    async for line in response.aiter_lines():
        if line.startswith("data: "):
            frames.append(json.loads(line.removeprefix("data: ")))
    return frames


#: What a client posts to start a run or a query; the scripted engine and
#: the fake executor ignore it.
BODY: dict[str, object] = {"prompt": "fix", "repoPath": "/tmp/fake"}


async def emitted(
    events: list[AgentEvent],
) -> tuple[dict[tuple[str, str], list[dict[str, object]]], set[tuple[str, str]]]:
    """What the app emits for *events* on every event-stream route it has.

    Driven over HTTP, through the app's own routes, handler and queue: the
    live query stream, whose events are what the agent run yields; the
    workflow stream, which leads with the job's handle and then attaches to
    the queued run; and a later attach to that finished job, which replays
    its buffer.  Returns the decoded frames per route, the handle stripped,
    and every event-stream route the app declares, so the caller can hold
    the two sets equal.
    """
    app = create_app()
    app.state.skills = SUPPRESS_ALL_SKILLS
    app.state.agent_service = AgentService(
        git_base_url="https://github.com",
        executor=FakeAgentExecutor(events=list(events)),
        workspace=FakeWorkspaceProvider(),
        persister=None,
    )
    frames: dict[tuple[str, str], list[dict[str, object]]] = {}
    async with (
        attached_job_queue(app, ScriptedFire(events)),
        AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client,
    ):
        async with client.stream("POST", "/api/v1/agent/query", json=BODY) as response:
            frames[("POST", "/api/v1/agent/query")] = await sse_frames(response)
        async with client.stream(
            "POST", "/api/v1/agent/workflow", json=BODY
        ) as response:
            handle, *run = await sse_frames(response)
        assert handle["type"] == "job_accepted"
        frames[("POST", "/api/v1/agent/workflow")] = run
        async with client.stream(
            "GET", f"/api/v1/jobs/{handle['jobId']}/stream"
        ) as response:
            frames[("GET", "/api/v1/jobs/{job_id}/stream")] = await sse_frames(response)
    return frames, event_stream_routes(app)


async def test_what_production_sends_for_the_terminal_is_its_fields_and_no_more():
    """What the app EMITS for the terminal, on every route that streams it.

    Compared at the egress, not at a rendering helper: the frames are read
    off the app's own event-stream routes the way a client reads them, so a
    key the handler, the queue or a route adds after an event is rendered is
    on the wire here exactly when it is in production.  The routes driven
    are held equal to every event-stream route the app declares.

    Per instance, an equality against the keys the model derives for it,
    and every value in the shape its field declares, recursively.  The
    instances are one base terminal and one per value of every enumerable
    field at any depth — the terminal's and every nested model's — each once
    with every optional field unset and once with every one of them set.
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
    built = terminals(full)
    count = 1 + sum(len(values) for _, values in sites(full))
    assert len(built) == 2 * count

    frames, routes = await emitted([terminal for _, terminal in built])
    assert set(frames) == routes
    for route, sent in frames.items():
        assert len(sent) == len(built), route
        for (name, terminal), frame in zip(built, sent, strict=True):
            assert_sends_its_fields(terminal, frame, f"{route}: {name}")

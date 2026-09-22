"""The vocabulary and its table form one complete boot contract.

The table and every surface that consumes it carry the stage and effect
vocabularies only: a tracker's own state string is resolved at the port
boundary, through the configured ``workflow_states`` mapping the adapter is
built with, and nowhere else (KOD-795).
"""

import ast
import inspect
import tomllib
import typing
from collections.abc import Mapping
from pathlib import Path
from typing import get_args
from unittest.mock import AsyncMock

import pytest
from pydantic import BaseModel, ValidationError, create_model

from kodezart.adapters.linear.tracker import LinearMcpTracker
from kodezart.adapters.toml_operation_config import load_operation_config
from kodezart.composition.tracker import DialledTracker, boot_tracker
from kodezart.config.app import AppConfig
from kodezart.core import prompt_namespaces
from kodezart.core.errors import OperationConfigError
from kodezart.types.domain.operation import LifecycleStage, OperationConfig
from kodezart.types.domain.run_event import (
    DERIVED_RUN_EVENTS,
    RUN_EVENT_PUBLISHERS,
    SILENT_STATE_EVENTS,
    RunEventEffect,
    RunEventKind,
    RunEventPublisher,
)
from tests.fakes import ManagedFakeLinearMcpServer
from tests.run_events import RUN_EVENT_STATES, RUN_EVENT_TOML

#: The non-human writer the dialling case declares and the backend reports, so
#: the boot's attribution check passes on a credential nobody has to invent.
ACTOR = "fixture-actor"


def operation(**updates):
    return OperationConfig(
        **{
            "operation_name": "fixture",
            "workspace": "fixture",
            "run_event_states": dict(RUN_EVENT_STATES),
            **updates,
        }
    )


def test_vocabulary_and_notification_partition_are_complete():
    assert {kind.value for kind in RunEventKind} == set(RUN_EVENT_STATES)
    assert set(RUN_EVENT_PUBLISHERS) == set(RunEventKind)
    assert {
        kind.value
        for kind, publisher in RUN_EVENT_PUBLISHERS.items()
        if publisher is RunEventPublisher.LANE
    } == {
        "first_push",
        "pr_opened",
        "gate_green",
        "gate_red",
        "evaluator_accepted",
        "lane_plateaued",
        "issue_crossed_off",
        "criterion_refuted",
        "escalation_raised",
    }
    operation().require_run_event_table()


@pytest.mark.parametrize("missing", tuple(RUN_EVENT_STATES))
def test_every_missing_member_and_every_unknown_row_are_named_together(missing):
    table = {key: value for key, value in RUN_EVENT_STATES.items() if key != missing}
    table["undeclared_transition"] = "NO_TRANSITION"
    with pytest.raises(ValidationError) as raised:
        operation(run_event_states=table)
    assert f"missing event '{missing}'" in str(raised.value)
    assert "undeclared event 'undeclared_transition'" in str(raised.value)


@pytest.mark.parametrize("event", tuple(RUN_EVENT_STATES))
def test_an_effect_cannot_change_its_ruled_class(event):
    table = dict(RUN_EVENT_STATES)
    table[event] = "in_progress" if table[event].isupper() else "NO_TRANSITION"
    with pytest.raises(ValidationError, match=event):
        operation(run_event_states=table)


#: The two classes whose effect is fixed, in one list: each member must keep its
#: own effect and must not be allowed to take the other class's.
SPECIAL_EVENTS = tuple(sorted(DERIVED_RUN_EVENTS | SILENT_STATE_EVENTS))


@pytest.mark.parametrize("event", SPECIAL_EVENTS)
def test_a_special_event_cannot_take_the_other_special_effect(event):
    """Swap DERIVED for NO_TRANSITION and back, not for a named state.

    The case above swaps each special effect for a named workflow state, which
    an arm widened to accept both special effects still refuses. This swap is
    the one such a widening lets through.
    """
    table = dict(RUN_EVENT_STATES)
    table[event.value] = "NO_TRANSITION" if event in DERIVED_RUN_EVENTS else "DERIVED"
    with pytest.raises(ValidationError, match=event.value):
        operation(run_event_states=table)


def test_a_declared_unknown_state_is_not_a_runtime_fallback():
    with pytest.raises(ValidationError, match="run_event_states"):
        operation(run_event_states={**RUN_EVENT_STATES, "pr_opened": "almost_done"})


@pytest.mark.parametrize("kind", list(RunEventKind))
def test_notification_partition_omission_is_not_silently_accepted(monkeypatch, kind):
    monkeypatch.delitem(RUN_EVENT_PUBLISHERS, kind)
    with pytest.raises(ValidationError, match="notification partition is missing"):
        operation()


def test_actual_file_loading_rejects_missing_and_extra_rows(tmp_path):
    path = tmp_path / "operation.toml"
    text = 'operation_name = "fixture"\nworkspace = "fixture"\n' + RUN_EVENT_TOML
    path.write_text(text)
    load_operation_config(path).require_run_event_table()
    path.write_text(
        text.replace(
            'node_session_started = "NO_TRANSITION"', 'rogue = "NO_TRANSITION"'
        )
    )
    with pytest.raises(OperationConfigError) as raised:
        load_operation_config(path)
    assert "node_session_started" in str(raised.value.failures)
    assert "rogue" in str(raised.value.failures)


async def test_tracker_boot_dials_without_a_run_event_table(monkeypatch):
    """The dial asks for no event table, because nothing it reaches reads one.

    Events are still posted and read on the scope path; their comment is
    rendered from `marker_prefixes` alone. The table's only readers are the
    load validator above and a prompt pass of the per-issue flow, so a
    deployment that declares no table dials, reconciles and runs.

    Replaces the case that asserted the opposite. That assertion stood for a
    boot gate the v0.2 flow needed; nothing carried into the scope flow reads
    the table, and the load-time cases below keep a DECLARED table total
    (KOD-806, KOD-766; the dial half of KOD-402 is superseded).
    """
    server = ManagedFakeLinearMcpServer(users=[ACTOR], teams=[], labels=[], actor=ACTOR)
    monkeypatch.setattr(
        "kodezart.composition.tracker.make_mcp_tool_caller",
        lambda **_: server,
    )
    log = AsyncMock()
    dialled = await boot_tracker(
        settings=AppConfig(tracker={"token": "lin_api_" + "0" * 40}).tracker,
        operation=operation(run_event_states={}, agent_identities=[ACTOR]),
        log=log,
    )
    assert isinstance(dialled, DialledTracker)
    assert server.lifecycle == ["probe", "open"]
    assert "tracker_mappings_reconciled" in [
        call.args[0] for call in log.ainfo.await_args_list
    ]


async def test_an_unconfigured_tracker_does_not_invent_an_event_table():
    assert (
        await boot_tracker(
            settings=AppConfig(tracker={"token": None}).tracker,
            operation=operation(run_event_states={}),
            log=AsyncMock(),
        )
        is None
    )


REPO_ROOT = Path(__file__).parents[2]
SOURCE_ROOT = REPO_ROOT / "src" / "kodezart"
TABLE_FIELD = "run_event_states"
#: The configured mapping a tracker state string is resolved through: every
#: operation field keyed by the lifecycle vocabulary, read off the model.
CONFIGURED = frozenset(
    name
    for name, info in OperationConfig.model_fields.items()
    if get_args(info.annotation)[:1] == (LifecycleStage,)
)
#: The adapter's constructor keyword that receives that mapping, read off its
#: own signature.
ADAPTER_MAPPING = frozenset(
    name
    for name, hint in typing.get_type_hints(LinearMcpTracker.__init__).items()
    if name in inspect.signature(LinearMcpTracker.__init__).parameters
    and get_args(hint)[:1] == (LifecycleStage,)
)
GROOMING_PROMPT = SOURCE_ROOT / "prompts" / "sets" / "claude-opus" / "grooming_pass.md"


def operation_files() -> tuple[Path, ...]:
    """Every shipped operation file."""
    return tuple(sorted((REPO_ROOT / "docs").glob("operation*.toml")))


def vendor_state_names() -> frozenset[str]:
    """Every tracker state string the shipped operation files declare."""
    return frozenset(
        name
        for path in operation_files()
        for field in CONFIGURED
        for name in tomllib.loads(path.read_text()).get(field, {}).values()
    )


def table_surfaces() -> dict[str, str]:
    """Each shipped ``[run_event_states]`` table, its rows rendered as text."""
    surfaces = {}
    for path in operation_files():
        table = tomllib.loads(path.read_text()).get(TABLE_FIELD)
        if table:
            surfaces[f"{path.name}:[{TABLE_FIELD}]"] = "\n".join(
                f"{event} = {effect!r}" for event, effect in table.items()
            )
    return surfaces


def binding_call(name: str) -> str:
    """The one prompt binding of the operation field *name*, as source text."""
    tree = ast.parse(inspect.getsource(prompt_namespaces))
    calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == prompt_namespaces._bind_absentable.__name__
        and any(
            isinstance(argument, ast.Constant) and argument.value == name
            for argument in node.args
        )
    ]
    if len(calls) != 1:
        raise LookupError(f"expected one binding of {name!r}, found {len(calls)}")
    return ast.unparse(calls[0])


def guarded_block(text: str, name: str) -> str:
    """The template region rendered only when *name* is bound."""
    opening = f"{{{{#if {name}}}}}"
    start = text.find(opening)
    if start < 0:
        raise LookupError(f"no {opening} block")
    end = text.find("{{/if}}", start)
    if end < 0:
        raise LookupError(f"{opening} is never closed")
    return text[start : end + len("{{/if}}")]


def scanned_surfaces() -> dict[str, str]:
    """The table and every surface that consumes it, located by name."""
    return {
        **table_surfaces(),
        "OperationConfig.require_run_event_table": inspect.getsource(
            OperationConfig.require_run_event_table
        ),
        "prompt_namespaces.operation_bindings": binding_call(TABLE_FIELD),
        f"{GROOMING_PROMPT.name}:{TABLE_FIELD}": guarded_block(
            GROOMING_PROMPT.read_text(), TABLE_FIELD
        ),
    }


def state_names_in(
    surfaces: Mapping[str, str], tokens: frozenset[str]
) -> dict[str, tuple[str, ...]]:
    """Each surface that carries a tracker state string, with the strings."""
    return {
        name: found
        for name, text in sorted(surfaces.items())
        if (found := tuple(sorted(token for token in tokens if token in text)))
    }


def value_domain(model: type[BaseModel], field: str) -> tuple[object, set[object]]:
    """The key type of a mapping field and every type its values may take."""
    key, value = get_args(model.model_fields[field].annotation)
    return key, set(get_args(value)) or {value}


def test_the_table_value_domain_is_the_stage_and_effect_vocabularies():
    assert value_domain(OperationConfig, TABLE_FIELD) == (
        str,
        {LifecycleStage, RunEventEffect},
    )


@pytest.mark.parametrize(
    "annotation",
    [dict[str, str], dict[str, LifecycleStage | RunEventEffect | str]],
)
def test_a_widened_value_domain_is_reported(annotation):
    probe = create_model("Probe", **{TABLE_FIELD: (annotation, ...)})
    assert value_domain(probe, TABLE_FIELD) != (
        str,
        {LifecycleStage, RunEventEffect},
    )


def test_no_tracker_state_string_appears_in_the_table_or_its_consumers():
    tokens = vendor_state_names()
    assert tokens
    surfaces = scanned_surfaces()
    assert table_surfaces()
    for name, text in surfaces.items():
        assert text, name
    for name in (
        "OperationConfig.require_run_event_table",
        "prompt_namespaces.operation_bindings",
        f"{GROOMING_PROMPT.name}:{TABLE_FIELD}",
    ):
        assert TABLE_FIELD in surfaces[name]
    assert state_names_in(surfaces, tokens) == {}


def test_the_state_resolution_site_is_outside_the_scanned_surfaces():
    """The configured mapping's own binding and the prompt's resolution site
    exist, carry what the scan forbids elsewhere, and are not scanned."""
    tokens = vendor_state_names()
    surfaces = scanned_surfaces()
    for field in CONFIGURED:
        sibling = binding_call(field)
        assert sibling not in surfaces.values()
    whole = GROOMING_PROMPT.read_text()
    assert state_names_in({"whole": whole}, tokens)
    block = guarded_block(whole, TABLE_FIELD)
    assert state_names_in({"block": block}, tokens) == {}


@pytest.mark.parametrize("surface", sorted(scanned_surfaces()))
def test_a_tracker_state_string_injected_into_each_scanned_surface_is_reported(
    surface,
):
    tokens = vendor_state_names()
    token = sorted(tokens)[0]
    surfaces = dict(scanned_surfaces())
    surfaces[surface] = f"{surfaces[surface]}\n{token}"
    assert state_names_in(surfaces, tokens) == {surface: (token,)}


def production_sources() -> dict[str, str]:
    """Every module of the package, keyed by its path under the package."""
    return {
        path.relative_to(SOURCE_ROOT).as_posix(): path.read_text()
        for path in sorted(SOURCE_ROOT.rglob("*.py"))
    }


def state_constants(
    sources: Mapping[str, str], tokens: frozenset[str]
) -> tuple[str, ...]:
    """Each string constant in a module that IS a tracker state string."""
    return tuple(
        f"{name}:{node.lineno}:{node.value}"
        for name, text in sorted(sources.items())
        for node in ast.walk(ast.parse(text))
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and node.value in tokens
    )


def mapping_sites(sources: Mapping[str, str]) -> tuple[tuple[str, bool], ...]:
    """Each call handing the adapter its state mapping, and whether the value
    names the configured field."""
    return tuple(
        (f"{name}:{keyword.value.lineno}", _names_configured(keyword.value))
        for name, text in sorted(sources.items())
        for node in ast.walk(ast.parse(text))
        if isinstance(node, ast.Call)
        for keyword in node.keywords
        if keyword.arg in ADAPTER_MAPPING
    )


def _names_configured(expression: ast.expr) -> bool:
    return any(
        isinstance(node, ast.Attribute) and node.attr in CONFIGURED
        for node in ast.walk(expression)
    )


def test_a_tracker_state_string_is_resolved_only_through_the_configured_mapping():
    assert CONFIGURED
    assert ADAPTER_MAPPING
    sources = production_sources()
    assert state_constants(sources, vendor_state_names()) == ()
    sites = mapping_sites(sources)
    assert len(sites) == 1
    assert all(named for _, named in sites)


def test_a_state_string_constant_or_a_second_mapping_site_is_reported():
    tokens = vendor_state_names()
    token = sorted(tokens)[0]
    (keyword,) = ADAPTER_MAPPING
    planted = {
        "chains/state_writer.py": f"stage_name = {token!r}\n",
        "composition/second.py": (
            f"adapter = Adapter({keyword}={{LifecycleStage.DONE: {token!r}}})\n"
        ),
    }
    assert state_constants(planted, tokens) == (
        f"chains/state_writer.py:1:{token}",
        f"composition/second.py:1:{token}",
    )
    sources = {**production_sources(), **planted}
    sites = mapping_sites(sources)
    assert len(sites) == 2
    assert ("composition/second.py:1", False) in sites

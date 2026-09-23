"""The vocabulary and its table form one complete boot contract.

The table and every surface that consumes it carry the stage and effect
vocabularies only: a tracker's own state string is resolved at the port
boundary, through the configured ``workflow_states`` mapping the adapter is
built with, and nowhere else (KOD-795).

The mapping is also read outside the adapter, at registered reading sites
only, each with the reason it reads it.

The state strings scanned for are the configured ones: the values the
shipped operation files declare under a configured mapping.  A board state
name no shipped file configures (``Todo``, ``Backlog``) is not a token, so
no scan here sees it.
"""

import ast
import inspect
import re
import textwrap
import tomllib
import typing
from collections.abc import Iterator, Mapping
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
from kodezart.types.domain.criterion_lifecycle import UndemonstratedReason
from kodezart.types.domain.operation import LifecycleStage, OperationConfig
from kodezart.types.domain.run_event import (
    DERIVED_RUN_EVENTS,
    EVIDENCE_ROW_WRITES,
    RUN_EVENT_PUBLISHERS,
    SILENT_STATE_EVENTS,
    UNDEMONSTRATED_EVENT_KINDS,
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
        "criterion_passed",
        "criterion_lapsed",
        "escalation_raised",
        "criterion_grading_unverified",
        "criterion_check_survived_mutation",
        "criterion_satisfied_at_base",
        "criterion_base_reading_unsettled",
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


def test_every_undemonstrated_reason_names_its_own_event_kind():
    """The join between the two vocabularies is total and one to one.

    A reason with no kind could not be recorded at all, and two reasons
    sharing one kind would be one reason on the stream: a reader of the
    comment could not tell which reading failed. So a reading added to the
    reason enum cannot land without the kind that carries it.
    """
    assert set(UNDEMONSTRATED_EVENT_KINDS) == set(UndemonstratedReason)
    kinds = tuple(UNDEMONSTRATED_EVENT_KINDS.values())
    assert len(set(kinds)) == len(kinds)
    assert set(kinds) <= set(RunEventKind)


def test_the_evidence_row_writes_are_the_two_gradings_that_restamp_the_row():
    """The kinds a restamp trace reads as the row's history, and no reading's.

    A pass and a take-back each restamp the Evidence row, and each is posted
    by the lane and derives the state it moves. No undemonstrated reading's
    kind is among them: that reading writes no row (KOD-506, KOD-610).
    """
    assert EVIDENCE_ROW_WRITES == {
        RunEventKind.CRITERION_PASSED,
        RunEventKind.CRITERION_REFUTED,
    }
    assert EVIDENCE_ROW_WRITES <= DERIVED_RUN_EVENTS
    assert {RUN_EVENT_PUBLISHERS[kind] for kind in EVIDENCE_ROW_WRITES} == {
        RunEventPublisher.LANE
    }
    assert EVIDENCE_ROW_WRITES.isdisjoint(UNDEMONSTRATED_EVENT_KINDS.values())


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
PROMPTS_ROOT = SOURCE_ROOT / "prompts"
#: A block helper's opening or closing tag, with the helper and its argument.
BLOCK_TAG = re.compile(r"\{\{([#/])(\w+)(?:\s+([^}]*?))?\s*\}\}")


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


def _is_binding(node: ast.AST, name: str) -> bool:
    """Whether *node* is the prompt binding of the operation field *name*."""
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == prompt_namespaces._bind_absentable.__name__
        and any(
            isinstance(argument, ast.Constant) and argument.value == name
            for argument in node.args
        )
    )


def binding_call(name: str) -> str:
    """The one prompt binding of the operation field *name*, as source text."""
    tree = ast.parse(inspect.getsource(prompt_namespaces))
    calls = [node for node in ast.walk(tree) if _is_binding(node, name)]
    if len(calls) != 1:
        raise LookupError(f"expected one binding of {name!r}, found {len(calls)}")
    return ast.unparse(calls[0])


def template_blocks(text: str, name: str) -> tuple[tuple[str, int, int], ...]:
    """Every block helper opened on *name*, as its helper and its span.

    Each span ends at the block's MATCHING close, read by nesting depth over
    every block helper of the template, so an ``{{#if}}`` nested inside the
    block does not end it.  An unbalanced template raises.
    """
    open_blocks: list[tuple[str, str, int]] = []
    blocks = []
    for tag in BLOCK_TAG.finditer(text):
        sign, helper, argument = tag.group(1), tag.group(2), tag.group(3) or ""
        if sign == "#":
            open_blocks.append((helper, argument.strip(), tag.start()))
            continue
        if not open_blocks or open_blocks[-1][0] != helper:
            raise LookupError(f"{tag.group(0)} at {tag.start()} closes no open block")
        opened, argument, start = open_blocks.pop()
        if argument == name:
            blocks.append((opened, start, tag.end()))
    if open_blocks:
        helper, argument, start = open_blocks[-1]
        raise LookupError(f"{{{{#{helper} {argument}}}}} at {start} is never closed")
    return tuple(sorted(blocks, key=lambda block: block[1]))


def table_regions(text: str) -> tuple[str, ...]:
    """Every region of a template rendered only when the table is bound.

    A region is an ``{{#if}}`` block on the field, up to its matching close.
    A template with no such region raises, and so does one whose
    ``{{#each}}`` over the field lies outside every region, because that
    block is what renders the table.
    """
    blocks = template_blocks(text, TABLE_FIELD)
    guards = [(start, end) for helper, start, end in blocks if helper == "if"]
    if not guards:
        raise LookupError(f"no {{{{#if {TABLE_FIELD}}}}} block")
    for helper, start, end in blocks:
        if helper == "each" and not any(
            first <= start and end <= last for first, last in guards
        ):
            raise LookupError(
                f"{{{{#each {TABLE_FIELD}}}}} at {start} lies outside every "
                "scanned region"
            )
    return tuple(text[start:end] for start, end in guards)


def prompt_sources(root: Path = PROMPTS_ROOT) -> dict[str, str]:
    """Every prompt template that references the table, keyed by its path."""
    return {
        path.relative_to(root).as_posix(): text
        for path in sorted(root.rglob("*.md"))
        if TABLE_FIELD in (text := path.read_text())
    }


def production_sources() -> dict[str, str]:
    """Every module of the package, keyed by its path under the package."""
    return {
        path.relative_to(SOURCE_ROOT).as_posix(): path.read_text()
        for path in sorted(SOURCE_ROOT.rglob("*.py"))
    }


def module_path(symbol: object) -> str:
    """Where *symbol* is declared, as a path under the package."""
    return Path(inspect.getfile(symbol)).relative_to(SOURCE_ROOT).as_posix()


def _functions(
    node: ast.AST, prefix: str = ""
) -> Iterator[tuple[str, ast.FunctionDef | ast.AsyncFunctionDef]]:
    for child in ast.iter_child_nodes(node):
        if isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef):
            yield f"{prefix}{child.name}", child
            yield from _functions(child, f"{prefix}{child.name}.<locals>.")
        elif isinstance(child, ast.ClassDef):
            yield from _functions(child, f"{prefix}{child.name}.")
        else:
            yield from _functions(child, prefix)


def _own_nodes(function: ast.AST) -> Iterator[ast.AST]:
    """The nodes of a function outside every function or class nested in it."""
    pending = list(ast.iter_child_nodes(function))
    while pending:
        node = pending.pop()
        yield node
        if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            pending.extend(ast.iter_child_nodes(node))


def table_readers(sources: Mapping[str, str]) -> dict[str, str]:
    """Every function of the package that reads the table, as source text.

    A function reads the table when an attribute naming the field is read in
    its own body.  The function holding the table's prompt binding is
    surfaced as that one binding call, because the same function binds the
    configured state mapping beside it, which is the resolution site and
    stays unscanned.  A read at module or class level, outside every
    function, is not seen.
    """
    readers = {}
    for name, text in sorted(sources.items()):
        for qualname, function in _functions(ast.parse(text)):
            own = list(_own_nodes(function))
            if not any(
                isinstance(node, ast.Attribute)
                and node.attr == TABLE_FIELD
                and isinstance(node.ctx, ast.Load)
                for node in own
            ):
                continue
            bindings = [node for node in own if _is_binding(node, TABLE_FIELD)]
            readers[f"{name}::{qualname}"] = (
                ast.unparse(bindings[0])
                if bindings
                else textwrap.dedent(
                    ast.get_source_segment(text, function, padded=True) or ""
                )
            )
    return readers


def consumer_functions() -> dict[str, str]:
    """The table's Python consumers, each as source text."""
    return table_readers(production_sources())


def consumer_templates(prompts: Mapping[str, str] | None = None) -> dict[str, str]:
    """The table's prompt consumers, each as a template region it renders."""
    return {
        f"{name}:{TABLE_FIELD}[{index}]": region
        for name, text in sorted(
            (prompt_sources() if prompts is None else prompts).items()
        )
        for index, region in enumerate(table_regions(text))
    }


#: A reader of the table that is also the model's own load validator, which
#: checks the configured mapping as a field of its own: its mapping reads are
#: looked for only in the statements that read the table.
LOAD_VALIDATORS = {
    f"{module_path(OperationConfig)}::{OperationConfig.__name__}._check_structure": (
        "the load validator checks that the configured mapping names every "
        "required stage, and reaches the table only to hand it to "
        "require_run_event_table"
    ),
}


def table_statements(text: str) -> str:
    """The statements of a function's own body that read the table."""
    (function,) = ast.parse(text).body
    assert isinstance(function, ast.FunctionDef | ast.AsyncFunctionDef)
    return "\n".join(
        ast.unparse(statement)
        for statement in function.body
        if any(
            isinstance(node, ast.Attribute) and node.attr == TABLE_FIELD
            for node in ast.walk(statement)
        )
    )


def scanned_surfaces() -> dict[str, str]:
    """The table and every surface that consumes it, located by name."""
    return {**table_surfaces(), **consumer_functions(), **consumer_templates()}


def mapping_reads(
    functions: Mapping[str, str], templates: Mapping[str, str]
) -> dict[str, tuple[str, ...]]:
    """Each consumer surface that reads a configured state mapping, with the
    fields it reads.

    A Python surface is parsed and reports every attribute naming a
    configured field, so a consumer that renders its effects through the
    mapping is seen although no state string is spelled in it.  A template
    surface reports every configured field it references.
    """
    reads = {
        name: tuple(
            sorted(
                {
                    node.attr
                    for node in ast.walk(ast.parse(textwrap.dedent(text)))
                    if isinstance(node, ast.Attribute) and node.attr in CONFIGURED
                }
            )
        )
        for name, text in functions.items()
    }
    reads.update(
        (name, tuple(sorted(field for field in CONFIGURED if field in text)))
        for name, text in templates.items()
    )
    return {name: fields for name, fields in sorted(reads.items()) if fields}


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
    readers = consumer_functions()
    templates = consumer_templates()
    assert templates
    assert (
        f"{module_path(OperationConfig)}::"
        f"{OperationConfig.require_run_event_table.__qualname__}"
    ) in readers
    binding = binding_call(TABLE_FIELD)
    assert binding in readers.values()
    assert f"{TABLE_FIELD}.items()" in binding
    for name, text in {**readers, **templates}.items():
        assert TABLE_FIELD in text, name
    for name, region in templates.items():
        assert "{{this.effect}}" in region, name
    assert state_names_in(surfaces, tokens) == {}


def _holds(text: str, fragment: str) -> bool:
    """Whether *text* holds *fragment*: as text, or, when *text* parses as
    source, as the unparsed form of one of its nodes, so a surface widened
    over the fragment is caught however its source is laid out."""
    if fragment in text:
        return True
    try:
        tree = ast.parse(textwrap.dedent(text))
    except SyntaxError:
        return False
    return any(ast.unparse(node) == fragment for node in ast.walk(tree))


def test_the_state_resolution_site_is_outside_the_scanned_surfaces():
    """The configured mapping's own prompt binding, and a prompt placeholder
    that renders a state through it, both exist and both lie outside every
    scanned surface: the scan does not ban the site the Check requires."""
    surfaces = scanned_surfaces()
    for field in CONFIGURED:
        sibling = binding_call(field)
        assert all(not _holds(text, sibling) for text in surfaces.values())
    remainder = []
    for text in prompt_sources().values():
        for region in table_regions(text):
            text = text.replace(region, "")
        remainder.append(text)
    placeholders = [f"{{{{{field}." for field in sorted(CONFIGURED)]
    assert any(
        placeholder in text for text in remainder for placeholder in placeholders
    )


def test_no_consumer_of_the_table_reads_the_configured_state_mapping():
    assert CONFIGURED
    functions = consumer_functions()
    validators = {name: functions[name] for name in LOAD_VALIDATORS}
    assert all(LOAD_VALIDATORS.values())
    assert mapping_reads(validators, {}) == {
        name: tuple(sorted(CONFIGURED)) for name in LOAD_VALIDATORS
    }
    read = {
        name: table_statements(text) if name in LOAD_VALIDATORS else text
        for name, text in functions.items()
    }
    assert all(read.values())
    assert mapping_reads(read, consumer_templates()) == {}


def test_a_consumer_reading_the_configured_state_mapping_is_reported():
    (field,) = sorted(CONFIGURED)
    binding = (
        f"_bind_absentable(bindings, {TABLE_FIELD!r}, [{{'event': name, "
        f"'effect': config.{field}.get(effect, effect.value)}} "
        f"for name, effect in config.{TABLE_FIELD}.items()], "
        f"absent=not config.{TABLE_FIELD})"
    )
    template = (
        f"{{{{#if {TABLE_FIELD}}}}}{{{{#each {TABLE_FIELD}}}}}- {{{{this.event}}}}: "
        f"{{{{{field}.done}}}}\n{{{{/each}}}}{{{{/if}}}}"
    )
    assert mapping_reads({"binding": binding}, {"template": template}) == {
        "binding": (field,),
        "template": (field,),
    }


def _splice_into_region(text: str, region: str, token: str) -> str:
    """*text* with *token* written inside *region*, in its ``{{#each}}``
    block over the table when it has one."""
    start = text.index(region)
    marker = f"{{{{#each {TABLE_FIELD}}}}}"
    offset = region.find(marker)
    at = start + (offset + len(marker) if offset >= 0 else region.index("}}") + 2)
    return f"{text[:at]}{token}{text[at:]}"


@pytest.mark.parametrize("surface", sorted(scanned_surfaces()))
def test_a_tracker_state_string_injected_into_each_scanned_surface_is_reported(
    surface,
):
    """A template surface is injected at its source and located again, so the
    locator itself is exercised; every other surface takes the token as
    located."""
    tokens = vendor_state_names()
    token = sorted(tokens)[0]
    prompts = prompt_sources()
    templates = consumer_templates(prompts)
    if surface in templates:
        name = surface.rsplit(":", 1)[0]
        spliced = {
            **prompts,
            name: _splice_into_region(prompts[name], templates[surface], token),
        }
        surfaces = consumer_templates(spliced)
        assert state_names_in(surfaces, tokens) == {surface: (token,)}
        return
    surfaces = dict(scanned_surfaces())
    surfaces[surface] = f"{surfaces[surface]}\n{token}"
    assert state_names_in(surfaces, tokens) == {surface: (token,)}


def _table_block(effects: str = "") -> str:
    return (
        f"{{{{#if {TABLE_FIELD}}}}}The harness declares these effects.{effects}\n"
        f"{{{{#each {TABLE_FIELD}}}}}- {{{{this.event}}}}: {{{{this.effect}}}}\n"
        f"{{{{/each}}}}{{{{/if}}}}\n"
    )


def test_a_state_string_after_a_nested_block_inside_the_region_is_reported():
    token = sorted(vendor_state_names())[0]
    text = _table_block(f"{{{{#if marker_prefixes}}}}x{{{{/if}}}} (move it to {token})")
    assert state_names_in(consumer_templates({"p.md": text}), vendor_state_names()) == {
        f"p.md:{TABLE_FIELD}[0]": (token,)
    }


def test_a_table_rendered_outside_the_guarded_region_is_reported():
    text = (
        f"{{{{#if {TABLE_FIELD}}}}}The harness declares these effects.{{{{/if}}}}\n"
        f"{{{{#each {TABLE_FIELD}}}}}- {{{{this.event}}}}: {{{{this.effect}}}}\n"
        f"{{{{/each}}}}\n"
    )
    with pytest.raises(LookupError, match="outside every scanned region"):
        consumer_templates({"p.md": text})


def test_a_second_table_region_in_one_prompt_is_reported():
    token = sorted(vendor_state_names())[0]
    text = (
        _table_block()
        + f"{{{{#if {TABLE_FIELD}}}}}Move every DERIVED event to {token}.{{{{/if}}}}\n"
    )
    assert state_names_in(consumer_templates({"p.md": text}), vendor_state_names()) == {
        f"p.md:{TABLE_FIELD}[1]": (token,)
    }


def test_a_second_prompt_rendering_the_table_is_reported(tmp_path):
    token = sorted(vendor_state_names())[0]
    (tmp_path / "sets").mkdir()
    (tmp_path / "sets" / "first.md").write_text(_table_block())
    (tmp_path / "sets" / "second.md").write_text(_table_block(f" (or {token})"))
    (tmp_path / "sets" / "other.md").write_text("No table here.\n")
    prompts = prompt_sources(tmp_path)
    assert sorted(prompts) == ["sets/first.md", "sets/second.md"]
    assert state_names_in(consumer_templates(prompts), vendor_state_names()) == {
        f"sets/second.md:{TABLE_FIELD}[0]": (token,)
    }
    (tmp_path / "sets" / "third.md").write_text(f"{{{{{TABLE_FIELD}}}}}\n")
    with pytest.raises(LookupError, match="no"):
        consumer_templates(prompt_sources(tmp_path))


def test_a_function_reading_the_table_is_a_scanned_consumer():
    token = sorted(vendor_state_names())[0]
    planted = {
        "services/reader.py": (
            "class Reader:\n"
            "    def effect(self, config):\n"
            f"        return config.{TABLE_FIELD}.get('x', {token!r})\n"
        ),
    }
    readers = table_readers(planted)
    assert list(readers) == ["services/reader.py::Reader.effect"]
    assert state_names_in(readers, vendor_state_names()) == {
        "services/reader.py::Reader.effect": (token,)
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
    """Whether the adapter is handed the configured field itself, and not an
    expression built around it."""
    return isinstance(expression, ast.Attribute) and expression.attr in CONFIGURED


def test_a_tracker_state_string_is_resolved_only_through_the_configured_mapping():
    assert CONFIGURED
    assert ADAPTER_MAPPING
    assert vendor_state_names()
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


def test_a_mapping_rebuilt_around_the_configured_field_is_not_the_field():
    (keyword,) = ADAPTER_MAPPING
    (field,) = sorted(CONFIGURED)
    planted = {
        "composition/tracker.py": (
            f"adapter = Adapter({keyword}={{**operation.{field}, "
            "LifecycleStage.DONE: 'Closed'})\n"
        ),
    }
    assert mapping_sites(planted) == (("composition/tracker.py:1", False),)


#: The reason each audit reader holds the configured mapping.
REVIEW_STATE = (
    "compares a board row's state with the operator's configured name for "
    "the review stage"
)
#: Every module outside the adapter and the mapping's own model module that
#: reads a configured state mapping: the reads it makes, and why it does.
MAPPING_READERS: dict[str, tuple[tuple[str, ...], str]] = {
    "chains/audit_evidence.py": (
        ("self._operation.workflow_states.get(LifecycleStage.IN_REVIEW)",),
        REVIEW_STATE,
    ),
    "chains/audit_sweep.py": (
        ("operation.workflow_states.get(LifecycleStage.IN_REVIEW)",),
        REVIEW_STATE,
    ),
    "composition/audit.py": (
        ("LifecycleStage.IN_REVIEW in operation.workflow_states",),
        "the presence check: configured audit scheduling does not start "
        "without a configured name for the review stage",
    ),
    "composition/tracker.py": (
        ("workflow_state_names=operation.workflow_states",),
        "the adapter handoff: the one call that hands the configured mapping "
        "to the tracker adapter",
    ),
    "core/prompt_namespaces.py": (
        ("config.workflow_states.items()", "not config.workflow_states"),
        "the prompt resolution site: the one binding that renders the "
        "configured names into a prompt, which the Check requires to exist",
    ),
    "services/audit_runtime.py": (
        ("operation.workflow_states.get(LifecycleStage.IN_REVIEW)",),
        REVIEW_STATE,
    ),
    "services/audit_sources.py": (
        ("operation.workflow_states.get(LifecycleStage.IN_REVIEW)",),
        REVIEW_STATE,
    ),
    "services/audit_terminal.py": (
        ("operation.workflow_states[LifecycleStage.IN_REVIEW]",),
        REVIEW_STATE,
    ),
    "services/tracker_boot.py": (
        ("config.workflow_states.items()",),
        "boot validation: every configured state name is handed to the "
        "resolution pass that checks it exists on the board before any pass "
        "runs",
    ),
}


def configured_field_reads(sources: Mapping[str, str]) -> dict[str, tuple[str, ...]]:
    """Each module's reads of a configured state mapping, outside the adapter
    package and the mapping's own model module.

    A read is rendered as the expression that uses the field: the call when
    a method of the mapping is called, otherwise the node that holds it.
    """
    reads: dict[str, list[str]] = {}
    for name, text in sorted(sources.items()):
        if name.startswith("adapters/") or name == module_path(OperationConfig):
            continue
        tree = ast.parse(text)
        parents = {
            child: node
            for node in ast.walk(tree)
            for child in ast.iter_child_nodes(node)
        }
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and node.attr in CONFIGURED:
                use = parents[node]
                if isinstance(use, ast.Attribute):
                    use = parents[use]
                reads.setdefault(name, []).append(ast.unparse(use))
    return {name: tuple(sorted(found)) for name, found in reads.items()}


def test_every_read_of_the_configured_mapping_outside_the_adapter_is_registered():
    assert CONFIGURED
    assert all(reason for _, reason in MAPPING_READERS.values())
    assert configured_field_reads(production_sources()) == {
        name: reads for name, (reads, _) in MAPPING_READERS.items()
    }


def test_a_new_read_of_the_configured_mapping_is_reported():
    (field,) = sorted(CONFIGURED)
    planted = {
        "services/lane_state_writer.py": (
            f"def _done_name(operation):\n"
            f"    return operation.{field}[LifecycleStage.DONE]\n"
        ),
        "adapters/linear/tracker.py": f"names = self.{field}\n",
        module_path(OperationConfig): f"names = self.{field}\n",
    }
    assert configured_field_reads(planted) == {
        "services/lane_state_writer.py": (f"operation.{field}[LifecycleStage.DONE]",),
    }

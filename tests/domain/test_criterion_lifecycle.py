"""Code backend: shared identities, the cross-off model, events, vendor freedom.

The static vendor check reads both invariant modules, every packaged module
whose values they assert over, and the committed workspace the spec backend
reads.  The roster of selectable adapters is the one place a vendor may be
named, so it is the only exemption.

The cross-off model is checked here against the packaged value it names:
its two enums verbatim, the paths a path-bound class must carry, the class a
verdict falls back to, the stickiness of that class per criterion identity,
and the absence of a boolean verdict anywhere in the graded-sha partition.
"""

import ast
import importlib
import pkgutil
import re
import tomllib
from enum import StrEnum
from pathlib import Path
from typing import get_args

import pytest
from pydantic import BaseModel, ValidationError, create_model

from kodezart.core.protocols import TrackerPort
from kodezart.types.base import CamelCaseModel
from kodezart.types.domain.criterion_evidence import CriterionEvidence
from kodezart.types.domain.criterion_lifecycle import (
    PATH_BOUND_CLASSES,
    CriterionCrossOff,
    CrossOffState,
    RederivationClass,
    StickyClassError,
    held_rederivation_classes,
)
from kodezart.types.domain.operation import OperationConfig
from kodezart.types.domain.run_event import (
    RUN_EVENT_PUBLISHERS,
    RunEventEffect,
    RunEventKind,
    RunEventPublisher,
    RunEventTableError,
)
from kodezart.types.domain.tracker import TrackerBackend
from tests.identity_guards import construction_sites, invalid_ruling_fields

REPO_ROOT = Path(__file__).parents[2]
SOURCE_ROOT = REPO_ROOT / "src" / "kodezart"
CODE = "code"
SPEC = "spec"
INVARIANT_MODULES = {
    CODE: Path(__file__),
    SPEC: REPO_ROOT / "tests" / "spec" / "test_model_agreement.py",
}
VENDOR_ROSTER = SOURCE_ROOT / "types" / "domain" / "tracker.py"
VENDOR_TERMS = tuple(sorted(backend.value for backend in TrackerBackend))
_WORD = re.compile(r"[A-Z]+(?![a-z])|[A-Z][a-z0-9]*|[a-z0-9]+")
IDENTITY_OWNERS = {
    "CriterionRef": "domain/fire_spec.py",
    "RulingId": "domain/agent.py",
}
BACKEND = CODE
#: Every cross-member invariant of the model, with the packaged module whose
#: code it checks. ``None`` says the code does not exist yet, which routes the
#: invariant onto the spec backend now rather than deferring it.
MODEL_INVARIANTS = {
    "criterion address minting": "kodezart.domain.fire_spec",
    "ruling address minting": "kodezart.domain.agent",
    "run event vocabulary": "kodezart.types.domain.run_event",
    "run event state table": "kodezart.types.domain.operation",
    "vendor freedom": "kodezart.types.domain.tracker",
    "cross-lane pointer resolution": None,
    "model value naming": None,
}
#: The invariants this backend runs, each with the test that runs it.
INVARIANTS = {
    "criterion address minting": "test_identity_invariant_uses_the_actual_code_backend",
    "ruling address minting": "test_identity_invariant_uses_the_actual_code_backend",
    "run event vocabulary": "test_run_event_invariant_uses_the_actual_code_backend",
    "run event state table": "test_run_event_invariant_uses_the_actual_code_backend",
    "vendor freedom": "test_the_invariant_modules_and_their_values_name_no_vendor",
}


def vendor_terms(text: str) -> tuple[str, ...]:
    """Vendor names a text carries, in any casing or word separation."""
    words = {word.casefold() for word in _WORD.findall(text)}
    return tuple(term for term in VENDOR_TERMS if term in words)


def vendor_violations(sources: dict[str, str]) -> dict[str, tuple[str, ...]]:
    return {
        name: terms
        for name, text in sorted(sources.items())
        if (terms := vendor_terms(text))
    }


def _module_path(name: str) -> Path | None:
    """The packaged file a dotted name addresses, or nothing."""
    parts = name.split(".")
    if parts[0] != SOURCE_ROOT.name or len(parts) < 2:
        return None
    module = SOURCE_ROOT.joinpath(*parts[1:])
    for candidate in (module.with_suffix(".py"), module / "__init__.py"):
        if candidate.is_file():
            return candidate
    return None


def first_party_modules(source: str) -> tuple[Path, ...]:
    """Every packaged module an invariant reads its asserted values from."""
    found = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.ImportFrom):
            names = (
                []
                if node.level
                else [
                    node.module or "",
                    *(f"{node.module}.{alias.name}" for alias in node.names),
                ]
            )
        elif isinstance(node, ast.Import):
            names = [alias.name for alias in node.names]
        else:
            continue
        found.update(path for name in names if (path := _module_path(name)))
    return tuple(sorted(found))


def invariant_sources() -> dict[str, str]:
    """The scanned set, read from the tree rather than transcribed."""
    paths = set()
    for module in INVARIANT_MODULES.values():
        paths.add(module)
        paths.update(first_party_modules(module.read_text()))
    paths.update(
        path
        for path in (INVARIANT_MODULES[SPEC].parent / "fixtures").iterdir()
        if path.is_file()
    )
    return {
        path.relative_to(REPO_ROOT).as_posix(): path.read_text()
        for path in sorted(paths)
        if path != VENDOR_ROSTER
    }


def _names_a_tracker(node) -> bool:
    if isinstance(node, ast.Name):
        return node.id.endswith("tracker")
    if isinstance(node, ast.Attribute):
        return node.attr.endswith("tracker")
    return False


def tracker_attributes(source: str) -> tuple[str, ...]:
    """Every attribute an invariant reads off a tracker object."""
    return tuple(
        sorted(
            {
                node.attr
                for node in ast.walk(ast.parse(source))
                if isinstance(node, ast.Attribute) and _names_a_tracker(node.value)
            }
        )
    )


def _literal(node, constants, seen=frozenset()):
    """Fold a module constant without importing or executing the module."""
    if isinstance(node, ast.Name):
        if node.id in seen:
            raise AssertionError(f"constant {node.id} is defined in terms of itself")
        return _literal(constants[node.id], constants, seen | {node.id})
    return ast.literal_eval(node)


def declared_invariants(module: Path) -> tuple[str, dict[str, str]]:
    """A module's declared backend and the test it runs each invariant by."""
    tree = ast.parse(module.read_text())
    constants = {
        target.id: node.value
        for node in tree.body
        if isinstance(node, ast.Assign)
        for target in node.targets
        if isinstance(target, ast.Name)
    }
    runners = _literal(constants["INVARIANTS"], constants)
    defined = {
        node.name
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name.startswith("test_")
    }
    missing = sorted(set(runners.values()) - defined)
    if missing:
        raise AssertionError(f"{module.name} runs no test named {missing}")
    return _literal(constants["BACKEND"], constants), runners


def backend_for(invariant: str) -> str:
    """An invariant whose packaged code exists runs on the code backend; one
    whose code does not yet exist runs on the spec backend, never nowhere."""
    declared = MODEL_INVARIANTS[invariant]
    return CODE if declared and _module_path(declared) else SPEC


def routing_failures(declarations: dict[str, dict[str, str]]) -> tuple[str, ...]:
    """Every invariant no backend runs, or that the wrong backend runs."""
    failures = []
    for invariant in sorted(MODEL_INVARIANTS):
        routed = backend_for(invariant)
        running = sorted(
            backend for backend, runners in declarations.items() if invariant in runners
        )
        if running != [routed]:
            failures.append(
                f"{invariant}: routed to the {routed} backend, run by "
                f"{' and '.join(running) or 'no backend'}"
            )
    for backend, runners in sorted(declarations.items()):
        for invariant in sorted(set(runners) - set(MODEL_INVARIANTS)):
            failures.append(
                f"{invariant}: the {backend} backend runs an invariant the "
                "model does not declare"
            )
    return tuple(failures)


def actual_declarations() -> dict[str, dict[str, str]]:
    return {
        backend: declared_invariants(module)[1]
        for backend, module in INVARIANT_MODULES.items()
    }


@pytest.mark.parametrize("backend", sorted(INVARIANT_MODULES))
def test_each_module_declares_the_backend_it_is_registered_under(backend):
    assert declared_invariants(INVARIANT_MODULES[backend])[0] == backend


def test_every_invariant_runs_on_the_backend_its_code_routes_it_to():
    assert routing_failures(actual_declarations()) == ()


def test_an_invariant_whose_code_does_not_exist_runs_on_the_spec_backend_now():
    codeless = {name for name, code in MODEL_INVARIANTS.items() if code is None}
    assert codeless
    assert all(backend_for(name) == SPEC for name in codeless)
    assert codeless <= set(declared_invariants(INVARIANT_MODULES[SPEC])[1])


def test_a_declared_module_that_does_not_exist_routes_to_the_spec_backend(
    monkeypatch,
):
    absent = f"{SOURCE_ROOT.name}.domain.not_yet_built"
    assert _module_path(absent) is None
    monkeypatch.setitem(MODEL_INVARIANTS, "invented invariant", absent)
    assert backend_for("invented invariant") == SPEC


@pytest.mark.parametrize("dropped", sorted(MODEL_INVARIANTS))
def test_an_invariant_running_on_neither_backend_fails(dropped):
    declarations = {
        backend: {name: test for name, test in runners.items() if name != dropped}
        for backend, runners in actual_declarations().items()
    }
    failures = routing_failures(declarations)
    assert len(failures) == 1
    assert dropped in failures[0] and "no backend" in failures[0]


@pytest.mark.parametrize("deferred", ["cross-lane pointer resolution"])
def test_a_codeless_invariant_moved_onto_the_code_backend_fails(deferred):
    declarations = actual_declarations()
    declarations[CODE][deferred] = declarations[SPEC].pop(deferred)
    failures = routing_failures(declarations)
    assert len(failures) == 1
    assert deferred in failures[0] and f"routed to the {SPEC} backend" in failures[0]


def test_a_backend_running_an_undeclared_invariant_fails():
    declarations = actual_declarations()
    declarations[CODE]["invented invariant"] = INVARIANTS["vendor freedom"]
    failures = routing_failures(declarations)
    assert len(failures) == 1 and "the model does not declare" in failures[0]


def test_a_declared_runner_must_be_a_test_defined_in_that_module(tmp_path):
    module = tmp_path / "test_another_backend.py"
    header = 'CODE = "code"\nBACKEND = CODE\n'
    module.write_text(f'{header}INVARIANTS = {{"an invariant": "test_absent"}}\n')
    with pytest.raises(AssertionError, match="test_absent"):
        declared_invariants(module)
    module.write_text(
        f'{header}INVARIANTS = {{"an invariant": "test_present"}}\n'
        "def test_present():\n    pass\n"
    )
    assert declared_invariants(module) == (CODE, {"an invariant": "test_present"})


def test_a_backend_constant_defined_in_terms_of_itself_refuses(tmp_path):
    module = tmp_path / "test_circular_backend.py"
    module.write_text("BACKEND = OTHER\nOTHER = BACKEND\nINVARIANTS = {}\n")
    with pytest.raises(AssertionError, match="defined in terms of itself"):
        declared_invariants(module)


def test_the_invariant_modules_and_their_values_name_no_vendor():
    assert vendor_violations(invariant_sources()) == {}


def test_the_scanned_set_covers_every_packaged_value_each_invariant_imports():
    sources = invariant_sources()
    for backend, module in INVARIANT_MODULES.items():
        assert module.relative_to(REPO_ROOT).as_posix() in sources, backend
        for path in first_party_modules(module.read_text()):
            if path != VENDOR_ROSTER:
                assert path.relative_to(REPO_ROOT).as_posix() in sources
    assert any(name.endswith(".json") for name in sources)
    for name, text in sources.items():
        assert (REPO_ROOT / name).read_text() == text


@pytest.mark.parametrize("scanned", sorted(invariant_sources()))
def test_a_vendor_term_injected_into_any_scanned_source_is_reported(scanned):
    sources = invariant_sources()
    sources[scanned] += f"\n{VENDOR_TERMS[0].capitalize()}Client\n"
    assert vendor_violations(sources) == {scanned: VENDOR_TERMS}


@pytest.mark.parametrize(
    "spelling",
    [
        "{title}McpTracker",
        "{lower}.app",
        "from adapters import {upper}",
        "{lower}_client",
        "{upper}-CLIENT",
        "tracker.{title}()",
    ],
)
def test_a_vendor_name_is_found_however_it_is_written(spelling):
    """The guard never spells a vendor itself; it renders the declared roster."""
    term = VENDOR_TERMS[0]
    rendered = spelling.format(title=term.capitalize(), lower=term, upper=term.upper())
    assert vendor_terms(rendered) == VENDOR_TERMS


@pytest.mark.parametrize("innocent", ["nonlinear", "linearity", "collinear", "linea"])
def test_a_longer_word_is_not_a_vendor_name(innocent):
    assert vendor_terms(innocent) == ()


def test_only_the_selectable_backend_roster_may_name_a_vendor():
    assert vendor_terms(VENDOR_ROSTER.read_text()) == VENDOR_TERMS
    assert VENDOR_ROSTER.relative_to(REPO_ROOT).as_posix() not in invariant_sources()


@pytest.mark.parametrize(
    "statement,found",
    [
        ("from kodezart.core.protocols import TrackerPort", ("core/protocols.py",)),
        ("import kodezart.core.protocols", ("core/protocols.py",)),
        ("from kodezart import core", ("core/__init__.py",)),
        ("from tests.fakes import FakeTrackerPort", ()),
        ("from . import sibling", ()),
        ("import kodezart", ()),
    ],
)
def test_only_packaged_imports_are_read_as_asserted_values(statement, found):
    assert first_party_modules(statement) == tuple(SOURCE_ROOT / name for name in found)


def test_the_spec_backend_reads_through_the_tracker_port_alone():
    surface = {name for name in dir(TrackerPort) if not name.startswith("_")}
    used = set(tracker_attributes(INVARIANT_MODULES[SPEC].read_text()))
    assert used
    assert used <= surface


@pytest.mark.parametrize(
    "source,expected",
    [
        ("await workspace.tracker.read_criteria(issue_key=key)", ("read_criteria",)),
        (
            "await live_model_tracker.read_labeled_issues(name)",
            ("read_labeled_issues",),
        ),
        ("tracker.caller.call_tool(name)", ("caller",)),
        ("issue.body", ()),
    ],
)
def test_a_reach_past_the_port_is_named_and_other_objects_are_not(source, expected):
    assert tracker_attributes(source) == expected


def identity_violations(sources: dict[str, str]) -> tuple[str, ...]:
    failures = []
    for identity, owner in IDENTITY_OWNERS.items():
        sites = [
            (path, line)
            for path, source in sources.items()
            for line in construction_sites(source, identity=identity)
        ]
        if len(sites) != 1 or sites[0][0] != owner:
            failures.append(
                f"{identity}: expected one construction in {owner}; {sites}"
            )
    for path, source in sources.items():
        if path.startswith("types/"):
            for line in invalid_ruling_fields(source):
                failures.append(f"{path}:{line}: ruling address lacks RulingId")
    return tuple(failures)


def test_identity_invariant_uses_the_actual_code_backend():
    sources = {
        path.relative_to(SOURCE_ROOT).as_posix(): path.read_text()
        for path in SOURCE_ROOT.rglob("*.py")
    }
    assert identity_violations(sources) == ()


@pytest.mark.parametrize("identity", IDENTITY_OWNERS)
@pytest.mark.parametrize(
    "extra",
    [
        "{identity}('another')",
        "from somewhere import {identity} as Key\nKey('another')",
        "namespace.{identity}('another')",
        "construct = namespace.{identity}\ncopy = construct\ncopy('another')",
        "from somewhere import {identity} as Key\n"
        "construct: object = Key\nconstruct('another')",
        "def function(value={identity}('another')):\n    return value",
        "@decorate({identity}('another'))\ndef function():\n    pass",
    ],
)
def test_a_second_explicit_construction_fails_the_same_invariant(identity, extra):
    sources = {
        owner: f"{name}('native-key')" for name, owner in IDENTITY_OWNERS.items()
    }
    assert identity_violations(sources) == ()
    sources["another.py"] = extra.format(identity=identity)
    failures = identity_violations(sources)
    assert len(failures) == 1
    assert identity in failures[0] and "another.py" in failures[0]


@pytest.mark.parametrize(
    "annotation",
    [
        "str",
        '"str | None"',
        "tuple[str, ...]",
        "bool",
        "int",
        "object",
        "Any",
        "RulingId | str",
        "Annotated[str, 'RulingId']",
        "Text",
        '"Text"',
        "CycleA",
    ],
)
def test_a_model_cannot_replace_a_ruling_address_with_text_or_another_type(annotation):
    sources = {
        owner: f"{name}('native-key')" for name, owner in IDENTITY_OWNERS.items()
    }
    sources["types/domain/record.py"] = (
        "Text = str\nCycleA = CycleB\nCycleB = CycleA\n"
        f"class Record:\n    ruling_id: {annotation}\n"
    )
    failures = identity_violations(sources)
    assert len(failures) == 1
    assert "types/domain/record.py" in failures[0] and "RulingId" in failures[0]


@pytest.mark.parametrize(
    "annotation",
    [
        "RulingId",
        '"RulingId | None"',
        'tuple["RulingId", ...]',
        "frozenset[RulingId]",
        "Annotated[RulingId, 'str']",
        "Alias",
        '"Alias"',
        "namespace.RulingId",
    ],
)
def test_typed_ruling_addresses_preserve_aliases_and_annotation_metadata(annotation):
    source = (
        "from somewhere import RulingId as Key\nAlias = Key\n"
        f"class Record:\n    ruling_ref: {annotation}\n"
    )
    assert invalid_ruling_fields(source) == ()


def test_identity_mentions_and_annotations_do_not_construct_addresses():
    source = """
from somewhere import CriterionRef
"RulingId('a quotation')"
class Record:
    criterion: CriterionRef
    ruling_id: RulingId
"""
    for identity in IDENTITY_OWNERS:
        assert construction_sites(source, identity=identity) == ()


@pytest.mark.parametrize(
    "replacement",
    ["RulingId = str", "from builtins import str as RulingId"],
)
def test_rebinding_the_identity_name_to_text_does_not_keep_the_address_typed(
    replacement,
):
    assert invalid_ruling_fields(
        f"{replacement}\nclass Record:\n    ruling_id: RulingId"
    )


@pytest.mark.parametrize("identity", IDENTITY_OWNERS)
def test_moving_the_only_construction_to_another_owner_fails(identity):
    sources = {
        owner: f"{name}('native-key')" for name, owner in IDENTITY_OWNERS.items()
    }
    sources["another.py"] = sources.pop(IDENTITY_OWNERS[identity])
    failures = identity_violations(sources)
    assert len(failures) == 1 and identity in failures[0]


@pytest.mark.parametrize("declaration", ["RulingId = str", "RulingId: TypeAlias = str"])
def test_qualified_shadow_cannot_make_text_an_identity(declaration):
    source = (
        f"class Types:\n    {declaration}\n"
        "class BadRecord:\n    ruling_id: Types.RulingId\n"
        "class GoodRecord:\n    ruling_id: RulingId\n"
    )
    assert invalid_ruling_fields(source) == (4,)
    assert (
        invalid_ruling_fields(
            "import somewhere as namespace\nclass Record:\n"
            "    ruling_id: namespace.RulingId"
        )
        == ()
    )


def test_aliasing_a_local_namespace_preserves_its_untyped_address_refusal():
    assert invalid_ruling_fields(
        "class Types:\n    RulingId = str\n"
        "Alias = Types\nclass Record:\n    ruling_id: Alias.RulingId"
    ) == (5,)


@pytest.fixture
def deployed_event_table():
    """The code backend reads the shipped table, not a copied invariant roster."""
    example = SOURCE_ROOT.parents[1] / "docs" / "operation.example.toml"
    return OperationConfig.model_validate(tomllib.loads(example.read_text()))


def test_run_event_invariant_uses_the_actual_code_backend(deployed_event_table):
    vocabulary = {event.value for event in RunEventKind}
    assert set(deployed_event_table.run_event_states) == vocabulary
    assert {event.value for event in RUN_EVENT_PUBLISHERS} == vocabulary
    deployed_event_table.require_run_event_table()


@pytest.mark.parametrize("posted", tuple(RUN_EVENT_PUBLISHERS))
def test_posted_event_missing_row_fails_and_restoring_it_passes(
    deployed_event_table, posted
):
    effect = deployed_event_table.run_event_states.pop(posted.value)
    with pytest.raises(RunEventTableError) as failure:
        deployed_event_table.require_run_event_table()
    assert failure.value.failures == (
        f"run_event_states is missing event {posted.value!r}",
    )
    deployed_event_table.run_event_states[posted.value] = effect
    deployed_event_table.require_run_event_table()


def test_table_only_event_fails_the_other_side_of_the_same_invariant(
    deployed_event_table,
):
    deployed_event_table.run_event_states["invented_event"] = (
        RunEventEffect.NO_TRANSITION
    )
    with pytest.raises(RunEventTableError) as failure:
        deployed_event_table.require_run_event_table()
    assert failure.value.failures == (
        "run_event_states names undeclared event 'invented_event'",
    )
    del deployed_event_table.run_event_states["invented_event"]
    deployed_event_table.require_run_event_table()


def test_a_posting_declaration_outside_the_vocabulary_is_named(
    deployed_event_table, monkeypatch
):
    class ExtraPostedEvent(StrEnum):
        UNKNOWN = "unregistered_posted_event"

    monkeypatch.setitem(
        RUN_EVENT_PUBLISHERS, ExtraPostedEvent.UNKNOWN, RunEventPublisher.RAISER
    )
    with pytest.raises(RunEventTableError) as failure:
        deployed_event_table.require_run_event_table()
    assert failure.value.failures == (
        "run-event notification partition names undeclared event "
        "'unregistered_posted_event'",
    )


def test_the_deployed_event_table_names_no_vendor_state(deployed_event_table):
    table = "\n".join(
        f"{event} {effect}"
        for event, effect in sorted(deployed_event_table.run_event_states.items())
    )
    assert vendor_violations({"run_event_states": table}) == {}


DOMAIN_PACKAGE = "kodezart.types.domain"
GRADED_SHA_FIELD = "graded_sha"
GRADED_SHA = "4f2c7a1b9e0d3c5a8f6b2d4e7c9a1b3d5f7e9c0a"


def _annotation_leaves(annotation) -> list[object]:
    """The annotation and every type argument nested inside it."""
    leaves: list[object] = [annotation]
    for argument in get_args(annotation):
        leaves.extend(_annotation_leaves(argument))
    return leaves


def _nested_records(annotation) -> list[type[BaseModel]]:
    return [
        leaf
        for leaf in _annotation_leaves(annotation)
        if isinstance(leaf, type) and issubclass(leaf, BaseModel)
    ]


def carries_graded_sha(
    record: type[BaseModel], seen: frozenset[type[BaseModel]] = frozenset()
) -> bool:
    """Whether a record reaches a graded sha through its own fields."""
    for name, field in record.model_fields.items():
        if name == GRADED_SHA_FIELD:
            return True
        if any(
            nested not in seen and carries_graded_sha(nested, seen | {record})
            for nested in _nested_records(field.annotation)
        ):
            return True
    return False


def boolean_verdicts(records: dict[str, type[BaseModel]]) -> tuple[str, ...]:
    """Every boolean-annotated field on a record that carries a graded sha."""
    return tuple(
        f"{name}.{field}"
        for name, record in sorted(records.items())
        if carries_graded_sha(record)
        for field, info in record.model_fields.items()
        if any(leaf is bool for leaf in _annotation_leaves(info.annotation))
    )


def domain_records() -> dict[str, type[BaseModel]]:
    """Every record the domain value package defines, read from the tree."""
    package = importlib.import_module(DOMAIN_PACKAGE)
    records = {}
    for module_info in pkgutil.iter_modules(package.__path__):
        module = importlib.import_module(f"{DOMAIN_PACKAGE}.{module_info.name}")
        for value in vars(module).values():
            if (
                isinstance(value, type)
                and issubclass(value, BaseModel)
                and value.__module__ == module.__name__
            ):
                records[f"{module_info.name}.{value.__name__}"] = value
    return records


class _RingHead(CamelCaseModel):
    """A record whose graded sha is reachable only through a cycle."""

    peer: "_RingTail | None" = None


class _RingTail(CamelCaseModel):
    peer: _RingHead | None = None
    graded_sha: str = GRADED_SHA


_RingHead.model_rebuild()


def cross_off(**overrides) -> CriterionCrossOff:
    fields = {
        "criterion": "criterion/alpha",
        "state": CrossOffState.passed,
        "evidence": CriterionEvidence(
            graded_sha=GRADED_SHA,
            test="tests/domain/test_criterion_lifecycle.py::test_a_cross_off",
        ),
    }
    return CriterionCrossOff(**(fields | overrides))


def test_the_re_derivation_class_and_cross_off_state_members_are_exactly_these():
    assert [(member.name, member.value) for member in RederivationClass] == [
        ("cheap", "cheap"),
        ("expensive", "expensive"),
        ("observed", "observed"),
    ]
    assert [(member.name, member.value) for member in CrossOffState] == [
        ("passed", "passed"),
        ("failed", "failed"),
        ("lapsed", "lapsed"),
    ]
    assert PATH_BOUND_CLASSES == {
        RederivationClass.expensive,
        RederivationClass.observed,
    }


@pytest.mark.parametrize(
    "declared", [RederivationClass.expensive, RederivationClass.observed]
)
def test_a_path_bound_cross_off_refuses_empty_exercised_paths(declared):
    with pytest.raises(ValidationError, match="path prefixes its grading exercised"):
        cross_off(rederivation_class=declared)
    named = cross_off(
        rederivation_class=declared,
        exercised_paths=("src/kodezart/domain/", "src/kodezart/adapters/"),
    )
    assert named.exercised_paths == (
        "src/kodezart/domain/",
        "src/kodezart/adapters/",
    )


@pytest.mark.parametrize("blank", [(""), (" ",), ("\t",)])
def test_an_exercised_path_that_names_nothing_refuses(blank):
    with pytest.raises(ValidationError):
        cross_off(
            rederivation_class=RederivationClass.expensive, exercised_paths=(blank,)
        )


def test_a_cheap_cross_off_carries_no_exercised_paths_and_still_validates():
    assert cross_off(rederivation_class=RederivationClass.cheap).exercised_paths == ()


def test_a_verdict_carrying_no_class_resolves_to_the_cheap_class():
    assert cross_off().rederivation_class is RederivationClass.cheap
    validated = CriterionCrossOff.model_validate(
        {
            "criterion": "criterion/alpha",
            "state": "passed",
            "evidence": {"gradedSha": GRADED_SHA, "test": "tests/x.py::test_y"},
        }
    )
    assert validated.rederivation_class is RederivationClass.cheap
    assert validated.state is CrossOffState.passed
    assert validated.evidence.graded_sha == GRADED_SHA


def test_a_second_iteration_declaring_a_different_class_raises_the_sticky_error():
    iterations = [
        cross_off(
            rederivation_class=RederivationClass.expensive, exercised_paths=("src/",)
        ),
        cross_off(
            rederivation_class=RederivationClass.expensive, exercised_paths=("src/",)
        ),
        cross_off(rederivation_class=RederivationClass.cheap),
    ]
    with pytest.raises(StickyClassError) as raised:
        held_rederivation_classes(iterations)
    assert raised.value.criterion == "criterion/alpha"
    assert raised.value.held is RederivationClass.expensive
    assert raised.value.declared is RederivationClass.cheap
    assert "criterion/alpha" in str(raised.value)


def test_each_identity_holds_its_own_class_across_interleaved_iterations():
    held = held_rederivation_classes(
        [
            cross_off(criterion="criterion/alpha"),
            cross_off(
                criterion="criterion/beta",
                rederivation_class=RederivationClass.expensive,
                exercised_paths=("src/kodezart/adapters/",),
            ),
            cross_off(
                criterion="criterion/gamma",
                rederivation_class=RederivationClass.observed,
                exercised_paths=("docs/",),
            ),
            cross_off(criterion="criterion/alpha"),
            cross_off(
                criterion="criterion/gamma",
                rederivation_class=RederivationClass.observed,
                exercised_paths=("docs/", "src/"),
            ),
            cross_off(
                criterion="criterion/beta",
                rederivation_class=RederivationClass.expensive,
                exercised_paths=("tests/",),
            ),
        ]
    )
    assert held == {
        "criterion/alpha": RederivationClass.cheap,
        "criterion/beta": RederivationClass.expensive,
        "criterion/gamma": RederivationClass.observed,
    }


def test_the_graded_sha_partition_carries_no_boolean_verdict():
    records = domain_records()
    partition = {name for name in records if carries_graded_sha(records[name])}
    assert "criterion_lifecycle.CriterionCrossOff" in partition
    assert "criterion_evidence.CriterionEvidence" in partition
    assert "audit_evidence.AuditEvidenceObservation" in partition
    assert "check_observation.ObservedChecks" not in partition
    assert boolean_verdicts(records) == ()


@pytest.mark.parametrize("annotation", [bool, bool | None, tuple[bool, ...]])
def test_a_boolean_verdict_beside_a_graded_sha_is_reported(annotation):
    probe = create_model(
        "Probe",
        __base__=CamelCaseModel,
        evidence=(CriterionEvidence, ...),
        verdict=(annotation, ...),
    )
    assert boolean_verdicts({"probe.Probe": probe}) == ("probe.Probe.verdict",)


def test_a_boolean_on_a_record_that_carries_no_graded_sha_is_not_reported():
    probe = create_model("Probe", __base__=CamelCaseModel, verdict=(bool, ...))
    assert boolean_verdicts({"probe.Probe": probe}) == ()


def test_a_graded_sha_reached_through_a_record_cycle_is_still_found():
    assert carries_graded_sha(_RingHead)
    assert carries_graded_sha(_RingTail)
    probe = create_model(
        "Probe", __base__=CamelCaseModel, ring=(_RingHead, ...), verdict=(bool, ...)
    )
    assert boolean_verdicts({"probe.Probe": probe}) == ("probe.Probe.verdict",)

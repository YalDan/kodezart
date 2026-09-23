"""No ticket draft is made anywhere but the two authored drafting sites (KOD-419).

The tracker arm parses no body into a draft because the whole package makes a
draft in exactly two authored places, names the class as a value in two type
tests, and calls a model method on two receivers whose own function states no
type for them.  The register below carries all three kinds and says what each
is, so a third entry in any kind is read rather than absorbed.

The scanned surface is derived, not listed: every module that imports or
declares the class, reaches it through a module it imports, or imports a
carrier whose annotation hands the value around.  The construction forms the
shared walk knows are reported — the class call, the building and parsing
model methods however the receiver is reached, a subclass, an adapter or
partial built around the class, ``type(x)(...)`` and ``x.__class__(...)``.
The adapter and partial forms are caught when the class is handed bare: the
walk reads an argument that spells the class itself, so a class wrapped in a
generic or a union — ``TypeAdapter(list[TicketDraftOutput])``,
``TypeAdapter(TicketDraftOutput | None)``, a subscript and a binary operation
rather than the class — is not seen by this walk.

Stated blind spots.  A construction whose receiver is reached through a string
constant — ``getattr(agent, "TicketDraftOutput")``, a ``model_fields`` lookup
keyed by a word — names the class nowhere this walk reads, because a string
constant is read here only inside an annotation, never as a receiver or a
lookup key.  Less escapes than that sentence alone suggests: in a module the
derived surface already holds, such a receiver handed a model method is still
reported, as one of the unstated-receiver parses below, since silence about a
receiver is not a statement that it is something else.  What goes unreported
is a call that is no model method at all, as in
``AuthoredSpec.model_fields["ticket"].annotation(...)``, and any form at all
in a module the derived surface never reaches.

A draft parsed through a carrier — ``AuthoredSpec.model_validate({"ticket":
{...}})`` — makes the draft in a nested parse and names its class nowhere, so
the register reports it nowhere.  Its stop stands at every place the arm has a
tracker body in hand, derived from the composition that captures the subject:
the composition's own module, every module that calls it, and every module
holding the concrete read whose result a caller hands the composition as its
subject.  None of them may spell a carrier beyond what it spells at head.  A
carrier is read off the objects, not off how their source is written: every
word the package binds to an object a draft can be had from — a class holding
one in a field, a union, an alias, a ``NewType`` or a type variable over one,
a function or a method returning one, an adapter or a partial built over one
— to a fixed point.  So the partition's union, the workflow state and a
remediation request are carriers, and a new alias is one without a line here.
However a module reaches a carrier — by name, through its module, through a
package ``__init__`` or the whole dotted path, through ``importlib`` or
``sys.modules``, by a relative import, through a module that re-exports it, or
by a string naming it — it spells the carrier's word where it uses it; the
planted rows put each route at each place.

The floor lines below still state, by the shared import walk, that neither the
composition's module nor the adapter holds the draft at head; that walk
counts a carrier imported by name or through its declaring module, and the
spelling stop above is the one that holds for every route.

Stated limits.  A method is read by its word on any receiver, so a method of
the same name on an unrelated class is recorded rather than resolved (the
audit sweep's own ``prepare``); that over-includes on the red side.  A body
handed out of these modules to a helper elsewhere that parses it into a draft
and hands back nothing a draft can be had from is not seen, because the
helper's module holds no tracker body of its own.  A word built at run time,
and ``eval``/``exec``, are out of reach.
"""

import ast
import importlib
import importlib.util
import inspect
import typing
from pathlib import Path
from types import ModuleType

import pytest
from pydantic import TypeAdapter

from kodezart.domain.fire_spec import tracker_spec_from_issues
from kodezart.types.domain import fire_spec as partition
from kodezart.types.domain.agent import TicketDraftOutput
from kodezart.types.domain.fire_spec import AuthoredSpec, FireSpec
from kodezart.types.domain.tracker import TrackerIssue
from kodezart.types.domain.workflow import RemediationRequest, WorkflowState
from tests.identity_guards import model_value_sites, value_holders
from tests.name_resolution import (
    SOURCE_ROOT,
    call_sites,
    names_reaching,
    package_modules,
    parsed,
    source_tree,
    spelled_sites,
)

DRAFT = TicketDraftOutput.__name__
#: The partition's carrier of the draft and the module declaring it, read off
#: the class so a moved or renamed carrier moves the planted routes with it.
CARRIER = AuthoredSpec.__name__
CARRIER_HOME = AuthoredSpec.__module__

#: The two authored drafting sites: the only places the package makes a draft.
CONSTRUCTIONS = {
    "parse": (
        "chains/remediation.py::run",
        "chains/ticket_generation.py::_create_node",
    )
}
#: Two positions where the class is the second argument of isinstance; the
#: shared walk counts a call handed the class as a value use.  A third entry
#: here is a reading of the site, never a construction.
TYPE_TESTS = {
    "build": (
        "chains/fire_remediation.py::draft_remediation",
        "domain/workflow_state.py::current_fire_spec",
    )
}
#: One model-method call on a receiver whose own function states no type for it
#: (a comprehension variable).  The shared walk reports it because silence about
#: a receiver is not a statement that it is something else; it was read and makes
#: no draft.  The classmethod that used to sit beside it, the run context's parse
#: from its configurable, states its own receiver now (KOD-695), so the walk no
#: longer has to guess and the register no longer carries it.
UNSTATED_RECEIVERS = {
    "build": ("chains/lane_delivery.py::__init__",),
    "parse": (),
}

REGISTER = {
    "build": tuple(sorted(TYPE_TESTS["build"] + UNSTATED_RECEIVERS["build"])),
    "parse": tuple(sorted(CONSTRUCTIONS["parse"] + UNSTATED_RECEIVERS["parse"])),
}

#: The tracker-arm modules that turn issue text into a captured subject.  A
#: floor rather than the whole surface, because the carrier fixed point grows
#: with every module that hands a workflow state around.
TRACKER_ARM_MODULES = frozenset(
    {
        "types/domain/fire_spec.py",
        "domain/ticket.py",
        "domain/workflow_state.py",
        "chains/remediation.py",
        "chains/ticket_generation.py",
        "chains/fire_remediation.py",
        "chains/criteria.py",
    }
)

PLAIN_IMPORT = f"from kodezart.types.domain.agent import {DRAFT}\n"
MODULE_IMPORT = "import kodezart.types.domain.agent as agent\n"
ALIASED_IMPORT = f"from kodezart.types.domain.agent import {DRAFT} as Draft\n"

CRITERIA_ANCHOR = (
    "    require_unamended_subject("
    'issue_key=issue_key, entry=state["lane_entry"], spec=spec)\n'
)
#: The adapter no longer composes the spec — KOD-710 moved that to the stage that
#: is its one caller — so the point where the arm holds a tracker body is the
#: subject it returns from the entry read.
TRACKER_ANCHOR = "        return subject\n"
TRACKER_MODULE = "adapters/linear/tracker.py"

#: Where a tracker body is in hand, the name holding it, and the function the
#: report would name.  Each anchor is asserted unique before it is replaced.
PLANT_SITES = (
    (
        "domain/fire_spec.py",
        "    return TrackerSpec(\n",
        "subject",
        "tracker_spec_from_issues",
    ),
    (TRACKER_MODULE, TRACKER_ANCHOR, "subject", "read_fire_subject"),
    ("chains/criteria.py", CRITERIA_ANCHOR, "spec", "revalidate_criteria"),
)

FORMS = (
    "model_validate",
    "model_validate_json",
    "model_construct",
    "direct_call",
    "module_route",
    "aliased_import",
)


def planted_draft(form, receiver):
    """The import a form needs, and the expression that makes a draft."""
    body = f"{receiver}.body"
    if form == "model_validate":
        return PLAIN_IMPORT, f"{DRAFT}.model_validate({{'title': {body}}})"
    if form == "model_validate_json":
        return PLAIN_IMPORT, f"{DRAFT}.model_validate_json({body})"
    if form == "model_construct":
        return PLAIN_IMPORT, f"{DRAFT}.model_construct(title={body})"
    if form == "direct_call":
        return PLAIN_IMPORT, f"{DRAFT}(title={body})"
    if form == "module_route":
        return MODULE_IMPORT, f"agent.{DRAFT}.model_validate({{'title': {body}}})"
    return ALIASED_IMPORT, f"Draft.model_validate({{'title': {body}}})"


def test_the_draft_is_made_only_where_the_authored_arm_drafts_one():
    """Every value use of the draft in the package, compared to the register.

    The two authored sites both parse a session's structured output: the
    remediation chain's authored branch, and the ticket generation node the
    native graph never compiles.  The other four are readings of the class,
    not makings of one, and the register says which is which.
    """
    assert model_value_sites(source_tree(), identity=DRAFT) == REGISTER


def test_the_tracker_arm_modules_are_on_the_scanned_surface():
    """The ban is not vacuous: the arm's own modules are scanned.

    ``domain/fire_spec.py`` is not a holder at head — it captures a subject
    and names no draft — and neither is the adapter the subject is read
    through, so the reddening plants below import the class, which is the
    natural form a bypass would take.  Those two assertions are statements of
    head rather than bans: any carrier or class import into either module reds
    them, and their message is what tells a reader who adds a legitimate one
    that the surface moved.  The adapter's also reds a tracker body parsed
    into a draft through a carrier at the arm's own entry read, by the two
    import routes the shared walk counts; the spelling stop below holds for
    every route.
    """
    holders = value_holders(source_tree(), identity=DRAFT)

    assert holders.keys() >= TRACKER_ARM_MODULES
    assert "domain/fire_spec.py" not in holders
    assert TRACKER_MODULE not in holders


#: Each spelling of the module route to the carrier: the module bound under an
#: alias, a submodule bound by a ``from`` import, and the bare dotted import.
CARRIER_PACKAGE, _, CARRIER_LEAF = CARRIER_HOME.rpartition(".")
CARRIER_ROUTES = {
    "aliased_module": (f"import {CARRIER_HOME} as routed\n", f"routed.{CARRIER}"),
    "submodule_from_its_package": (
        f"from {CARRIER_PACKAGE} import {CARRIER_LEAF}\n",
        f"{CARRIER_LEAF}.{CARRIER}",
    ),
    "dotted_module": (f"import {CARRIER_HOME}\n", f"{CARRIER_HOME}.{CARRIER}"),
}


@pytest.mark.parametrize("route", sorted(CARRIER_ROUTES))
def test_a_carrier_reached_through_its_module_puts_the_adapter_on_the_surface(
    route,
):
    """The adapter's floor line reds a carrier parse however its module is bound.

    The nested parse names no draft, so the floor is what stops it at the
    entry read, and the floor rested on the carrier's own name: binding the
    carrier's module instead and reading the carrier off it walked past.  Each
    route plants the parse of the subject's body into the carrier before the
    entry read returns, and the adapter must join the scanned surface.
    """
    sources = source_tree()
    assert sources[TRACKER_MODULE].count(TRACKER_ANCHOR) == 1
    imports, carrier = CARRIER_ROUTES[route]
    sources[TRACKER_MODULE] = imports + sources[TRACKER_MODULE].replace(
        TRACKER_ANCHOR,
        f"        draft = {carrier}.model_validate("
        "{'ticket': {'title': subject.body}})\n" + TRACKER_ANCHOR,
    )

    assert TRACKER_MODULE in value_holders(sources, identity=DRAFT)


@pytest.mark.parametrize("form", FORMS)
@pytest.mark.parametrize(
    ("module", "anchor", "receiver", "function"),
    PLANT_SITES,
    ids=[module for module, _anchor, _receiver, _function in PLANT_SITES],
)
def test_the_ban_reddens_when_the_tracker_arm_parses_a_body_into_a_draft(
    module, anchor, receiver, function, form
):
    """A draft made from a tracker body on the arm is reported, in every form.

    The first row is the mutation the criterion was refuted on: a
    ``model_validate`` of the subject's own body inside the tracker spec
    capture.  The rest are the other forms the same bypass can take,
    including the class reached through the module it lives in and under an
    import alias.
    """
    sources = source_tree()
    assert sources[module].count(anchor) == 1
    imports, expression = planted_draft(form, receiver)
    indent = anchor[: len(anchor) - len(anchor.lstrip())]
    sources[module] = imports + sources[module].replace(
        anchor, f"{indent}draft = {expression}\n{anchor}"
    )

    sites = model_value_sites(sources, identity=DRAFT)

    assert f"{module}::{function}" in sites["build"] + sites["parse"]
    assert sites != REGISTER


# The places the arm has a tracker body in hand, and the carriers they spell.

PACKAGE = source_tree()
PARSED = parsed(PACKAGE)
MODULES = package_modules(PACKAGE)
#: Every word the package binds to an object a draft can be had from,
#: derived from the objects themselves: the draft, every carrier of it, the
#: partition's union, every alias and adapter over one, every function and
#: method returning one.
CARRIER_WORDS = names_reaching(TicketDraftOutput, modules=MODULES)

COMPOSITION = tracker_spec_from_issues
COMPOSITION_HOME = (
    Path(inspect.getsourcefile(COMPOSITION) or "").relative_to(SOURCE_ROOT).as_posix()
)
#: The composition's parameter that takes the subject issue, read off its own
#: signature rather than named here.
SUBJECT = next(
    name
    for name, hint in typing.get_type_hints(COMPOSITION).items()
    if hint is TrackerIssue
)


def _reader_words(tree: ast.Module, name: str) -> set[str]:
    """The callee words whose result *tree* binds to *name*."""
    found: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign) or not any(
            isinstance(target, ast.Name) and target.id == name
            for target in node.targets
        ):
            continue
        value = node.value.value if isinstance(node.value, ast.Await) else node.value
        if isinstance(value, ast.Call) and isinstance(
            value.func, ast.Name | ast.Attribute
        ):
            found.add(
                value.func.id if isinstance(value.func, ast.Name) else value.func.attr
            )
    return found


def _concrete_definitions(words: set[str]) -> set[str]:
    """The modules defining one of *words* as a method of a class no port is."""
    found: set[str] = set()
    for path, tree in PARSED.items():
        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef):
                continue
            if any(
                typing.Protocol.__name__ in ast.unparse(base) for base in node.bases
            ):
                continue
            if any(
                isinstance(member, ast.FunctionDef | ast.AsyncFunctionDef)
                and member.name in words
                for member in node.body
            ):
                found.add(path)
    return found


def _tracker_body_modules() -> frozenset[str]:
    """Where the arm has a tracker body in hand, derived from the composition.

    The composition's own module; every module that calls it; and every
    module holding the concrete read whose result a caller hands the
    composition as its subject.
    """
    modules = {COMPOSITION_HOME}
    readers: set[str] = set()
    for site in call_sites(PARSED, names={COMPOSITION.__name__}):
        modules.add(site.module)
        tree = PARSED[site.module]
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or node.lineno != site.line:
                continue
            for keyword in node.keywords:
                if keyword.arg == SUBJECT and isinstance(keyword.value, ast.Name):
                    readers |= _reader_words(tree, keyword.value.id)
    return frozenset(modules | _concrete_definitions(readers))


TRACKER_BODY_MODULES = _tracker_body_modules()

#: What each module holding a tracker body spells of the carriers at head, by
#: the definition that spells it.  The criteria stage imports the workflow
#: state its stage signatures are annotated with; the audit sweep calls its
#: own ``prepare``, a word a native delivery method returning the workflow
#: state also binds, so it is read here rather than resolved.  Neither makes
#: a draft.  A new entry is a carrier reached where a tracker body is in hand.
SPELLED = {
    TRACKER_MODULE: (),
    "chains/audit_sweep.py": (("AuditReadSweep.run", "prepare"),),
    "chains/criteria.py": (("<module>", WorkflowState.__name__),),
    COMPOSITION_HOME: (),
}


def test_where_the_arm_holds_a_tracker_body_it_reaches_no_carrier():
    """No module holding a tracker body spells a carrier beyond the register.

    However a module reaches a carrier — by name, through its module, the
    package, ``importlib``, ``sys.modules``, a relative import, a re-export
    or a string naming it — it spells the carrier's word where it uses it,
    and the word comes from the objects, so an alias or a union over a
    carrier is one.
    """
    assert {
        path: spelled_sites(PARSED[path], words=CARRIER_WORDS)
        for path in sorted(TRACKER_BODY_MODULES)
    } == SPELLED


def test_every_derived_list_of_the_carrier_stop_is_populated():
    """Not parametrised: an empty derivation fails here, not silently."""
    union = {name for name, value in vars(partition).items() if value is FireSpec}
    assert union
    assert {DRAFT, CARRIER, RemediationRequest.__name__, WorkflowState.__name__} | (
        union
    ) <= CARRIER_WORDS
    assert METHOD_WORDS
    assert ADAPTERS
    assert TRACKER_BODY_MODULES >= {module for module, *_ in BODY_PLANT_SITES}
    assert TRACKER_MODULE in TRACKER_BODY_MODULES
    assert REEXPORT


UNION = next(name for name, value in vars(partition).items() if value is FireSpec)
#: The words that are no module-level name anywhere: methods returning a
#: carrier, reached by that word on any receiver.
METHOD_WORDS = sorted(
    CARRIER_WORDS - {name for module in MODULES for name in vars(module)}
)
#: A module-level adapter built over a carrier, and the module binding it.
ADAPTERS = sorted(
    (module.__name__, name)
    for module in MODULES
    for name, value in vars(module).items()
    if isinstance(value, TypeAdapter) and name in CARRIER_WORDS
)
#: A module other than the carrier's own that re-exports it.
REEXPORT = next(
    (
        module.__name__
        for module in MODULES
        if module.__name__ != CARRIER_HOME and vars(module).get(CARRIER) is AuthoredSpec
    ),
    "",
)

#: Every place a tracker body is in hand that a plant can stand at, and the
#: name holding it.  The entry read of the criteria stage is the window the
#: other stops left open: the subject is held there before the spec exists.
BODY_PLANT_SITES = (
    (COMPOSITION_HOME, "    return TrackerSpec(\n", "subject"),
    (TRACKER_MODULE, TRACKER_ANCHOR, "subject"),
    ("chains/criteria.py", "        spec = tracker_spec_from_issues(\n", "subject"),
)


def planted_carrier(route: str, module: str, body: str) -> tuple[str, str]:
    """The import a route needs, and the expression that parses *body* into a
    draft through a carrier reached that way."""
    data = f"{{'ticket': {{'title': {body}}}}}"
    parse = f".model_validate({data})"
    depth = module.count("/") + 1
    relative = "." * depth + CARRIER_HOME.partition(".")[2]
    routes = {
        "carrier_by_name": (f"from {CARRIER_HOME} import {CARRIER}\n", CARRIER + parse),
        "partition_union_by_name": (
            f"from pydantic import TypeAdapter\nfrom {CARRIER_HOME} import {UNION}\n",
            f"TypeAdapter({UNION}).validate_python({data})",
        ),
        "importlib": (
            "import importlib\n",
            f"importlib.import_module('{CARRIER_HOME}').{CARRIER}{parse}",
        ),
        "sys_modules": (
            "import sys\n",
            f"sys.modules['{CARRIER_HOME}'].{CARRIER}{parse}",
        ),
        "package_init": (
            f"import {CARRIER_PACKAGE} as routed_package\n",
            f"routed_package.{CARRIER_LEAF}.{CARRIER}{parse}",
        ),
        "whole_dotted_path": (
            f"import {SOURCE_ROOT.name}\n",
            f"{CARRIER_HOME}.{CARRIER}{parse}",
        ),
        "relative_import": (
            f"from {relative} import {CARRIER} as Held\n",
            "Held" + parse,
        ),
        "re_export": (
            f"import {REEXPORT} as reexported\n",
            f"reexported.{CARRIER}{parse}",
        ),
        "string_naming_the_union": (
            "import importlib\nfrom pydantic import TypeAdapter\n",
            f"TypeAdapter(vars(importlib.import_module('{CARRIER_HOME}'))"
            f"['{UNION}']).validate_python({data})",
        ),
        "workflow_state_already_in_scope": (
            "from pydantic import TypeAdapter\n",
            f"TypeAdapter({WorkflowState.__name__}).validate_python("
            f"{{'fire_spec': {data}}})",
        ),
        "method_returning_a_carrier": ("", f"held.{METHOD_WORDS[0]}({body})"),
        "adapter_built_elsewhere": (
            f"from {ADAPTERS[0][0]} import {ADAPTERS[0][1]}\n",
            f"{ADAPTERS[0][1]}.validate_python({data})",
        ),
    }
    return routes[route]


CARRIER_PLANT_ROUTES = (
    "carrier_by_name",
    "partition_union_by_name",
    "importlib",
    "sys_modules",
    "package_init",
    "whole_dotted_path",
    "relative_import",
    "re_export",
    "string_naming_the_union",
    "workflow_state_already_in_scope",
    "method_returning_a_carrier",
    "adapter_built_elsewhere",
)


@pytest.mark.parametrize("route", CARRIER_PLANT_ROUTES)
@pytest.mark.parametrize(
    ("module", "anchor", "receiver"),
    BODY_PLANT_SITES,
    ids=[module for module, _anchor, _receiver in BODY_PLANT_SITES],
)
def test_a_body_parsed_through_a_carrier_where_the_arm_holds_it_is_reported(
    module, anchor, receiver, route
):
    """Each route to a carrier, planted where a tracker body is in hand.

    The parse names no draft, so the register above never sees it; the
    spelling of the carrier at the plant is what reds the stop, whichever
    route reached it.
    """
    sources = dict(PACKAGE)
    assert sources[module].count(anchor) == 1
    imports, expression = planted_carrier(route, module, f"{receiver}.body")
    indent = anchor[: len(anchor) - len(anchor.lstrip())]
    sources[module] = imports + sources[module].replace(
        anchor, f"{indent}draft = {expression}\n{anchor}"
    )

    planted = spelled_sites(ast.parse(sources[module]), words=CARRIER_WORDS)

    assert set(planted) - set(SPELLED[module])


#: One module-level binding per kind of object a draft can be had from, and
#: beside them the kinds that hand no draft back.  Loaded as a package module
#: from a file, so every object is the package's own as a shipped one is.
REACH_CONTROL = f"""
import functools
from typing import TYPE_CHECKING, Annotated, NewType, TypeVar

from pydantic import BaseModel, TypeAdapter

from {TicketDraftOutput.__module__} import {DRAFT}

if TYPE_CHECKING:
    from {SOURCE_ROOT.name}._reach_elsewhere import Elsewhere


class Holder(BaseModel):
    draft: {DRAFT}


class Subclass(Holder):
    pass


class Forward:
    held: "Elsewhere"


class Maker:
    def make(self) -> {DRAFT}:
        raise NotImplementedError

    def use(self, draft: {DRAFT}) -> None:
        raise NotImplementedError


def returns() -> list[{DRAFT}]:
    raise NotImplementedError


def takes(draft: {DRAFT}) -> None:
    raise NotImplementedError


Union = {DRAFT} | None
Wrapped = Annotated[{DRAFT}, "note"]
Named = NewType("Named", {DRAFT})
Bound = TypeVar("Bound", bound={DRAFT})
type Aliased = list[{DRAFT}]
Bound_later = functools.partial(returns)
Adapter = TypeAdapter({DRAFT})
Table = {{"one": Holder}}
Unrelated = int
"""
#: A holder defined in another module, which the control names only for the
#: type check: the walk finds it where it is defined.
REACH_ELSEWHERE = f"""
from pydantic import BaseModel

from {TicketDraftOutput.__module__} import {DRAFT}


class Elsewhere(BaseModel):
    draft: {DRAFT}
"""
REACHED = {
    DRAFT,
    "Holder",
    "Subclass",
    "Forward",
    "Maker",
    "make",
    "returns",
    "Union",
    "Wrapped",
    "Named",
    "Bound",
    "Aliased",
    "Bound_later",
    "Adapter",
    "Table",
}
UNREACHED = {"use", "takes", "Unrelated", "functools", "BaseModel", "TypeAdapter"}


def _loaded(root: Path, name: str, text: str) -> ModuleType:
    """A module of the package loaded from *text*, never registered."""
    source = root / f"{name}.py"
    source.write_text(text, encoding="utf-8")
    spec = importlib.util.spec_from_file_location(f"{SOURCE_ROOT.name}.{name}", source)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_every_kind_of_object_a_draft_can_be_had_from_is_a_carrier(tmp_path):
    """The carrier walk follows each kind of object, and only toward the draft.

    Each binding stands for one leg of the walk, so dropping the leg drops
    its word; the unreached words pin that a parameter annotation, an
    unrelated alias and a foreign name do not count.
    """
    control = _loaded(tmp_path, "_reach_control", REACH_CONTROL)
    elsewhere = _loaded(tmp_path, "_reach_elsewhere", REACH_ELSEWHERE)

    reached = names_reaching(TicketDraftOutput, modules=(control, elsewhere))

    assert reached >= REACHED
    assert not reached & UNREACHED

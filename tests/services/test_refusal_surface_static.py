"""The refusal path offers nothing that clears a refusal (KOD-432).

Undemonstrability fails closed, and what makes it fail closed is that the only
way out of a recorded refusal is a person's act on the board: declaring the
capability the runner environment lacks, or cancelling the criterion with a
supersession. A callable that cleared the reason, withdrew the escalation or
deleted the marker comment would be a second way out — one a session could
reach — and it would be invisible to every behavioural assertion, because a
surface nobody calls yet changes no observed walk.

So the absence is asserted over the syntax tree of the modules that handle the
refusal vocabulary, over the tracker role the writers depend on, and over every
role protocol whose methods carry an amendment type.

**Blind spots, stated rather than hidden.**

* The scanned set is DERIVED: every module under ``src/kodezart`` whose own
  import nodes bring in ``UpheldReason`` or ``LaneEscalation`` or import from
  one of the two amendment modules (``kodezart.domain.amendment``,
  ``kodezart.types.domain.amendment``), plus the two modules that declare the
  refusal names. A module that reaches the refusal some other way — through a
  value handed to it, or by a name built at runtime — is not in the set, and
  neither is one that only mentions the vocabulary in prose.
* The role protocols are DERIVED too: ``TrackerPort``, plus every ``Protocol``
  in ``kodezart.core.protocols`` whose method annotations mention a type
  declared in ``kodezart.types.domain.amendment``. The rest of that module is
  not scanned, because it declares git-service methods such as
  ``remove_worktree`` that have nothing to do with a refusal.
* It is a scan over DEFINITION names, leading underscores ignored. A clearing
  surface spelled without one of the stems below, or reached through
  ``setattr`` on a model, is not seen.
* ``TrackerPort.upsert_comment`` can rewrite a marker comment's body. It is the
  write every marker record takes, so it is not a removal-stemmed name and the
  scan does not see it; what keeps it from clearing a refusal is its callers,
  not this guard.
* It says nothing about the tracker ADAPTERS' private helpers, only about the
  role protocol its consumers depend on: a vendor adapter may delete a comment
  of its own, but no consumer of the tracker role can ask it to.

The detector has a control, because a guard whose expected result is "nothing"
says nothing until it has been seen to fire: a planted definition is put through
the same detector and must be found.
"""

import ast
import inspect
from collections.abc import Iterator
from pathlib import Path
from typing import Protocol, get_args

import kodezart.core.protocols as protocols
import kodezart.types.domain.amendment as amendment_types
from kodezart.core.protocols import TrackerPort

#: What a callable that undoes a recorded refusal would be called. These are the
#: guard's own vocabulary, not a reading of the code: a stem added here is a
#: shape newly forbidden, and the controls below are drawn from this tuple.
REMOVAL_STEMS = (
    "clear",
    "remove",
    "delete",
    "withdraw",
    "retract",
    "revoke",
    "unset",
    "rescind",
    "revive",
)

#: The two names that mark a module as handling the refusal itself: the reason a
#: refusal carries, and the escalation it raises.
REFUSAL_NAMES = frozenset({"UpheldReason", "LaneEscalation"})

#: The modules that declare the amendment vocabulary and its resolver: importing
#: anything from either marks a module as on the refusal path.
AMENDMENT_MODULES = frozenset(
    {"kodezart.domain.amendment", "kodezart.types.domain.amendment"}
)

SRC = Path(__file__).resolve().parents[2] / "src"


def handles_refusal(tree: ast.AST) -> bool:
    """Whether *tree* imports one of the refusal names, or declares it.

    The declaring module is in the set for the same reason its consumers are: a
    helper that undid a refusal would most naturally sit beside the vocabulary
    it undoes.
    """
    return any(
        (
            isinstance(node, ast.ImportFrom)
            and (
                node.module in AMENDMENT_MODULES
                or any(alias.name in REFUSAL_NAMES for alias in node.names)
            )
        )
        or (isinstance(node, ast.ClassDef) and node.name in REFUSAL_NAMES)
        for node in ast.walk(tree)
    )


def clearing_definitions(tree: ast.AST, *, label: str) -> list[str]:
    """Every function or method in *tree* whose name begins with a removal stem.

    Over a tree rather than a path, so the detector can be asked about a source
    written for the purpose: a control cannot be drawn from the surface it is
    the control for.
    """
    return [
        f"{label}:{node.lineno}: {node.name}"
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
        and node.name.lstrip("_").startswith(REMOVAL_STEMS)
    ]


#: The role protocols module. It imports the amendment types, but it also
#: declares git-service methods such as ``remove_worktree``, so it is read
#: through the derived role protocols below rather than scanned whole.
PROTOCOLS_MODULE = SRC / "kodezart" / "core" / "protocols.py"


def refusal_modules() -> dict[Path, ast.AST]:
    """Every module under ``src/kodezart`` that handles the refusal vocabulary.

    The role protocols module is left to the role half of the guard.
    """
    found = {}
    for path in sorted(SRC.rglob("*.py")):
        if path == PROTOCOLS_MODULE:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        if handles_refusal(tree):
            found[path] = tree
    return found


def _mentioned(annotation: object) -> Iterator[object]:
    """Every type mentioned anywhere inside one annotation.

    A callable's parameter list arrives as a plain list, so it is walked too.
    """
    if isinstance(annotation, list):
        for item in annotation:
            yield from _mentioned(item)
        return
    yield annotation
    for argument in get_args(annotation):
        yield from _mentioned(argument)


AMENDMENT_TYPES = frozenset(
    value
    for value in vars(amendment_types).values()
    if isinstance(value, type) and value.__module__ == amendment_types.__name__
)


def _role_methods(role: type) -> dict[str, object]:
    """Every method a role protocol declares, its bases included, dunders aside."""
    return {
        name: value
        for base in role.__mro__
        if base not in (object, Protocol) and base.__module__ != "typing"
        for name, value in vars(base).items()
        if not (name.startswith("__") and name.endswith("__"))
        and (callable(value) or isinstance(value, property))
    }


def _carries_an_amendment_type(role: type) -> bool:
    for value in _role_methods(role).values():
        function = value.fget if isinstance(value, property) else value
        annotations = inspect.get_annotations(function, eval_str=True)
        if any(
            isinstance(found, type) and found in AMENDMENT_TYPES
            for annotation in annotations.values()
            for found in _mentioned(annotation)
        ):
            return True
    return False


def amendment_roles() -> set[type]:
    """``TrackerPort`` and every role protocol whose methods carry an amendment type."""
    derived = {
        value
        for value in vars(protocols).values()
        if isinstance(value, type)
        and value.__module__ == protocols.__name__
        and Protocol in value.__bases__
        and _carries_an_amendment_type(value)
    }
    return {TrackerPort, *derived}


def test_the_refusal_path_exposes_no_clearing_surface():
    """No module handling a refusal, and no tracker role method, undoes one.

    The scanned set is found through the code's own imports, so the surface grows
    with the code rather than with this list. The tracker half is the role the
    write-back and the escalation writer depend on: even a consumer that wanted
    to erase the record has no method to call.
    """
    scanned = refusal_modules()
    # The derived set is non-empty, so an import change that emptied it cannot
    # pass as an absence of findings.
    assert len(scanned) > 1
    assert {
        path.relative_to(SRC).as_posix()
        for path in scanned
        if "amendment" in path.name or "escalation" in path.name
    } >= {
        "kodezart/domain/amendment.py",
        "kodezart/services/amendment_writeback.py",
        "kodezart/services/lane_escalation.py",
        "kodezart/services/native_amendments.py",
        "kodezart/types/domain/amendment.py",
    }
    assert [
        site
        for path, tree in scanned.items()
        for site in clearing_definitions(tree, label=path.relative_to(SRC).as_posix())
    ] == []
    roles = amendment_roles()
    # Non-empty for the same reason: an empty role would pass vacuously, and the
    # writer guard is the role the whole refusal path runs through.
    assert protocols.NativeWriteGuard in roles
    assert "upsert_comment" in _role_methods(TrackerPort)
    assert [
        f"{role.__name__}.{name}"
        for role in sorted(roles, key=lambda role: role.__name__)
        for name in sorted(_role_methods(role))
        if name.lstrip("_").startswith(REMOVAL_STEMS)
    ] == []


#: One planted definition per stem, so the detector is seen to fire on each
#: shape it claims to forbid, and one private spelling, so a leading underscore
#: does not hide one. Written here rather than drawn from the scanned surface,
#: because that surface is expected to name none of them.
CONTROLS = (
    *(f"{stem}_upheld_reason" for stem in REMOVAL_STEMS),
    "_clear_upheld_reason",
)


def test_the_detector_finds_a_planted_clearing_surface():
    """The guard above asserts an absence, so its detector is exercised here."""
    for name in CONTROLS:
        planted = ast.parse(
            f"class Writer:\n    async def {name}(self, *, key):\n        return None\n"
        )
        assert clearing_definitions(planted, label="planted") == [f"planted:2: {name}"]
    kept = ast.parse(
        "def upheld_reason(claim, judgment, *, environment):\n    return None\n"
    )
    assert clearing_definitions(kept, label="planted") == []


def test_the_refusal_path_is_recognised_by_an_amendment_import():
    """A module importing the resolver alone is on the refusal path; others are not."""
    assert handles_refusal(
        ast.parse("from kodezart.domain.amendment import upheld_reason\n")
    )
    assert not handles_refusal(
        ast.parse("from kodezart.domain.git_url import parse_repo_url\n")
    )

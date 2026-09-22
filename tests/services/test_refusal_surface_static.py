"""The refusal path offers nothing that clears a refusal (KOD-432).

Undemonstrability fails closed, and what makes it fail closed is that the only
way out of a recorded refusal is a person's act on the board: declaring the
capability the runner environment lacks, or cancelling the criterion with a
supersession. A callable that cleared the reason, withdrew the escalation or
deleted the marker comment would be a second way out — one a session could
reach — and it would be invisible to every behavioural assertion, because a
surface nobody calls yet changes no observed walk.

So the absence is asserted over the syntax tree of the modules that handle the
refusal vocabulary, and over the tracker role the writers depend on.

**Blind spots, stated rather than hidden.**

* The scanned set is DERIVED: every module under ``src/kodezart`` whose own
  import nodes bring in ``UpheldReason`` or ``LaneEscalation``, plus the two
  modules that declare them. A module that reaches the refusal some other way —
  through a value handed to it, or by a name built at runtime — is not in the
  set, and neither is one that only mentions the vocabulary in prose.
* It is a scan over DEFINITION names. A clearing surface spelled without one of
  the stems below, or reached through ``setattr`` on a model, is not seen.
* It says nothing about the tracker ADAPTERS' private helpers, only about the
  role protocol its consumers depend on: a vendor adapter may delete a comment
  of its own, but no consumer of the tracker role can ask it to.

The detector has a control, because a guard whose expected result is "nothing"
says nothing until it has been seen to fire: a planted definition is put through
the same detector and must be found.
"""

import ast
import inspect
from pathlib import Path

from kodezart.core.protocols import TrackerPort

#: What a callable that undoes a recorded refusal would be called. These are the
#: guard's own vocabulary, not a reading of the code: a stem added here is a
#: shape newly forbidden, and the control below is compared against the set, so
#: a stem cannot be added without a control that exercises it.
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
            and any(alias.name in REFUSAL_NAMES for alias in node.names)
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
        and node.name.startswith(REMOVAL_STEMS)
    ]


def refusal_modules() -> dict[Path, ast.AST]:
    """Every module under ``src/kodezart`` that handles the refusal vocabulary."""
    found = {}
    for path in sorted(SRC.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        if handles_refusal(tree):
            found[path] = tree
    return found


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
        "kodezart/types/domain/amendment.py",
    }
    assert [
        site
        for path, tree in scanned.items()
        for site in clearing_definitions(tree, label=path.relative_to(SRC).as_posix())
    ] == []
    tracker_methods = {
        name
        for name, _ in inspect.getmembers(TrackerPort, callable)
        if not name.startswith("_")
    }
    # Non-empty for the same reason: an empty role would pass vacuously.
    assert "upsert_comment" in tracker_methods
    assert [
        name for name in sorted(tracker_methods) if name.startswith(REMOVAL_STEMS)
    ] == []


#: One planted definition per stem, so the detector is seen to fire on each
#: shape it claims to forbid. Written here rather than drawn from the scanned
#: surface, because that surface is expected to name none of them.
CONTROLS = tuple(f"{stem}_upheld_reason" for stem in REMOVAL_STEMS)


def test_the_detector_finds_a_planted_clearing_surface():
    """The guard above asserts an absence, so its detector is exercised here."""
    assert {name.partition("_")[0] for name in CONTROLS} == set(REMOVAL_STEMS)
    for name in CONTROLS:
        planted = ast.parse(
            f"class Writer:\n    async def {name}(self, *, key):\n        return None\n"
        )
        assert clearing_definitions(planted, label="planted") == [f"planted:2: {name}"]
    kept = ast.parse(
        "def upheld_reason(claim, judgment, *, environment):\n    return None\n"
    )
    assert clearing_definitions(kept, label="planted") == []

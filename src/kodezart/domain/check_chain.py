"""Root-versus-cascade classification over a repository's check chain.

Pure: the chain and the set of failed step names go in, a classification
comes out.  No I/O, no judgment, no tolerance — a failed step is a cascade
exactly when some step it transitively depends on also failed, and a root
otherwise.

This is what the passes' honesty rule operates on.  Reporting three reds as
three problems when two of them only ran-and-failed because the gate below
them failed is the failure mode the rule names; a flat list of command
strings cannot distinguish them, so the structure lives in the config model
and the arithmetic lives here.

Restored under KOD-112 R5 after the deletion whose ground was "no caller":
the caller is the reinstated pass path (KOD-60), which consumes this
classification when reporting check results, and that assignment is
recorded on the tracker before this restoration.  Deleting this again as
unreferenced would have to be undone by the pass that lands the routine.
"""

from collections.abc import Iterable, Sequence

from pydantic import ConfigDict

from kodezart.types.base import CamelCaseModel
from kodezart.types.domain.operation import CheckStep


class CheckFailureClassification(CamelCaseModel):
    """Which failures are causes and which are consequences.

    Both tuples preserve the chain's declared order, so two runs over one
    chain and one failure set produce one classification.
    """

    model_config = ConfigDict(frozen=True)

    roots: tuple[str, ...]
    cascades: tuple[str, ...]


def classify_check_failures(
    steps: Sequence[CheckStep],
    failed: Iterable[str],
) -> CheckFailureClassification:
    """Split *failed* step names into root causes and cascades.

    Unknown names in *failed* are not silently dropped: a name the chain
    does not declare has no dependencies to be a cascade of, so it is a
    root and is reported as one.
    """
    failed_names = set(failed)
    by_name = {step.name: step for step in steps}
    order = [step.name for step in steps]
    order.extend(sorted(failed_names - set(by_name)))

    roots: list[str] = []
    cascades: list[str] = []
    for name in order:
        if name not in failed_names:
            continue
        if _has_failed_ancestor(name, by_name, failed_names):
            cascades.append(name)
        else:
            roots.append(name)
    return CheckFailureClassification(roots=tuple(roots), cascades=tuple(cascades))


def _has_failed_ancestor(
    name: str,
    by_name: dict[str, CheckStep],
    failed_names: set[str],
) -> bool:
    step = by_name.get(name)
    cursor = None if step is None else step.depends_on
    while cursor is not None:
        if cursor in failed_names:
            return True
        ancestor = by_name.get(cursor)
        cursor = None if ancestor is None else ancestor.depends_on
    return False

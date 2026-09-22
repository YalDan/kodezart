"""Arithmetic over a declared repository check chain.

The historical classifier is restored with the real union consumer. A
failed step is a cascade exactly when a declared ancestor also failed;
unknown failed names remain roots. No command execution occurs here.

Which of a run's reported checks a declared chain rosters, and which of
those failed, is the same kind of arithmetic over the same declaration, so
it is stated here once and read by the arm and by the record it builds.
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


class CountedChecks(CamelCaseModel):
    """Which reported checks a forge arm leaves out, and which of them failed.

    ``rostered`` empty is a STATE of a repository and not a missing value: a
    repository that names no forge check delegates the roster to the forge,
    so everything reported counts and nothing is excluded.  Any other
    reading would make such a repository's claims hold vacuously.  The rule
    is stated here once and read by the arm and by the record it builds.
    """

    model_config = ConfigDict(frozen=True)

    excluded: frozenset[str]
    failures: frozenset[str]


def rostered_forge_checks(steps: Sequence[CheckStep]) -> frozenset[str]:
    """The forge check names a repository's declared chain rosters."""
    return frozenset(step.forge_check for step in steps if step.forge_check)


def counted_checks(
    *, reported: frozenset[str], failed: frozenset[str], rostered: frozenset[str]
) -> CountedChecks:
    """What the arm leaves out of *reported*, and what of the rest failed.

    One function, because "which checks decide this claim" and "did they
    pass" are two readings of one set, and an arm answering the second from
    the whole reported roster is refuted by a check nobody rostered.
    ``failed`` must be a subset of ``reported`` — which the observation
    model already guarantees — and is refused here when it is not.
    """
    if not failed <= reported:
        raise ValueError("a failed check must belong to the reported roster")
    counted = reported if not rostered else reported & rostered
    return CountedChecks(excluded=reported - counted, failures=failed & counted)


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

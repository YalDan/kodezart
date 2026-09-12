"""One declaration and same-commit re-observation policy for check reds."""

from kodezart.core.protocols import CIMonitor
from kodezart.domain.errors import CheckObservationError
from kodezart.types.domain.check_observation import (
    AbsentChecks,
    IncompleteChecks,
    ObservedChecks,
)
from kodezart.types.domain.delivery import CheckRedClass, CheckRedObservation
from kodezart.types.domain.operation import RepoEntry


async def classify_red_checks(
    *,
    ci: CIMonitor,
    repo_url: str,
    repository: RepoEntry | None,
    initial: ObservedChecks,
    max_attempts: int,
) -> CheckRedObservation:
    """Classify an already-observed red, preserving the immutable commit.

    An explicitly unmet prerequisite wins before a rerun is consumed.
    Otherwise every observation contributes: a later return to the original
    failing set cannot erase an earlier differing red set.
    """
    if initial.checks_passed or max_attempts < 0:
        raise ValueError("classification requires red checks and a nonnegative bound")
    original = initial.failed_check_names
    unmet = repository is not None and any(
        repository.runner_environment.get(prerequisite) is False
        for step in repository.checks
        if step.forge_check in original
        for prerequisite in step.requires
    )
    if unmet:
        return CheckRedObservation(
            red_class=CheckRedClass.ENVIRONMENT_PREREQUISITE_UNMET,
            observation=initial,
        )
    reproduced = True
    observed = initial
    for _ in range(max_attempts):
        await ci.rerun_checks(repo_url=repo_url, ref=initial.commit_sha)
        result = await ci.wait_for_checks(repo_url=repo_url, ref=initial.commit_sha)
        if isinstance(result, IncompleteChecks):
            raise CheckObservationError(
                repo_url=repo_url, ref=initial.commit_sha, reason=result.summary
            )
        if (
            isinstance(result, ObservedChecks)
            and result.commit_sha != initial.commit_sha
        ):
            raise CheckObservationError(
                repo_url=repo_url,
                ref=initial.commit_sha,
                reason="re-observed checks changed the immutable commit",
            )
        if isinstance(result, AbsentChecks) or result.checks_passed:
            return CheckRedObservation(
                red_class=CheckRedClass.RUNNER_FLAKE,
                observation=result,
            )
        observed = result
        reproduced = reproduced and observed.failed_check_names == original
    return CheckRedObservation(
        red_class=CheckRedClass.WORK_DEFECT
        if reproduced
        else CheckRedClass.UNCLASSIFIED,
        observation=observed,
    )

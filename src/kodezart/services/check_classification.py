"""One declaration and same-commit re-observation policy for check reds."""

from kodezart.core.protocols import CIMonitor
from kodezart.types.domain.delivery import CheckRedClass, CheckRedObservation
from kodezart.types.domain.operation import RepoEntry


async def classify_red_checks(
    *,
    ci: CIMonitor,
    repo_url: str,
    repository: RepoEntry | None,
    final_commit_sha: str,
    initial_summary: str,
    initial_failed_names: frozenset[str],
    max_attempts: int,
) -> CheckRedObservation:
    """Classify an already-observed red, preserving the immutable commit.

    An explicitly unmet prerequisite wins before a rerun is consumed.
    Otherwise every observation contributes: a later return to the original
    failing set cannot erase an earlier differing red set.
    """
    original = initial_failed_names
    if not original:
        raise ValueError("a red observation must identify a failing check")
    unmet = repository is not None and any(
        repository.runner_environment.get(prerequisite) is False
        for step in repository.checks
        if step.forge_check in original
        for prerequisite in step.requires
    )
    if unmet:
        return CheckRedObservation(
            red_class=CheckRedClass.ENVIRONMENT_PREREQUISITE_UNMET,
            checks_passed=False,
            checks_summary=initial_summary,
        )
    reproduced = True
    summary = initial_summary
    for _ in range(max_attempts):
        await ci.rerun_checks(repo_url=repo_url, ref=final_commit_sha)
        passed, summary = await ci.wait_for_checks(
            repo_url=repo_url, ref=final_commit_sha
        )
        if passed is not False:
            return CheckRedObservation(
                red_class=CheckRedClass.RUNNER_FLAKE,
                checks_passed=passed,
                checks_summary=summary,
            )
        observed = await ci.failed_check_names(repo_url=repo_url, ref=final_commit_sha)
        if not observed:
            raise ValueError("a red re-observation must identify a failing check")
        reproduced = reproduced and observed == original
    return CheckRedObservation(
        red_class=CheckRedClass.WORK_DEFECT
        if reproduced
        else CheckRedClass.UNCLASSIFIED,
        checks_passed=False,
        checks_summary=summary,
    )

"""Delivery check classification from declarations and same-commit evidence.

PR delivery and residual publication will consume this one classifier. No
classification reads summary or log text; summaries are carried as evidence.
"""

from kodezart.core.config import AppConfig
from kodezart.core.protocols import CIMonitor
from kodezart.types.domain.delivery import CheckRedClass, CheckRedObservation
from kodezart.types.domain.operation import RepoEntry


async def classify_red_checks(
    *,
    ci: CIMonitor,
    repository: RepoEntry,
    final_commit_sha: str,
    initial_summary: str,
    config: AppConfig,
) -> CheckRedObservation:
    """Classify an already-observed red, preserving the immutable commit.

    An explicitly unmet prerequisite wins before a rerun is consumed.
    Otherwise every observation contributes: a later return to the original
    failing set cannot erase an earlier differing red set.
    """
    original = await ci.failed_check_names(
        repo_url=repository.url, ref=final_commit_sha
    )
    if not original:
        raise ValueError("a red observation must identify a failing check")
    unmet = any(
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
    for _ in range(config.delivery_red_rerun_max_attempts):
        await ci.rerun_checks(repo_url=repository.url, ref=final_commit_sha)
        passed, summary = await ci.wait_for_checks(
            repo_url=repository.url, ref=final_commit_sha
        )
        if passed is not False:
            return CheckRedObservation(
                red_class=CheckRedClass.RUNNER_FLAKE,
                checks_passed=passed,
                checks_summary=summary,
            )
        observed = await ci.failed_check_names(
            repo_url=repository.url, ref=final_commit_sha
        )
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

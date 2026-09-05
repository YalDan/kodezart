"""Construction of the forge client this deployment talks to.

Moved verbatim from the composition root, which imports and wires rather
than defines.
"""

from kodezart.adapters.github_api import GitHubAPIClient
from kodezart.core.config import AppConfig


def build_forge_client(*, config: AppConfig) -> GitHubAPIClient | None:
    """The forge API client, or ``None`` when no credential is configured.

    One client serves four protocols downstream — pull-request creation,
    CI monitoring, repository visibility, and the forge-origin arm of
    delivery probing — so it is built once here and handed to each of
    them rather than dialled four times.
    """
    return (
        GitHubAPIClient(
            token=config.github_token,
            base_url=config.forge_api_base_url,
            ci_poll_interval_seconds=config.ci_poll_interval_seconds,
            ci_poll_max_attempts=config.ci_poll_max_attempts,
            ci_no_checks_grace_polls=config.ci_no_checks_grace_polls,
            ci_no_workflows_grace_polls=config.ci_no_workflows_grace_polls,
            ci_grace_poll_interval_seconds=config.ci_grace_poll_interval_seconds,
            ci_ref_not_found_grace_polls=config.ci_ref_not_found_grace_polls,
            ci_check_runs_max_pages=config.ci_check_runs_max_pages,
            timeout_seconds=config.forge_api_timeout_seconds,
            max_retries=config.forge_api_max_retries,
            retry_backoff_factor=config.forge_api_retry_backoff_factor,
        )
        if config.github_token is not None
        else None
    )

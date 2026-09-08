"""Construction of the forge client this deployment talks to.

Moved verbatim from the composition root, which imports and wires rather
than defines.
"""

from kodezart.adapters.github_api import GitHubAPIClient
from kodezart.core.config import AppConfig
from kodezart.core.protocols import CIObservationReader, ForgeQuery, PRContentEditor
from kodezart.domain.git_url import is_forge_less_origin


def build_forge_client(*, config: AppConfig) -> GitHubAPIClient | None:
    """The forge API client, or ``None`` when no credential is configured.

    One client serves forge writes, queries, CI monitoring, repository
    visibility and delivery probing. It is built once here and supplied
    to the origin-specific capability selections.
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


def forge_query_for_origin(
    *,
    client: ForgeQuery | None,
    repo_url: str,
) -> ForgeQuery | None:
    """Select the read capability before a caller asks a forge-less origin.

    Query consumers receive no capability for a local repository, using the
    same origin predicate as workflow and delivery selection. A configured
    credential alone never establishes that an origin has a forge.
    """
    return None if is_forge_less_origin(repo_url) else client


def pr_content_editor_for_origin(
    *, client: PRContentEditor | None, repo_url: str
) -> PRContentEditor | None:
    """Select PR content access only for an origin with a forge capability."""
    return None if is_forge_less_origin(repo_url) else client


def ci_observation_reader_for_origin(
    *, client: CIObservationReader | None, repo_url: str
) -> CIObservationReader | None:
    """Select access to a forge watch's recorded commit evidence per origin."""
    return None if is_forge_less_origin(repo_url) else client

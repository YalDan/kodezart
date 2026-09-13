"""Original addressed PR fixture shared by delivery and later audit controls."""

from kodezart.types.domain.pr_state import PRLifecycle, PRState

REPO = "https://github.com/example/project"


HEAD = "a" * 40


BRANCH = "ordinary-name"


def pr_state(**changes):
    return PRState(
        url=f"{REPO}/pull/7",
        number=7,
        head_repo_url=REPO,
        base_repo_url=REPO,
        base_branch="main",
        head_branch=BRANCH,
        head_sha=HEAD,
        lifecycle=PRLifecycle.OPEN,
        **changes,
    )

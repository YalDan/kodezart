"""GitHub token authentication adapter implementing the GitAuth port."""


class GitHubTokenAuth:
    """Injects GitHub personal access token via HTTPS URL."""

    def __init__(self, token: str) -> None:
        self._token = token

    def authenticated_url(self, clone_url: str) -> str:
        """Inject the GitHub token into an HTTPS clone URL as x-access-token."""
        return clone_url.replace("https://", f"https://x-access-token:{self._token}@")

    def subprocess_env(self) -> dict[str, str]:
        """Return additional environment variables for git subprocesses.

        Currently empty -- auth is handled via URL rewriting.
        """
        return {}

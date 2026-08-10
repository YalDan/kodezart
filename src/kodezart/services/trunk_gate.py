"""The deterministic pre-query the verification pass is gated on.

Two port calls — the local mirror, then ``git ls-remote`` against it — and
neither is a prompt, a session or a model.  Written against ``GitService``
and ``RepoCache`` alone, and a test asserts that collaborator surface
rather than trusting this paragraph.

Why the trunk tip rather than a board query (KOD-60 R11): the verification
pass's whole product is a classification of a repository's declared check
chain at a HEAD sha, and that classification is a pure function of the
chain and the code.  A chain goes red because trunk moved, never because a
label moved, so no query over the board predicts it.  Re-verifying an
already-verified tip does not merely waste a checkout and a session — it
recomputes a value the pass already holds and posts a byte-identical
comment on every blocked issue, once per interval, forever.

The verified tip is recorded by the pass that COMPLETED, never by the gate
on observation.  A tick whose session did not answer leaves the tip
unrecorded, so the next tick verifies it again instead of reporting a
build nobody performed.
"""

from kodezart.core.logging import BoundLogger, get_logger
from kodezart.core.protocols import GitService, RepoCache


class TrunkGate:
    """Answers "has the code moved since the last verification?", deterministically."""

    def __init__(
        self,
        *,
        git: GitService,
        cache: RepoCache,
        repo_url: str,
        trunk: str,
        remote: str,
    ) -> None:
        self._git: GitService = git
        self._cache: RepoCache = cache
        self._repo_url: str = repo_url
        self._trunk: str = trunk
        self._remote: str = remote
        self._verified: str | None = None
        self._log: BoundLogger = get_logger(__name__)

    @property
    def verified(self) -> str | None:
        """The tip a completed verification last reported on."""
        return self._verified

    def record(self, sha: str) -> None:
        """Mark a tip verified, so the next tick over it costs nothing."""
        self._verified = sha

    async def unverified_tip(self) -> str | None:
        """The trunk tip when it is not the verified one; ``None`` otherwise.

        Three outcomes, none of them silent and none of them conflated: a
        tip to verify, a tip already verified, and a declared trunk the
        remote does not carry — which is a configuration fault rather than
        a quiet repository, and is reported as one.
        """
        repo_path = await self._cache.ensure_available(self._repo_url)
        tip = await self._git.remote_branch_sha(repo_path, self._remote, self._trunk)
        if tip is None:
            await self._log.awarning(
                "trunk_gate_branch_absent",
                repo_url=self._repo_url,
                trunk=self._trunk,
            )
            return None
        if tip == self._verified:
            await self._log.ainfo(
                "trunk_gate_no_new_commit",
                repo_url=self._repo_url,
                trunk=self._trunk,
                head_sha=tip,
            )
            return None
        await self._log.ainfo(
            "trunk_gate_new_commit",
            repo_url=self._repo_url,
            trunk=self._trunk,
            head_sha=tip,
            verified_sha=self._verified,
        )
        return tip

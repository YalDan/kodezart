"""Git artifact persister — persist and clean .kodezart/ workflow metadata."""

import shutil
from collections.abc import Mapping
from pathlib import Path

from kodezart.core.logging import BoundLogger, get_logger
from kodezart.core.protocols import GitService, WorkspaceProvider
from kodezart.types.domain.persist import ArtifactPersistStatus

ARTIFACT_DIR = ".kodezart"


class GitArtifactPersister:
    """Write named files under .kodezart/, commit, and push.

    Implements the ``ArtifactPersister`` protocol.  Generic file writer —
    the caller decides what to serialize and how to name each file.
    """

    def __init__(
        self,
        git: GitService,
        workspace: WorkspaceProvider,
        committer_name: str,
        committer_email: str,
    ) -> None:
        self._git: GitService = git
        self._workspace: WorkspaceProvider = workspace
        self._committer_name: str = committer_name
        self._committer_email: str = committer_email
        self._log: BoundLogger = get_logger(__name__)

    async def persist(
        self,
        *,
        repo_path: str | None,
        repo_url: str | None,
        branch: str,
        base_branch: str,
        artifacts: Mapping[str, str],
        cache_key: str | None = None,
    ) -> ArtifactPersistStatus:
        """Write artifacts to .kodezart/, commit, push.

        Nothing staged has two causes and they are reported apart: the
        target's ignore rules match the artifact directory
        (``IGNORED_BY_TARGET`` — no run will ever persist artifacts to
        this repository), or the artifacts already match the commit on
        the branch (``UNCHANGED``).
        """
        workspace_path = await self._workspace.acquire(
            repo_path=repo_path,
            repo_url=repo_url,
            ref=base_branch,
            branch_name=branch,
            create_branch=True,
            cache_key=cache_key,
        )
        try:
            artifact_dir = Path(workspace_path) / ARTIFACT_DIR
            artifact_dir.mkdir(exist_ok=True)
            for name, content in artifacts.items():
                (artifact_dir / name).write_text(content)
            await self._git.add_all(workspace_path)
            if not await self._git.has_changes(workspace_path):
                return await self._skip_status(workspace_path, branch)
            await self._git.commit(
                cwd=workspace_path,
                message="kodezart: persist workflow artifacts",
                author_name=self._committer_name,
                author_email=self._committer_email,
            )
            await self._git.push(workspace_path, branch)
            await self._log.ainfo(
                "artifacts_persisted",
                branch=branch,
            )
            return ArtifactPersistStatus.PERSISTED
        finally:
            await self._workspace.release(workspace_path)

    async def _skip_status(
        self,
        workspace_path: str,
        branch: str,
    ) -> ArtifactPersistStatus:
        """Classify an empty stage: ignored by the target, or unchanged."""
        if await self._git.is_path_ignored(workspace_path, ARTIFACT_DIR):
            await self._log.awarning(
                "artifacts_persist_skipped",
                branch=branch,
                reason=ArtifactPersistStatus.IGNORED_BY_TARGET,
            )
            return ArtifactPersistStatus.IGNORED_BY_TARGET
        await self._log.ainfo(
            "artifacts_persist_skipped",
            branch=branch,
            reason=ArtifactPersistStatus.UNCHANGED,
        )
        return ArtifactPersistStatus.UNCHANGED

    async def clean(
        self,
        *,
        repo_path: str | None,
        repo_url: str | None,
        branch: str,
        cache_key: str | None = None,
    ) -> None:
        """Remove .kodezart/ directory, commit, push. Must not raise."""
        try:
            workspace_path = await self._workspace.acquire(
                repo_path=repo_path,
                repo_url=repo_url,
                ref=branch,
                branch_name=branch,
                create_branch=False,
                cache_key=cache_key,
            )
            try:
                artifact_dir = Path(workspace_path) / ARTIFACT_DIR
                if not artifact_dir.exists():
                    return
                shutil.rmtree(artifact_dir)
                await self._git.add_all(workspace_path)
                if await self._git.has_changes(workspace_path):
                    await self._git.commit(
                        cwd=workspace_path,
                        message="kodezart: remove workflow artifacts",
                        author_name=self._committer_name,
                        author_email=self._committer_email,
                    )
                    await self._git.push(workspace_path, branch)
                    await self._log.ainfo(
                        "artifacts_cleaned",
                        branch=branch,
                    )
            finally:
                await self._workspace.release(workspace_path)
        except Exception as exc:
            # The ``clean`` docstring promises "Must not raise" — this
            # is housekeeping that runs immediately before opening a PR
            # and must never abort the PR-open path.  But the failure is
            # operator-relevant (the merged PR diff will still contain
            # the .kodezart/ artifacts), so log at ``aerror`` (NOT
            # ``awarning``) to surface it on the standard error stream
            # alongside other operational failures.
            await self._log.aerror(
                "artifact_cleanup_failed",
                branch=branch,
                error=str(exc),
            )

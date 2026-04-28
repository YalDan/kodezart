"""Git artifact persister — persist and clean .kodezart/ workflow metadata."""

import shutil
from collections.abc import Mapping
from pathlib import Path

from kodezart.core.logging import BoundLogger, get_logger
from kodezart.core.protocols import GitService, WorkspaceProvider

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
    ) -> None:
        """Write artifacts to .kodezart/, commit, push."""
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
        finally:
            await self._workspace.release(workspace_path)

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
            await self._log.awarning(
                "artifact_cleanup_failed",
                branch=branch,
                error=str(exc),
            )

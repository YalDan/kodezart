"""Read exact Git source objects without worktree changes or content filters."""

import asyncio
import os
import re
import signal
from pathlib import PurePosixPath

from kodezart.core.owned_tasks import finish_owned
from kodezart.domain.errors import GitSourceReadError
from kodezart.types.domain.assertion_drift import GitSourceBlob

_OBJECT_ID = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})")


class SubprocessGitSourceReader:
    """A local object reader; fetching and source protection belong to callers."""

    async def resolve_commit(self, *, cwd: str, ref: str) -> str:
        if not ref or "\x00" in ref:
            raise GitSourceReadError(
                ref=ref, path=None, reason="invalid commit reference"
            )
        output = await self._command(
            cwd=cwd,
            ref=ref,
            path=None,
            args=("rev-parse", "--verify", "--end-of-options", f"{ref}^{{commit}}"),
        )
        try:
            sha = output.decode("ascii").strip()
        except UnicodeError as exc:
            raise GitSourceReadError(
                ref=ref, path=None, reason="invalid object identity"
            ) from exc
        if _OBJECT_ID.fullmatch(sha) is None:
            raise GitSourceReadError(
                ref=ref, path=None, reason="incomplete object identity"
            )
        return sha

    async def read_source(
        self, *, cwd: str, commit_sha: str, path: str
    ) -> GitSourceBlob:
        canonical = PurePosixPath(path)
        if (
            _OBJECT_ID.fullmatch(commit_sha) is None
            or not path
            or canonical.is_absolute()
            or str(canonical) != path
            or ".." in canonical.parts
            or "\x00" in path
        ):
            raise GitSourceReadError(
                ref=commit_sha, path=path, reason="invalid source address"
            )
        if await self.resolve_commit(cwd=cwd, ref=commit_sha) != commit_sha:
            raise GitSourceReadError(
                ref=commit_sha, path=path, reason="the source address is not a commit"
            )
        listing = await self._command(
            cwd=cwd,
            ref=commit_sha,
            path=path,
            args=("ls-tree", "-z", "--full-tree", commit_sha, "--", path),
        )
        records = listing.split(b"\x00")
        if len(records) != 2 or records[-1] != b"":
            raise GitSourceReadError(
                ref=commit_sha, path=path, reason="source path is missing or ambiguous"
            )
        try:
            metadata, returned_path = records[0].split(b"\t", 1)
            mode, kind, identity = metadata.split(b" ")
            blob_sha = identity.decode("ascii")
        except (ValueError, UnicodeError) as exc:
            raise GitSourceReadError(
                ref=commit_sha, path=path, reason="invalid tree entry"
            ) from exc
        if (
            returned_path != path.encode("utf-8")
            or mode not in {b"100644", b"100755"}
            or kind != b"blob"
            or _OBJECT_ID.fullmatch(blob_sha) is None
        ):
            raise GitSourceReadError(
                ref=commit_sha, path=path, reason="source is not the exact regular file"
            )
        content = await self._command(
            cwd=cwd,
            ref=commit_sha,
            path=path,
            args=("cat-file", "blob", blob_sha),
        )
        return GitSourceBlob(
            commit_sha=commit_sha, path=path, blob_sha=blob_sha, content=content
        )

    async def _command(
        self, *, cwd: str, ref: str, path: str | None, args: tuple[str, ...]
    ) -> bytes:
        try:
            return await self._owned_command(cwd=cwd, ref=ref, path=path, args=args)
        except OSError as exc:
            raise GitSourceReadError(ref=ref, path=path, reason=str(exc)) from exc

    async def _owned_command(
        self, *, cwd: str, ref: str, path: str | None, args: tuple[str, ...]
    ) -> bytes:
        spawning = asyncio.create_task(
            asyncio.create_subprocess_exec(
                "git",
                "--no-replace-objects",
                "--literal-pathspecs",
                *args,
                cwd=cwd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                start_new_session=True,
            )
        )
        communication: asyncio.Task[tuple[bytes, bytes]] | None = None
        try:
            process = await asyncio.shield(spawning)
            communication = asyncio.create_task(process.communicate())
            output, error = await asyncio.shield(communication)
        except BaseException:
            process, _ = await finish_owned(spawning)
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            if communication is None:
                communication = asyncio.create_task(process.communicate())
            await finish_owned(communication)
            raise
        if process.returncode != 0:
            raise GitSourceReadError(
                ref=ref,
                path=path,
                reason=error.decode("utf-8", errors="replace").strip()
                or "Git object read failed",
            )
        return output

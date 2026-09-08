"""Actual Git assertion comparisons against source-owned protected references."""

import re

from kodezart.core.protocols import GitSourceReader
from kodezart.domain.assertion_drift import protected_assertions
from kodezart.domain.errors import AssertionComparisonError
from kodezart.types.domain.assertion_drift import (
    AssertionDeviationClaim,
    GitSourceBlob,
    ProtectedTestRef,
)


class AssertionDriftDetector:
    """Return deviation claims without accepting code, changing state or running it."""

    def __init__(self, *, git: GitSourceReader) -> None:
        self._git = git

    async def compare(
        self,
        *,
        repo_path: str,
        graded_sha: str,
        head_ref: str,
        protected_tests: tuple[ProtectedTestRef, ...],
    ) -> tuple[AssertionDeviationClaim, ...]:
        """Pin both commits before any file read, then compare the declared tests.

        Full green executions are a fixture condition, not an input that can
        suppress a claim. Explicit protection provenance is supplied by its
        owner; this service does not discover or persist it.
        """
        if re.fullmatch(r"(?:[0-9a-f]{40}|[0-9a-f]{64})", graded_sha) is None:
            raise AssertionComparisonError(
                source_ref=graded_sha,
                reason="the graded reference must be a complete commit SHA",
            )
        addresses = [
            (value.source_ref, value.path, value.qualified_name)
            for value in protected_tests
        ]
        if len(set(addresses)) != len(addresses):
            raise AssertionComparisonError(
                source_ref=graded_sha, reason="duplicate protected-test references"
            )
        resolved_graded = await self._git.resolve_commit(cwd=repo_path, ref=graded_sha)
        resolved_head = await self._git.resolve_commit(cwd=repo_path, ref=head_ref)
        if resolved_graded != graded_sha:
            raise AssertionComparisonError(
                source_ref=graded_sha, reason="the graded commit identity changed"
            )
        claims: list[AssertionDeviationClaim] = []
        blobs: dict[tuple[str, str], GitSourceBlob] = {}
        for protected in protected_tests:
            for sha in (resolved_graded, resolved_head):
                key = (sha, protected.path)
                if key not in blobs:
                    blob = await self._git.read_source(
                        cwd=repo_path, commit_sha=sha, path=protected.path
                    )
                    if blob.commit_sha != sha or blob.path != protected.path:
                        raise AssertionComparisonError(
                            source_ref=protected.source_ref,
                            reason="returned source differs from its requested address",
                        )
                    blobs[key] = blob
            before_blob = blobs[(resolved_graded, protected.path)]
            after_blob = blobs[(resolved_head, protected.path)]
            try:
                before = protected_assertions(
                    source=before_blob.content,
                    path=protected.path,
                    qualified_name=protected.qualified_name,
                )
                after = protected_assertions(
                    source=after_blob.content,
                    path=protected.path,
                    qualified_name=protected.qualified_name,
                )
            except (SyntaxError, ValueError) as exc:
                raise AssertionComparisonError(
                    source_ref=protected.source_ref, reason=str(exc)
                ) from exc
            if not before:
                raise AssertionComparisonError(
                    source_ref=protected.source_ref,
                    reason="the graded test has no Python assertions to protect",
                )
            if tuple(row.structural_form for row in before) != tuple(
                row.structural_form for row in after
            ):
                claims.append(
                    AssertionDeviationClaim(
                        protected_test=protected,
                        graded_sha=resolved_graded,
                        head_sha=resolved_head,
                        graded_blob_sha=before_blob.blob_sha,
                        head_blob_sha=after_blob.blob_sha,
                        before=before,
                        after=after,
                    )
                )
        return tuple(claims)

"""Adoption observations bind native bytes and refuse invented witnesses."""

import asyncio

import pytest

from kodezart.domain.errors import AssertionComparisonError, AuditClaimReadError
from kodezart.types.domain.assertion_drift import GitSourceBlob
from kodezart.types.domain.audit import AuditVerdict
from kodezart.types.domain.audit_overclaim import OverclaimKind
from tests.tracker import test_audit_overclaim as fixtures

setup = fixtures.setup
claim_setup = fixtures.claim_setup
server = fixtures.server


def adopted_output(**changes):
    output = fixtures.payload(OverclaimKind.ADOPTION, **changes)
    output["bytePairs"] = [
        {
            "sourceSha": fixtures.fixtures.PRIOR,
            "sourcePath": "source.md",
            "artifactPath": "copy.md",
        }
    ]
    return output


@pytest.mark.parametrize(
    "damage",
    [
        "foreign-sha",
        "self-witness",
        "wrong-sha",
        "wrong-path",
        "contradictory-refutation",
    ],
)
async def test_adoption_refuses_unbound_or_contradictory_witnesses(
    setup, tracker, damage
):
    output = adopted_output()
    pair = output["bytePairs"][0]
    if damage == "foreign-sha":
        pair["sourceSha"] = "c" * 40
    elif damage == "self-witness":
        pair.update(sourceSha=fixtures.fixtures.HEAD, sourcePath="copy.md")
    elif damage == "contradictory-refutation":
        output = adopted_output(verdict="refuted", evidence="The named files differ.")
    calls = []

    class NativeBoundary:
        async def read_source(self, *, cwd, commit_sha, path):
            calls.append((commit_sha, path))
            return GitSourceBlob(
                commit_sha="c" * 40 if damage == "wrong-sha" else commit_sha,
                path="other.md" if damage == "wrong-path" else path,
                blob_sha="d" * 40,
                content=b"identical bytes",
            )

    fixtures.answer(setup[1], output)
    with pytest.raises(AuditClaimReadError):
        await fixtures.build(setup, tracker, source=NativeBoundary()).observe(
            fixtures.fixtures.REQUEST
        )
    if damage in {"foreign-sha", "self-witness"}:
        assert calls == []


@pytest.mark.parametrize("missing_path", ["source.md", "copy.md"])
async def test_unreadable_native_witness_cannot_accept_optimistic_adoption(
    setup, tracker, missing_path
):
    class NativeBoundary:
        async def read_source(self, *, cwd, commit_sha, path):
            if path == missing_path:
                raise AssertionComparisonError(
                    source_ref=f"{commit_sha}:{path}",
                    reason="the native object is unreadable",
                )
            return GitSourceBlob(
                commit_sha=commit_sha, path=path, blob_sha="d" * 40, content=b"bytes"
            )

    fixtures.answer(setup[1], adopted_output())
    result = await fixtures.build(setup, tracker, source=NativeBoundary()).observe(
        fixtures.fixtures.REQUEST
    )
    assert result.judgment.verdict is AuditVerdict.UNVERIFIABLE
    adoption = next(
        row for row in result.judgment.checks if row.kind is OverclaimKind.ADOPTION
    )
    assert missing_path in adoption.missing_artifact


async def test_equal_named_bytes_do_not_manufacture_missing_external_coverage(
    setup, tracker
):
    class NativeBoundary:
        async def read_source(self, *, cwd, commit_sha, path):
            return GitSourceBlob(
                commit_sha=commit_sha, path=path, blob_sha="d" * 40, content=b"equal"
            )

    fixtures.answer(
        setup[1],
        adopted_output(
            verdict="unverifiable", missingArtifact="external source archive"
        ),
    )
    result = await fixtures.build(setup, tracker, source=NativeBoundary()).observe(
        fixtures.fixtures.REQUEST
    )
    assert result.judgment.verdict is AuditVerdict.UNVERIFIABLE
    assert result.judgment.checks[2].missing_artifact == "external source archive"


async def test_cancelled_native_byte_read_never_returns_a_judgment(setup, tracker):
    class NativeBoundary:
        async def read_source(self, **kwargs):
            raise asyncio.CancelledError

    fixtures.answer(setup[1], adopted_output())
    with pytest.raises(asyncio.CancelledError):
        await fixtures.build(setup, tracker, source=NativeBoundary()).observe(
            fixtures.fixtures.REQUEST
        )

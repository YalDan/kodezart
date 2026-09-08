"""Incomplete or unsupported over-claim readings cannot become a clean audit."""

import pytest
from pydantic import ValidationError

from kodezart.types.domain.audit import AuditVerdict
from kodezart.types.domain.audit_overclaim import (
    AuditBytePair,
    AuditOverclaimJudgment,
    OverclaimKind,
    OverclaimReading,
)


def reading(kind, verdict=AuditVerdict.HOLDS, **changes):
    return OverclaimReading(
        **{
            "kind": kind,
            "verdict": verdict,
            "evidence": "Current source was inspected.",
            "recomputed_value": None,
            "missing_artifact": None,
            **changes,
        }
    )


def judgment(*, changed=None, **changes):
    return AuditOverclaimJudgment(
        **{
            "criterion_key": "external/check",
            "checks": tuple(
                changed
                if changed is not None and changed.kind is kind
                else reading(kind)
                for kind in OverclaimKind
            ),
            "byte_pairs": (),
            **changes,
        }
    )


@pytest.mark.parametrize("kind", list(OverclaimKind))
async def test_each_standing_check_is_required_exactly_once(kind):
    complete = judgment()
    with pytest.raises(ValidationError, match="once"):
        judgment(
            checks=tuple(item for item in complete.checks if item.kind is not kind)
        )
    with pytest.raises(ValidationError, match="once"):
        judgment(checks=(*complete.checks, reading(kind)))


@pytest.mark.parametrize("value", [None, "", "  "])
def test_aggregate_refutation_requires_the_recomputed_value(value):
    with pytest.raises(ValidationError, match="recomputation"):
        reading(OverclaimKind.AGGREGATE, AuditVerdict.REFUTED, recomputed_value=value)


@pytest.mark.parametrize("kind", list(OverclaimKind))
def test_unverifiable_never_loses_its_missing_witness(kind):
    with pytest.raises(ValidationError, match="missing artifact"):
        reading(kind, AuditVerdict.UNVERIFIABLE)
    observation = judgment(
        changed=reading(
            kind, AuditVerdict.UNVERIFIABLE, missing_artifact="enumerable source roster"
        )
    )
    assert observation.verdict is AuditVerdict.UNVERIFIABLE


def test_refutation_dominates_unverifiability_without_coercing_either_to_bool():
    rows = list(judgment().checks)
    rows[0] = reading(
        OverclaimKind.AGGREGATE, AuditVerdict.REFUTED, recomputed_value="3"
    )
    rows[1] = reading(
        OverclaimKind.COMPLETENESS, AuditVerdict.UNVERIFIABLE, missing_artifact="roster"
    )
    result = judgment(checks=tuple(rows))
    assert result.verdict is AuditVerdict.REFUTED
    assert result.checks[1].verdict is AuditVerdict.UNVERIFIABLE
    with pytest.raises(TypeError):
        bool(result.verdict)


@pytest.mark.parametrize(
    "path", ["", ".", "../secret", "/absolute", "a/../secret", "a//b", "a\x00b"]
)
def test_adoption_paths_do_not_escape_the_requested_repository(path):
    with pytest.raises(ValidationError):
        AuditBytePair(source_sha="a" * 40, source_path=path, artifact_path="result.md")


def test_duplicate_pair_is_not_additional_coverage():
    pair = AuditBytePair(
        source_sha="a" * 40, source_path="source.md", artifact_path="result.md"
    )
    with pytest.raises(ValidationError, match="duplicate"):
        judgment(byte_pairs=(pair, pair))

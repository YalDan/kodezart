"""Native terminal identities remain distinct from criterion judgments."""

import json

import pytest
from pydantic import ValidationError

from kodezart.types.domain.audit import AuditMandateContext, AuditMandateObservation
from kodezart.types.domain.audit_terminal import (
    AuditTerminalObservation,
    AuditTerminalReport,
)
from kodezart.types.domain.scope import ScopeKind, ScopeRef
from kodezart.types.domain.surface import SurfaceKind, WritableSurface

SURFACE = WritableSurface(
    kind=SurfaceKind.ISSUE_DESCRIPTION,
    ref=ScopeRef(kind=ScopeKind.ISSUE, key="native/issue"),
)


def observation(verdict="refuted"):
    return AuditTerminalObservation(
        issue_key="native/issue",
        record_ref="native/comment",
        verdict=verdict,
        discrepancies=("closed_unmerged_pr",) if verdict == "refuted" else (),
        branch_head="observed-head",
        pr=None,
    )


def mandate(observed, verdict="refuted"):
    return AuditMandateObservation(
        verdict=verdict,
        covered=()
        if verdict == "unverifiable"
        else (
            {
                "surface": SURFACE,
                "native_ref": "native/issue",
                "content": "Close the PR.",
            },
        ),
        unreadable=({"surface": SURFACE, "reason": "Native read failed"},)
        if verdict == "unverifiable"
        else (),
        finding={
            "issue_id": "native/issue",
            "defect_class": observed.defect_class(),
            "role": "mandate",
            "mandate_text": "Close the PR.",
            "evidence": "Exact source.",
        }
        if verdict == "holds"
        else None,
        finding_surface=SURFACE if verdict == "holds" else None,
        evidence="Native coverage evidence.",
    )


@pytest.mark.parametrize("verdict", ["holds", "refuted", "unverifiable"])
def test_terminal_report_retains_exact_observation_with_all_mandate_states(verdict):
    observed = observation()
    result = AuditTerminalReport(
        observation=observed, mandate=mandate(observed, verdict)
    )
    assert result.observation is observed
    assert AuditTerminalReport.model_validate_json(result.model_dump_json()) == result
    assert "criterion" not in result.model_dump_json()
    facts = json.loads(observed.refutation_evidence())
    assert facts == observed.model_dump(mode="json", exclude={"verdict"})
    assert (
        facts["issue_key"] == "native/issue" and facts["record_ref"] == "native/comment"
    )
    with pytest.raises(ValidationError, match="frozen"):
        result.mandate = None


@pytest.mark.parametrize("verdict", ["holds", "unverifiable"])
def test_nonrefuted_terminal_requires_no_mandate(verdict):
    observed = observation(verdict)
    assert AuditTerminalReport(observation=observed, mandate=None).mandate is None
    with pytest.raises(ValidationError, match="mandate"):
        AuditTerminalReport(observation=observed, mandate=mandate(observed))


@pytest.mark.parametrize(
    "damage", ["no-mandate", "no-head", "no-discrepancy", "foreign-defect"]
)
def test_bare_or_unbound_refutation_cannot_be_completed(damage):
    observed = observation()
    data = AuditTerminalReport(
        observation=observed, mandate=mandate(observed, "holds")
    ).model_dump()
    if damage == "no-mandate":
        data["mandate"] = None
    elif damage == "no-head":
        data["observation"]["branch_head"] = None
    elif damage == "no-discrepancy":
        data["observation"]["discrepancies"] = ()
    else:
        data["mandate"]["finding"]["defect_class"] = "another defect"
    with pytest.raises(ValidationError, match="mandate"):
        AuditTerminalReport.model_validate(data)


@pytest.mark.parametrize(
    "damage",
    [
        "duplicate-surface",
        "empty-surface",
        "empty-head",
        "empty-evidence",
        "foreign-field",
    ],
)
def test_shared_invocation_refuses_missing_or_ambiguous_context(damage):
    data = {
        "defect_class": "observed defect",
        "refutation_evidence": "native fact",
        "head_sha": "head",
        "surfaces": [SURFACE],
        "repo_url": "repository",
    }
    assert AuditMandateContext.model_validate(data).surfaces == (SURFACE,)
    if damage == "duplicate-surface":
        data["surfaces"] *= 2
    elif damage == "empty-surface":
        data["surfaces"] = []
    elif damage == "empty-head":
        data["head_sha"] = " "
    elif damage == "empty-evidence":
        data["refutation_evidence"] = " "
    else:
        data["criterion_key"] = "fabricated"
    with pytest.raises(ValidationError):
        AuditMandateContext.model_validate(data)

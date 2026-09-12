"""A halt carries only evidence belonging to its declared stopping cause."""

import pytest
from pydantic import ValidationError

from kodezart.types.domain.organize_owner import StageHaltReport


def bound(loop="admission"):
    return {
        "setting": (
            "organize.max_convergence_rounds"
            if loop == "convergence"
            else "organize.max_admission_rounds"
        ),
        "value": 2,
        "rounds_used": 2,
        "loop": loop,
    }


def admission(kind="human_decision"):
    return {
        "issue_id": "native-child",
        "admitted_body_digest": "actual-body-digest",
        "verdict": "not_buildable",
        "invented_decision": "Which of the two sources is authoritative?",
        "refusal_kind": kind,
        "evidence": "The current sources require mutually exclusive outcomes.",
    }


def question():
    return {
        "kind": "unresolved",
        "issue_id": "native-child",
        "question": "Which source is authoritative?",
        "evidence": "Two current sources conflict.",
    }


def write_back():
    return {
        "verdict": "unverifiable",
        "artifact": {
            "surface": {
                "kind": "issue_description",
                "ref": {"kind": "issue", "key": "native-child"},
            },
            "native_ref": "native-child",
            "content": "An unsupported landed claim.",
        },
        "rounds": [
            {
                "verdict": "refuted",
                "evidence": "tests/absent.py does not exist at the checked commit.",
                "cited_refs": ["tests/absent.py"],
            }
        ]
        * 2,
    }


@pytest.mark.parametrize(
    "payload",
    [
        {"cause": "human_decision"},
        {"cause": "human_decision", "admission_results": [admission("spec_gap")]},
        {
            "cause": "human_decision",
            "admission_results": [
                {
                    "issue_id": "native-child",
                    "admitted_body_digest": "actual-body-digest",
                    "verdict": "buildable",
                    "evidence": "The source is buildable.",
                }
            ],
        },
        {
            "cause": "human_decision",
            "surviving_findings": [
                {
                    "issue_id": "native-child",
                    "defect_class": "missing_source",
                    "evidence": "The source is absent.",
                    "role": "instance",
                }
            ],
        },
        {"cause": "human_decision", "write_back_results": [write_back()]},
        {"cause": "human_decision", "questions": [question()], "bound": bound()},
        {"cause": "admission_exhausted"},
        {
            "cause": "admission_exhausted",
            "bound": bound(),
            "questions": [question()],
        },
        {
            "cause": "admission_exhausted",
            "bound": bound(),
            "write_back_results": [write_back()],
        },
        {
            "cause": "convergence_exhausted",
            "bound": bound("convergence"),
            "questions": [question()],
        },
        {
            "cause": "convergence_exhausted",
            "bound": bound("convergence"),
            "write_back_results": [write_back()],
        },
        {
            "cause": "admission_exhausted",
            "bound": bound("convergence"),
        },
        {
            "cause": "admission_exhausted",
            "bound": bound("write_back"),
            "write_back_results": [
                {**write_back(), "rounds": write_back()["rounds"][:1]}
            ],
        },
        {
            "cause": "admission_exhausted",
            "bound": bound("write_back"),
        },
        {"cause": "escalation_unrecorded", "unrecorded_escalation_issue_ids": [" "]},
        {
            "cause": "escalation_unrecorded",
            "unrecorded_escalation_issue_ids": ["native-child"],
            "bound": bound(),
        },
    ],
)
def test_wrong_cause_or_missing_evidence_refuses(payload):
    with pytest.raises(ValidationError):
        StageHaltReport.model_validate(payload)


@pytest.mark.parametrize(
    "payload",
    [
        {"cause": "human_decision", "questions": [question()]},
        {"cause": "human_decision", "admission_results": [admission()]},
        {"cause": "admission_exhausted", "bound": bound()},
        {
            "cause": "admission_exhausted",
            "bound": bound("write_back"),
            "write_back_results": [write_back()],
        },
        {"cause": "convergence_exhausted", "bound": bound("convergence")},
        {"cause": "escalation_unrecorded", "unrecorded_escalation_issue_ids": ["x"]},
        {
            "cause": "escalation_unrecorded",
            "unrecorded_escalation_issue_ids": ["native-child"],
            "write_back_results": [write_back()],
        },
    ],
)
def test_valid_halt_roundtrips_the_existing_flat_json(payload):
    report = StageHaltReport.model_validate(payload)
    serialized = report.model_dump(by_alias=True, mode="json")
    assert set(serialized) == {
        "cause",
        "bound",
        "admissionResults",
        "survivingFindings",
        "writeBackResults",
        "questions",
        "unrecordedEscalationIssueIds",
    }
    assert StageHaltReport.model_validate(serialized) == report
    assert StageHaltReport(**payload) == report
    assert StageHaltReport.model_validate_json(report.model_dump_json()) == report
    if payload.get("write_back_results"):
        assert report.write_back_results[0].rounds[0].cited_refs == ("tests/absent.py",)
        assert len(report.write_back_results[0].rounds) == 2

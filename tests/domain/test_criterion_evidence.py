"""Recorded grading is one explicit field, never a SHA guessed from prose."""

import json

import pytest
from pydantic import ValidationError

from kodezart.domain.criterion_evidence import (
    parse_criterion_evidence,
    render_evidence_field,
)
from kodezart.domain.fire_spec import criterion_check
from kodezart.types.domain.criterion_evidence import CriterionEvidence
from tests.fakes import make_tracker_issue

SHA = "e" * 40
TEST = "tests/test_café.py::test_二\nObservation quoted: <!--literal--> ```"


def evidence(**changes):
    return CriterionEvidence(graded_sha=SHA, test=TEST, **changes)


def test_one_structured_field_retains_exact_grading_facts_and_check():
    value = evidence()
    field = render_evidence_field(value)
    body = "**Check:** Current check.\n**Do:** Mechanism.\n" + field
    assert parse_criterion_evidence(body) == value
    assert json.loads(field.split("```json\n")[1].removesuffix("\n```")) == {
        "gradedSha": SHA,
        "test": TEST,
    }
    criterion = make_tracker_issue("native/二", body=body)
    assert criterion_check(criterion=criterion, issue_key="parent") == "Current check."
    assert value.test == TEST
    with pytest.raises(ValidationError, match="frozen_instance"):
        value.test = "substitute"


@pytest.mark.parametrize("sha", ["main", "abcd", "a" * 39, "A" * 40, "g" * 40])
def test_a_recorded_sha_is_a_complete_object_identity_not_a_moving_ref(sha):
    with pytest.raises(ValidationError):
        CriterionEvidence(graded_sha=sha, test="test_path")


def test_the_alternate_complete_git_object_identity_shape_is_preserved():
    value = CriterionEvidence(graded_sha="c" * 64, test="named observation")
    assert parse_criterion_evidence(render_evidence_field(value)) == value


@pytest.mark.parametrize(
    "payload",
    [
        {"gradedSha": SHA},
        {"test": "test_case"},
        {"gradedSha": SHA, "test": " "},
        {"gradedSha": SHA, "test": True},
        {"gradedSha": SHA, "test": "test_case", "verdict": "holds"},
        [{"gradedSha": SHA, "test": "test_case"}],
    ],
)
def test_incomplete_or_extra_evidence_cannot_masquerade_as_the_declared_record(payload):
    with pytest.raises(ValueError):
        parse_criterion_evidence(
            "**Evidence:**\n```json\n" + json.dumps(payload) + "\n```"
        )


@pytest.mark.parametrize("case", ["absent", "prose", "duplicate", "suffix", "two-json"])
def test_legacy_or_ambiguous_records_are_unreadable_without_changing_the_body(case):
    field = render_evidence_field(evidence())
    body = {
        "absent": "**Check:** It works.",
        "prose": f"**Evidence:** {SHA} passed test; prior {'a' * 40} was green too.",
        "duplicate": field + "\n" + field,
        "suffix": field + "\nThis second narrative is not part of the record.",
        "two-json": field + "\n" + field.partition("**Evidence:**\n")[2],
    }[case]
    before = body
    with pytest.raises(ValueError):
        parse_criterion_evidence(body)
    assert body == before


@pytest.mark.parametrize("member", ["gradedSha", "test"])
def test_repeated_json_keys_are_refused_instead_of_taking_the_last(member):
    record = f'{{"gradedSha":"{SHA}","test":"test","{member}":"{SHA}"}}'
    with pytest.raises(ValueError, match="duplicate"):
        parse_criterion_evidence("**Evidence:**\n```json\n" + record + "\n```")


def test_quoted_rows_and_comments_are_not_evidence_but_fenced_string_bytes_are():
    field = render_evidence_field(evidence())
    body = (
        "**Check:** Current check.\n"
        "```text\n**Evidence:** fake example\n```\n"
        "> **Evidence:** quoted example\n"
        "<!-- **Evidence:** comment\ncontinued -->\n"
        + field
        + "\n**Class:** historical"
    )
    assert parse_criterion_evidence(body) == evidence()
    assert parse_criterion_evidence(body.replace("\n", "\r\n")) == evidence()

"""Recorded grading is one explicit field, never a SHA guessed from prose."""

import json

import pytest
from pydantic import ValidationError

from kodezart.domain.criterion_creation import criterion_body
from kodezart.domain.criterion_evidence import (
    parse_criterion_evidence,
    render_evidence_field,
)
from kodezart.domain.fire_spec import criterion_check, criterion_field_bodies
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


def test_a_created_criterion_names_its_demonstration_and_still_reads_as_unfilled():
    """The row a criterion is born with: nothing graded, one grader named.

    A created criterion has been graded by nothing, so the codec must keep
    refusing it — that refusal is how an ungraded criterion is told from a
    graded one. What the row carries meanwhile is the demonstration its
    author declared, which no later reader can reconstruct from the Check.
    """
    demonstration = "tests/domain/test_prepared_bytes.py::test_bytes_are_preserved"
    body = criterion_body(
        parent_key="native/parent",
        check="The prepared bytes match the declared source.",
        do="Compare the source and the prepared bytes.",
        demonstration=demonstration,
    )
    assert demonstration in criterion_field_bodies(body, field="Evidence")[0]
    with pytest.raises(ValueError, match="explicit fenced JSON record"):
        parse_criterion_evidence(body)

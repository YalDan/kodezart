"""Pinned ruling text retains required authorship and exact question identity."""

import json

import pytest
from pydantic import ValidationError

from kodezart.domain.agent import mint_ruling_id
from kodezart.domain.rulings import parse_ruling, render_ruling, ruling_marker
from kodezart.types.domain.agent import Ruling, RulingAuthor, RulingClass, RulingOutput
from kodezart.types.domain.operation import OperationMemberAbsentError

PREFIXES = {"ruling": "fixture-pinned", "decision": "fixture-answer"}
LANE = "lane:café/alpha"


def ruling_data(**changes):
    fields = {
        "issue_ref": "EXT/42",
        "question": "Which interpretation applies?\nKeep exact bytes.",
        "ruling_class": "pin_reading",
        "resolution": "Use the observable reading.",
        "rejected_alternative": "The reading that cannot terminate.",
        "repo_evidence": ("src/one.py:12 — existing behavior", "HEAD:path/二.py"),
        "authored_by": "machine",
    }
    fields.update(changes)
    fields.setdefault(
        "ruling_id",
        mint_ruling_id(issue_ref=fields["issue_ref"], question=fields["question"]),
    )
    return fields


def render(data=None):
    return render_ruling(
        ruling=Ruling.model_validate(ruling_data() if data is None else data),
        lane_key=LANE,
        marker_prefixes=PREFIXES,
    )


@pytest.mark.parametrize("author", list(RulingAuthor))
@pytest.mark.parametrize("kind", list(RulingClass))
async def test_closed_model_and_pinned_text_round_trip_every_author_and_class(
    author, kind
):
    original = Ruling.model_validate(ruling_data(authored_by=author, ruling_class=kind))
    body = render_ruling(ruling=original, lane_key=LANE, marker_prefixes=PREFIXES)
    parsed = parse_ruling(body=body, lane_key=LANE, marker_prefixes=PREFIXES)
    assert parsed == original
    assert json.loads(body.split("\n```json\n")[1][:-4])["authoredBy"] == author
    assert body.splitlines()[0] == ruling_marker(
        ruling_id=original.ruling_id, lane_key=LANE, marker_prefixes=PREFIXES
    )
    assert "fixture-pinned:lane%3Acaf%C3%A9%2Falpha:" in body
    assert RulingOutput(rulings=[parsed]).rulings == [original]
    with pytest.raises(ValidationError, match="frozen"):
        parsed.authored_by = RulingAuthor.PRINCIPAL


@pytest.mark.parametrize("field", list(ruling_data()))
def test_every_ruling_field_is_required(field):
    data = ruling_data()
    del data[field]
    with pytest.raises(ValidationError):
        Ruling.model_validate(data)


@pytest.mark.parametrize("author", [None, "", "operator", "MACHINE", 1])
def test_no_account_or_unknown_author_substitutes_for_the_required_enum(author):
    with pytest.raises(ValidationError):
        Ruling.model_validate(ruling_data(authored_by=author))


@pytest.mark.parametrize("kind", list(RulingClass))
def test_rejected_alternative_is_explicit_and_required_for_a_losing_reading(kind):
    data = ruling_data(ruling_class=kind, rejected_alternative=None)
    if kind in {RulingClass.PIN_READING, RulingClass.RESOLVE_CONTRADICTION}:
        with pytest.raises(ValidationError, match="rejected alternative"):
            Ruling.model_validate(data)
    else:
        assert Ruling.model_validate(data).rejected_alternative is None


@pytest.mark.parametrize(
    "change",
    [
        lambda body: body.replace('"authoredBy": "machine"', '"authoredBy": null'),
        lambda body: body.replace('"authoredBy": "machine"', '"authoredBy": "other"'),
        lambda body: body.replace(
            '"authoredBy": "machine"',
            '"authoredBy": "machine", "authoredBy": "principal"',
        ),
        lambda body: body.replace(
            '"authoredBy": "machine"', '"authoredBy": "machine", "extra": true'
        ),
        lambda body: body.replace("Which interpretation", "A different interpretation"),
        lambda body: body.replace("fixture-pinned", "fixture-answer", 1),
        lambda body: body.replace("lane%3Acaf%C3%A9%2Falpha", "other", 1),
        lambda body: "quoted\n" + body,
        lambda body: body + "\nextra text",
        lambda body: body[:-4],
    ],
)
def test_damaged_or_ambiguous_text_refuses(change):
    with pytest.raises(ValueError):
        parse_ruling(body=change(render()), lane_key=LANE, marker_prefixes=PREFIXES)


def test_answer_amendment_retains_question_address_but_a_new_question_does_not():
    first = Ruling.model_validate(ruling_data())
    amended = Ruling.model_validate(
        ruling_data(resolution="A corrected pinned answer.")
    )
    second = Ruling.model_validate(ruling_data(question="Another exact question?"))
    assert first.ruling_id == amended.ruling_id
    assert first.ruling_id != second.ruling_id
    assert (
        render(ruling_data()).splitlines()[0]
        == render(ruling_data(resolution="Changed")).splitlines()[0]
    )


def test_renderer_refuses_an_unrelated_minted_id():
    invalid = Ruling.model_validate(
        ruling_data(ruling_id=mint_ruling_id(issue_ref="other", question="elsewhere"))
    )
    with pytest.raises(ValueError, match="exact question"):
        render_ruling(ruling=invalid, lane_key=LANE, marker_prefixes=PREFIXES)


def test_absent_ruling_purpose_cannot_borrow_the_decision_reply_prefix():
    with pytest.raises(OperationMemberAbsentError, match="ruling"):
        render_ruling(
            ruling=Ruling.model_validate(ruling_data()),
            lane_key=LANE,
            marker_prefixes={"decision": "fixture-answer"},
        )

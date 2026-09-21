"""What a pass owes the tracker is arithmetic over identities, not judgement."""

import pytest

from kodezart.domain.agent import mint_ruling_id
from kodezart.domain.errors import RulingUnrecordedError
from kodezart.domain.rulings import (
    EMPTY_REGISTRY,
    excess_answers,
    owed_rulings,
    pinned_registry,
)
from kodezart.types.domain.agent import Ruling, RulingAnswer, RulingAuthor

SUBJECT = "EXT/1"
CHECK = "EXT/1-a"
ADDRESSABLE = frozenset({SUBJECT, CHECK})
QUESTION = "Which of the two readings applies?"

#: One item a subject's own section states, and the words inside it an answer
#: could name instead of the item.
STATED_DELIVERABLE = "bound a failed item's retries on the existing queue predicate"


def answer(**changes) -> RulingAnswer:
    fields: dict[str, object] = {
        "issue_ref": CHECK,
        "question": QUESTION,
        "ruling_class": "pin_reading",
        "resolution": "The reading the Check can be observed under.",
        "rejected_alternative": "The reading under which the work cannot end.",
        "repo_evidence": ("src/one.py:12 — the existing behaviour",),
    }
    fields.update(changes)
    return RulingAnswer.model_validate(fields)


def owed(*answers, recorded=()):
    return owed_rulings(
        subject=SUBJECT,
        answers=answers,
        addressable=ADDRESSABLE,
        recorded=recorded,
    )


def test_an_answer_becomes_a_machine_authored_record_under_a_minted_identity() -> None:
    """Identity and authorship are the step's, never the answer's."""
    (record,) = owed(answer())

    assert record.ruling_id == mint_ruling_id(issue_ref=CHECK, question=QUESTION)
    assert record.authored_by is RulingAuthor.MACHINE
    assert record.protected_tests is None
    assert record.issue_ref == CHECK
    assert record.question == QUESTION
    # Every field the answer carried survives byte for byte.
    assert record.resolution == answer().resolution
    assert record.rejected_alternative == answer().rejected_alternative
    assert record.repo_evidence == answer().repo_evidence
    # And the answer model cannot state either of the two the step owns.
    assert "ruling_id" not in RulingAnswer.model_fields
    assert "authored_by" not in RulingAnswer.model_fields


def test_records_come_back_in_identity_order_whatever_order_they_were_answered_in() -> (
    None
):
    """The order is the identities', so two passes render the same registry."""
    first = answer()
    second = answer(question="Which artifact does the Check name?")

    forwards = owed(first, second)
    backwards = owed(second, first)

    assert [record.ruling_id for record in forwards] == sorted(
        record.ruling_id for record in forwards
    )
    assert forwards == backwards


def test_an_answer_addressed_outside_the_fire_is_refused() -> None:
    """A record whose reader could not address it back is never built."""
    with pytest.raises(RulingUnrecordedError) as caught:
        owed(answer(issue_ref="EXT/999"))

    assert caught.value.issue_key == SUBJECT
    assert "EXT/999" in caught.value.reason


def test_two_answers_to_one_question_are_refused() -> None:
    """One question has one answer; two would share an identity."""
    with pytest.raises(RulingUnrecordedError):
        owed(answer(), answer(resolution="A second, different answer."))


def test_a_pinned_reading_without_its_rejected_reading_is_refused() -> None:
    """The record model's own validator is what decides, here."""
    with pytest.raises(RulingUnrecordedError) as caught:
        owed(answer(rejected_alternative=None))

    assert "not a valid record" in caught.value.reason
    # Non-vacuous: the same answer under a class with no loser is owed.
    (record,) = owed(
        answer(ruling_class="pin_artifact", rejected_alternative=None),
    )
    assert record.rejected_alternative is None


def test_an_identity_the_tracker_already_carries_is_not_owed() -> None:
    """The second pass over one fire owes nothing, and writes nothing."""
    (record,) = owed(answer())

    assert owed(answer(), recorded=(record.ruling_id,)) == ()
    # A DIFFERENT answer to the same question is the same identity, so it is
    # dropped too: an existing record is never overwritten from here.
    assert (
        owed(
            answer(resolution="A later, different answer."),
            recorded=(record.ruling_id,),
        )
        == ()
    )


def test_a_restated_question_mints_a_new_identity_that_names_the_one_it_replaces() -> (
    None
):
    """A restatement is a new record, and it addresses the earlier one.

    The earlier identity is minted here from the exact words the answer
    quotes, so the pointer is arithmetic over the same pair the first record
    was addressed by rather than a second address the answer could choose.
    """
    (earlier,) = owed(answer())
    restated = "Does a queue holding only failed items count as drained?"

    (record,) = owed(
        answer(question=restated, supersedes_question=QUESTION),
        recorded=(earlier.ruling_id,),
    )

    assert record.ruling_id == mint_ruling_id(issue_ref=CHECK, question=restated)
    assert record.ruling_id != earlier.ruling_id
    assert record.supersedes == earlier.ruling_id
    # The answer's own field never reaches the record, and nothing on the
    # earlier record moved: this call built one new record and no edit.
    assert "supersedes_question" not in Ruling.model_fields
    assert earlier.supersedes is None
    # A record that replaces nothing says so, rather than leaving it unstated.
    (plain,) = owed(answer())
    assert plain.supersedes is None


def test_an_answer_naming_a_question_the_tracker_carries_no_answer_for_is_refused() -> (
    None
):
    """A pointer at no record is an address a later reader cannot follow."""
    with pytest.raises(RulingUnrecordedError) as caught:
        owed(
            answer(
                question="A restatement of nothing pinned?",
                supersedes_question="A question nobody answered.",
            )
        )

    assert caught.value.issue_key == SUBJECT
    assert "carries no answer for" in caught.value.reason
    # Non-vacuous: the same answer is owed once that question is on record.
    (earlier,) = owed(answer(question="A question nobody answered."))
    (record,) = owed(
        answer(
            question="A restatement of nothing pinned?",
            supersedes_question="A question nobody answered.",
        ),
        recorded=(earlier.ruling_id,),
    )
    assert record.supersedes == earlier.ruling_id


def test_an_answer_cannot_name_its_own_question_as_the_one_it_replaces() -> None:
    """Its own identity is the one address a record can never replace."""
    (earlier,) = owed(answer())

    with pytest.raises(RulingUnrecordedError) as caught:
        owed(
            answer(supersedes_question=QUESTION),
            recorded=(),
        )

    assert "supersedes its own question" in caught.value.reason
    # The record model refuses it too, so no other builder can construct one.
    with pytest.raises(ValueError, match="supersede its own question"):
        Ruling.model_validate({**earlier.model_dump(), "supersedes": earlier.ruling_id})


def test_the_registry_text_is_one_json_line_per_record_or_the_stated_empty_form() -> (
    None
):
    """What a session is shown for the answers already pinned."""
    records = owed(answer(), answer(question="Which artifact does the Check name?"))

    # The stated form, as a literal: an absence a reader can tell from a
    # rendering that failed.
    assert EMPTY_REGISTRY == "Confirmed empty ruling registry."
    assert pinned_registry(()) == EMPTY_REGISTRY
    assert pinned_registry(records).splitlines() == [
        record.model_dump_json() for record in records
    ]
    assert all(
        Ruling.model_validate_json(line) in records
        for line in pinned_registry(records).splitlines()
    )


@pytest.mark.parametrize(
    "named",
    ["retries", "the existing queue predicate", "bound", "a failed item's retries"],
)
def test_an_answer_naming_part_of_a_stated_deliverable_is_still_excess(named) -> None:
    """Membership, never containment (KOD-629).

    A containment test would let one stated item cover every item whose words
    happen to occur inside it, so an answer naming only part of a stated item
    would pass as covered. Part of an item is not an item: the section does not
    state it, so the answer names work beyond what the subject states and is
    excess.
    """
    named_answer = answer(deliverable=named)

    assert named_answer.deliverable in STATED_DELIVERABLE
    assert excess_answers(answers=(named_answer,), stated=(STATED_DELIVERABLE,)) == (
        named_answer,
    )
    # Not "everything is excess": the item as the section states it is covered.
    whole = answer(deliverable=STATED_DELIVERABLE)
    assert excess_answers(answers=(whole,), stated=(STATED_DELIVERABLE,)) == ()

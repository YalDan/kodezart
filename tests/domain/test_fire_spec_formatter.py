"""Generated authored corpus equivalence and verbatim tracker rendering."""

from itertools import product

import pytest

from kodezart.domain.ticket import format_fire_spec, format_ticket_as_task
from kodezart.types.domain.agent import CodeReference, FileChange, TicketDraftOutput
from kodezart.types.domain.fire_spec import (
    AuthoredSpec,
    CriterionRef,
    IssueRef,
    TrackerSpec,
)


def tickets():
    for text, references, exclusions, questions in product(
        ["plain", "Unicode café 雪", "Markdown **bold**\n# heading"],
        [False, True],
        [False, True],
        [False, True],
    ):
        yield TicketDraftOutput(
            title=text,
            summary=text,
            context=text,
            references=[CodeReference(location="src/file.py:7", note=text)]
            if references
            else [],
            required_changes=[
                FileChange(
                    file_path="src/file.py",
                    change_type=kind,
                    description=text,
                    rationale=text,
                )
                for kind in ["create", "modify", "delete"]
            ],
            out_of_scope=[text] if exclusions else [],
            open_questions=[text] if questions else [],
        )


@pytest.mark.parametrize("ticket", list(tickets()))
def test_authored_generated_corpus_is_byte_identical(ticket):
    assert (
        format_fire_spec(AuthoredSpec(ticket=ticket)).encode()
        == format_ticket_as_task(ticket).encode()
    )


@pytest.mark.parametrize(
    "body",
    [
        "",
        "  leading and trailing  ",
        "# Check\n\ntext\n",
        "{not ticket JSON}",
        "雪 café",
    ],
)
def test_tracker_arm_returns_own_body_verbatim(body):
    spec = TrackerSpec(
        subject=IssueRef("opaque"),
        body=body,
        criteria=(CriterionRef("own-key"),),
        read_at_version="v1",
    )
    assert format_fire_spec(spec).encode() == body.encode()

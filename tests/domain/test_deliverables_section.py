"""What a subject's own text states it commits to building, read off the body.

The parser alone here: one rule per test, and the one input the amended Check
makes reachable — a body with no such section — has its own.
"""

import pytest

from kodezart.domain.fire_spec import DELIVERABLES_SECTION, deliverables_section

FIRST = "bound a failed item's retries on the existing queue predicate"
SECOND = "report the bound in the run summary"


def test_the_items_come_back_in_body_order_stripped() -> None:
    """Every visible list item of the section, and nothing else in it."""
    body = (
        "Some opening prose.\n"
        f"## {DELIVERABLES_SECTION}\n"
        "\n"
        "Prose inside the section, which states no item.\n"
        f"-   {FIRST}   \n"
        f"* {SECOND}\n"
        "\n"
        "## Out Of Scope\n"
        "- a second queue that holds failed items\n"
    )

    assert deliverables_section(body) == (FIRST, SECOND)
    # The heading's exact text decides, so the section is named as a literal.
    assert DELIVERABLES_SECTION == "Deliverables"


@pytest.mark.parametrize("level", range(1, 7))
def test_a_heading_of_any_level_opens_the_section(level: int) -> None:
    """Six heading levels, one rule: the text of the heading is what matters."""
    body = f"{'#' * level} {DELIVERABLES_SECTION}\n- {FIRST}\n"

    assert deliverables_section(body) == (FIRST,)


def test_a_heading_inside_a_fenced_block_is_no_heading() -> None:
    """The reader's own fence rule decides what is visible."""
    body = (
        "```markdown\n"
        f"## {DELIVERABLES_SECTION}\n"
        f"- {SECOND}\n"
        "```\n"
        f"## {DELIVERABLES_SECTION}\n"
        f"- {FIRST}\n"
    )

    assert deliverables_section(body) == (FIRST,)


def test_a_list_item_inside_a_fenced_block_inside_the_section_is_no_item() -> None:
    """A fenced example under the heading states nothing."""
    body = f"## {DELIVERABLES_SECTION}\n- {FIRST}\n```\n- {SECOND}\n```\n"

    assert deliverables_section(body) == (FIRST,)


def test_a_commented_out_heading_is_no_heading() -> None:
    """The reader's own HTML-comment rule decides what is visible.

    The opening `<!--` is on its own line, so the heading and the item sit
    inside the comment with nothing else on their lines to hide them: only the
    comment rule keeps them out. A heading sharing a line with its `<!--` is
    already refused by the heading pattern, whatever the comment rule does, so
    such a body states nothing about this rule.
    """
    body = (
        "<!--\n"
        f"## {DELIVERABLES_SECTION}\n"
        f"- {SECOND}\n"
        "-->\n"
        f"### {DELIVERABLES_SECTION}\n"
        f"- {FIRST}\n"
    )

    assert deliverables_section(body) == (FIRST,)


def test_the_section_ends_at_the_next_visible_heading_of_any_level() -> None:
    """An item below the next heading belongs to that heading, not this one."""
    body = f"# {DELIVERABLES_SECTION}\n- {FIRST}\n###### Notes\n- {SECOND}\n"

    assert deliverables_section(body) == (FIRST,)


def test_two_such_headings_contribute_both_their_item_lists() -> None:
    """Nothing here edits the body, so an ambiguous section is not a refusal."""
    body = (
        f"## {DELIVERABLES_SECTION}\n"
        f"- {FIRST}\n"
        "## Something Else\n"
        "- not a deliverable\n"
        f"## {DELIVERABLES_SECTION}\n"
        f"- {SECOND}\n"
    )

    assert deliverables_section(body) == (FIRST, SECOND)


@pytest.mark.parametrize(
    "body",
    [
        "the subject's own text",
        f"## {DELIVERABLES_SECTION}\n",
        f"## {DELIVERABLES_SECTION}\n\nOnly prose, no item.\n",
        f"## {DELIVERABLES_SECTION} extra\n- an item of another section\n",
    ],
    ids=["no-section", "empty-section", "prose-only", "another-heading"],
)
def test_a_body_that_states_nothing_and_a_section_with_no_items_answer_alike(
    body: str,
) -> None:
    """One rule, no special case: nothing is stated either way."""
    assert deliverables_section(body) == ()

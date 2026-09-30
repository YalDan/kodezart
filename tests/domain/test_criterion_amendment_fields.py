"""Native field editing leaves unaddressed content and exact archived input intact."""

import pytest

from kodezart.domain.fire_spec import criterion_field_bodies, replace_criterion_fields


def test_edit_preserves_preamble_unaddressed_fields_and_quoted_row_labels():
    before = (
        "Exact preamble  \r\n<!-- **Check:** hidden -->\r\n"
        "**Check:** old\r\n\r\n**Do:** exact implementation  \r\n"
        "```markdown\r\n**Check:** quoted\r\n```\r\n"
        "**Evidence:** exact prior evidence\r\n"
    )
    after = replace_criterion_fields(before, replacements={"Check": "new  Check"})
    assert after.startswith("Exact preamble  \r\n<!-- **Check:** hidden -->\r\n")
    assert after[after.index("**Do:**") :] == before[before.index("**Do:**") :]
    assert criterion_field_bodies(after, field="Check") == ("new  Check",)
    assert "**Evidence:** exact prior evidence\r\n" in before


@pytest.mark.parametrize(
    "replacement",
    ["new\n**Do:** injected", "new\n**Check:** duplicate", "new\n<!-- unterminated"],
)
def test_replacement_cannot_change_the_meaning_of_another_template_field(replacement):
    body = "**Check:** old\n**Do:** existing\n**Evidence:** old evidence"
    with pytest.raises(ValueError):
        replace_criterion_fields(body, replacements={"Check": replacement})


def test_duplicate_native_rows_refuse_before_replacement():
    with pytest.raises(ValueError, match="unambiguous"):
        replace_criterion_fields(
            "**Check:** first\n**Check:** second\n", replacements={"Check": "new"}
        )

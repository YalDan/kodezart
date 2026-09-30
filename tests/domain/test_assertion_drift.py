"""Protected assertions compare syntax, with source identity resolved explicitly."""

import pytest
from pydantic import ValidationError

from kodezart.domain.assertion_drift import (
    lost_assertions,
    protected_assertions,
    weakening_mark,
)
from kodezart.types.domain.assertion_drift import (
    AssertionDeviationClaim,
    ProtectedTestRef,
)


def assertions(source, name="test_behavior"):
    return protected_assertions(
        source=source.encode(), path="tests/protected.py", qualified_name=name
    )


@pytest.mark.parametrize("kind", ["", "async "])
def test_expected_value_change_and_source_locations_are_observable(kind):
    first = assertions(f"{kind}def test_behavior():\n    assert calls == 1\n")
    second = assertions(f"{kind}def test_behavior():\n    assert calls == 2\n")
    assert first[0].line == second[0].line == 2
    assert first[0].expression == "calls == 1"
    assert second[0].expression == "calls == 2"
    assert first[0].structural_form != second[0].structural_form


def test_comments_formatting_diagnostic_text_and_other_tests_do_not_change_condition():
    first = assertions('def test_behavior():\n    assert calls == 1, "old message"\n')
    second = assertions(
        '# heading\n\ndef test_behavior():\n    assert (calls  ==  1), "new message"\n'
        "\ndef test_new():\n    assert calls == 2\n"
    )
    assert tuple(row.structural_form for row in first) == tuple(
        row.structural_form for row in second
    )
    assert first[0].line != second[0].line


def test_class_qualified_reference_never_borrows_another_function_of_same_name():
    source = (
        "def test_behavior():\n    assert calls == 9\n\nclass TestContract:\n"
        "    def test_behavior(self):\n        assert calls == 1\n"
    )
    assert (
        assertions(source, "TestContract.test_behavior")[0].expression == "calls == 1"
    )
    assert assertions(source)[0].expression == "calls == 9"


@pytest.mark.parametrize(
    "source,name",
    [
        ("def another():\n    assert True\n", "test_behavior"),
        (
            "def test_behavior():\n    assert True\n"
            "def test_behavior():\n    assert False\n",
            "test_behavior",
        ),
        (
            "if flag:\n    def test_behavior():\n        assert True\n"
            "else:\n    def test_behavior():\n        assert False\n",
            "test_behavior",
        ),
        ("class TestContract:\n    pass\n", "TestContract"),
    ],
)
def test_missing_ambiguous_or_nonfunction_reference_refuses(source, name):
    with pytest.raises(ValueError):
        assertions(source, name)


def test_declared_python_encoding_is_read_without_utf8_guessing():
    rows = protected_assertions(
        source=(
            b'# coding: latin-1\ndef test_behavior():\n    assert text == "caf\xe9"\n'
        ),
        path="tests/protected.py",
        qualified_name="test_behavior",
    )
    assert rows[0].expression == "text == 'caf\u00e9'"


def test_source_is_parsed_without_running_any_import_or_top_level_expression():
    rows = assertions(
        'raise RuntimeError("must never execute")\n'
        "def test_behavior():\n    assert calls == 1\n"
    )
    assert len(rows) == 1


@pytest.mark.parametrize(
    "path,name",
    [
        ("/tmp/test.py", "test_behavior"),
        ("../test.py", "test_behavior"),
        ("tests/../test.py", "test_behavior"),
        ("tests//test.py", "test_behavior"),
        ("tests/test.js", "test_behavior"),
        ("tests/test.py", "test_behavior[param]"),
        ("tests/test.py", ".test_behavior"),
        ("tests/test.py", "test_behavior..nested"),
    ],
)
def test_protected_reference_requires_canonical_explicit_python_address(path, name):
    with pytest.raises(ValidationError):
        ProtectedTestRef(
            source_ref="owning-ruling/native-id", path=path, qualified_name=name
        )


def claim(*, before, after, graded="a" * 40, head="b" * 40):
    """One deviation claim over the designated test the fixtures above use."""
    return AssertionDeviationClaim(
        protected_test=ProtectedTestRef(
            source_ref="owning-record/native-id",
            path="tests/protected.py",
            qualified_name="test_behavior",
        ),
        graded_sha=graded,
        head_sha=head,
        graded_blob_sha="c" * 40,
        head_blob_sha="d" * 40,
        before=before,
        after=after,
    )


def test_a_removed_or_changed_assertion_is_lost_and_an_added_one_is_not():
    before = assertions("def test_behavior():\n    assert calls == 1\n")
    removed = assertions("def test_behavior():\n    pass\n")
    changed = assertions("def test_behavior():\n    assert calls is not None\n")
    added = assertions(
        "def test_behavior():\n    assert calls == 1\n    assert calls > 0\n"
    )

    assert lost_assertions(before=before, after=removed) == before
    assert lost_assertions(before=before, after=changed) == before
    assert lost_assertions(before=before, after=added) == ()


def test_counted_so_a_reorder_loses_nothing_and_a_dropped_duplicate_loses_one():
    before = assertions(
        "def test_behavior():\n    assert calls == 1\n    assert calls == 2\n"
    )
    reordered = assertions(
        "def test_behavior():\n    assert calls == 2\n    assert calls == 1\n"
    )
    twice = assertions(
        "def test_behavior():\n    assert calls == 1\n    assert calls == 1\n"
    )
    once = assertions("def test_behavior():\n    assert calls == 1\n")

    assert lost_assertions(before=before, after=reordered) == ()
    assert lost_assertions(before=twice, after=once) == (twice[1],)
    assert len(lost_assertions(before=twice, after=once)) == 1


def test_a_reformatted_assertion_loses_nothing():
    """A moved, rewrapped assertion with a new message is the same condition.

    What differs between the two readings is the line and the message; the
    unparsed condition is canonical, so it reads the same in both, and the
    case pins that the loss is independent of where the assertion sits and
    what it says on failure.
    """
    before = assertions('def test_behavior():\n    assert calls == 1, "old message"\n')
    after = assertions(
        "# heading\n\ndef test_behavior():\n"
        '    assert (\n        calls  ==  1\n    ), "new message"\n'
    )

    assert before[0].line != after[0].line
    assert before[0].expression == after[0].expression
    assert lost_assertions(before=before, after=after) == ()


def test_the_mark_names_the_test_and_the_record_and_no_assertion_text_or_sha():
    """One mark per designated test and record, whatever the starting head.

    Neither the starting head nor the refused commit appears, so a weakening
    of the same test from another starting head, with another commit,
    renders the same bytes; the writer reopens that one mark rather than
    minting a second.
    """
    before = assertions(
        "def test_behavior():\n    assert calls == 1\n    assert seen == 'x'\n"
    )
    after = assertions("def test_behavior():\n    assert replaced is not None\n")
    lost = lost_assertions(before=before, after=after)
    first = claim(before=before, after=after)
    second = claim(before=before, after=after, graded="e" * 40, head="f" * 40)

    mark = weakening_mark(claim=first, lost=lost)
    text = mark.title + mark.check + mark.do
    later = weakening_mark(claim=second, lost=lost)
    later_text = later.title + later.check + later.do

    assert lost == before
    assert mark == later
    assert "tests/protected.py::test_behavior" in mark.check
    assert "owning-record/native-id" in mark.check
    # No assertion source leaves the repository: neither the conditions that
    # went nor the one that replaced them.
    for row in (*before, *after):
        assert row.expression not in text
    assert "a" * 40 not in text
    assert "b" * 40 not in text
    assert "e" * 40 not in later_text
    assert "f" * 40 not in later_text


def test_a_claim_that_lost_nothing_renders_no_mark():
    before = assertions("def test_behavior():\n    assert calls == 1\n")
    after = assertions(
        "def test_behavior():\n    assert calls == 1\n    assert calls > 0\n"
    )

    assert lost_assertions(before=before, after=after) == ()
    with pytest.raises(ValueError, match="at least one lost assertion"):
        weakening_mark(claim=claim(before=before, after=after), lost=())

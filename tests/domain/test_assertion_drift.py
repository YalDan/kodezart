"""Protected assertions compare syntax, with source identity resolved explicitly."""

import pytest
from pydantic import ValidationError

from kodezart.domain.assertion_drift import protected_assertions
from kodezart.types.domain.assertion_drift import ProtectedTestRef


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

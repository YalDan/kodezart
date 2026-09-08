"""Explicit ruling designation preserves unknown, empty and named protection."""

import json

import pytest
from pydantic import ValidationError

from kodezart.domain.rulings import parse_ruling, render_ruling
from kodezart.types.domain.agent import Ruling
from kodezart.types.domain.assertion_drift import ProtectedTestRef
from tests.domain.test_rulings import LANE, PREFIXES, ruling_data


def designated(**changes):
    data = ruling_data()
    reference = {
        "source_ref": data["ruling_id"],
        "path": "tests/test_contract.py",
        "qualified_name": "TestContract.test_expected_value",
    }
    data["protected_tests"] = (ProtectedTestRef(**{**reference, **changes}),)
    return data


def round_trip(data):
    value = Ruling.model_validate(data)
    text = render_ruling(ruling=value, lane_key=LANE, marker_prefixes=PREFIXES)
    return value, text, parse_ruling(body=text, lane_key=LANE, marker_prefixes=PREFIXES)


@pytest.mark.parametrize("explicit_null", [False, True])
def test_unknown_designation_preserves_the_original_generic_record_bytes(explicit_null):
    data = ruling_data()
    if explicit_null:
        data["protected_tests"] = None
    value, text, decoded = round_trip(data)
    old_fields = {
        "rulingId": data["ruling_id"],
        "issueRef": data["issue_ref"],
        "question": data["question"],
        "rulingClass": data["ruling_class"],
        "resolution": data["resolution"],
        "rejectedAlternative": data["rejected_alternative"],
        "repoEvidence": list(data["repo_evidence"]),
        "authoredBy": data["authored_by"],
    }
    assert text.split("\n```json\n")[1] == (
        json.dumps(old_fields, ensure_ascii=False, indent=2) + "\n```"
    )
    assert decoded == value and decoded.protected_tests is None


@pytest.mark.parametrize("references", [(), None])
def test_explicit_empty_is_distinct_from_unknown_on_wire(references):
    value, text, decoded = round_trip(ruling_data(protected_tests=references))
    assert decoded.protected_tests == references
    assert ('"protectedTests": []' in text) is (references == ())
    assert value == decoded


def test_protected_names_round_trip_in_the_owning_ruling_without_new_identity():
    value, text, decoded = round_trip(designated())
    assert decoded == value
    assert decoded.protected_tests[0].source_ref == value.ruling_id
    assert '"protectedTests"' in text
    with pytest.raises(ValidationError, match="frozen"):
        decoded.protected_tests[0].path = "tests/other.py"
    with pytest.raises(ValidationError, match="frozen"):
        decoded.protected_tests = ()


def test_native_explicit_null_is_unknown_not_empty():
    _, text, _ = round_trip(ruling_data(protected_tests=()))
    decoded = parse_ruling(
        body=text.replace('"protectedTests": []', '"protectedTests": null'),
        lane_key=LANE,
        marker_prefixes=PREFIXES,
    )
    assert decoded.protected_tests is None


def test_reference_to_another_ruling_is_rejected():
    with pytest.raises(ValidationError, match="owning ruling"):
        Ruling.model_validate(designated(source_ref="another-ruling"))


def test_repeated_test_address_cannot_duplicate_one_rulings_protection():
    data = designated()
    data["protected_tests"] *= 2
    with pytest.raises(ValidationError, match="duplicate protected test"):
        Ruling.model_validate(data)


@pytest.mark.parametrize(
    "change",
    [
        lambda text: text.replace(
            '"sourceRef":', '"sourceRef": "foreign", "sourceRef":'
        ),
        lambda text: text.replace(
            '"sourceRef":', '"inventedOwner": true, "sourceRef":'
        ),
        lambda text: text.replace("tests/test_contract.py", "../test_contract.py"),
        lambda text: text.replace("TestContract.test_expected_value", "test-value"),
        lambda text: text.replace(
            '"protectedTests": [', '"protectedTests": {}, "protectedTests": ['
        ),
    ],
)
def test_invalid_native_designation_refuses_instead_of_losing_protection(change):
    _, text, _ = round_trip(designated())
    with pytest.raises(ValueError):
        parse_ruling(body=change(text), lane_key=LANE, marker_prefixes=PREFIXES)

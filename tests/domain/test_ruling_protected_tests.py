"""Explicit ruling designation preserves unknown, empty and named protection."""

import json
import subprocess
import sys
from typing import get_type_hints

import pytest
from pydantic import ValidationError

from kodezart.domain.amendment import amended_records
from kodezart.domain.rulings import (
    designated_tests,
    parse_ruling,
    render_ruling,
    repeated_designations,
)
from kodezart.types.domain.agent import Ruling, RulingId, RulingProtectedTestRef
from kodezart.types.domain.amendment import (
    AmendedAmendment,
    AmendmentReport,
    RulingSubject,
)
from kodezart.types.domain.audit import TrackerArtifact
from kodezart.types.domain.scope import ScopeKind, ScopeRef
from kodezart.types.domain.surface import SurfaceKind, WritableSurface
from tests.domain.test_amendment import amended, record
from tests.domain.test_rulings import LANE, PREFIXES, ruling_data

#: Two designated-test addresses whose sorted order is the reverse of the order
#: the rows below write them in, so a dropped sort is visible.
CONTRACT = ("tests/test_contract.py", "TestContract.test_expected_value")
BOUNDARY = ("tests/test_boundary.py", "test_the_boundary_holds")


def designated(**changes):
    data = ruling_data()
    reference = {
        "source_ref": data["ruling_id"],
        "path": "tests/test_contract.py",
        "qualified_name": "TestContract.test_expected_value",
    }
    data["protected_tests"] = (RulingProtectedTestRef(**{**reference, **changes}),)
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


@pytest.mark.parametrize(
    "rows,expected",
    [
        ([(CONTRACT,), (CONTRACT,)], (CONTRACT,)),
        ([(CONTRACT,), (("tests/test_contract.py", "test_a_second_name"),)], ()),
        ([(CONTRACT,), (("tests/test_second_file.py", CONTRACT[1]),)], ()),
        ([(CONTRACT,)], ()),
        ([(CONTRACT,), None], ()),
        ([(CONTRACT, BOUNDARY), (CONTRACT, BOUNDARY)], (BOUNDARY, CONTRACT)),
    ],
)
def test_repeated_designations_names_an_address_two_records_claim(rows, expected):
    """One address, two claimants: the path and the name together are the address.

    A shared path under two names, and one name under two paths, are two
    addresses and not a repeat. A record that recorded no designation at all
    contributes nothing. The returned addresses are sorted, not in the order the
    records were read.
    """
    records = []
    for index, addresses in enumerate(rows):
        data = ruling_data(question=f"Which reading applies to case {index}?")
        data["protected_tests"] = (
            None
            if addresses is None
            else tuple(
                RulingProtectedTestRef(
                    source_ref=data["ruling_id"], path=path, qualified_name=name
                )
                for path, name in addresses
            )
        )
        records.append(Ruling.model_validate(data))
    assert len({record.ruling_id for record in records}) == len(rows)
    assert repeated_designations(records) == expected


def test_native_designation_has_a_typed_ruling_owner():
    assert get_type_hints(RulingProtectedTestRef)["source_ref"] is RulingId


def test_mypy_preserves_reader_identity_and_refuses_a_plain_string_owner(tmp_path):
    program = """from kodezart.domain.agent import mint_ruling_id
from kodezart.types.domain.agent import Ruling, RulingId, RulingProtectedTestRef

def recorded_owner(ruling: Ruling) -> RulingId:
    if not ruling.protected_tests:
        raise ValueError("No designated test")
    return ruling.protected_tests[0].source_ref

reference = RulingProtectedTestRef(
    source_ref=mint_ruling_id(issue_ref="external/issue", question="Exact question"),
    path="tests/test_contract.py",
    qualified_name="test_contract",
)
"""
    path = tmp_path / "ruling_designation_typecheck.py"

    def typecheck(text):
        path.write_text(text)
        return subprocess.run(
            [sys.executable, "-m", "mypy", "--strict", str(path)],
            capture_output=True,
            text=True,
            check=False,
        )

    accepted = typecheck(program)
    assert accepted.returncode == 0, accepted.stdout + accepted.stderr
    refused = typecheck(
        program.replace(
            'mint_ruling_id(issue_ref="external/issue", question="Exact question")',
            '"an untyped ruling address"',
        )
    )
    assert refused.returncode == 1, refused.stdout + refused.stderr
    assert 'Argument "source_ref"' in refused.stdout
    assert 'incompatible type "str"; expected "RulingId"' in refused.stdout


def amended_ruling(identity):
    """An applied amendment of the pinned record *identity* designates tests with."""
    subject = RulingSubject(id=identity)
    surface = WritableSurface(
        kind=SurfaceKind.MARKER_COMMENT,
        ref=ScopeRef(kind=ScopeKind.ISSUE, key=LANE),
        marker="[fixture-pinned:lane]",
    )
    prior = TrackerArtifact(
        surface=surface, native_ref="record-comment", content="[prior record bytes]"
    )
    return AmendedAmendment.model_validate(
        {
            **amended().model_dump(),
            "claim": {**amended().claim.model_dump(), "subject": subject.model_dump()},
            "judgment": {
                **amended().judgment.model_dump(),
                "subject": subject.model_dump(),
            },
            "prior": prior.model_dump(),
            "archive": {
                **amended().archive.model_dump(),
                "artifact": prior.model_copy(
                    update={"native_ref": "record-archive"}
                ).model_dump(),
            },
            "applied": {
                **amended().applied.model_dump(),
                "artifact": prior.model_copy(
                    update={"content": "[amended record bytes]"}
                ).model_dump(),
            },
        }
    )


def designating(identity, *addresses):
    """A record with *identity* designating each ``(path, name)`` of *addresses*."""
    data = ruling_data()
    data["ruling_id"] = identity
    data["protected_tests"] = tuple(
        RulingProtectedTestRef(source_ref=identity, path=path, qualified_name=name)
        for path, name in addresses
    )
    return Ruling.model_validate(data)


def test_designated_tests_skip_an_amended_record_and_an_undesignating_record():
    designating_record = designating("record/one", CONTRACT)
    silent = Ruling.model_validate(ruling_data())
    explicitly_none = Ruling.model_validate({**ruling_data(), "protected_tests": ()})
    roster = (designating_record, silent, explicitly_none)

    assert silent.protected_tests is None
    assert designated_tests(roster, amended=()) == designating_record.protected_tests
    assert designated_tests(roster, amended=("record/one",)) == ()
    assert designated_tests((), amended=()) == ()


def test_only_the_records_a_report_amended_are_exempt():
    first = designating("record/one", CONTRACT)
    second = designating("record/two", BOUNDARY)
    roster = (first, second)
    report = AmendmentReport(verdicts=(amended_ruling("record/one"),))
    upheld = AmendmentReport(verdicts=(record(kind="ruling", identity="record/one"),))
    criterion = AmendmentReport(verdicts=(amended(),))

    assert amended_records(report) == frozenset({"record/one"})
    assert designated_tests(roster, amended=amended_records(report)) == (
        second.protected_tests[0],
    )
    assert amended_records(upheld) == frozenset()
    assert amended_records(criterion) == frozenset()
    assert designated_tests(roster, amended=amended_records(upheld)) == (
        first.protected_tests[0],
        second.protected_tests[0],
    )

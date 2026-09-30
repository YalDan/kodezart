"""Scope addresses preserve opaque keys and reject invalid or mutable values."""

from enum import StrEnum

import pytest
from pydantic import ValidationError

from kodezart.types.domain.scope import ScopeKind, ScopeRef


def test_scope_kind_has_exactly_the_four_supported_members() -> None:
    assert issubclass(ScopeKind, StrEnum)
    assert {name: member.value for name, member in ScopeKind.__members__.items()} == {
        "INITIATIVE": "initiative",
        "PROJECT": "project",
        "MILESTONE": "milestone",
        "ISSUE": "issue",
    }


@pytest.mark.parametrize("kind", ["initiative", "project", "milestone", "issue"])
def test_each_scope_kind_round_trips_as_a_kind_and_key(kind: str) -> None:
    payload = {"kind": kind, "key": "scope-address"}

    ref = ScopeRef.model_validate(payload)

    assert ref.kind is ScopeKind(kind)
    assert ref.key == payload["key"]
    assert ref.model_dump(mode="json", by_alias=True) == payload
    assert ScopeRef.model_validate_json(ref.model_dump_json(by_alias=True)) == ref


@pytest.mark.parametrize("key", ["7", "roadmap/launch", "  opaque address  ", "λ"])
def test_keys_are_opaque_and_preserved_without_vendor_formatting(key: str) -> None:
    ref = ScopeRef(kind=ScopeKind.PROJECT, key=key)

    assert ScopeRef.model_validate_json(ref.model_dump_json()).key == key


def test_empty_key_is_rejected() -> None:
    with pytest.raises(ValidationError) as caught:
        ScopeRef(kind=ScopeKind.ISSUE, key="")

    assert [(error["loc"], error["type"]) for error in caught.value.errors()] == [
        (("key",), "string_too_short"),
    ]


@pytest.mark.parametrize("missing", ["kind", "key"])
def test_both_address_fields_are_required(missing: str) -> None:
    payload = {"kind": "issue", "key": "scope-address"}
    del payload[missing]

    with pytest.raises(ValidationError) as caught:
        ScopeRef.model_validate(payload)

    assert [(error["loc"], error["type"]) for error in caught.value.errors()] == [
        ((missing,), "missing"),
    ]


def test_unknown_scope_kind_is_rejected() -> None:
    with pytest.raises(ValidationError) as caught:
        ScopeRef.model_validate({"kind": "cycle", "key": "scope-address"})

    assert [(error["loc"], error["type"]) for error in caught.value.errors()] == [
        (("kind",), "enum"),
    ]


@pytest.mark.parametrize("extra_field", ["vendor_id", "id"])
def test_no_vendor_field_or_second_address_is_accepted(extra_field: str) -> None:
    assert set(ScopeRef.model_fields) == {"kind", "key"}

    with pytest.raises(ValidationError) as caught:
        ScopeRef.model_validate(
            {"kind": "issue", "key": "scope-address", extra_field: "other-address"},
        )

    assert [(error["loc"], error["type"]) for error in caught.value.errors()] == [
        ((extra_field,), "extra_forbidden"),
    ]


@pytest.mark.parametrize(
    ("field", "replacement"),
    [("kind", ScopeKind.PROJECT), ("key", "another-address")],
)
def test_neither_address_field_can_be_changed(field: str, replacement: str) -> None:
    ref = ScopeRef(kind=ScopeKind.ISSUE, key="scope-address")

    with pytest.raises(ValidationError) as caught:
        setattr(ref, field, replacement)

    assert [(error["loc"], error["type"]) for error in caught.value.errors()] == [
        ((field,), "frozen_instance"),
    ]
    assert ref == ScopeRef(kind=ScopeKind.ISSUE, key="scope-address")

"""Container metadata preserves domain values on the scope read boundary."""

import inspect
from collections.abc import Sequence
from typing import get_type_hints

import pytest
from pydantic import ValidationError

from kodezart.core.protocols import TrackerPort
from kodezart.types.domain.scope import ScopeContainer, ScopeKind, ScopeRef
from kodezart.types.domain.tracker import TrackerIssue


@pytest.mark.parametrize(
    ("kind", "parent"),
    [
        (ScopeKind.INITIATIVE, None),
        (ScopeKind.PROJECT, {"kind": "initiative", "key": "roadmap/λ"}),
        (ScopeKind.MILESTONE, {"kind": "project", "key": "launch"}),
    ],
)
def test_container_metadata_round_trips_without_rewriting_content(
    kind: ScopeKind,
    parent: dict[str, str] | None,
) -> None:
    payload = {
        "ref": {"kind": kind.value, "key": "container/opaque"},
        "name": "  A launch  ",
        "description": "**Outcome:** preserve this.\n\nA second paragraph.\n",
        "url": "https://tracker.example/container/opaque",
        "parent": parent,
    }

    container = ScopeContainer.model_validate(payload)

    assert container.ref == ScopeRef(kind=kind, key="container/opaque")
    assert container.parent == (
        None if parent is None else ScopeRef.model_validate(parent)
    )
    assert container.model_dump(mode="json", by_alias=True) == payload
    assert ScopeContainer.model_validate_json(container.model_dump_json()) == container


def test_a_container_without_a_parent_has_no_invented_ancestor() -> None:
    container = ScopeContainer(
        ref=ScopeRef(kind=ScopeKind.INITIATIVE, key="roadmap"),
        name="Roadmap",
        description="",
        url="https://tracker.example/roadmap",
    )

    assert container.parent is None
    assert set(container.model_dump()) == {
        "ref",
        "name",
        "description",
        "url",
        "parent",
    }


@pytest.mark.parametrize(
    "extra_field",
    ["dates", "health", "progress", "statusUpdates", "ownership", "counts"],
)
def test_container_rejects_metadata_outside_the_port_vocabulary(
    extra_field: str,
) -> None:
    with pytest.raises(ValidationError) as caught:
        ScopeContainer.model_validate(
            {
                "ref": {"kind": "project", "key": "launch"},
                "name": "Launch",
                "description": "",
                "url": "https://tracker.example/launch",
                extra_field: "unexpected metadata",
            },
        )

    assert [(error["loc"], error["type"]) for error in caught.value.errors()] == [
        ((extra_field,), "extra_forbidden"),
    ]


@pytest.mark.parametrize("field", ["ref", "parent"])
def test_container_validates_both_scope_addresses(field: str) -> None:
    payload: dict[str, object] = {
        "ref": {"kind": "project", "key": "launch"},
        "name": "Launch",
        "description": "",
        "url": "https://tracker.example/launch",
        field: {"kind": "initiative", "key": ""},
    }

    with pytest.raises(ValidationError) as caught:
        ScopeContainer.model_validate(payload)

    assert [(error["loc"], error["type"]) for error in caught.value.errors()] == [
        ((field, "key"), "string_too_short"),
    ]


def test_container_metadata_cannot_change_after_it_is_read() -> None:
    container = ScopeContainer(
        ref=ScopeRef(kind=ScopeKind.PROJECT, key="launch"),
        name="Launch",
        description="",
        url="https://tracker.example/launch",
    )

    with pytest.raises(ValidationError) as caught:
        container.name = "A different container"

    assert caught.value.errors()[0]["type"] == "frozen_instance"
    assert container.name == "Launch"


@pytest.mark.parametrize(
    ("method_name", "return_type"),
    [("scope_issues", Sequence[TrackerIssue]), ("container_metadata", ScopeContainer)],
)
def test_scope_read_contracts_are_async_keyword_only_and_domain_typed(
    method_name: str,
    return_type: object,
) -> None:
    method = getattr(TrackerPort, method_name)
    signature = inspect.signature(method)
    ref = ScopeRef(kind=ScopeKind.PROJECT, key="launch")

    assert inspect.iscoroutinefunction(method)
    assert tuple(signature.parameters) == ("self", "ref")
    assert signature.parameters["ref"].kind is inspect.Parameter.KEYWORD_ONLY
    assert signature.bind(object(), ref=ref).arguments["ref"] == ref
    assert get_type_hints(method) == {"ref": ScopeRef, "return": return_type}
    with pytest.raises(TypeError):
        signature.bind(object(), ref)

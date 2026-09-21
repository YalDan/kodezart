"""A marked model's surface set is arithmetic over reads, not a judgement."""

import pytest

from kodezart.domain.model_surfaces import (
    MODEL_CLASSIFICATION,
    member_surfaces,
    model_classification,
    model_lease_set,
)
from kodezart.types.domain.operation import OperationConfig
from kodezart.types.domain.scope import ScopeKind, ScopeRef
from kodezart.types.domain.surface import SurfaceKind, WritableSurface
from tests.fakes import make_tracker_issue

MEMBER_A = "MODEL-1"
MEMBER_B = "MODEL-2"
CRITERION_A = "MODEL-1-C1"
OUTSIDER = "OTHER-9"


def description(key: str) -> WritableSurface:
    return WritableSurface(
        kind=SurfaceKind.ISSUE_DESCRIPTION,
        ref=ScopeRef(kind=ScopeKind.ISSUE, key=key),
    )


def criterion(key: str) -> WritableSurface:
    return WritableSurface(
        kind=SurfaceKind.CRITERION_SUB_ISSUE,
        ref=ScopeRef(kind=ScopeKind.ISSUE, key=key),
    )


def two_member_model() -> frozenset[WritableSurface]:
    """The model both arithmetic cases are stated over."""
    return member_surfaces(
        members=[make_tracker_issue(MEMBER_A), make_tracker_issue(MEMBER_B)],
        criteria={MEMBER_A: [make_tracker_issue(CRITERION_A, parent_key=MEMBER_A)]},
    )


def test_a_member_contributes_its_body_and_each_criterion_its_own() -> None:
    assert two_member_model() == frozenset(
        {description(MEMBER_A), description(MEMBER_B), criterion(CRITERION_A)}
    )


def test_a_member_with_no_criteria_contributes_its_body_alone() -> None:
    assert member_surfaces(members=[make_tracker_issue(MEMBER_A)], criteria={}) == (
        frozenset({description(MEMBER_A)})
    )


def test_no_members_resolve_to_no_surfaces() -> None:
    assert member_surfaces(members=[], criteria={}) == frozenset()


@pytest.mark.parametrize(
    "requested",
    [
        pytest.param(description(MEMBER_A), id="a_member_body"),
        pytest.param(description(MEMBER_B), id="another_member_body"),
        pytest.param(criterion(CRITERION_A), id="a_member_criterion"),
    ],
)
def test_a_request_inside_the_model_takes_the_whole_model(
    requested: WritableSurface,
) -> None:
    model = two_member_model()

    assert model_lease_set(requested=frozenset({requested}), model=model) == (
        frozenset({requested}) | model
    )


def test_a_request_on_another_surface_of_a_covered_issue_is_inside_it() -> None:
    """Membership is the issue, so a second surface of a member is the model.

    A comparison over whole addresses would let a job take a marked
    member's label set while another job held that member's body, which is
    two jobs writing one member.
    """
    labels = WritableSurface(
        kind=SurfaceKind.ISSUE_LABEL_SET,
        ref=ScopeRef(kind=ScopeKind.ISSUE, key=MEMBER_A),
    )
    model = two_member_model()

    assert model_lease_set(requested=frozenset({labels}), model=model) == (
        frozenset({labels}) | model
    )


def test_a_request_outside_the_model_takes_exactly_what_it_asked_for() -> None:
    requested = frozenset({description(OUTSIDER)})

    assert model_lease_set(requested=requested, model=two_member_model()) == requested


def test_an_empty_model_leaves_every_request_as_it_was() -> None:
    requested = frozenset({description(MEMBER_A)})

    assert model_lease_set(requested=requested, model=frozenset()) == requested


@pytest.mark.parametrize(
    "issue_labels,expected",
    [
        pytest.param({MODEL_CLASSIFICATION: "model:marked"}, MODEL_CLASSIFICATION),
        pytest.param({"criterion": "acceptance-condition"}, None),
        pytest.param({}, None),
    ],
)
def test_the_classification_is_resolved_from_configuration_presence(
    issue_labels: dict[str, str], expected: str | None
) -> None:
    """A board mapping no label onto the model resolves to nothing at all."""
    operation = OperationConfig(
        operation_name="fixture", workspace="fixture", issue_labels=issue_labels
    )

    assert model_classification(operation) == expected

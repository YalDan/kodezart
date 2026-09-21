"""A marked model's surface set is resolved at acquisition, from the board."""

from collections.abc import Sequence

import pytest

from kodezart.core.protocols import TrackerPort
from kodezart.domain.errors import SurfaceLeaseError
from kodezart.domain.model_surfaces import member_surfaces
from kodezart.domain.surface_lease import surface_address
from kodezart.services.model_surface_lease import ModelSurfaceLease
from kodezart.types.domain.scope import ScopeKind, ScopeRef
from kodezart.types.domain.surface import SurfaceKind, WritableSurface
from kodezart.types.domain.tracker import TrackerIssue
from tests.model_members import CLASSIFICATION, ModelWorkspace, model_workspace

ARMS = ("native", "fake")

MEMBER_A = "MODEL-A"
MEMBER_B = "MODEL-B"
#: An issue the marked model does not cover until a case marks it, which is
#: what tells a resolution at acquisition from an answer kept from before.
MEMBER_C = "MODEL-C"
CRITERION_A = "MODEL-A-C1"
UNMARKED_X = "PLAIN-X"
UNMARKED_Y = "PLAIN-Y"

FIRST = "first-writing-job"
SECOND = "second-writing-job"
LEASE_SECONDS = 900.0

#: Every seeded body, naming no surface and no other member at all, so a
#: resolution that read one would have nothing whatever to read.
PLAIN_BODY = "prose that addresses nothing"


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


async def marked_workspace(arm: str) -> ModelWorkspace:
    """One marked model of two members, beside two issues carrying no mark.

    Both in the SAME workspace, because that is the contrast the criterion
    is about: a job over a member of the model meets the whole model, and a
    job over an issue the model does not cover meets nothing.
    """
    workspace = await model_workspace(arm)
    await workspace.seed(
        [
            {"key": MEMBER_A, "body": PLAIN_BODY, "labels": [CLASSIFICATION]},
            {"key": MEMBER_B, "body": PLAIN_BODY, "labels": [CLASSIFICATION]},
            {
                "key": CRITERION_A,
                "body": PLAIN_BODY,
                "parent": MEMBER_A,
                "labels": ["criterion"],
            },
            {"key": UNMARKED_X, "body": PLAIN_BODY},
            {"key": UNMARKED_Y, "body": PLAIN_BODY},
        ]
    )
    return workspace


def model_lease(
    tracker: TrackerPort,
    *,
    holder: str,
    surfaces: set[WritableSurface],
    classification: str | None = CLASSIFICATION,
) -> ModelSurfaceLease:
    return ModelSurfaceLease(
        reader=tracker,
        tracker=tracker,
        classification=classification,
        job_id=holder,
        surfaces=frozenset(surfaces),
        lease_seconds=LEASE_SECONDS,
    )


async def declared_model(tracker: TrackerPort) -> frozenset[WritableSurface]:
    """The model's surface set, read through the port the same way."""
    members: Sequence[TrackerIssue] = await tracker.read_labeled_issues(
        classification=CLASSIFICATION
    )
    return member_surfaces(
        members=members,
        criteria={
            member.issue_key: await tracker.read_criteria(issue_key=member.issue_key)
            for member in members
        },
    )


@pytest.mark.parametrize("arm", ARMS)
async def test_two_holders_over_one_marked_model_contend(arm: str) -> None:
    """Two members of one marked model are one thing to write, not two."""
    workspace = await marked_workspace(arm)

    async with model_lease(
        workspace.tracker, holder=FIRST, surfaces={description(MEMBER_A)}
    ):
        with pytest.raises(SurfaceLeaseError) as refused:
            async with model_lease(
                workspace.tracker, holder=SECOND, surfaces={description(MEMBER_B)}
            ):
                pass

    assert refused.value.current_holder == FIRST
    assert refused.value.scope_key in {
        surface.ref.key for surface in await declared_model(workspace.tracker)
    }


@pytest.mark.parametrize("arm", ARMS)
async def test_a_criterion_sub_issue_of_a_marked_member_is_in_the_same_model(
    arm: str,
) -> None:
    """The marked issues PLUS their criterion sub-issues is what the model is."""
    workspace = await marked_workspace(arm)

    async with model_lease(
        workspace.tracker, holder=FIRST, surfaces={description(MEMBER_A)}
    ):
        with pytest.raises(SurfaceLeaseError) as refused:
            async with model_lease(
                workspace.tracker, holder=SECOND, surfaces={criterion(CRITERION_A)}
            ):
                pass

    assert refused.value.current_holder == FIRST


@pytest.mark.parametrize("arm", ARMS)
async def test_two_holders_over_unmarked_independent_surfaces_both_acquire(
    arm: str,
) -> None:
    """A surface no marked model covers is leased as itself.

    Stated in the workspace that also holds a marked model, so the contrast
    is between marked and unmarked rather than between two unrelated issues.
    """
    workspace = await marked_workspace(arm)

    async with model_lease(
        workspace.tracker, holder=FIRST, surfaces={description(UNMARKED_X)}
    ):
        async with model_lease(
            workspace.tracker, holder=SECOND, surfaces={description(UNMARKED_Y)}
        ):
            # Neither job widened to the model, so the model is still free.
            async with model_lease(
                workspace.tracker,
                holder="third-writing-job",
                surfaces={description(MEMBER_A)},
                classification=None,
            ):
                pass


@pytest.mark.parametrize("arm", ARMS)
async def test_the_model_surface_set_is_read_and_never_parsed_from_a_body(
    arm: str,
) -> None:
    """Exactly the read model is held, and no body names a member at all.

    Each held address is probed with a request for that one surface alone,
    so what the holder took is stated surface by surface rather than read
    off an implementation's own bookkeeping.
    """
    workspace = await marked_workspace(arm)
    for key in (MEMBER_A, MEMBER_B, CRITERION_A):
        body = (await workspace.tracker.read_issue(issue_key=key)).body
        assert not any(other in body for other in (MEMBER_A, MEMBER_B, CRITERION_A))
    declared = await declared_model(workspace.tracker)
    assert declared == frozenset(
        {description(MEMBER_A), description(MEMBER_B), criterion(CRITERION_A)}
    )

    async with model_lease(
        workspace.tracker, holder=FIRST, surfaces={description(MEMBER_A)}
    ):
        for surface in sorted(declared, key=surface_address):
            with pytest.raises(SurfaceLeaseError):
                async with model_lease(
                    workspace.tracker,
                    holder=SECOND,
                    surfaces={surface},
                    classification=None,
                ):
                    pass
        async with model_lease(
            workspace.tracker,
            holder=SECOND,
            surfaces={description(UNMARKED_X)},
            classification=None,
        ):
            pass


@pytest.mark.parametrize("arm", ARMS)
async def test_a_member_marked_between_two_acquisitions_is_covered_by_the_second(
    arm: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AT acquisition: each acquisition asks the board what the model is.

    ONE lease object acquires twice with the marked membership moved in
    between, so an answer kept on the object is as visible here as an
    answer kept anywhere else — and the count says the query was issued
    again rather than remembered.  A resolution held over from the first
    acquisition would leave a member marked after it outside every later
    lease, which is a second writer acquiring a model somebody holds.
    """
    workspace = await marked_workspace(arm)
    await workspace.seed([{"key": MEMBER_C, "body": PLAIN_BODY}])
    asked: list[str] = []
    answered = workspace.tracker.read_labeled_issues

    async def counted(*, classification: str) -> Sequence[TrackerIssue]:
        asked.append(classification)
        return await answered(classification=classification)

    monkeypatch.setattr(workspace.tracker, "read_labeled_issues", counted)
    holding = model_lease(
        workspace.tracker, holder=FIRST, surfaces={description(MEMBER_A)}
    )

    async with holding:
        # Unmarked, so outside the model the first acquisition resolved.
        async with model_lease(
            workspace.tracker,
            holder=SECOND,
            surfaces={description(MEMBER_C)},
            classification=None,
        ):
            pass

    await workspace.seed(
        [{"key": MEMBER_C, "body": PLAIN_BODY, "labels": [CLASSIFICATION]}]
    )

    async with holding:
        with pytest.raises(SurfaceLeaseError) as refused:
            async with model_lease(
                workspace.tracker,
                holder=SECOND,
                surfaces={description(MEMBER_C)},
                classification=None,
            ):
                pass

    assert refused.value.current_holder == FIRST
    assert refused.value.scope_key == MEMBER_C
    assert asked == [CLASSIFICATION, CLASSIFICATION]


@pytest.mark.parametrize("arm", ARMS)
async def test_a_failed_acquisition_holds_nothing(arm: str) -> None:
    """The refused job took no part of the model, so the model frees whole."""
    workspace = await marked_workspace(arm)

    async with model_lease(
        workspace.tracker, holder=FIRST, surfaces={description(MEMBER_A)}
    ):
        with pytest.raises(SurfaceLeaseError):
            async with model_lease(
                workspace.tracker, holder=SECOND, surfaces={description(MEMBER_B)}
            ):
                pass

    async with model_lease(
        workspace.tracker, holder=SECOND, surfaces={description(MEMBER_B)}
    ):
        pass


@pytest.mark.parametrize("arm", ARMS)
async def test_nothing_unconfigured_queries_the_marker(
    arm: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A board mapping no label onto the model reads nothing and widens nothing."""
    workspace = await marked_workspace(arm)
    asked: list[str] = []
    answered = workspace.tracker.read_labeled_issues

    async def counted(*, classification: str) -> Sequence[TrackerIssue]:
        asked.append(classification)
        return await answered(classification=classification)

    monkeypatch.setattr(workspace.tracker, "read_labeled_issues", counted)

    async with model_lease(
        workspace.tracker,
        holder=FIRST,
        surfaces={description(MEMBER_A)},
        classification=None,
    ):
        # The requested surface alone was taken, so another member is free.
        async with model_lease(
            workspace.tracker,
            holder=SECOND,
            surfaces={description(MEMBER_B)},
            classification=None,
        ):
            pass

    assert asked == []


@pytest.mark.parametrize(
    "job_id,surfaces,classification",
    [
        pytest.param(" ", {description(MEMBER_A)}, CLASSIFICATION, id="blank_job"),
        pytest.param(FIRST, set(), CLASSIFICATION, id="empty_set"),
        pytest.param(FIRST, {description(MEMBER_A)}, "  ", id="blank_classification"),
    ],
)
async def test_a_refusal_is_typed_before_any_backend_call(
    job_id: str, surfaces: set[WritableSurface], classification: str | None
) -> None:
    """The refusal is at construction, before the marker query is issued."""
    workspace = await marked_workspace("native")
    calls = len(workspace.server.calls)

    with pytest.raises(ValueError):
        model_lease(
            workspace.tracker,
            holder=job_id,
            surfaces=surfaces,
            classification=classification,
        )

    assert len(workspace.server.calls) == calls

"""Staleness — the recorded base against the base the current blockers imply.

The four positive fixtures are real mutations of a fixture graph, put back
through the resolver: an edge added, an edge removed, a blocker's deliverable
ref replaced, an input ref's sha advanced.  The paired negative is what stops
staleness degenerating into "something changed" — a comment, a label and a
workflow-state transition on a blocker touch none of the inputs and leave the
base live.
"""

from collections.abc import Sequence

from kodezart.domain.base_staleness import is_base_stale
from kodezart.services.base_resolver import BaseResolver
from kodezart.types.domain.branch import BaseInput, BaseSpec, WorkRef, WorkRefRole
from kodezart.types.domain.operation import LifecycleStage, QueueState
from tests.fakes import (
    FIXTURE_EPOCH,
    FakeGitService,
    FakeTrackerPort,
    make_tracker_issue,
)

REPO_PATH = "/fixture/repo"
INTEGRATION_WORKSPACE = "/fixture/integration"
REMOTE = "fixture-remote"
CONFIGURED_TRUNK = "fixture-trunk"
LANE = "LANE-1"


def work_ref(issue_id: str, branch: str, sha: str) -> WorkRef:
    return WorkRef(
        issue_id=issue_id,
        role=WorkRefRole.DELIVERABLE,
        branch=branch,
        pushed_head_sha=sha,
        recorded_at=FIXTURE_EPOCH,
    )


def tracker_with(
    *,
    blockers: Sequence[str],
    refs: dict[str, WorkRef],
) -> FakeTrackerPort:
    return FakeTrackerPort(
        issues=[
            make_tracker_issue(LANE, blocked_by=list(blockers)),
            *[make_tracker_issue(key) for key in refs],
        ],
        recorded_work_refs={key: [ref] for key, ref in refs.items()},
    )


async def spec_of(tracker: FakeTrackerPort) -> BaseSpec:
    resolver = BaseResolver(tracker=tracker, git=FakeGitService(), remote=REMOTE)
    return await resolver.resolve(
        issue_key=LANE,
        repo_path=REPO_PATH,
        integration_workspace=INTEGRATION_WORKSPACE,
        trunk=CONFIGURED_TRUNK,
        now=FIXTURE_EPOCH,
    )


async def baseline() -> tuple[FakeTrackerPort, BaseSpec]:
    tracker = tracker_with(
        blockers=["B-1"],
        refs={"B-1": work_ref("B-1", "feature-b1", "sha-1")},
    )
    return tracker, await spec_of(tracker)


# ---------------------------------------------------------------------------
# The comparison itself
# ---------------------------------------------------------------------------


def test_the_comparison_is_a_pure_function_of_two_base_specs() -> None:
    """Called directly, with two values and nothing else."""
    one = BaseSpec(
        inputs=(BaseInput(blocker_issue_id="B-1", branch="f1", sha="s1"),),
        base_branch="f1",
        base_role=WorkRefRole.DELIVERABLE,
    )
    other = one.model_copy(
        update={
            "inputs": (BaseInput(blocker_issue_id="B-1", branch="f1", sha="s2"),),
        },
    )
    assert is_base_stale(one, one) is False
    assert is_base_stale(one, other) is True


# ---------------------------------------------------------------------------
# Four mutations, each of which moves the base
# ---------------------------------------------------------------------------


async def test_adding_a_blocker_makes_the_recorded_base_stale() -> None:
    _, recorded = await baseline()
    implied = await spec_of(
        tracker_with(
            blockers=["B-1", "B-2"],
            refs={
                "B-1": work_ref("B-1", "feature-b1", "sha-1"),
                "B-2": work_ref("B-2", "feature-b2", "sha-2"),
            },
        ),
    )
    assert is_base_stale(recorded, implied)


async def test_removing_a_blocker_makes_the_recorded_base_stale() -> None:
    recorded = await spec_of(
        tracker_with(
            blockers=["B-1", "B-2"],
            refs={
                "B-1": work_ref("B-1", "feature-b1", "sha-1"),
                "B-2": work_ref("B-2", "feature-b2", "sha-2"),
            },
        ),
    )
    _, implied = await baseline()
    assert is_base_stale(recorded, implied)


async def test_replacing_a_blockers_deliverable_ref_makes_the_base_stale() -> None:
    _, recorded = await baseline()
    implied = await spec_of(
        tracker_with(
            blockers=["B-1"],
            refs={"B-1": work_ref("B-1", "feature-b1-redone", "sha-1")},
        ),
    )
    assert is_base_stale(recorded, implied)


async def test_advancing_an_input_refs_sha_makes_the_base_stale() -> None:
    """The base branch NAME is unchanged; only the sha moved."""
    _, recorded = await baseline()
    implied = await spec_of(
        tracker_with(
            blockers=["B-1"],
            refs={"B-1": work_ref("B-1", "feature-b1", "sha-2")},
        ),
    )
    assert recorded.base_branch == implied.base_branch
    assert is_base_stale(recorded, implied)


# ---------------------------------------------------------------------------
# The paired negative
# ---------------------------------------------------------------------------


async def test_a_change_touching_none_of_the_inputs_leaves_the_base_live() -> None:
    """Without this, staleness degenerates into "something changed"."""
    tracker, recorded = await baseline()

    await tracker.post_comment(issue_key="B-1", body="an ordinary comment")
    await tracker.set_queue_state(issue_key="B-1", state=QueueState.DONE)
    await tracker.set_workflow_state(issue_key="B-1", stage=LifecycleStage.DONE)

    implied = await spec_of(tracker)
    assert is_base_stale(recorded, implied) is False

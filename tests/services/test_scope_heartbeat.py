"""The pass that turns an approval label into a scope run.

Over the real approval composition and the real submission builder, so what
is asserted is the decision a live tick makes: a label read per declared
row, this process's own memory of what it submitted, and one queue
submission. Nothing here opens a session, and the assertions say so.
"""

import pytest

from kodezart.composition.organize import build_scope_heartbeat
from kodezart.config.app import AppConfig
from kodezart.config.organize import OrganizeSettings
from kodezart.config.write_back import WriteBackSettings
from kodezart.domain.errors import ScopeReadError
from kodezart.domain.scope_submission import standing_scope_submission
from kodezart.services.scope_heartbeat import ScopeHeartbeat
from kodezart.types.domain.branch import trunk_base
from kodezart.types.domain.dispatch import PassRun
from kodezart.types.domain.job import JobState
from kodezart.types.domain.operation import (
    OperationConfig,
    OperationMemberAbsentError,
    OrganizeScopeBinding,
    ScopeLabel,
)
from kodezart.types.domain.scope import ScopeContainer, ScopeKind, ScopeRef
from kodezart.types.domain.scope_heartbeat import HeartbeatOutcome
from kodezart.types.domain.session import PermissionMode, ToolPreset
from tests.fakes import FIXTURE_EPOCH, FakeJobQueue, FakeTrackerPort
from tests.prompts.test_organize_mandate_bindings import declared_operation

#: The standing scopes: two projects under one initiative, both bound to the
#: same repository, so "one job per scope" and "one job per repository" are
#: different answers on this fixture rather than the same one.
FIRST = ScopeRef(kind=ScopeKind.PROJECT, key="first-standing-project")
SECOND = ScopeRef(kind=ScopeKind.PROJECT, key="second-standing-project")
INITIATIVE = ScopeRef(kind=ScopeKind.INITIATIVE, key="standing-initiative")

REPO = "https://example.invalid/repository"
TRUNK = "standing-trunk"
LANE = "standing-lane"


def containers() -> list[ScopeContainer]:
    """Both projects, and the initiative that contains them."""
    return [
        ScopeContainer(
            ref=INITIATIVE,
            name="standing initiative",
            description="",
            url="https://tracker.invalid/initiative/standing-initiative",
        ),
        *[
            ScopeContainer(
                ref=ref,
                name=ref.key,
                description="",
                url=f"https://tracker.invalid/project/{ref.key}",
                parent=INITIATIVE,
            )
            for ref in (FIRST, SECOND)
        ],
    ]


def board(**approvals: frozenset[ScopeLabel]) -> FakeTrackerPort:
    """The board the heartbeat reads: containers, and whatever carries a label.

    Approvals are named by their ref's key so a case states the node the
    label sits on. Nothing else is seeded: the heartbeat reads labels and a
    parent edge, and a fixture that offered it issues would let a mutant
    that reads an issue's own state look right.
    """
    seeded = {FIRST: FIRST, SECOND: SECOND, INITIATIVE: INITIATIVE}
    return FakeTrackerPort(
        scope_containers=containers(),
        scope_label_members={
            ref: approvals[name]
            for name, ref in (
                ("first", seeded[FIRST]),
                ("second", seeded[SECOND]),
                ("initiative", seeded[INITIATIVE]),
            )
            if name in approvals
        },
    )


APPROVED = frozenset({ScopeLabel.APPROVED})


def bindings(*refs: ScopeRef) -> tuple[OrganizeScopeBinding, ...]:
    return tuple(OrganizeScopeBinding(scope=ref, repo_url=REPO) for ref in refs)


def heartbeat(
    port: FakeTrackerPort,
    queue: FakeJobQueue,
    *refs: ScopeRef,
) -> ScopeHeartbeat:
    """One heartbeat over *refs*, in the order they are declared here."""
    return ScopeHeartbeat(
        approvals=port,
        queue=queue,
        registry=queue,
        bindings=bindings(*(refs or (FIRST,))),
        trunks={REPO: TRUNK},
        lane=LANE,
    )


def outcomes(report) -> list[tuple[ScopeRef, HeartbeatOutcome]]:
    return [(entry.scope, entry.outcome) for entry in report.entries]


def untouched(port: FakeTrackerPort) -> bool:
    """Whether the board is exactly as it was handed over.

    Every write journal the double keeps, together: the pass takes no lease
    and writes nothing at all (KOD-788), and a check of one journal would
    pass against a write into another.
    """
    return not any(
        (
            port.issue_writes,
            port.classification_writes,
            port.comment_writes,
            port.workflow_writes,
            port.claim_writes,
            port.lease_writes,
            port.renewals,
            port.issue_creations,
        )
    )


async def test_an_approved_standing_scope_becomes_one_scope_run() -> None:
    """The whole of what a declared row becomes when approval lands on it."""
    port = board(first=APPROVED)
    queue = FakeJobQueue()
    beat = heartbeat(port, queue)

    report = await beat.tick()

    (entry,) = report.entries
    assert entry.scope == FIRST
    assert entry.outcome is HeartbeatOutcome.SUBMITTED
    assert report.ran is True
    ((lane, request),) = queue.submissions
    assert lane == LANE
    assert entry.job_id == queue.records[entry.job_id].job_id
    # The submission is the pure builder's, byte for byte: the scope is the
    # address the run walks, the base is the bound repository's trunk, and
    # nothing about it is assembled a second way here.
    assert request == standing_scope_submission(binding=bindings(FIRST)[0], trunk=TRUNK)
    assert request.scope == FIRST
    assert request.repo_url == REPO
    assert request.base_spec == trunk_base(TRUNK)
    assert request.issue_key is None
    assert request.permission_mode is PermissionMode.UNATTENDED
    assert request.allowed_tools is ToolPreset.IMPLEMENTATION
    assert untouched(port)


async def test_an_unapproved_standing_scope_is_never_submitted() -> None:
    """The resting state of a declared row, and it is reported rather than logged."""
    port = board()
    queue = FakeJobQueue()
    beat = heartbeat(port, queue)

    report = await beat.tick()

    assert outcomes(report) == [(FIRST, HeartbeatOutcome.UNAPPROVED)]
    assert report.ran is False
    assert queue.submissions == []
    assert untouched(port)
    # And it stays unsubmitted across ticks: nothing about having been asked
    # already turns into permission.
    assert outcomes(await beat.tick()) == [(FIRST, HeartbeatOutcome.UNAPPROVED)]
    assert queue.submissions == []


async def test_approval_above_the_scope_admits_it() -> None:
    """The label cascades the way it is granted: a container above counts.

    The project carries nothing of its own; its initiative carries the
    label. A gate reading only the addressed node's own labels reports this
    row as unapproved forever.
    """
    port = board(initiative=APPROVED)
    queue = FakeJobQueue()

    report = await heartbeat(port, queue).tick()

    assert outcomes(report) == [(FIRST, HeartbeatOutcome.SUBMITTED)]
    assert len(queue.submissions) == 1


async def test_a_scope_whose_run_is_live_is_not_submitted_again() -> None:
    """A second run of a live scope would contend with itself over every lane."""
    port = board(first=APPROVED)
    queue = FakeJobQueue()
    beat = heartbeat(port, queue)

    first = await beat.tick()
    (submitted,) = first.entries
    second = await beat.tick()

    assert outcomes(second) == [(FIRST, HeartbeatOutcome.LIVE)]
    assert second.entries[0].job_id == submitted.job_id
    assert second.ran is False
    assert len(queue.submissions) == 1


async def test_a_terminal_job_frees_the_scope_for_the_next_tick() -> None:
    """The run ended, so the next tick is the next round of the same scope."""
    port = board(first=APPROVED)
    queue = FakeJobQueue()
    beat = heartbeat(port, queue)

    (submitted,) = (await beat.tick()).entries
    queue.mark(submitted.job_id, JobState.TERMINAL)
    again = await beat.tick()

    assert outcomes(again) == [(FIRST, HeartbeatOutcome.SUBMITTED)]
    assert again.entries[0].job_id != submitted.job_id
    assert len(queue.submissions) == 2


async def test_an_evicted_record_is_not_a_live_job() -> None:
    """A registry that forgot a job says nothing about a run still walking.

    Read as live, the absence would retire the scope from every later tick —
    the one state that is silent, because a retired scope and a scope
    nobody approved look the same in a log.
    """
    port = board(first=APPROVED)
    queue = FakeJobQueue()
    beat = heartbeat(port, queue)

    (submitted,) = (await beat.tick()).entries
    del queue.records[submitted.job_id]
    after = await beat.tick()

    assert outcomes(after) == [(FIRST, HeartbeatOutcome.SUBMITTED)]
    assert len(queue.submissions) == 2


async def test_a_restarted_process_submits_on_its_first_tick() -> None:
    """The map is one process's memory of one process's queue.

    A fresh heartbeat over the same board and the same approved scope
    submits, because the queue a restart inherits is empty too. An instance
    that shared the map would report the previous instance's job as live and
    the scope would never run again.
    """
    port = board(first=APPROVED)
    queue = FakeJobQueue()

    (submitted,) = (await heartbeat(port, queue).tick()).entries
    restarted = await heartbeat(port, queue).tick()

    assert outcomes(restarted) == [(FIRST, HeartbeatOutcome.SUBMITTED)]
    assert restarted.entries[0].job_id != submitted.job_id
    assert len(queue.submissions) == 2


async def test_two_scopes_on_one_repository_each_get_their_own_job() -> None:
    """The memory is keyed by the scope, and two rows may share a repository.

    Keyed by the repository, the first row's live job would stand for the
    second and the second scope would never be submitted at all.
    """
    port = board(first=APPROVED, second=APPROVED)
    queue = FakeJobQueue()

    report = await heartbeat(port, queue, FIRST, SECOND).tick()

    assert outcomes(report) == [
        (FIRST, HeartbeatOutcome.SUBMITTED),
        (SECOND, HeartbeatOutcome.SUBMITTED),
    ]
    assert len({entry.job_id for entry in report.entries}) == len(report.entries)
    assert [request.scope for _, request in queue.submissions] == [FIRST, SECOND]


async def test_every_declared_row_is_reported_in_its_declared_order() -> None:
    """Total and ordered: a row missing from the report is a row never reached."""
    port = board(second=APPROVED)
    queue = FakeJobQueue()

    report = await heartbeat(port, queue, SECOND, FIRST).tick()

    assert outcomes(report) == [
        (SECOND, HeartbeatOutcome.SUBMITTED),
        (FIRST, HeartbeatOutcome.UNAPPROVED),
    ]


def refusing(port: FakeTrackerPort, ref: ScopeRef, reason: str) -> FakeTrackerPort:
    """A board whose label read refuses for exactly one node."""
    original = port.read_scope_labels

    async def read(*, ref: ScopeRef, _refused=ref, _reason=reason):
        if ref == _refused:
            raise ScopeReadError(_reason, ref=ref)
        return await original(ref=ref)

    port.read_scope_labels = read
    return port


async def test_one_unreadable_row_does_not_starve_the_next_one() -> None:
    """The failure is named on its own line and the walk carries on.

    Aborting the loop at the first exception would make the rows after it
    hostage to the first one's readability, which is the cost the report
    exists to make visible.
    """
    port = refusing(board(second=APPROVED), FIRST, "the label read failed")
    queue = FakeJobQueue()

    report = await heartbeat(port, queue, FIRST, SECOND).tick()

    assert outcomes(report) == [
        (FIRST, HeartbeatOutcome.FAILED),
        (SECOND, HeartbeatOutcome.SUBMITTED),
    ]
    assert report.entries[0].detail is not None
    assert "the label read failed" in report.entries[0].detail
    assert [request.scope for _, request in queue.submissions] == [SECOND]


async def test_the_scheduled_tick_reraises_the_first_failure_it_contained() -> None:
    """A swallowed fault reads exactly like a board nobody has approved yet."""
    port = refusing(board(second=APPROVED), FIRST, "the label read failed")
    queue = FakeJobQueue()
    beat = heartbeat(port, queue, FIRST, SECOND)

    with pytest.raises(ScopeReadError, match="the label read failed"):
        await beat.run(FIXTURE_EPOCH)

    # Loud AND total: the row after the failing one was still submitted.
    assert [request.scope for _, request in queue.submissions] == [SECOND]


@pytest.mark.parametrize("approved", [True, False])
async def test_the_tick_answers_ran_only_when_it_started_something(
    approved: bool,
) -> None:
    """A tick that submitted nothing produced no run to record."""
    port = board(**({"first": APPROVED} if approved else {}))
    queue = FakeJobQueue()

    answer = await heartbeat(port, queue).run(FIXTURE_EPOCH)

    assert answer is (PassRun.RAN if approved else PassRun.SKIPPED)


# ---------------------------------------------------------------------------
# Composition: the same predicate the scheduled organize tick is built on
# ---------------------------------------------------------------------------


def standing_operation(*, scopes: bool) -> OperationConfig:
    """The declared operation, with or without its standing-scope rows."""
    fields = declared_operation().model_dump()
    fields["organize_scopes"] = (
        [
            {
                "scope": FIRST.model_dump(mode="json"),
                "repo_url": fields["repos"][0]["url"],
            }
        ]
        if scopes
        else []
    )
    return OperationConfig.model_validate(fields)


def owner_config(**overrides: object) -> AppConfig:
    settings: dict[str, object] = {
        "organize": OrganizeSettings(max_admission_rounds=2, max_convergence_rounds=2),
        "write_back": WriteBackSettings(max_verify_rounds=2),
    }
    settings.update(overrides)
    return AppConfig(**settings)  # type: ignore[arg-type]


def built(operation: OperationConfig, *, tracker, config=None) -> ScopeHeartbeat | None:
    queue = FakeJobQueue()
    return build_scope_heartbeat(
        config=owner_config() if config is None else config,
        operation=operation,
        tracker=tracker,
        queue=queue,
        registry=queue,
    )


async def test_no_standing_scope_declared_builds_no_heartbeat() -> None:
    """Absence is undeclared, so nothing is scheduled and nothing refuses.

    The same predicate the organize tick is built on: an operation with no
    standing rows and a deployment with no owner bounds declares no owner at
    all, and neither pass exists. A deployment that DOES configure the
    bounds and declares no row is the partial case below, which refuses.
    """
    assert (
        built(standing_operation(scopes=False), tracker=board(), config=AppConfig())
        is None
    )
    with pytest.raises(OperationMemberAbsentError, match="organize_scopes"):
        built(standing_operation(scopes=False), tracker=board())


async def test_a_declared_row_without_its_owner_configuration_refuses() -> None:
    """Partial configuration is a named refusal, not a pass that never runs."""
    with pytest.raises(OperationMemberAbsentError, match="organize"):
        built(
            standing_operation(scopes=True),
            tracker=board(),
            config=AppConfig(write_back=WriteBackSettings(max_verify_rounds=2)),
        )


async def test_a_declared_row_without_a_tracker_refuses() -> None:
    """The three reads the gate needs have to come from somewhere."""
    with pytest.raises(OperationMemberAbsentError, match="tracker"):
        built(standing_operation(scopes=True), tracker=None)


async def test_the_built_heartbeat_submits_the_declared_row_on_the_dispatch_lane() -> (
    None
):
    """The composed pass, ticked: configuration reaches the submission.

    The lane and the trunk are read off the configuration and the operation
    rather than spelled here, so a builder wiring either of them from
    somewhere else fails against the source it was supposed to use.
    """
    operation = standing_operation(scopes=True)
    repo = operation.repos[0]
    port = FakeTrackerPort(
        scope_containers=containers(),
        scope_label_members={FIRST: APPROVED},
    )
    queue = FakeJobQueue()
    config = owner_config()
    beat = build_scope_heartbeat(
        config=config,
        operation=operation,
        tracker=port,
        queue=queue,
        registry=queue,
    )
    assert beat is not None

    report = await beat.tick()

    assert outcomes(report) == [(FIRST, HeartbeatOutcome.SUBMITTED)]
    ((lane, request),) = queue.submissions
    assert lane == config.dispatch_lane
    assert request.repo_url == repo.url
    assert request.base_spec == trunk_base(repo.trunk)
    assert untouched(port)

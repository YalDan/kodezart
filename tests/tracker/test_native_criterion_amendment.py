"""Native amendment writes use actual state identity, source checks and leases."""

from datetime import timedelta

import pytest

from kodezart.core.errors import McpTransportError
from kodezart.domain.errors import (
    CriterionReadError,
    StaleWriteError,
    SurfaceLeaseError,
)
from kodezart.services.run_surface_lease import RunSurfaceLease
from kodezart.types.domain.scope import ScopeKind, ScopeRef
from kodezart.types.domain.surface import (
    DescriptionWriteAuthority,
    SurfaceKind,
    WritableSurface,
)
from kodezart.types.domain.tracker import WorkflowStateKind
from kodezart.types.domain.tracker_writes import DescriptionEditResult
from tests.fakes import FakeMcpIssue, FakeTrackerPort, make_tracker_issue
from tests.services.test_run_surface_lease import _Board
from tests.tracker.test_linear_mcp_tracker import tracker_over

KEY = "native-check"
PARENT = "fixture-parent"
HOLDER = "real-parent-job"
BODY = "**Check:** old predicate\n\n**Do:** old guidance\n\n**Evidence:**\n"


def board_and_tracker():
    board = _Board()
    board.server.issues[KEY] = FakeMcpIssue(
        id=KEY,
        parent_id=PARENT,
        labels=["acceptance-condition"],
        description=BODY,
        status="Done",
        status_type="completed",
    )
    tracker = tracker_over(
        board.server, caller=board, max_retries=2, clock=lambda: board.now
    )
    return board, tracker


def lease(tracker, kind=SurfaceKind.CRITERION_SUB_ISSUE):
    return RunSurfaceLease(
        tracker=tracker,
        job_id=HOLDER,
        lease_seconds=900,
        surfaces=frozenset(
            {
                WritableSurface(
                    kind=kind,
                    ref=ScopeRef(kind=ScopeKind.ISSUE, key=KEY),
                )
            }
        ),
    )


async def test_reset_uses_actual_native_unstarted_id_and_replays_without_write():
    board, tracker = board_and_tracker()
    expected = await tracker.read_issue(issue_key=KEY)
    async with lease(tracker):
        reset = await tracker.reset_criterion_pending(expected=expected, holder=HOLDER)
        assert reset.state_kind is WorkflowStateKind.UNSTARTED
        assert reset.body == BODY
        assert reset.issue_key == expected.issue_key
        again = await tracker.reset_criterion_pending(expected=expected, holder=HOLDER)
        assert again == reset
    saves = [args for name, args in board.calls if name == "save_issue"]
    assert saves == [{"id": KEY, "state": "fixture-team-Todo-id"}]


@pytest.mark.parametrize("action", ["reset", "description"])
@pytest.mark.parametrize(
    "drift", ["body", "parent", "class", "title", "expiry", "retry"]
)
async def test_final_boundary_and_retry_refuse_changed_native_source(
    monkeypatch,
    action,
    drift,
):
    board, tracker = board_and_tracker()
    expected = await tracker.read_issue(issue_key=KEY)
    async with lease(tracker):
        actual = board.call_tool
        reads = 0
        writes = 0
        changed = False

        async def call(*, name, arguments):
            nonlocal reads, writes, changed
            if name == "get_issue" and arguments.get("id") == KEY:
                reads += 1
                if reads == 2 and drift != "retry":
                    changed = True
                    row = board.server.issues[KEY]
                    if drift == "body":
                        row.description = BODY + "changed"
                    elif drift == "parent":
                        row.parent_id = "other-parent"
                    elif drift == "class":
                        row.labels.clear()
                    elif drift == "title":
                        row.title = "renamed between the two reads"
                    else:
                        board.now = max(
                            c.updated_at for c in board.server.comments
                        ) + timedelta(seconds=901)
            if name == "save_issue":
                writes += 1
                if drift == "retry" and writes == 1:
                    changed = True
                    board.server.issues[KEY].description = BODY + "changed on retry"
                    raise McpTransportError("known unsent", server_name="fixture")
            return await actual(name=name, arguments=arguments)

        monkeypatch.setattr(board, "call_tool", call)
        with pytest.raises((CriterionReadError, StaleWriteError, SurfaceLeaseError)):
            if action == "reset":
                await tracker.reset_criterion_pending(expected=expected, holder=HOLDER)
            else:
                await tracker.edit_description(
                    target=KEY,
                    expected=BODY,
                    replacement=BODY.replace("old predicate", "new predicate"),
                    authorization=DescriptionWriteAuthority(
                        holder=HOLDER,
                        surface=WritableSurface(
                            kind=SurfaceKind.CRITERION_SUB_ISSUE,
                            ref=ScopeRef(kind=ScopeKind.ISSUE, key=KEY),
                        ),
                    ),
                )
        assert changed
        assert writes == (1 if drift == "retry" else 0)
        assert not any(name == "save_issue" for name, _ in board.calls)


@pytest.mark.parametrize("action", ["reset", "description"])
async def test_description_surface_does_not_grant_criterion_amendment(action):
    board, tracker = board_and_tracker()
    expected = await tracker.read_issue(issue_key=KEY)
    async with lease(tracker, SurfaceKind.ISSUE_DESCRIPTION):
        with pytest.raises(SurfaceLeaseError):
            if action == "reset":
                await tracker.reset_criterion_pending(expected=expected, holder=HOLDER)
            else:
                await tracker.edit_description(
                    target=KEY,
                    expected=BODY,
                    replacement=BODY + "new",
                    authorization=DescriptionWriteAuthority(
                        holder=HOLDER,
                        surface=WritableSurface(
                            kind=SurfaceKind.CRITERION_SUB_ISSUE,
                            ref=ScopeRef(kind=ScopeKind.ISSUE, key=KEY),
                        ),
                    ),
                )
    assert not any(name == "save_issue" for name, _ in board.calls)


async def test_explicit_issue_description_authority_preserves_generic_description_use():
    board, tracker = board_and_tracker()
    board.server.issues[KEY].labels.clear()
    async with lease(tracker, SurfaceKind.ISSUE_DESCRIPTION):
        await tracker.edit_description(
            target=KEY,
            expected=BODY,
            replacement="A generic issue description.",
            authorization=DescriptionWriteAuthority(
                holder=HOLDER,
                surface=WritableSurface(
                    kind=SurfaceKind.ISSUE_DESCRIPTION,
                    ref=ScopeRef(kind=ScopeKind.ISSUE, key=KEY),
                ),
            ),
        )
    assert board.server.issues[KEY].description == "A generic issue description."
    assert board.server.issues[KEY].status == "Done"


async def test_an_authorized_edit_writes_the_description_alone_and_keeps_the_title():
    """The one field the write sends, read off the arguments that reached the board.

    The criterion's title carries the identity every consumer addresses it by,
    so a write that sent a title alongside the description would rename the row
    on the board even though the readback afterwards refused it. The sent
    arguments are asserted exactly, not counted.
    """
    board, tracker = board_and_tracker()
    title_before = board.server.issues[KEY].title
    replacement = BODY.replace("old predicate", "new predicate")

    async with lease(tracker):
        result = await tracker.edit_description(
            target=KEY,
            expected=BODY,
            replacement=replacement,
            authorization=DescriptionWriteAuthority(
                holder=HOLDER,
                surface=WritableSurface(
                    kind=SurfaceKind.CRITERION_SUB_ISSUE,
                    ref=ScopeRef(kind=ScopeKind.ISSUE, key=KEY),
                ),
            ),
        )

    assert result is DescriptionEditResult.EDITED
    assert [args for name, args in board.calls if name == "save_issue"] == [
        {"id": KEY, "description": replacement}
    ]
    assert board.server.issues[KEY].title == title_before
    assert board.server.issues[KEY].description == replacement
    # The description write moves no state: the row stays where it was.
    assert board.server.issues[KEY].status == "Done"


async def test_description_authority_target_mismatch_refuses_before_any_backend_call():
    board, tracker = board_and_tracker()
    before = list(board.calls)
    with pytest.raises(ValueError, match="target"):
        await tracker.edit_description(
            target=KEY,
            expected=BODY,
            replacement="Different.",
            authorization=DescriptionWriteAuthority(
                holder=HOLDER,
                surface=WritableSurface(
                    kind=SurfaceKind.ISSUE_DESCRIPTION,
                    ref=ScopeRef(kind=ScopeKind.ISSUE, key="another"),
                ),
            ),
        )
    assert board.calls == before


@pytest.mark.parametrize(
    "holder,kind",
    [(" ", SurfaceKind.ISSUE_DESCRIPTION), (HOLDER, SurfaceKind.ISSUE_LABEL_SET)],
)
def test_description_authority_has_only_two_real_description_surfaces(holder, kind):
    with pytest.raises(ValueError):
        DescriptionWriteAuthority(
            holder=holder,
            surface=WritableSurface(
                kind=kind, ref=ScopeRef(kind=ScopeKind.ISSUE, key=KEY)
            ),
        )


@pytest.mark.parametrize("action", ["reset", "description"])
async def test_known_unsent_native_write_retries_only_the_fresh_authorized_attempt(
    monkeypatch, action
):
    board, tracker = board_and_tracker()
    expected = await tracker.read_issue(issue_key=KEY)
    actual = board.call_tool
    attempts = 0

    async def call(*, name, arguments):
        nonlocal attempts
        if name == "save_issue":
            attempts += 1
            if attempts == 1:
                raise McpTransportError("known unsent", server_name="fixture")
        return await actual(name=name, arguments=arguments)

    async with lease(tracker):
        monkeypatch.setattr(board, "call_tool", call)
        if action == "reset":
            assert (
                await tracker.reset_criterion_pending(expected=expected, holder=HOLDER)
            ).state_kind is WorkflowStateKind.UNSTARTED
        else:
            await tracker.edit_description(
                target=KEY,
                expected=BODY,
                replacement=BODY.replace("old predicate", "new predicate"),
                authorization=DescriptionWriteAuthority(
                    holder=HOLDER,
                    surface=WritableSurface(
                        kind=SurfaceKind.CRITERION_SUB_ISSUE,
                        ref=ScopeRef(kind=ScopeKind.ISSUE, key=KEY),
                    ),
                ),
            )
    assert attempts == 2
    assert sum(name == "save_issue" for name, _ in board.calls) == 1


async def test_generic_description_grant_cannot_write_a_native_criterion_body():
    board, tracker = board_and_tracker()
    async with lease(tracker, SurfaceKind.ISSUE_DESCRIPTION):
        with pytest.raises(ValueError, match="current native surface"):
            await tracker.edit_description(
                target=KEY,
                expected=BODY,
                replacement=BODY + "unauthorized",
                authorization=DescriptionWriteAuthority(
                    holder=HOLDER,
                    surface=WritableSurface(
                        kind=SurfaceKind.ISSUE_DESCRIPTION,
                        ref=ScopeRef(kind=ScopeKind.ISSUE, key=KEY),
                    ),
                ),
            )
    assert board.server.issues[KEY].description == BODY
    assert not any(name == "save_issue" for name, _ in board.calls)


async def test_an_unleased_reset_moves_the_criterion_and_reads_no_lease_marker():
    """The lane's own move-back consults no grant, and asks the board for none.

    The regression write is the single writer's own act over its own lane,
    so an absent holder is that write rather than an unheld one: the state
    moves and no ownership marker is read at all. Read as an unheld write it
    would refuse, and a criterion this fire broke would stay finished.
    """
    board, tracker = board_and_tracker()
    expected = await tracker.read_issue(issue_key=KEY)
    asked = len(board.calls)

    reset = await tracker.reset_criterion_pending(expected=expected, holder=None)

    assert reset.state_kind is WorkflowStateKind.UNSTARTED
    assert [args for name, args in board.calls if name == "save_issue"] == [
        {"id": KEY, "state": "fixture-team-Todo-id"}
    ]
    assert not [name for name, _ in board.calls[asked:] if name == "list_comments"]


@pytest.mark.parametrize(
    "holder", [pytest.param("", id="blank"), pytest.param("another-job", id="foreign")]
)
async def test_a_supplied_holder_nobody_granted_refuses_the_reset(holder):
    """A holder that was supplied is a holder, and it has to hold the surface."""
    board, tracker = board_and_tracker()
    expected = await tracker.read_issue(issue_key=KEY)

    with pytest.raises(SurfaceLeaseError):
        await tracker.reset_criterion_pending(expected=expected, holder=holder)

    assert not any(name == "save_issue" for name, _ in board.calls)
    assert (
        await tracker.read_issue(issue_key=KEY)
    ).state_kind is WorkflowStateKind.COMPLETED


def fake_board() -> FakeTrackerPort:
    """The same finished criterion sub-issue, on the in-process double."""
    return FakeTrackerPort(
        issues=[
            make_tracker_issue(PARENT),
            make_tracker_issue(
                KEY,
                parent_key=PARENT,
                issue_labels=frozenset({"criterion"}),
                body=BODY,
                state_name="Done",
                state_kind=WorkflowStateKind.COMPLETED,
            ),
        ]
    )


@pytest.mark.parametrize(
    "holder", [pytest.param("", id="blank"), pytest.param("another-job", id="foreign")]
)
async def test_the_double_refuses_a_supplied_holder_nobody_granted_too(holder):
    """The double refuses what the adapter refuses, or it proves nothing.

    Every consumer test of the regression path drives the double, so a
    double that took any supplied holder would let a write the deployed
    adapter refuses read as one it takes.
    """
    port = fake_board()
    expected = await port.read_issue(issue_key=KEY)

    with pytest.raises(SurfaceLeaseError):
        await port.reset_criterion_pending(expected=expected, holder=holder)

    assert port.issues[KEY].state_kind is WorkflowStateKind.COMPLETED


async def test_the_double_takes_the_unleased_move_back_the_adapter_takes():
    port = fake_board()
    expected = await port.read_issue(issue_key=KEY)

    reset = await port.reset_criterion_pending(expected=expected, holder=None)

    assert reset.state_kind is WorkflowStateKind.UNSTARTED
    assert port.issues[KEY].state_kind is WorkflowStateKind.UNSTARTED

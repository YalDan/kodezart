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
from tests.fakes import FakeMcpIssue
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


@pytest.mark.parametrize(
    "expected",
    [
        pytest.param(BODY, id="identical"),
        pytest.param(
            "**Check:** the cleared prior\n\n**Do:** old guidance\n\n**Evidence:**\n",
            id="already_present",
        ),
    ],
)
async def test_an_authorized_replay_of_the_same_bytes_is_unchanged_and_sends_no_write(
    expected,
):
    """The authorized no-op is a no-op at the board, not a second identical write.

    Both arms of the replacement's no-op are here: the request that asks for
    the bytes it names, and the request whose bytes the row already carries
    after an earlier round landed them. Either one returning EDITED would send
    a second edit of bytes already on the board.
    """
    board, tracker = board_and_tracker()

    async with lease(tracker):
        result = await tracker.edit_description(
            target=KEY,
            expected=expected,
            replacement=BODY,
            authorization=DescriptionWriteAuthority(
                holder=HOLDER,
                surface=WritableSurface(
                    kind=SurfaceKind.CRITERION_SUB_ISSUE,
                    ref=ScopeRef(kind=ScopeKind.ISSUE, key=KEY),
                ),
            ),
        )

    assert result is DescriptionEditResult.UNCHANGED
    assert not any(name == "save_issue" for name, _ in board.calls)
    assert board.server.issues[KEY].description == BODY


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

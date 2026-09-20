"""The container half of the approval walk, over explicit answers."""

import pytest

from kodezart.domain.errors import ScopeReadError
from kodezart.domain.scope_approval import resolve_container_approval
from kodezart.types.domain.scope import ScopeKind, ScopeRef

INITIATIVE = ScopeRef(kind=ScopeKind.INITIATIVE, key="initiative-one")
PROJECT = ScopeRef(kind=ScopeKind.PROJECT, key="project-one")
MILESTONE = ScopeRef(kind=ScopeKind.MILESTONE, key="milestone-one")
ISSUE = ScopeRef(kind=ScopeKind.ISSUE, key="FIX-1")


def _chain(
    answers: dict[ScopeRef, tuple[bool, ScopeRef | None]],
    log: list[ScopeRef],
):
    async def read_container(ref: ScopeRef) -> tuple[bool, ScopeRef | None]:
        log.append(ref)
        return answers[ref]

    return read_container


async def test_a_milestone_node_carries_no_approval_in_the_chain_walk() -> None:
    """A milestone reported as approved is still answered by its project."""
    log: list[ScopeRef] = []
    answers = {
        MILESTONE: (True, PROJECT),
        PROJECT: (False, INITIATIVE),
        INITIATIVE: (False, None),
    }

    assert (
        await resolve_container_approval(
            ref=MILESTONE, read_container=_chain(answers, log)
        )
        is False
    )
    assert log == [MILESTONE, PROJECT, INITIATIVE]

    answers[PROJECT] = (True, INITIATIVE)
    assert (
        await resolve_container_approval(
            ref=MILESTONE, read_container=_chain(answers, [])
        )
        is True
    )


async def test_a_milestone_without_an_owning_project_refuses() -> None:
    for owner in (None, INITIATIVE):
        with pytest.raises(ScopeReadError, match="milestone has no owning project"):
            await resolve_container_approval(
                ref=MILESTONE,
                read_container=_chain({MILESTONE: (False, owner)}, []),
            )


async def test_an_issue_ref_is_not_an_approval_container() -> None:
    with pytest.raises(ScopeReadError, match="not an approval container"):
        await resolve_container_approval(ref=ISSUE, read_container=_chain({}, []))


async def test_a_container_parent_cycle_refuses_instead_of_answering_false() -> None:
    answers = {PROJECT: (False, INITIATIVE), INITIATIVE: (False, INITIATIVE)}
    with pytest.raises(ScopeReadError, match="container parent cycle"):
        await resolve_container_approval(
            ref=PROJECT, read_container=_chain(answers, [])
        )


async def test_a_non_initiative_container_parent_refuses() -> None:
    answers = {PROJECT: (False, MILESTONE)}
    with pytest.raises(ScopeReadError, match="invalid approval container parent"):
        await resolve_container_approval(
            ref=PROJECT, read_container=_chain(answers, [])
        )


async def test_the_callers_seen_set_continues_one_walk() -> None:
    """A node the issue half already walked is a cycle, not a second visit."""
    answers = {PROJECT: (False, INITIATIVE), INITIATIVE: (False, None)}
    with pytest.raises(ScopeReadError, match="container parent cycle"):
        await resolve_container_approval(
            ref=PROJECT, read_container=_chain(answers, []), seen={PROJECT}
        )

"""Base resolution — one rule, total, with no branch on the number of blockers.

Pure: no I/O.  Every git answer arrives as a resolved value, the discipline
``kodezart.domain.ticket`` already holds.  The caller reads the ``blockedBy``
edges through the tracker port, resolves each blocker to its deliverable ref,
asks the git port which refs contain which, and hands the answers here.

Four steps, in order:

1. **Resolve** — done by the caller (D1's ancestor resolution).
2. **Dedupe** — to distinct refs.  Several blockers riding one pull request
   contribute one ref.
3. **Reduce to the frontier** — drop every ref that is an ancestor of another
   ref in the set.  Reduction is over COMMITS, never over the shape of the
   graph: an edge whose premise is already contained in another blocker's
   branch contributes nothing, and only the commits can say so.
4. **Combine** — three arms, total, no default arm.

The degenerate case is degenerate because the FRONTIER came back holding one
element, not because anything asked how many blockers there were.  A lane
with one blocker and a lane whose three blockers form a chain traverse
exactly the same code; there is no single-blocker shortcut here and adding
one is what ``test_base_resolution.py``'s call-path assertion detects.
"""

from collections.abc import Sequence

from pydantic import ConfigDict

from kodezart.types.base import CamelCaseModel
from kodezart.types.domain.branch import (
    BaseInput,
    BaseSpec,
    IntegrationBranchName,
    WorkRefRole,
)


class BasePlan(CamelCaseModel):
    """The resolved base plus the merge work, if any, that realises it.

    ``merge_inputs`` is the ordered tail the caller merges into a branch cut
    from ``branch_point``.  Both are empty/``None`` on the trunk and
    singleton arms, where the base already exists and is used unchanged and
    unbuilt.
    """

    model_config = ConfigDict(frozen=True)

    spec: BaseSpec
    branch_point: str | None = None
    merge_inputs: tuple[str, ...] = ()

    def requires_construction(self) -> bool:
        """Whether the base named by :attr:`spec` still has to be built."""
        return self.branch_point is not None


def resolve_base(
    *,
    issue_id: str,
    blocker_inputs: Sequence[BaseInput],
    containment: Sequence[tuple[str, str]],
    trunk: str,
) -> BasePlan:
    """The base for *issue_id*, given its blockers' resolved deliverable refs.

    *containment* holds ``(contained_branch, containing_branch)`` pairs the
    git port answered — one entry per ordered pair where the first ref is an
    ancestor of the second.  *trunk* is the scope's configured trunk; it is
    used on exactly one arm and is never a fallback for the others.
    """
    ordered = _deduped_in_deterministic_order(blocker_inputs)
    frontier = _frontier(ordered, frozenset(containment))
    return _combine(issue_id=issue_id, frontier=frontier, trunk=trunk)


def _deduped_in_deterministic_order(
    blocker_inputs: Sequence[BaseInput],
) -> tuple[BaseInput, ...]:
    """Distinct refs, ordered ascending by the issue id carrying each.

    Total, stable across runs, independent of tracker read order and of any
    lane ordering computed elsewhere — so two runs over one graph produce
    one base.
    """
    by_branch: dict[str, BaseInput] = {}
    for item in sorted(blocker_inputs, key=lambda i: i.blocker_issue_id):
        by_branch.setdefault(item.branch, item)
    return tuple(
        sorted(by_branch.values(), key=lambda i: i.blocker_issue_id),
    )


def _frontier(
    ordered: Sequence[BaseInput],
    containment: frozenset[tuple[str, str]],
) -> tuple[BaseInput, ...]:
    """Every ref not already contained in another ref of the set."""
    return tuple(
        item
        for item in ordered
        if not any(
            other.branch != item.branch and (item.branch, other.branch) in containment
            for other in ordered
        )
    )


def _combine(
    *,
    issue_id: str,
    frontier: Sequence[BaseInput],
    trunk: str,
) -> BasePlan:
    """The three arms. Total over the frontier, with no default arm.

    Written by destructuring rather than by counting: the arms are "no
    ref", "one ref and nothing after it" and "a ref with a tail", so no
    size is ever compared against a number and nothing here can be
    mistaken for a threshold.
    """
    inputs = tuple(frontier)
    if not inputs:
        return BasePlan(
            spec=BaseSpec(inputs=(), base_branch=trunk, base_role=None),
        )
    head, *tail = inputs
    if not tail:
        return BasePlan(
            spec=BaseSpec(
                inputs=inputs,
                base_branch=head.branch,
                base_role=WorkRefRole.DELIVERABLE,
            ),
        )
    name = IntegrationBranchName(issue_id=issue_id, inputs=inputs)
    return BasePlan(
        spec=BaseSpec(
            inputs=inputs,
            base_branch=str(name),
            base_role=WorkRefRole.INTEGRATION,
        ),
        branch_point=head.branch,
        merge_inputs=tuple(item.branch for item in tail),
    )

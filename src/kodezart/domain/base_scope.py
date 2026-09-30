"""The baseline every scope surface compares against, and its refusal.

One function answers *what do we diff against*, and every scope surface
calls it: the iteration changeset digest, the outer review diff, and any
generated scope criterion.  No surface ever hardcoded a trunk — the defect
was that each took whatever base it was handed and nothing established
what that base should be.
"""

from collections.abc import Sequence

from kodezart.domain.base_staleness import is_base_stale
from kodezart.domain.errors import StaleBaseError
from kodezart.types.domain.branch import BaseInput, BaseSpec


def _named(inputs: Sequence[BaseInput]) -> list[str]:
    return [f"{item.blocker_issue_id}@{item.branch}:{item.sha}" for item in inputs]


def changed_inputs(recorded: BaseSpec, implied: BaseSpec) -> list[str]:
    """The inputs present in exactly one of the two specs, named.

    Symmetric difference over the ordered tuples, so an edge added or
    removed, a ref replaced and a sha advanced all surface here.
    """
    before = _named(recorded.inputs)
    after = _named(implied.inputs)
    return [item for item in before if item not in after] + [
        item for item in after if item not in before
    ]


def scope_base(recorded: BaseSpec, implied: BaseSpec | None) -> str:
    """The ref the scope check compares against. Raises when it has moved.

    *implied* is the base the lane's blockers imply RIGHT NOW, or ``None``
    when there is no association to recompute from.  Whether the two are
    the same base is not asked here: it is
    :func:`kodezart.domain.base_staleness.is_base_stale` that answers it,
    so this surface and every other reader of that answer are reading one
    comparison and cannot come to disagree about what a moved base is.
    """
    if implied is not None and is_base_stale(recorded, implied):
        msg = (
            "The lane's recorded base is not the base its blockers imply, "
            "so no scope verdict may be computed against it"
        )
        raise StaleBaseError(
            msg,
            recorded_ref=recorded.base_branch,
            implied_ref=implied.base_branch,
            changed_inputs=changed_inputs(recorded, implied),
        )
    return recorded.base_branch

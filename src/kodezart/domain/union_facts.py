"""State one scope-grain composition as the facts one line carries."""

from kodezart.types.domain.union import UnionCompositionResult


def union_facts(observed: UnionCompositionResult) -> dict[str, object]:
    """What a measured composition said, as plain values a reader can compare.

    The red/green reading lives here and not at the call site: the walk is
    scanned whole for every spelling of a fire's ending, and a composition's
    own verdict is spelled the same way (KOD-725).  Reading it in a function
    the walk calls keeps the two questions apart in code as well as in prose.

    Every lane is named once, with the head it was measured at, in the order
    the composition took them; a green composition states no remediation and
    a red one states the detail of its single entry.
    """
    return {
        "composition": observed.outcome.value,
        "lanes": len(observed.lane_heads),
        "heads": tuple(
            f"{head.lane_key}:{head.head_sha}" for head in observed.lane_heads
        ),
        "scratch_sha": observed.scratch_sha,
        "remediation": (
            None if observed.remediation is None else observed.remediation.detail
        ),
    }

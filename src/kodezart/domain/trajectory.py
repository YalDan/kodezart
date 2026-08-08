"""Pure fold over a loop's iteration records — no I/O, no model calls."""

from kodezart.types.domain.trajectory import IterationRecord, LoopTrajectory


def fold_trajectory(
    records: list[IterationRecord],
    *,
    plateau_window: int,
) -> LoopTrajectory:
    """Fold *records* into a ``LoopTrajectory``.

    ``never_passed_ids`` are the criteria that failed in every recorded
    iteration, in the order they first appeared.  Plateau recognition is
    arithmetic over the window *w*: ``best_before`` is the best
    ``passed_count`` over ``records[:-w]`` and the trajectory has
    plateaued when there are more than *w* records and none of the last
    *w* beat ``best_before``.
    """
    if not records:
        return LoopTrajectory(
            records=[],
            never_passed_ids=[],
            best_passed_count=0,
            best_iteration=0,
            best_commit_sha=None,
            plateaued=False,
        )

    always_failing: set[str] = set(records[0].failing_criterion_ids)
    for record in records[1:]:
        always_failing &= set(record.failing_criterion_ids)
    never_passed_ids = [
        criterion
        for criterion in records[0].failing_criterion_ids
        if criterion in always_failing
    ]

    best = max(records, key=lambda r: r.passed_count)
    best_record = next(r for r in records if r.passed_count == best.passed_count)

    plateaued = False
    if len(records) > plateau_window:
        best_before = max(r.passed_count for r in records[:-plateau_window])
        plateaued = all(
            r.passed_count <= best_before for r in records[-plateau_window:]
        )

    return LoopTrajectory(
        records=list(records),
        never_passed_ids=never_passed_ids,
        best_passed_count=best_record.passed_count,
        best_iteration=best_record.iteration,
        best_commit_sha=best_record.commit_sha,
        plateaued=plateaued,
    )

"""What one evaluation of a scope run owes the board, as comment bodies."""

from kodezart.types.domain.agent import AcceptanceCriteriaOutput


def rulings_to_record(
    previous: AcceptanceCriteriaOutput | None,
    current: AcceptanceCriteriaOutput,
    *,
    iteration: int,
) -> tuple[list[tuple[str, str]], list[str]]:
    """The comments *current* owes, by criterion key, and its flags on none.

    A ruling is owed when its verdict or its reasoning differs from the one
    *previous* gave the same criterion, so an unchanged ruling is not
    repeated; with no previous evaluation every ruling is owed. Each flag
    on a criterion is owed on that criterion, and a flag on none is handed
    back for the iteration's summary.
    """
    before = (
        {}
        if previous is None
        else {
            result.criterion_id: (result.passed, result.reasoning)
            for result in previous.criteria_results
        }
    )
    comments = [
        (
            str(result.criterion_id),
            f"Iteration {iteration} ruling: "
            f"{'PASS' if result.passed else 'FAIL'} — {result.reasoning}",
        )
        for result in current.criteria_results
        if before.get(result.criterion_id) != (result.passed, result.reasoning)
    ]
    comments += [
        (str(flag.criterion_id), f"Iteration {iteration} flag: {flag.concern}")
        for flag in current.sherlock_flags
        if flag.criterion_id is not None
    ]
    unplaced = [
        flag.concern for flag in current.sherlock_flags if flag.criterion_id is None
    ]
    return comments, unplaced

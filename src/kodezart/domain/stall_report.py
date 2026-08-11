"""The note a non-convergent run carries into its pull request.

Facts only.  Which criteria never passed, how the pass count moved, and
which iteration held the best state — nothing about what to do next.
Whether the run is worth continuing, why a criterion never passed, and
what the remedy is are the reviewing team's judgment, so this reports and
stops.  The separation is the point, not a stylistic preference: a note
that also prescribed would be read as a plan, and this file has no
standing to write one.

Rendered from the trajectory and the criteria set, both already in hand.
No model call: every number here is arithmetic over data the loop
produced, so the note cannot disagree with the run it describes.
"""

from collections.abc import Sequence

from kodezart.types.domain.criteria import CriterionId, ValidatedCriterion
from kodezart.types.domain.trajectory import LoopTrajectory

DO_NOT_MERGE_PREFIX = "[do-not-merge]"
NOT_CONVERGED_HEADING = "## This run did not converge"


def stall_pr_title(ticket_title: str) -> str:
    """Prefix *ticket_title* so a reader sees the disposition first.

    The prefix is for humans reading a list.  It is NOT the machine
    marker — that is the terminal outcome, which a tool can check
    without parsing prose.
    """
    return f"{DO_NOT_MERGE_PREFIX} {ticket_title}"


def stall_pr_body(
    trajectory: LoopTrajectory,
    criteria: Sequence[ValidatedCriterion],
    *,
    landed_commit: str,
) -> str:
    """Render the factual note for a run that stopped short.

    ``landed_commit`` is the commit this pull request's head carries —
    stated explicitly because it is the BEST iteration's, which a reader
    would otherwise reasonably assume to be the last.
    """
    total = len(criteria)
    text_by_id = {criterion.id: criterion.text for criterion in criteria}
    sections = [
        NOT_CONVERGED_HEADING,
        (
            "The loop stopped before every acceptance criterion passed. "
            "The head of this pull request is the run's BEST iteration, "
            "not its last — a run can finish below its own peak."
        ),
        "\n".join(
            [
                "| fact | value |",
                "| --- | --- |",
                f"| best pass count | {trajectory.best_passed_count} of {total} |",
                f"| best iteration | {trajectory.best_iteration} |",
                f"| iterations run | {len(trajectory.records)} |",
                f"| head commit | `{landed_commit}` |",
            ]
        ),
        _never_passed_section(trajectory, text_by_id),
        _pass_count_section(trajectory),
    ]
    return "\n\n".join(sections)


def _never_passed_section(
    trajectory: LoopTrajectory,
    text_by_id: dict[CriterionId, str],
) -> str:
    if not trajectory.never_passed_ids:
        return (
            "### Criteria that passed in no iteration\n\n"
            "None — every criterion passed in at least one iteration, "
            "though not all in the same one."
        )
    lines = [
        f"- {criterion_id}: {text_by_id[criterion_id]}"
        if criterion_id in text_by_id
        else f"- {criterion_id}"
        for criterion_id in trajectory.never_passed_ids
    ]
    return "\n".join(["### Criteria that passed in no iteration", "", *lines])


def _pass_count_section(trajectory: LoopTrajectory) -> str:
    rows = [
        f"| {record.iteration} | {record.passed_count} | "
        f"{f'`{record.commit_sha}`' if record.commit_sha is not None else '—'} |"
        for record in trajectory.records
    ]
    return "\n".join(
        [
            "### Pass count by iteration",
            "",
            "| iteration | passed | commit |",
            "| --- | --- | --- |",
            *rows,
        ]
    )

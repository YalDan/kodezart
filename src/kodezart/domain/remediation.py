"""What a remediation session is told about the work already done.

Pure rendering over the request — no I/O and no model call, so the
summary cannot describe a run other than the one it was built from.

It reports what EXISTS, never what to do about it: deciding the remedy is
the remediation session's whole job, and a summary that pre-empted it
would be answering the question it was supposed to ask.
"""

from kodezart.types.domain.workflow import RemediationRequest

NO_TRAJECTORY = "The loop recorded no iterations."


def done_work_summary(request: RemediationRequest) -> str:
    """Render the state of the work this round is built on top of."""
    lines = [
        f"- Branch carrying the work: `{request.work_branch}`",
        f"- This round is based on: `{request.work_base_ref}`",
        f"- Iterations spent so far: {request.total_iterations}",
    ]
    if request.pr_url is not None:
        lines.append(f"- Open pull request: {request.pr_url}")
    return "\n\n".join(
        [
            "\n".join(lines),
            _criteria_section(request),
            _trajectory_section(request),
        ]
    )


def _criteria_section(request: RemediationRequest) -> str:
    rows = [f"| {criterion.id} | {criterion.text} |" for criterion in request.criteria]
    return "\n".join(
        [
            "### Criteria the work was graded against",
            "",
            "| id | criterion |",
            "| --- | --- |",
            *rows,
        ]
    )


def _trajectory_section(request: RemediationRequest) -> str:
    trajectory = request.trajectory
    if trajectory is None or not trajectory.records:
        return f"### How the work progressed\n\n{NO_TRAJECTORY}"
    rows = [
        f"| {record.iteration} | {record.passed_count} | "
        f"{', '.join(record.failing_criterion_ids) or '—'} |"
        for record in trajectory.records
    ]
    never_passed = ", ".join(trajectory.never_passed_ids) or "—"
    return "\n".join(
        [
            "### How the work progressed",
            "",
            "| iteration | passed | still failing |",
            "| --- | --- | --- |",
            *rows,
            "",
            f"Passed in no iteration: {never_passed}",
            f"Best pass count: {trajectory.best_passed_count} "
            f"at iteration {trajectory.best_iteration}",
        ]
    )

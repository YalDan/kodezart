"""Per-call render variables derived from domain values — pure, no I/O."""

from collections.abc import Sequence

from kodezart.types.domain.consolidation import ChangesetDigest
from kodezart.types.domain.criteria import (
    ExecutionCriterion,
    TrackerCriterionSet,
    ValidatedCriterion,
)
from kodezart.types.domain.organize import AdmissionResult


def changeset_variables(changeset: ChangesetDigest) -> dict[str, object]:
    """Render variables for the changeset section of an evaluation template.

    The empty / non-empty split is expressed as PRESENCE of a variable,
    matching the renderer's ``{{#if}}``: a name is bound only when its
    section applies.
    """
    variables: dict[str, object] = {
        "commit_count": changeset.commit_count,
        "file_paths": changeset.file_paths,
        "commit_subjects": changeset.commit_subjects,
    }
    if changeset.is_empty:
        variables["changeset_is_empty"] = True
    else:
        variables["changeset_has_commits"] = True
    if not changeset.file_paths:
        variables["file_paths_absent"] = True
    return variables


def organize_variables(
    *,
    mandate_rubric: str,
    issue_body: str,
    linked_issue_bodies: Sequence[str],
    criterion_issue_bodies: Sequence[str],
    refusal_evidence: AdmissionResult | None,
    defect_classes: Sequence[str],
) -> dict[str, object]:
    """Bind one dispatch's source material without retaining another call's values.

    Refusal evidence is the admission result, never an author's rationale.
    The read-only role templates consume source bodies and class evidence;
    only authoring templates read the optional refusal for a repair round.
    The selected repository base uses the existing caller-owned base_ref binding.
    """
    return {
        "mandate_rubric": mandate_rubric,
        "issue_body": issue_body,
        "linked_issue_bodies": tuple(linked_issue_bodies),
        "criterion_issue_bodies": tuple(criterion_issue_bodies),
        "refusal_evidence": (
            None
            if refusal_evidence is None
            else refusal_evidence.model_dump_json(by_alias=False)
        ),
        "defect_classes": tuple(defect_classes),
    }


def execution_criteria_variables(
    criteria: Sequence[ExecutionCriterion],
) -> dict[str, object]:
    """Render each entry source without claiming a sweep on tracker Checks."""
    return {
        "criteria": list(criteria),
        "swept_criteria": True
        if any(isinstance(c, ValidatedCriterion) for c in criteria)
        else None,
        "tracker_criteria": True
        if any(not isinstance(c, ValidatedCriterion) for c in criteria)
        else None,
    }


def tracker_checks_section(snapshot: TrackerCriterionSet) -> str:
    """Render current native obligations separately from historical evidence."""
    return "## Current tracker Checks\n" + "\n\n".join(
        f"### {criterion.id}\n{criterion.text}" for criterion in snapshot.criteria
    )

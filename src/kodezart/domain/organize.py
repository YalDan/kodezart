"""Admission actions computed from verdict data before any tracker write."""

from collections.abc import Mapping, Sequence

from kodezart.domain.dispatch import blocker_keys
from kodezart.types.domain.organize import (
    AdmissionResult,
    AdmissionRoute,
    AdmissionVerdict,
    RefusalKind,
    RefusedAdmission,
    ResolvedMandateSpec,
    SpecFinding,
    UnverifiableAdmission,
)
from kodezart.types.domain.tracker import (
    TrackerIssue,
    TrackerIssueRevision,
    WorkflowStateKind,
)


def is_admission_live(*, admitted_body_digest: str, current_body_digest: str) -> bool:
    """A judgment applies only to the exact body revision it examined.

    Digests are opaque; this predicate neither normalizes them nor fills a
    missing one. Reading liveness never changes the original judgment.
    """
    if not admitted_body_digest.strip() or not current_body_digest.strip():
        raise ValueError("admission liveness requires both nonempty body digests")
    return admitted_body_digest == current_body_digest


def admission_route(
    result: AdmissionResult,
    *,
    issue: TrackerIssue,
    scope_issue_keys: frozenset[str],
) -> AdmissionRoute:
    """Choose the next action without judging refusal prose or changing verdicts.

    Only an explicitly classified human decision escalates at admission.
    A spec gap can be re-authored. An unverifiable result can advance only
    when its named blocker is both a real ``blockedBy`` edge and inside
    the scope. Parentage, reverse edges and prose supply neither fact.

    The caller supplies the port's current issue and resolved scope members.
    This function preserves the three-state verdict and only returns an
    action: it does not set phase markers, write escalations, or re-author.
    """
    if result.issue_id != issue.issue_key:
        raise ValueError(
            f"admission issue {result.issue_id} does not match tracker issue "
            f"{issue.issue_key}"
        )
    if result.verdict is AdmissionVerdict.BUILDABLE:
        return AdmissionRoute.MARK_COMPLETE
    if isinstance(result.root, UnverifiableAdmission):
        blocker = result.root.pending_blocker_id
        if blocker in scope_issue_keys and blocker in blocker_keys(issue):
            return AdmissionRoute.MARK_COMPLETE
        return AdmissionRoute.REAUTHOR
    if (
        isinstance(result.root, RefusedAdmission)
        and result.root.refusal_kind is RefusalKind.HUMAN_DECISION
    ):
        return AdmissionRoute.ESCALATE
    return AdmissionRoute.REAUTHOR


def stage_rows(
    rows: Sequence[ResolvedMandateSpec], *, under_approval: bool
) -> tuple[ResolvedMandateSpec, ...]:
    """The rows of one organize table that run on one side of approval.

    Reads the role each resolved row already carries, never its kind, and
    keeps the governed order the table came back in.
    """
    return tuple(row for row in rows if row.role.runs_under_approval is under_approval)


def is_organize_subject(issue: TrackerIssue) -> bool:
    """The phase work roster excludes criteria and record-shaped issues."""
    return not bool(issue.issue_labels & {"criterion", "tracker", "decision"})


def owes_stage_label(issue: TrackerIssue) -> bool:
    """Every member a stage must label: not a criterion, not a tracker record.

    An escalated member owes the label and is not a work subject: a stage
    counts it, names it, and spends no session on it. Workflow state is not
    read — removing the escalation label is the act that returns the member
    to the roster, and nothing else does.
    """
    return not bool(issue.issue_labels & {"criterion", "tracker"})


def stage_unlabelled(*, issues: Sequence[TrackerIssue], marker: str) -> tuple[str, ...]:
    """Keys of the members that owe *marker* and do not carry it.

    In snapshot order. Refuses a blank marker and a repeated key, for the
    reason the gap computation refuses them: a roster over an incoherent
    snapshot is not a smaller roster, it is a wrong one.
    """
    if not marker.strip():
        raise ValueError("a stage roster requires a nonempty marker key")
    seen: set[str] = set()
    owed: list[str] = []
    for issue in issues:
        if issue.issue_key in seen:
            raise ValueError("a stage roster requires one record per issue")
        seen.add(issue.issue_key)
        if owes_stage_label(issue) and marker not in issue.issue_labels:
            owed.append(issue.issue_key)
    return tuple(owed)


def stage_pending(
    *,
    unlabelled: Sequence[str],
    admitted: Mapping[str, bool],
    under_approval: bool,
) -> tuple[str, ...] | None:
    """What the phase still owes here.

    A run stage owes every unlabelled member, whatever its admission: the
    stage is complete only when every member carries its marker, and one
    that cannot be admitted holds it rather than being filtered out. A
    pre-approval phase owes only the admitted ones, and is not open here at
    all (``None``) when it admits nobody — an approved scope, or one whose
    gate is absent, has no pre-approval work and no pre-approval failure.
    """
    if under_approval:
        return tuple(unlabelled)
    open_here = tuple(key for key in unlabelled if admitted.get(key, False))
    return open_here or None


def organize_gap(
    *,
    revisions: Sequence[TrackerIssueRevision],
    admissions: Sequence[AdmissionResult],
    open_findings: Sequence[SpecFinding],
    body_marker_key: str,
) -> tuple[TrackerIssue, ...]:
    """Return the exact issue records whose specifications need organize work.

    The caller supplies a complete scope snapshot, including full criterion
    child revisions, and only findings that remain open. Label values are
    configured semantic issue-label keys. A missing admission is not live.
    Record-shaped members and criterion children are never work targets.

    This function reads no tracker, judges no text and dispatches no author.
    Each body is compared with its own admission; a stale child surface
    puts its parent in the work set without changing the parent admission.
    Criterion execution state only answers whether a non-Canceled child
    exists; a code-only regression cannot put a specification in the gap.

    A vendor change timestamp enters no clause of this computation. A
    mention bumps it without touching a body, so it reports movement that
    is not change and a work set keyed on it re-processes items nothing
    happened to. The prohibition is absolute here rather than a tuned
    window size, and it is scoped to this computation alone: it does not
    reach the reply/mention scan, whose own window is correct precisely
    because there a mention IS the signal being scanned for. Neither half
    is evidence for the other.
    """
    if not body_marker_key.strip():
        raise ValueError("organize gap requires a body phase marker key")
    by_key = {revision.issue.issue_key: revision for revision in revisions}
    if len(by_key) != len(revisions):
        raise ValueError("organize gap requires one revision per issue")
    admitted = {result.issue_id: result for result in admissions}
    if len(admitted) != len(admissions):
        raise ValueError("organize gap requires one admission per surface")
    children: dict[str, list[TrackerIssueRevision]] = {}
    for revision in revisions:
        issue = revision.issue
        if "criterion" in issue.issue_labels:
            if issue.parent_key not in by_key:
                raise ValueError("organize gap requires each criterion's parent")
            children.setdefault(issue.parent_key, []).append(revision)
    finding_keys = {finding.issue_id for finding in open_findings}
    gap: list[TrackerIssue] = []
    for revision in revisions:
        issue = revision.issue
        if not is_organize_subject(issue):
            continue
        surfaces = (revision, *children.get(issue.issue_key, ()))
        has_lapsed_surface = any(
            surface.issue.issue_key not in admitted
            or not is_admission_live(
                admitted_body_digest=admitted[
                    surface.issue.issue_key
                ].admitted_body_digest,
                current_body_digest=surface.body_digest,
            )
            for surface in surfaces
        )
        has_criterion = any(
            child.issue.state_kind is not WorkflowStateKind.CANCELED
            for child in children.get(issue.issue_key, ())
        )
        if (
            body_marker_key not in issue.issue_labels
            or has_lapsed_surface
            or not has_criterion
            or any(surface.issue.issue_key in finding_keys for surface in surfaces)
        ):
            gap.append(issue)
    return tuple(gap)


def organize_at_rest(
    *,
    revisions: Sequence[TrackerIssueRevision],
    admissions: Sequence[AdmissionResult],
    open_findings: Sequence[SpecFinding],
    body_marker_key: str,
) -> bool:
    """Answer "nothing to organize" as the gap's own cardinality.

    This pre-query is the gap counted, not a second opinion about it. It
    takes the one snapshot the gap takes, spends no tracker read of its
    own, judges no text and reaches no backend: a caller that asks it
    first and stops on ``True`` gets exactly the decision the whole gap
    would have handed it, for the price of the read already made.

    An incoherent snapshot refuses here for the reason it refuses there.
    """
    return not organize_gap(
        revisions=revisions,
        admissions=admissions,
        open_findings=open_findings,
        body_marker_key=body_marker_key,
    )

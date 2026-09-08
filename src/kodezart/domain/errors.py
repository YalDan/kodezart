"""Domain exceptions — no I/O, no infrastructure concerns."""

from collections.abc import Sequence

from kodezart.types.domain.gating import ScanFailureKind, ScanHit
from kodezart.types.domain.scope import ScopeRef
from kodezart.types.domain.surface import WritableSurface


class GitSourceReadError(Exception):
    """The requested immutable repository object cannot supply source bytes."""

    def __init__(self, *, ref: str, path: str | None, reason: str) -> None:
        self.ref = ref
        self.path = path
        self.reason = reason
        super().__init__(f"source {ref!r}:{path!r} could not be read: {reason}")


class AssertionComparisonError(Exception):
    """A protected comparison cannot establish a readable, unambiguous pair."""

    def __init__(self, *, source_ref: str, reason: str) -> None:
        self.source_ref = source_ref
        self.reason = reason
        super().__init__(f"assertion comparison for {source_ref!r} refused: {reason}")


class AuditEvidenceReadError(Exception):
    """A criterion's recorded grading cannot establish one current observation."""

    def __init__(self, *, criterion_key: str, reason: str) -> None:
        self.criterion_key = criterion_key
        self.reason = reason
        super().__init__(f"Evidence for {criterion_key!r} could not be read: {reason}")


class RulingProposalError(Exception):
    """Current native sources or the session cannot establish a ruling proposal."""


class WorkspaceError(Exception):
    """Raised when workspace acquisition or release fails."""


class CheckObservationError(Exception):
    """A completed watch cannot establish one immutable check-set identity."""

    def __init__(self, *, repo_url: str, ref: str, reason: str) -> None:
        self.repo_url = repo_url
        self.ref = ref
        self.reason = reason
        super().__init__(f"Cannot read watched checks for {repo_url}@{ref}: {reason}")


class PRContentConflictError(Exception):
    """An open PR cannot be identified or edited from the observed content."""

    def __init__(
        self, *, repo_url: str, head: str, pr_number: int | None, reason: str
    ) -> None:
        self.repo_url = repo_url
        self.head = head
        self.pr_number = pr_number
        self.reason = reason
        super().__init__(f"PR content for {repo_url!r}/{head!r}: {reason}")


class PRTrackerIdentityError(Exception):
    """The publishable PR body lost its required tracker identity."""

    def __init__(self, *, issue_key: str) -> None:
        self.issue_key = issue_key
        super().__init__(
            "gated PR body does not retain the fixed tracker issue identity "
            f"{issue_key!r}"
        )


class DeliveryContextError(Exception):
    """A delivery handoff does not identify one consistent execution."""

    def __init__(self, *, lane_key: str, issue_id: str, reason: str) -> None:
        self.lane_key = lane_key
        self.issue_id = issue_id
        self.reason = reason
        super().__init__(f"delivery context for {lane_key!r}/{issue_id!r}: {reason}")


class DeliveryRouteUnavailableError(Exception):
    """An observed delivery needs a consumer that is not connected yet.

    This carries the observed PR and check facts without inventing a lane
    outcome or claiming that a required residual has been published.
    """

    def __init__(
        self,
        *,
        lane_key: str,
        issue_id: str,
        reason: str,
        pr_url: str | None,
        pr_number: int | None,
        checks_passed: bool | None,
        checks_summary: str | None,
    ) -> None:
        self.lane_key = lane_key
        self.issue_id = issue_id
        self.reason = reason
        self.pr_url = pr_url
        self.pr_number = pr_number
        self.checks_passed = checks_passed
        self.checks_summary = checks_summary
        super().__init__(f"delivery route for {lane_key!r}/{issue_id!r}: {reason}")


class RunShapeReadError(Exception):
    """Recorded observations cannot establish a run-shape predicate."""

    def __init__(self, *, signal: str, source_ref: str, reason: str) -> None:
        self.signal = signal
        self.source_ref = source_ref
        self.reason = reason
        super().__init__(f"{signal} cannot read {source_ref!r}: {reason}")


class CriterionReadCapabilityError(Exception):
    """The configured tracker cannot read criterion sub-issues."""

    def __init__(self, *, adapter: str, reason: str) -> None:
        self.capability = "criterion_reads"
        self.adapter = adapter
        self.reason = reason
        super().__init__(
            f"required tracker capability {self.capability} on {adapter}: {reason}"
        )


class BodyDigestCapabilityError(Exception):
    """The configured tracker cannot provide stable body revisions."""

    def __init__(self, *, reason: str) -> None:
        self.capability = "body_digest_stability"
        self.reason = reason
        super().__init__(f"required tracker capability {self.capability}: {reason}")


class SurfaceLeaseError(Exception):
    """A surface acquisition or write lacks the required live lease.

    The address is snapshotted as primitive fields; no adapter object or
    lease record crosses the boundary. ``current_holder=None`` reports that
    no run currently holds the surface, including after a lease expired.
    Contention is not transient: the caller decides its next action, and a
    retry policy must not silently retry a failed acquisition.
    """

    def __init__(
        self,
        message: str,
        *,
        surface: WritableSurface,
        current_holder: str | None,
    ) -> None:
        address = f"{surface.kind.value}:{surface.ref.kind.value}:{surface.ref.key}"
        if surface.marker is not None:
            address = f"{address} (marker: {surface.marker})"
        holder = "none" if current_holder is None else current_holder
        super().__init__(f"{message} (surface: {address}; current holder: {holder})")
        self.surface_kind: str = surface.kind.value
        self.scope_kind: str = surface.ref.kind.value
        self.scope_key: str = surface.ref.key
        self.marker: str | None = surface.marker
        self.current_holder: str | None = current_holder


class DuplicateCommentMarkerError(Exception):
    """Several comments claim the same first-line marker on one target."""

    def __init__(
        self, *, target: str, marker: str, comment_keys: Sequence[str]
    ) -> None:
        super().__init__(
            f"duplicate comment marker {marker!r} on {target!r}: "
            f"{', '.join(comment_keys)}"
        )
        self.target = target
        self.marker = marker
        self.comment_keys = tuple(comment_keys)


class StaleWriteError(Exception):
    """Neither the asserted anchor nor its replacement is on the target."""

    def __init__(self, *, target: str, expected: str) -> None:
        super().__init__(f"stale description write on {target!r}: anchor {expected!r}")
        self.target = target
        self.expected = expected


class RulingRecordReadError(Exception):
    """The addressed issue's ruling records are unreadable or ambiguous."""

    def __init__(self, *, issue_key: str, lane_key: str, reason: str) -> None:
        self.issue_key = issue_key
        self.lane_key = lane_key
        self.reason = reason
        super().__init__(
            f"rulings on {issue_key!r} for {lane_key!r} could not be read: {reason}"
        )


class LaneRecordReadError(Exception):
    """A lane's branch record cannot be read from its addressed tracker comment."""

    def __init__(
        self,
        *,
        issue_key: str,
        lane_key: str,
        record_ref: str | None,
        reason: str,
    ) -> None:
        self.issue_key = issue_key
        self.lane_key = lane_key
        self.record_ref = record_ref
        self.reason = reason
        super().__init__(
            f"lane record {record_ref!r} on {issue_key!r} "
            f"for {lane_key!r} could not be read: {reason}"
        )


class EscalationReadError(Exception):
    """Resolution cannot be established from a readable, unique escalation."""

    def __init__(
        self, *, issue_key: str, lane_key: str, escalation_key: str, reason: str
    ) -> None:
        self.issue_key = issue_key
        self.lane_key = lane_key
        self.escalation_key = escalation_key
        self.reason = reason
        super().__init__(
            f"escalation {escalation_key!r} on {issue_key!r} "
            f"in lane {lane_key!r} could not be read: {reason}"
        )


class CriterionReadError(Exception):
    """A criterion membership read could not establish a complete answer."""

    def __init__(self, *, issue_key: str, reason: str) -> None:
        self.issue_key = issue_key
        self.reason = reason
        super().__init__(f"criteria of {issue_key!r} could not be read: {reason}")


class CriterionResolutionError(ValueError):
    """A native criterion key has no unique current child in the addressed family."""

    def __init__(self, *, issue_key: str, criterion_key: str, reason: str) -> None:
        self.issue_key = issue_key
        self.criterion_key = criterion_key
        self.reason = reason
        super().__init__(
            f"criterion {criterion_key!r} of {issue_key!r} could not be resolved: "
            f"{reason}"
        )


class FireSpecEntryError(Exception):
    """The current subject lacks its machine completion or human approval."""

    def __init__(self, *, issue_key: str, reason: str) -> None:
        self.issue_key = issue_key
        self.reason = reason
        super().__init__(f"fire subject {issue_key!r} cannot enter: {reason}")


class EmptyFireCriteriaError(Exception):
    """A successful tracker spec read found no criterion sub-issues."""

    def __init__(self, *, issue_key: str) -> None:
        self.issue_key = issue_key
        super().__init__(f"fire subject {issue_key!r} has no criterion sub-issues")


class InvalidFireCriterionError(Exception):
    """A criterion cannot supply its required specification at fire entry."""

    def __init__(self, *, issue_key: str, criterion_key: str, reason: str) -> None:
        self.issue_key = issue_key
        self.criterion_key = criterion_key
        self.reason = reason
        super().__init__(
            f"criterion {criterion_key!r} of fire subject {issue_key!r} "
            f"cannot be consumed: {reason}"
        )


class DuplicateIssueIdentityError(Exception):
    """Several issues claim one scope-and-deliverable identity."""

    def __init__(
        self, *, scope_key: ScopeRef, deliverable_key: str, issue_keys: Sequence[str]
    ) -> None:
        super().__init__(
            f"duplicate deliverable {deliverable_key!r} in "
            f"{scope_key.kind.value}:{scope_key.key}: {', '.join(issue_keys)}"
        )
        self.scope_key = scope_key
        self.deliverable_key = deliverable_key
        self.issue_keys = tuple(issue_keys)


class ScopeCycleError(Exception):
    """A cycle in the scope's dependency graph prevents any plan being returned.

    ``issue_keys`` is one offending directed cycle, without unrelated issues
    that merely lead into it. No edge is removed or invented to produce an
    order; the caller receives the tracker keys that require repair.
    """

    def __init__(self, *, issue_keys: Sequence[str]) -> None:
        self.issue_keys: tuple[str, ...] = tuple(issue_keys)
        super().__init__(f"scope dependency cycle: {', '.join(self.issue_keys)}")


class ScopeReadError(Exception):
    """A scope cannot be resolved without inventing membership or metadata."""

    def __init__(self, message: str, *, ref: ScopeRef) -> None:
        super().__init__(f"{message} (scope: {ref.kind.value}:{ref.key})")
        self.ref: ScopeRef = ref


class ScopePlanRefusalError(ScopeReadError):
    """Live scope facts violate the stage barrier before dispatch can begin."""

    def __init__(
        self,
        *,
        ref: ScopeRef,
        open_decisions: Sequence[str],
        backlog_criteria: Sequence[str],
        cross_subtree_edges: Sequence[tuple[str, str]],
    ) -> None:
        self.open_decisions = tuple(open_decisions)
        self.backlog_criteria = tuple(backlog_criteria)
        self.cross_subtree_edges = tuple(cross_subtree_edges)
        details = []
        if self.open_decisions:
            details.append("open decisions: " + ", ".join(self.open_decisions))
        if self.backlog_criteria:
            details.append("backlog-kind criteria: " + ", ".join(self.backlog_criteria))
        if self.cross_subtree_edges:
            details.append(
                "cross-subtree criterion edges: "
                + ", ".join(
                    f"{source} -> {target}"
                    for source, target in self.cross_subtree_edges
                )
            )
        super().__init__("scope plan refused; " + "; ".join(details), ref=ref)


class ScopeSupersessionReadError(ScopeReadError):
    """Readiness needs a cancellation reference without an established reader."""

    def __init__(self, *, ref: ScopeRef, criterion_keys: Sequence[str]) -> None:
        self.criterion_keys = tuple(criterion_keys)
        super().__init__(
            "criterion supersession resolution is unavailable: "
            + ", ".join(self.criterion_keys),
            ref=ref,
        )


class ScopedExecutionUnavailableError(Exception):
    """An addressed scope cannot execute through the legacy workflow pipeline."""

    def __init__(self, message: str, *, ref: ScopeRef) -> None:
        super().__init__(f"{message} (scope: {ref.kind.value}:{ref.key})")
        self.ref: ScopeRef = ref


class TransientAPIError(Exception):
    """Raised for transient, retry-eligible API failures (e.g. 5xx, network)."""

    def __init__(self, message: str, *, retry_after: float | None = None) -> None:
        super().__init__(message)
        self.retry_after: float | None = retry_after


class RateLimitError(TransientAPIError):
    """Raised when an API rate limit is hit; carries timing and utilization metadata."""

    def __init__(
        self,
        message: str,
        *,
        retry_after: float | None = None,
        resets_at: int | None = None,
        utilization: float | None = None,
    ) -> None:
        super().__init__(message, retry_after=retry_after)
        self.resets_at: int | None = resets_at
        self.utilization: float | None = utilization


class ForgeAPIError(Exception):
    """Raised when a forge request failed in a way no retry would change.

    The other half of the forge taxonomy: ``TransientAPIError`` and its
    ``RateLimitError`` subclass carry the retry-eligible failures, and
    every remaining one arrives here, so no vendor exception type
    crosses ``PRCreator``, ``CIMonitor``, ``DeliveryProbe`` or
    ``RepoVisibilityResolver``.  ``status_code`` is a field rather than
    prose because consumers route on it — a 404 on a check-runs page is
    a ref not yet visible, not a failure.

    ``status_code`` is ``None`` when the request failed before any
    status existed: a body that would not decode, a redirect loop, a URL
    the client would not build.  Those are forge failures all the same —
    the request did not complete and no retry completes it — and a
    taxonomy admitting only the status-carrying ones would leave exactly
    those to cross the port wearing the transport's own exception types.

    ``detail`` names the request that failed.  Never the response body:
    vendor text of unbounded shape is the surface ``redact_credentials``
    exists to keep out of egress, and a request target carries no
    credential to begin with.
    """

    def __init__(self, message: str, *, status_code: int | None, detail: str) -> None:
        super().__init__(f"{message} (status: {status_code}; request: {detail})")
        self.status_code: int | None = status_code
        self.detail: str = detail


class AgentSDKError(Exception):
    """Raised when the Claude Agent SDK reports a non-transient failure.

    Carries the structured ``ProcessError`` metadata (``exit_code``,
    ``stderr_tail``) when re-raised from ``ClaudeClientExecutor``.
    Both are nullable; the non-``ProcessError`` branches of the SDK
    exception handler (``CLIConnectionError``, ``ClaudeSDKError``)
    leave them ``None``.  Storing primitive scalars only — no SDK
    exception object reference is retained, mirroring the
    ``RateLimitError`` primitive-only shape.
    """

    def __init__(
        self,
        message: str,
        *,
        error_kind: str,
        exit_code: int | None = None,
        stderr_tail: str | None = None,
    ) -> None:
        super().__init__(message)
        self.error_kind: str = error_kind
        self.exit_code: int | None = exit_code
        self.stderr_tail: str | None = stderr_tail


class OutboundContentBlockedError(Exception):
    """Raised when the outbound gate blocks a write. Nothing is posted.

    Carries the categories that triggered the block and the writer that was
    about to run, so the workflow can surface both in its event stream.

    ``failure`` is the typed reason a scanner had NO answer, and it is a
    separate field rather than a category because "the scanner did not
    answer" and "the scanner found something" are different states an
    operator must be able to tell apart.  ``hits`` carries the per-span
    rationale, without which a human can neither confirm nor overrule the
    block — and a gate that cannot be confirmed gets worked around.
    """

    def __init__(
        self,
        message: str,
        *,
        writer: str,
        categories: Sequence[str],
        failure: ScanFailureKind | None = None,
        hits: Sequence[ScanHit] = (),
    ) -> None:
        detail = f"{message} (writer: {writer}; categories: {', '.join(categories)})"
        if failure is not None:
            detail = f"{detail} (scan failure: {failure.value})"
        for hit in hits:
            if hit.has_span and hit.matched_text is not None:
                detail = (
                    f"{detail} (start: {hit.start}; end: {hit.end}; "
                    f"matched text: {hit.matched_text!r})"
                )
        super().__init__(detail)
        self.writer: str = writer
        self.categories: tuple[str, ...] = tuple(categories)
        self.failure: ScanFailureKind | None = failure
        self.hits: tuple[ScanHit, ...] = tuple(hits)


class QueueFullError(Exception):
    """Raised when a lane's queue is at capacity and cannot accept a submission."""


class DuplicateWorkRefError(Exception):
    """Raised when a second ref is recorded at a role that admits only one.

    At most one ``DELIVERABLE`` ref exists per issue.  A second is an error
    and never a silent replacement: silently replacing it would move every
    dependent lane's base without anything saying so.
    """

    def __init__(
        self,
        message: str,
        *,
        issue_id: str,
        role: str,
        existing_branch: str,
        offered_branch: str,
    ) -> None:
        super().__init__(message)
        self.issue_id: str = issue_id
        self.role: str = role
        self.existing_branch: str = existing_branch
        self.offered_branch: str = offered_branch


class AssetFetchError(Exception):
    """Raised when a fire's referenced asset cannot be brought into its context.

    Every asset a ticket references is required: kodezart does not decide
    which of an author's references matter.  A fetch that failed and a fetch
    that was skipped are indistinguishable to the session working the fire,
    so there is no skip — the fire does not build.

    ``reason`` is a short machine-readable token (``unreadable``,
    ``too_large``, ``too_many``, ``timeout``, ``private_content``) so a
    consumer can route on the failure without parsing the message.
    """

    def __init__(
        self,
        message: str,
        *,
        issue_key: str,
        reason: str,
        asset_key: str | None = None,
    ) -> None:
        super().__init__(f"{message} (issue: {issue_key}; reason: {reason})")
        self.issue_key: str = issue_key
        self.reason: str = reason
        self.asset_key: str | None = asset_key


class BaseResolutionError(Exception):
    """Raised when a lane's base cannot be resolved. The lane does not dispatch.

    Trunk is NEVER substituted on this path.  A lane whose premise cannot be
    located must not build without it, and a base that silently fell back to
    trunk is indistinguishable from a lane that had no premise at all.

    Carries primitives only, the metadata shape ``RateLimitError`` uses.
    """

    def __init__(
        self,
        message: str,
        *,
        issue_id: str,
        blocker_issue_ids: Sequence[str] = (),
        branches: Sequence[str] = (),
    ) -> None:
        super().__init__(message)
        self.issue_id: str = issue_id
        self.blocker_issue_ids: tuple[str, ...] = tuple(blocker_issue_ids)
        self.branches: tuple[str, ...] = tuple(branches)


class BaseIntegrationConflictError(BaseResolutionError):
    """Raised when two inputs to a constructed base conflict textually.

    Never resolved by judgment, never by dropping an input, and trunk is
    never substituted.  No partial integration ref is pushed or recorded.
    """

    def __init__(
        self,
        message: str,
        *,
        issue_id: str,
        branches: Sequence[str],
        paths: Sequence[str],
    ) -> None:
        detail = (
            f"{message} (refs: {', '.join(branches)}; "
            f"conflicting paths: {', '.join(paths)})"
        )
        super().__init__(detail, issue_id=issue_id, branches=branches)
        self.paths: tuple[str, ...] = tuple(paths)


class MergeConflictError(Exception):
    """Raised by the git port when a merge cannot be completed.

    ``paths`` carries the paths git named as conflicting; it is empty when
    git refused the merge without naming any (a non-fast-forwardable
    divergence, for instance).
    """

    def __init__(
        self, message: str, *, source_branch: str, paths: Sequence[str]
    ) -> None:
        super().__init__(message)
        self.source_branch: str = source_branch
        self.paths: tuple[str, ...] = tuple(paths)


class CriteriaFanInError(Exception):
    """Raised when validator findings do not correspond 1:1 to dispatched ids.

    Fail-closed and observable: the missing, duplicate and unknown ids are
    all named, so the failure reads as a fan-in defect rather than as an
    absent verdict silently defaulting to a pass.
    """

    def __init__(
        self,
        message: str,
        *,
        missing_ids: Sequence[str],
        duplicate_ids: Sequence[str],
        unknown_ids: Sequence[str],
    ) -> None:
        detail = (
            f"{message} (missing: {', '.join(missing_ids) or '-'}; "
            f"duplicate: {', '.join(duplicate_ids) or '-'}; "
            f"unknown: {', '.join(unknown_ids) or '-'})"
        )
        super().__init__(detail)
        self.missing_ids: tuple[str, ...] = tuple(missing_ids)
        self.duplicate_ids: tuple[str, ...] = tuple(duplicate_ids)
        self.unknown_ids: tuple[str, ...] = tuple(unknown_ids)


class UngroundedVerdictError(Exception):
    """Raised when a verdict or a resource claim arrives without its grounds.

    Two raise surfaces.  The feasibility sweep raises it before anything
    is recorded, on three grounds: a stated verdict its own evidence does
    not derive (``_grounded``, comparing the statement with the derivation
    ``classify_finding`` computed beside it), a repair demanded on a
    criterion demonstrated satisfied at base (``classify_finding``), and a
    measured uneconomic cost filed anywhere but the environment arm (both
    ``_classify_criterion_side`` and ``_classify_no_repair``).  The accept
    gate raises it at ``accept_gate.named_resource`` — the last check
    before a resource name reaches a pull-request body.

    A single finding's completeness — a verdict arriving with its own
    repair and its evidence fields filled — is checked earlier still, on
    ``CriterionFinding``'s ``model_validator``, and fails as a
    ``ValidationError`` at the model boundary.  ``CriterionFeasibility``
    carries no validator of its own, so what keeps its fields filled is
    the finding it was projected from.
    """

    def __init__(self, message: str, *, criterion_id: str) -> None:
        super().__init__(f"{message} (criterion: {criterion_id})")
        self.criterion_id: str = criterion_id


class StaleBaseError(Exception):
    """Raised when a lane's recorded base is not the base its blockers imply.

    A criterion graded against a branch is graded against that branch ON
    ITS BASE, so when the base moves the tree the verdict was about no
    longer exists.  Grading anyway would produce a fresh assertion about
    a tree nobody has: the check refuses instead, and carries both refs
    as primitives so a reader can see what moved.
    """

    def __init__(
        self,
        message: str,
        *,
        recorded_ref: str,
        implied_ref: str,
        changed_inputs: Sequence[str] = (),
    ) -> None:
        super().__init__(message)
        self.recorded_ref: str = recorded_ref
        self.implied_ref: str = implied_ref
        self.changed_inputs: list[str] = list(changed_inputs)


class OrganizeAdmissionIdentityError(Exception):
    """The judgment did not address the source issue that was dispatched."""

    def __init__(self, *, expected: str, observed: str) -> None:
        self.expected = expected
        self.observed = observed
        super().__init__(
            f"organize admission returned issue {observed!r}, expected {expected!r}"
        )


class CheckChainExecutionError(Exception):
    """The configured chain could not be observed as command results."""

    def __init__(self, *, cwd: str, step_name: str | None, reason: str) -> None:
        self.cwd = cwd
        self.step_name = step_name
        self.reason = reason
        super().__init__(f"Cannot execute check chain in {cwd!r}: {reason}")


class UnionHeadReadError(Exception):
    """Current remote heads could not establish a complete union snapshot."""

    def __init__(self, *, scope_key: str, branch: str | None, reason: str) -> None:
        self.scope_key = scope_key
        self.branch = branch
        self.reason = reason
        super().__init__(f"Union head observation for {scope_key!r} refused: {reason}")


class UnionUnstableError(Exception):
    """Every allowed union attempt was superseded by current remote heads."""

    def __init__(
        self,
        *,
        scope_key: str,
        attempts: int,
        lane_keys: tuple[str, ...],
        measured_shas: tuple[str, ...],
        current_shas: tuple[str, ...],
    ) -> None:
        self.scope_key = scope_key
        self.attempts = attempts
        self.lane_keys = lane_keys
        self.measured_shas = measured_shas
        self.current_shas = current_shas
        super().__init__(
            f"Union heads for {scope_key!r} changed across {attempts} attempts"
        )


class AuditClaimReadError(ValueError):
    """The claim's source or remote head cannot support this observation."""


class WriteBackReadError(ValueError):
    """An addressed artifact cannot be re-read completely for verification."""


class TrackerFeasibilityReadError(Exception):
    """The selected tracker family or repository changed before judgment settled."""


class TrackerFirePreparationError(Exception):
    """An addressed fire cannot establish its native first-entry source facts."""

    def __init__(self, *, issue_key: str, reason: str) -> None:
        self.issue_key = issue_key
        self.reason = reason
        super().__init__(f"tracker fire {issue_key!r} cannot prepare: {reason}")


class PRStateReadError(ValueError):
    """A native PR observation cannot establish the requested identity."""

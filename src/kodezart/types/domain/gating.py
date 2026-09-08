"""Outbound-content gating domain types.

Every outbound write from a run carries a verdict that is explicit and
observable: content is never silently dropped and never silently posted.
"""

import hashlib
from collections.abc import Mapping
from enum import StrEnum
from types import MappingProxyType

from pydantic import ConfigDict, Field, model_validator

from kodezart.types.base import CamelCaseModel


class RepoVisibility(StrEnum):
    """Three-state repository visibility, resolved once per run.

    ``UNKNOWN`` is a first-class inhabitant, not a missing value: a
    resolution failure, a tokenless deployment and a local-only run all
    land here and all take the public path with the gate engaged.
    """

    PRIVATE = "private"
    PUBLIC = "public"
    UNKNOWN = "unknown"


class GateVerdict(StrEnum):
    """Verdict for one outbound payload, ordered BLOCKED > REDACTED > CLEAN."""

    CLEAN = "clean"
    REDACTED = "redacted"
    BLOCKED = "blocked"


class SurfaceDurability(StrEnum):
    """Whether a reader treats a write as current or as a past observation."""

    DURABLE = "durable"
    POINT_IN_TIME = "point_in_time"


_SEVERITY: dict[GateVerdict, int] = {
    GateVerdict.CLEAN: 0,
    GateVerdict.REDACTED: 1,
    GateVerdict.BLOCKED: 2,
}


def max_verdict(left: GateVerdict, right: GateVerdict) -> GateVerdict:
    """Max-severity-wins combination of two verdicts."""
    return left if _SEVERITY[left] >= _SEVERITY[right] else right


def content_digest(content: str) -> str:
    """The payload hash that keys the gate's memo and rides on its event.

    Across runs the judgment verdict is genuinely non-deterministic, and
    that is not engineered away here.  What rides on the event instead is
    this hash plus the fragment digest, so a disagreement between two runs
    over the same payload is RECONSTRUCTIBLE by an operator rather than
    invisible.
    """
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


class RedactionCategory(StrEnum):
    """Privacy classes with fixed outbound consequences."""

    CROSS_REPO_NAMES = "cross_repo_names"
    TRACKER_URLS = "tracker_urls"
    EMAIL_HANDLES = "email_handles"
    INFRA_ENDPOINTS = "infra_endpoints"
    CREDENTIALS = "credentials"
    ORG_PRIVATE = "org_private"


TRACKER_ROSTER_MIN_REFERENCES = 3


class DurabilityCategory(StrEnum):
    """Aggregate claims always block; redacting one would preserve the claim."""

    OBJECT_COUNT = "object_count"
    IDENTIFIER_ROSTER = "identifier_roster"


type ScanCategory = RedactionCategory | DurabilityCategory


REDACTION_VERDICTS: Mapping[RedactionCategory, GateVerdict] = MappingProxyType(
    {
        RedactionCategory.CROSS_REPO_NAMES: GateVerdict.REDACTED,
        RedactionCategory.TRACKER_URLS: GateVerdict.REDACTED,
        RedactionCategory.EMAIL_HANDLES: GateVerdict.REDACTED,
        RedactionCategory.INFRA_ENDPOINTS: GateVerdict.BLOCKED,
        RedactionCategory.CREDENTIALS: GateVerdict.BLOCKED,
        RedactionCategory.ORG_PRIVATE: GateVerdict.REDACTED,
    }
)


class WriterShape(StrEnum):
    """What kind of artifact a writer emits.

    ``IDENTIFIER`` writers (a git ref) cannot carry a placeholder, so any hit
    blocks regardless of the category's declared verdict.  ``PROSE`` writers
    follow the per-category rule.
    """

    PROSE = "prose"
    IDENTIFIER = "identifier"


class OutboundSurface(StrEnum):
    """What kind of surface a destination writes onto.

    ``PUBLICATION`` is published to the open internet at write time — a pull
    request field, and a branch ref, which appears in a public repository's
    branch list the moment it is pushed.  ``REPOSITORY`` is carried in the
    repository's own history.  ``TRACKER`` is the coordination surface,
    which mirrors publicly.
    """

    PUBLICATION = "publication"
    REPOSITORY = "repository"
    TRACKER = "tracker"


class OutboundDestination(StrEnum):
    """Where one outbound payload is going. One member per REAL writer.

    A member is registered when its writer exists, never in advance: a
    destination naming a writer that does not exist is an invented value.
    """

    BRANCH_NAME = "branch_name"
    COMMIT_MESSAGE = "commit_message"
    COMMIT_MESSAGE_DIVERGENCE_REPLAY = "commit_message_divergence_replay"
    PR_TITLE = "pr_title"
    PR_BODY = "pr_body"
    PR_COMMENT = "pr_comment"
    ARTIFACT_TICKET_JSON = "artifact_ticket_json"
    ARTIFACT_CRITERIA_JSON = "artifact_criteria_json"
    TRACKER_COMMENT = "tracker_comment"


#: Total over :class:`OutboundDestination`; a test asserts the totality so a
#: new member cannot be added without classifying its surface.
DESTINATION_SURFACE: Mapping[OutboundDestination, OutboundSurface] = {
    OutboundDestination.BRANCH_NAME: OutboundSurface.PUBLICATION,
    OutboundDestination.PR_TITLE: OutboundSurface.PUBLICATION,
    OutboundDestination.PR_BODY: OutboundSurface.PUBLICATION,
    OutboundDestination.PR_COMMENT: OutboundSurface.PUBLICATION,
    OutboundDestination.COMMIT_MESSAGE: OutboundSurface.REPOSITORY,
    OutboundDestination.COMMIT_MESSAGE_DIVERGENCE_REPLAY: OutboundSurface.REPOSITORY,
    OutboundDestination.ARTIFACT_TICKET_JSON: OutboundSurface.REPOSITORY,
    OutboundDestination.ARTIFACT_CRITERIA_JSON: OutboundSurface.REPOSITORY,
    OutboundDestination.TRACKER_COMMENT: OutboundSurface.TRACKER,
}


def surface_of(destination: OutboundDestination) -> OutboundSurface:
    """The surface class *destination* writes onto."""
    return DESTINATION_SURFACE[destination]


#: Classify real writers in code, alongside their surface classification.
#: Descriptions and replaceable artifacts are read as current; appended
#: comments and commit messages describe a particular event.
DESTINATION_DURABILITY: Mapping[OutboundDestination, SurfaceDurability] = {
    OutboundDestination.BRANCH_NAME: SurfaceDurability.DURABLE,
    OutboundDestination.PR_TITLE: SurfaceDurability.DURABLE,
    OutboundDestination.PR_BODY: SurfaceDurability.DURABLE,
    OutboundDestination.PR_COMMENT: SurfaceDurability.POINT_IN_TIME,
    OutboundDestination.COMMIT_MESSAGE: SurfaceDurability.POINT_IN_TIME,
    OutboundDestination.COMMIT_MESSAGE_DIVERGENCE_REPLAY: (
        SurfaceDurability.POINT_IN_TIME
    ),
    OutboundDestination.ARTIFACT_TICKET_JSON: SurfaceDurability.DURABLE,
    OutboundDestination.ARTIFACT_CRITERIA_JSON: SurfaceDurability.DURABLE,
    OutboundDestination.TRACKER_COMMENT: SurfaceDurability.POINT_IN_TIME,
}


def durability_of(destination: OutboundDestination | None) -> SurfaceDurability:
    """An unclassified write is durable; every named writer has a mapping."""
    if destination is None:
        return SurfaceDurability.DURABLE
    return DESTINATION_DURABILITY[destination]


class ContentClass(StrEnum):
    """Where a payload CAME FROM, declared by the call site that built it.

    Provenance, not typography.  The partition is one question: can this
    write be recomputed from durable state by a process that never held the
    session?  A criterion tick, a state transition, a note assembled from an
    enum member and a job id all can — they are ``DERIVED`` and take the
    cheap path.  Anything a model or a third party wrote is ``AUTHORED`` and
    is audited.

    Only the writer knows this.  It cannot be recovered from the bytes: a
    derived note is a sentence with spaces in it, and a leaked credential is
    one unbroken token, so any rule read off the shape of the payload is
    anti-correlated with the thing the audit exists to catch.  Hence the
    parameter is required at every call site and has no default — a default
    would be a silent cheap path.
    """

    DERIVED = "derived"
    AUTHORED = "authored"


class ScanFailureKind(StrEnum):
    """One member per way for a scanner to have NO answer.

    Every member resolves to ``BLOCKED``.  Never ``CLEAN`` — "did not
    answer" and "said it is clean" are two distinct observable states, the
    same three-state discipline :class:`RepoVisibility` already holds.
    Never "skip this scanner and continue": a declared scanner that cannot
    answer is a blocked payload, not an absent one.
    """

    TIMEOUT = "timeout"
    REFUSAL = "refusal"
    MALFORMED_VERDICT = "malformed_verdict"
    RATE_LIMITED = "rate_limited"
    TRANSPORT_ERROR = "transport_error"
    EMPTY_RESPONSE = "empty_response"
    SPANS_UNRESOLVABLE = "spans_unresolvable"
    BUDGET_EXHAUSTED = "budget_exhausted"
    NOT_CONFIGURED = "not_configured"


class ScanHit(CamelCaseModel):
    """One finding: its category, its span if it has one, and why.

    ``start``/``end`` are absent on a JUDGMENT hit that localizes to no
    span — "this paragraph implies an unreleased capability" has nothing to
    excise.  Redaction is span surgery, so a span-less hit blocks rather
    than redacts; :meth:`has_span` is what the gate asks.
    """

    model_config = ConfigDict(frozen=True)

    category: ScanCategory
    start: int | None = Field(default=None, ge=0)
    end: int | None = Field(default=None, ge=0)
    rationale: str | None = None
    matched_text: str | None = None

    @property
    def has_span(self) -> bool:
        """Whether this hit localizes to a non-empty span of the payload."""
        return self.start is not None and self.end is not None and self.end > self.start

    def sort_key(self) -> tuple[int, int]:
        """Payload order, span-less hits first so they are never lost."""
        if self.start is None or self.end is None:
            return (-1, -1)
        return (self.start, self.end)


class ScanResult(CamelCaseModel):
    """What one scanner returns across the port: hits OR a typed failure.

    Never an exception crossing the port, and never ``None``.  The two
    states are mutually exclusive by construction, so "no hits" and "no
    answer" cannot be confused at any call site.
    """

    model_config = ConfigDict(frozen=True)

    hits: tuple[ScanHit, ...] = ()
    failure: ScanFailureKind | None = None

    @model_validator(mode="after")
    def _exactly_one_state(self) -> "ScanResult":
        """A failed scan reports no hits; a completed scan reports no failure."""
        if self.failure is not None and self.hits:
            msg = "A ScanResult carries either hits or a failure, never both"
            raise ValueError(msg)
        return self


class GateDecision(CamelCaseModel):
    """Result of gating one outbound payload.

    ``content`` is the payload to write when the verdict is CLEAN or
    REDACTED.  On BLOCKED nothing is written — the caller raises.
    """

    model_config = ConfigDict(frozen=True)

    verdict: GateVerdict
    content: str
    categories: tuple[ScanCategory, ...] = ()
    hits: tuple[ScanHit, ...] = ()
    failure: ScanFailureKind | None = None

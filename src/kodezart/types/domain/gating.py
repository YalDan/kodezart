"""Outbound-content gating domain types.

Every outbound write from a run carries a verdict that is explicit and
observable: content is never silently dropped and never silently posted.
"""

from enum import StrEnum

from pydantic import ConfigDict, Field

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


_SEVERITY: dict[GateVerdict, int] = {
    GateVerdict.CLEAN: 0,
    GateVerdict.REDACTED: 1,
    GateVerdict.BLOCKED: 2,
}


def max_verdict(left: GateVerdict, right: GateVerdict) -> GateVerdict:
    """Max-severity-wins combination of two verdicts."""
    return left if _SEVERITY[left] >= _SEVERITY[right] else right


class RedactionCategory(StrEnum):
    """Deny-pattern categories. Each declares a verdict in AppConfig."""

    CROSS_REPO_NAMES = "cross_repo_names"
    TRACKER_URLS = "tracker_urls"
    EMAIL_HANDLES = "email_handles"
    INFRA_ENDPOINTS = "infra_endpoints"
    CREDENTIALS = "credentials"


class WriterShape(StrEnum):
    """What kind of artifact a writer emits.

    ``IDENTIFIER`` writers (a git ref) cannot carry a placeholder, so any hit
    blocks regardless of the category's declared verdict.  ``PROSE`` writers
    follow the per-category rule.
    """

    PROSE = "prose"
    IDENTIFIER = "identifier"


class ScanHit(CamelCaseModel):
    """One deny-pattern match: its category and its span in the payload."""

    model_config = ConfigDict(frozen=True)

    category: RedactionCategory
    start: int = Field(ge=0)
    end: int = Field(ge=0)


class GateDecision(CamelCaseModel):
    """Result of gating one outbound payload.

    ``content`` is the payload to write when the verdict is CLEAN or
    REDACTED.  On BLOCKED nothing is written — the caller raises.
    """

    model_config = ConfigDict(frozen=True)

    verdict: GateVerdict
    content: str
    categories: tuple[RedactionCategory, ...] = ()

"""Immutable typed observations of a run's recorded shape."""

from enum import StrEnum
from typing import Annotated, Literal

from pydantic import ConfigDict, Field

from kodezart.types.base import CamelCaseModel
from kodezart.types.domain.escalation import EscalationResolution
from kodezart.types.domain.mandate_graph import LaneGraphSnapshot, LaneRulingSnapshot
from kodezart.types.domain.run_state import LaneCommit, LaneEscalation
from kodezart.types.domain.scope import ScopeRef
from kodezart.types.domain.surface import WritableSurface

Identity = Annotated[str, Field(min_length=1, pattern=r"\S")]


class AlarmSignal(StrEnum):
    """The declared signals; wider observation windows retain one signal."""

    TALLY_UNMOVED = "tally_unmoved"
    TALLY_REGRESSED = "tally_regressed"
    LAPSE_UNDISCHARGED = "lapse_undischarged"
    ESCALATION_AGEING = "escalation_ageing"
    WRITE_BACK_MISSING = "write_back_missing"
    SURFACE_CONTENDED = "surface_contended"
    RECORD_SUPERSEDED = "record_superseded"
    COMPOSITION_SUBSTITUTED = "composition_substituted"
    BARREN_TICK_WITH_DIFF_GROWTH = "barren_tick_with_diff_growth"
    COMMITS_AHEAD_OF_RECORD = "commits_ahead_of_record"
    RULINGS_OUTPACE_CLOSURES = "rulings_outpace_closures"
    STRUCTURAL_WRITE_UNCROSSES_MILESTONE = "structural_write_uncrosses_milestone"


class AlarmSubjectKind(StrEnum):
    SCOPE = "scope"
    LANE = "lane"
    ISSUE = "issue"
    CRITERION = "criterion"
    SURFACE = "surface"
    ESCALATION = "escalation"


class _Subject(CamelCaseModel):
    model_config = ConfigDict(frozen=True)
    scope_key: Identity


class ScopeSubject(_Subject):
    """A whole scope, with no inferred lane or issue carrier."""

    kind: Literal[AlarmSubjectKind.SCOPE] = AlarmSubjectKind.SCOPE


class LaneSubject(_Subject):
    """One explicit lane inside the recorded scope."""

    kind: Literal[AlarmSubjectKind.LANE] = AlarmSubjectKind.LANE
    lane_key: Identity


class IssueSubject(_Subject):
    """One native issue, optionally qualified by its observed lane."""

    kind: Literal[AlarmSubjectKind.ISSUE] = AlarmSubjectKind.ISSUE
    issue_id: Identity
    lane_key: Identity | None = None


class CriterionSubject(_Subject):
    """A native criterion identity and its observed owning issue."""

    kind: Literal[AlarmSubjectKind.CRITERION] = AlarmSubjectKind.CRITERION
    issue_id: Identity
    member_id: Identity
    lane_key: Identity | None = None


class SurfaceSubject(_Subject):
    """One typed writable address, including its marker when applicable."""

    kind: Literal[AlarmSubjectKind.SURFACE] = AlarmSubjectKind.SURFACE
    surface: WritableSurface
    lane_key: Identity | None = None
    issue_id: Identity | None = None


class EscalationSubject(_Subject):
    """One recorded question occurrence with explicit known context."""

    kind: Literal[AlarmSubjectKind.ESCALATION] = AlarmSubjectKind.ESCALATION
    member_id: Identity
    lane_key: Identity | None = None
    issue_id: Identity | None = None


AlarmSubject = Annotated[
    ScopeSubject
    | LaneSubject
    | IssueSubject
    | CriterionSubject
    | SurfaceSubject
    | EscalationSubject,
    Field(discriminator="kind"),
]


class LaneFieldValue(CamelCaseModel):
    """An explicit field projection supplied by a record or event reader.

    This is an observation input, not a run event or an event vocabulary.
    The producer supplies one field identity and its opaque string value;
    no body text is parsed to infer either. Source and SHA stay on the
    AlarmReading that carries this value.
    """

    model_config = ConfigDict(frozen=True)

    lane_key: str = Field(min_length=1, pattern=r"\S")
    field_key: str = Field(min_length=1, pattern=r"\S")
    value: str


class Evidence[T](CamelCaseModel):
    """One already-typed projection, never a serialized domain payload."""

    model_config = ConfigDict(frozen=True)
    value: T


class TextEvidence(Evidence[str]):
    """Verbatim text or opaque identity; no interpretation is implied."""

    kind: Literal["text"] = "text"


class CountEvidence(Evidence[Annotated[int, Field(strict=True, ge=0)]]):
    """An observed nonnegative integer, without string or boolean coercion."""

    kind: Literal["count"] = "count"


class PresenceEvidence(Evidence[Annotated[bool, Field(strict=True)]]):
    """The boolean result of a completed addressed lookup."""

    kind: Literal["presence"] = "presence"


class ReferencesEvidence(Evidence[tuple[Identity, ...]]):
    """An ordered projection of native references or commit identities."""

    kind: Literal["references"] = "references"


class LabelsEvidence(Evidence[tuple[Identity, ...] | None]):
    """Actual labels, or an explicitly missing label observation."""

    kind: Literal["labels"] = "labels"


class SurfaceEvidence(Evidence[WritableSurface]):
    """The actual writable address declared by an observed obligation."""

    kind: Literal["surface"] = "surface"


class EscalationEvidence(Evidence[LaneEscalation]):
    """The typed question record returned by its native reader."""

    kind: Literal["escalation"] = "escalation"


class ResolutionEvidence(Evidence[EscalationResolution]):
    """The actual resolution and its decision reference, when resolved."""

    kind: Literal["resolution"] = "resolution"


class CommitsEvidence(Evidence[tuple[LaneCommit, ...]]):
    """Recorded commit rows in trajectory order."""

    kind: Literal["commits"] = "commits"


class LaneFieldEvidence(Evidence[LaneFieldValue]):
    """One explicit lane field assertion, without prose inference."""

    kind: Literal["lane_field"] = "lane_field"


class ScopeEvidence(Evidence[ScopeRef]):
    """The native scope address supplied by its current reader."""

    kind: Literal["scope"] = "scope"


class RulingsEvidence(Evidence[LaneRulingSnapshot]):
    """A typed ruling observation window with recorded attribution."""

    kind: Literal["rulings"] = "rulings"


class GraphEvidence(Evidence[LaneGraphSnapshot]):
    """A complete typed membership observation, without inferred closure."""

    kind: Literal["graph"] = "graph"


AlarmEvidence = Annotated[
    TextEvidence
    | CountEvidence
    | PresenceEvidence
    | ReferencesEvidence
    | LabelsEvidence
    | SurfaceEvidence
    | EscalationEvidence
    | ResolutionEvidence
    | CommitsEvidence
    | LaneFieldEvidence
    | ScopeEvidence
    | RulingsEvidence
    | GraphEvidence,
    Field(discriminator="kind"),
]


class AlarmReading(CamelCaseModel):
    """A referenced input retaining the exact typed projection and source SHA."""

    model_config = ConfigDict(frozen=True)
    source_ref: Identity
    value: AlarmEvidence
    at_sha: Identity | None = None


class AlarmBound(CamelCaseModel):
    """The configured count and observation that caused a threshold alarm."""

    model_config = ConfigDict(frozen=True)

    config_field: str = Field(min_length=1, pattern=r"\S")
    configured_value: int = Field(ge=0)
    observed_value: int = Field(ge=0)


class RunAlarm(CamelCaseModel):
    """One signal over one subject, with the readings needed to replay it."""

    model_config = ConfigDict(frozen=True)

    subject: AlarmSubject
    signal: AlarmSignal
    readings: tuple[AlarmReading, ...] = Field(min_length=1)
    bound: AlarmBound | None
    raised_at_sha: str = Field(min_length=1, pattern=r"\S")
    raised_by: str = Field(min_length=1, pattern=r"\S")

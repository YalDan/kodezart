"""Immutable observations of a run's recorded shape, with one subject each."""

from enum import StrEnum
from typing import Self

from pydantic import ConfigDict, Field, TypeAdapter, model_validator

from kodezart.types.base import CamelCaseModel
from kodezart.types.domain.surface import WritableSurface


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


_SURFACE_ADDRESS = TypeAdapter(WritableSurface)


def surface_alarm_member_id(surface: WritableSurface) -> str:
    """Encode a surface address as a canonical, backend-neutral member key.

    This retains the subject's declared string shape while preserving every
    address component, including a comment's marker. One spelling prevents
    equivalent surface addresses from creating different alarm identities.
    """
    return _SURFACE_ADDRESS.dump_json(surface).decode("utf-8")


class AlarmSubject(CamelCaseModel):
    """One addressed run object; criterion member keys are tracker identities.

    Lane and issue context may accompany member subjects. Scope and lane
    subjects cannot carry a narrower member masquerading as their identity.
    """

    model_config = ConfigDict(frozen=True)

    kind: AlarmSubjectKind
    scope_key: str = Field(min_length=1, pattern=r"\S")
    lane_key: str | None = Field(default=None, min_length=1, pattern=r"\S")
    issue_id: str | None = Field(default=None, min_length=1, pattern=r"\S")
    member_id: str | None = Field(default=None, min_length=1, pattern=r"\S")

    @model_validator(mode="after")
    def _required_region(self) -> Self:
        match self.kind:
            case AlarmSubjectKind.SCOPE:
                if any(
                    value is not None
                    for value in (self.lane_key, self.issue_id, self.member_id)
                ):
                    raise ValueError(
                        "a scope alarm subject cannot carry a lane, issue or member"
                    )
            case AlarmSubjectKind.LANE:
                if self.lane_key is None:
                    raise ValueError("a lane alarm subject requires its lane key")
                if self.issue_id is not None or self.member_id is not None:
                    raise ValueError(
                        "a lane alarm subject cannot carry an issue or member"
                    )
            case AlarmSubjectKind.ISSUE:
                if self.issue_id is None:
                    raise ValueError(
                        "an issue alarm subject requires its issue identity"
                    )
                if self.member_id is not None:
                    raise ValueError("an issue alarm subject cannot carry a member")
            case AlarmSubjectKind.CRITERION:
                if self.issue_id is None or self.member_id is None:
                    raise ValueError(
                        "a criterion alarm subject requires its issue "
                        "and criterion sub-issue identities"
                    )
            case AlarmSubjectKind.SURFACE:
                if self.member_id is None:
                    raise ValueError(
                        "a surface alarm subject requires its surface address"
                    )
                address = _SURFACE_ADDRESS.validate_json(self.member_id)
                if self.member_id != surface_alarm_member_id(address):
                    raise ValueError(
                        "a surface alarm member must use the canonical surface address"
                    )
            case AlarmSubjectKind.ESCALATION:
                if self.member_id is None:
                    raise ValueError(
                        "an escalation alarm subject requires its escalation key"
                    )
        return self


_ALARM_SUBJECT = TypeAdapter(AlarmSubject)


def alarm_subject_key(subject: AlarmSubject) -> str:
    """Encode a whole alarm subject as one canonical, backend-neutral key.

    An alarm is addressed by its complete subject, so two subjects that
    differ anywhere address two records: two writable surfaces on one
    issue in one lane, and a scope subject carrying no lane key at all.
    One spelling per subject, the discipline ``surface_alarm_member_id``
    already applies to the surface component.
    """
    return _ALARM_SUBJECT.dump_json(subject).decode("utf-8")


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


class AlarmReading(CamelCaseModel):
    """A referenced input, retaining the value exactly as it was read."""

    model_config = ConfigDict(frozen=True)

    source_ref: str = Field(min_length=1, pattern=r"\S")
    value: str
    at_sha: str | None = Field(default=None, min_length=1, pattern=r"\S")


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

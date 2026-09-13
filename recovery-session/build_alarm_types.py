from pathlib import Path
path = Path('/private/tmp/kodezart-v03-recovery-alarm-owner/src/kodezart/types/domain/run_alarm.py')
old = path.read_text()
signals = old[old.index('class AlarmSignal'):old.index('_SURFACE_ADDRESS')]
lane_field = old[old.index('class LaneFieldValue'):old.index('class AlarmReading')]
tail = old[old.index('class AlarmBound'):]
head = '''"""Immutable typed observations of a run's recorded shape."""

from typing import Annotated, Literal
from enum import StrEnum

from pydantic import ConfigDict, Field

from kodezart.types.base import CamelCaseModel
from kodezart.types.domain.escalation import EscalationResolution
from kodezart.types.domain.mandate_graph import LaneGraphSnapshot, LaneRulingSnapshot
from kodezart.types.domain.run_state import LaneCommit, LaneEscalation
from kodezart.types.domain.scope import ScopeRef
from kodezart.types.domain.surface import WritableSurface

Identity = Annotated[str, Field(min_length=1, pattern=r"\\S")]

'''
subjects = '''class _Subject(CamelCaseModel):
    model_config = ConfigDict(frozen=True)
    scope_key: Identity


class ScopeSubject(_Subject):
    kind: Literal[AlarmSubjectKind.SCOPE] = AlarmSubjectKind.SCOPE


class LaneSubject(_Subject):
    kind: Literal[AlarmSubjectKind.LANE] = AlarmSubjectKind.LANE
    lane_key: Identity


class IssueSubject(_Subject):
    kind: Literal[AlarmSubjectKind.ISSUE] = AlarmSubjectKind.ISSUE
    issue_id: Identity
    lane_key: Identity | None = None


class CriterionSubject(_Subject):
    kind: Literal[AlarmSubjectKind.CRITERION] = AlarmSubjectKind.CRITERION
    issue_id: Identity
    member_id: Identity
    lane_key: Identity | None = None


class SurfaceSubject(_Subject):
    kind: Literal[AlarmSubjectKind.SURFACE] = AlarmSubjectKind.SURFACE
    surface: WritableSurface
    lane_key: Identity | None = None
    issue_id: Identity | None = None


class EscalationSubject(_Subject):
    kind: Literal[AlarmSubjectKind.ESCALATION] = AlarmSubjectKind.ESCALATION
    member_id: Identity
    lane_key: Identity | None = None
    issue_id: Identity | None = None


AlarmSubject = Annotated[
    ScopeSubject | LaneSubject | IssueSubject | CriterionSubject | SurfaceSubject | EscalationSubject,
    Field(discriminator="kind"),
]


'''
evidence = '''class Evidence[T](CamelCaseModel):
    """One already-typed projection, never a serialized domain payload."""

    model_config = ConfigDict(frozen=True)
    value: T


'''
rows = [
('TextEvidence', 'str', 'text'),
('CountEvidence', 'Annotated[int, Field(strict=True, ge=0)]', 'count'),
('PresenceEvidence', 'Annotated[bool, Field(strict=True)]', 'presence'),
('ReferencesEvidence', 'tuple[Identity, ...]', 'references'),
('LabelsEvidence', 'tuple[Identity, ...] | None', 'labels'),
('SurfaceEvidence', 'WritableSurface', 'surface'),
('EscalationEvidence', 'LaneEscalation', 'escalation'),
('ResolutionEvidence', 'EscalationResolution', 'resolution'),
('CommitsEvidence', 'tuple[LaneCommit, ...]', 'commits'),
('LaneFieldEvidence', 'LaneFieldValue', 'lane_field'),
('ScopeEvidence', 'ScopeRef', 'scope'),
('RulingsEvidence', 'LaneRulingSnapshot', 'rulings'),
('GraphEvidence', 'LaneGraphSnapshot', 'graph'),
]
for name, value, kind in rows:
    evidence += f'class {name}(Evidence[{value}]):\n    kind: Literal["{kind}"] = "{kind}"\n\n\n'
evidence += 'AlarmEvidence = Annotated[\n    ' + ' | '.join(row[0] for row in rows) + ',\n    Field(discriminator="kind"),\n]\n\n\n'
reading = '''class AlarmReading(CamelCaseModel):
    """A referenced input retaining the exact typed projection and source SHA."""

    model_config = ConfigDict(frozen=True)
    source_ref: Identity
    value: AlarmEvidence
    at_sha: Identity | None = None


'''
path.write_text(head + signals + subjects + lane_field + evidence + reading + tail)

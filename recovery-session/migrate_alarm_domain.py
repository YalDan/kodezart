from pathlib import Path
root=Path('/private/tmp/kodezart-v03-recovery-alarm-owner/src/kodezart')
p=root/'domain/run_alarm_record.py'
s=p.read_text().replace('from collections.abc import Mapping','from collections.abc import Mapping\n\nfrom pydantic import TypeAdapter')
s=s.replace('    alarm_subject_key,\n','')
pos=s.index('MARKER_PURPOSE =')
s=s[:pos]+'''from kodezart.types.domain.surface import WritableSurface

_SUBJECT = TypeAdapter(AlarmSubject)
_SURFACE = TypeAdapter(WritableSurface)


def alarm_subject_key(subject: AlarmSubject) -> str:
    """One canonical spelling of the complete typed subject at its address boundary."""
    return _SUBJECT.dump_json(subject).decode("utf-8")


def surface_alarm_member_id(surface: WritableSurface) -> str:
    """Canonical source-reference spelling; typed evidence retains the surface itself."""
    return _SURFACE.dump_json(surface).decode("utf-8")


'''+s[pos:]
p.write_text(s)
p=root/'domain/run_shape.py';s=p.read_text()
s=s.replace('from typing import Annotated\n\nfrom pydantic import Field, NonNegativeInt, TypeAdapter, ValidationError\n\n','')
s=s.replace('    surface_alarm_member_id,\n','')
s=s.replace('from kodezart.domain.errors import RunShapeReadError','from kodezart.domain.errors import RunShapeReadError\nfrom kodezart.domain.run_alarm_record import surface_alarm_member_id')
start=s.index('_ESCALATION =');end=s.index('\n\ndef _unreadable',start)
s=s[:start]+s[end:]
start=s.index('def _decode');end=s.index('\n\ndef record_superseded',start)
s=s[:start]+'''def _read_value[T](
    reading: AlarmReading, expected: type[Evidence[T]], signal: AlarmSignal
) -> T:
    value = reading.value
    if not isinstance(value, expected):
        raise _unreadable(signal, reading.source_ref, "another evidence kind was recorded")
    return value.value


def _identity(reading: AlarmReading, signal: AlarmSignal) -> str:
    value = _read_value(reading, TextEvidence, signal)
    if not value.strip():
        raise _unreadable(signal, reading.source_ref, "recorded identity is empty")
    return value
'''+s[end:]
mapping={'_LANE_FIELD':'LaneFieldEvidence','_COMMITS':'ReferencesEvidence','_SURFACE':'SurfaceEvidence','_PRESENT':'PresenceEvidence','_COUNT':'CountEvidence','_COMMIT_ROWS':'CommitsEvidence','_ESCALATION':'EscalationEvidence','_RESOLUTION':'ResolutionEvidence','_REFERENCES':'ReferencesEvidence','_SCOPE':'ScopeEvidence','_MARKER_LABELS':'LabelsEvidence'}
for old,new in mapping.items():
    s=s.replace(', '+old+', signal)',', '+new+', signal)')
s=s.replace('_decode(', '_read_value(')
import re
s=re.sub(r'_read_value\((\w+), _IDENTITY, signal\)',r'_identity(\1, signal)',s)
s=s.replace('subject.member_id != address','subject.surface != owed')
s=s.replace('subject.member_id != surface_alarm_member_id(address)','subject.surface != address')
s=s.replace('subject.member_id or subject.scope_key','subject.scope_key')
s=s.replace('    LaneFieldValue,\n','')
s=s.replace('from kodezart.types.domain.run_state import LaneCommit, LaneEscalation\n','')
s=s.replace('from kodezart.types.domain.scope import ScopeRef\n','').replace('from kodezart.types.domain.surface import WritableSurface\n','')
s=s.replace('from kodezart.types.domain.escalation import (\n    EscalationResolution,\n    EscalationResolutionState,\n)','from kodezart.types.domain.escalation import EscalationResolutionState')
names=sorted(set(mapping.values())|{'Evidence','TextEvidence'})
s=s.replace('    AlarmBound,','    '+',\n    '.join(names)+',\n    AlarmBound,')
s=s.replace('JSON readings','typed readings').replace('after JSON decoding','as typed field values').replace('Values use JSON; their original bytes\n    and source references survive in the alarm.','Values remain typed; their original values\n    and source references survive in the alarm.').replace('JSON null label set','absent label set')
p.write_text(s)
p=root/'domain/mandate_graph.py';s=p.read_text()
s=s.replace('from typing import Annotated\n\nfrom pydantic import Field, NonNegativeInt, TypeAdapter\n\n','')
s=s.replace('import _decode, _unreadable','import _read_value, _unreadable')
start=s.index('_RULINGS =');end=s.index('\n\ndef _ruling_authors',start);s=s[:start]+s[end:]
for old,new in {'_RULINGS':'RulingsEvidence','_GRAPH':'GraphEvidence','_COUNT':'CountEvidence','_REFS':'ReferencesEvidence'}.items():
    s=s.replace(', '+old+', signal)',', '+new+', signal)')
s=s.replace('_decode(', '_read_value(')
s=s.replace('    AlarmBound,','    CountEvidence,\n    GraphEvidence,\n    ReferencesEvidence,\n    RulingsEvidence,\n    AlarmBound,')
s=s.replace('JSON readings','typed readings').replace('JSON LaneGraphSnapshot readings','typed LaneGraphSnapshot readings')
p.write_text(s)

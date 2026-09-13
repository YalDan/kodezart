from pathlib import Path
import re
root=Path('/private/tmp/kodezart-v03-recovery-alarm-owner/tests')
for p in root.rglob('*.py'):
    s=p.read_text()
    if 'AlarmSubject' not in s or p.name in {'test_run_alarm.py','test_alarm_typed_evidence.py','test_run_alarm_records.py'}:
        continue
    names=set()
    for kind,cls in {'SCOPE':'ScopeSubject','LANE':'LaneSubject','ISSUE':'IssueSubject','CRITERION':'CriterionSubject','SURFACE':'SurfaceSubject','ESCALATION':'EscalationSubject'}.items():
        pattern=r'AlarmSubject\(\s*kind=AlarmSubjectKind\.'+kind+r'\s*,'
        if re.search(pattern,s):
            names.add(cls);s=re.sub(pattern,cls+'(',s)
    if 'AlarmSubject.model_validate' in s:
        s=s.replace('AlarmSubject.model_validate_json(', 'TypeAdapter(AlarmSubject).validate_json(').replace('AlarmSubject.model_validate(', 'TypeAdapter(AlarmSubject).validate_python(')
        if 'from pydantic import ' in s:
            s=s.replace('from pydantic import ', 'from pydantic import TypeAdapter, ',1)
        else:s=s.replace('import pytest','import pytest\nfrom pydantic import TypeAdapter',1)
    s=re.sub(r'member_id=surface_alarm_member_id\((\w+)\)',r'surface=\1',s)
    if names:
        s=s.replace('from kodezart.types.domain.run_alarm import (','from kodezart.types.domain.run_alarm import (\n    '+',\n    '.join(sorted(names))+',',1)
    if '    surface_alarm_member_id,\n' in s:
        s=s.replace('    surface_alarm_member_id,\n','')
        s=s.replace('from kodezart.types.domain.run_alarm import (','from kodezart.domain.run_alarm_record import surface_alarm_member_id\nfrom kodezart.types.domain.run_alarm import (',1)
    p.write_text(s)

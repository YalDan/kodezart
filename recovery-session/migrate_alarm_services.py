from pathlib import Path
root=Path('/private/tmp/kodezart-v03-recovery-alarm-owner/src/kodezart/services')

def imports(s, names):
    anchor='from kodezart.types.domain.run_alarm import ('
    if anchor not in s:
        s=s.replace('from kodezart.types.domain.run_alarm import AlarmReading, AlarmSignal, RunAlarm',anchor+'\n    AlarmReading, AlarmSignal, RunAlarm,\n)')
    return s.replace(anchor,anchor+'\n    '+',\n    '.join(names)+',',1)

for name in ['run_shape','lane_record_signals','barren_record_signals','escalation_signals','scope_tally','mandate_graph']:
    p=root/(name+'.py');s=p.read_text()
    s=s.replace('import json\n\n','')
    s=s.replace('from pydantic import TypeAdapter, ValidationError','from pydantic import ValidationError').replace('from pydantic import TypeAdapter\n\n','')
    if name=='run_shape':
        s=s.replace('_REFERENCE_JSON = TypeAdapter(tuple[str, ...])\n\n\n','')
        start=s.index('    try:\n        record = LaneEscalation.model_validate_json');end=s.index('    subject = AlarmSubject(',start)
        s=s[:start]+'''    record = _read_value(escalation, EscalationEvidence, AlarmSignal.ESCALATION_AGEING)
'''+s[end:]
        s=s.replace('subject = AlarmSubject(\n        kind=AlarmSubjectKind.ESCALATION,','subject = EscalationSubject(')
        s=s.replace('subject = AlarmSubject(\n        kind=AlarmSubjectKind.LANE, scope_key=scope_key, lane_key=lane_key\n    )','subject = LaneSubject(scope_key=scope_key, lane_key=lane_key)')
        s=s.replace('value=resolution.model_dump_json(by_alias=True)','value=ResolutionEvidence(value=resolution)')
        s=s.replace('value=_REFERENCE_JSON.dump_json(closed_keys).decode("utf-8")','value=ReferencesEvidence(value=closed_keys)')
        s=s.replace('    barren_tick_with_diff_growth,','    _read_value,\n    barren_tick_with_diff_growth,')
        s=imports(s,['CountEvidence','EscalationEvidence','EscalationSubject','LaneSubject','ReferencesEvidence','ResolutionEvidence'])
    elif name=='lane_record_signals':
        s=s.replace('_COMMIT_ROWS = TypeAdapter(tuple[LaneCommit, ...])\n\n\n','')
        s=s.replace('subject = AlarmSubject(\n        kind=AlarmSubjectKind.LANE, scope_key=scope_key, lane_key=lane_key\n    )','subject = LaneSubject(scope_key=scope_key, lane_key=lane_key)')
        s=s.replace('json.dumps(record.lane_key)','TextEvidence(value=record.lane_key)').replace('json.dumps(record.head_sha)','TextEvidence(value=record.head_sha)')
        s=s.replace('_COMMIT_ROWS.dump_json(tuple(record.commits), by_alias=True).decode("utf-8")','CommitsEvidence(value=tuple(record.commits))')
        s=imports(s,['CommitsEvidence','CountEvidence','LaneSubject','TextEvidence'])
    elif name=='barren_record_signals':
        s=imports(s,['CountEvidence'])
    elif name=='escalation_signals':
        s=s.replace('value=escalation_comment.body.partition("\\n")[2]','value=EscalationEvidence(value=escalation)')
        s=s.replace('value=json.dumps(commit_order)','value=ReferencesEvidence(value=commit_order)')
        s=imports(s,['EscalationEvidence','ReferencesEvidence'])
    elif name=='scope_tally':
        s=s.replace('_LABELS = TypeAdapter(tuple[str, ...])\n_KEY = TypeAdapter(str)\n','')
        s=s.replace('value=_KEY.dump_json(current).decode()','value=TextEvidence(value=current)').replace('value=_KEY.dump_json(following).decode()','value=TextEvidence(value=following)')
        s=s.replace('value=scope.model_dump_json(by_alias=True)','value=ScopeEvidence(value=scope)').replace('value=_LABELS.dump_json(roster).decode()','value=ReferencesEvidence(value=roster)')
        s=s.replace('value=_LABELS.dump_json(\n                    tuple(sorted(facts[key].issue_labels))\n                ).decode()','value=LabelsEvidence(value=tuple(sorted(facts[key].issue_labels)))')
        s=s.replace('AlarmSubject(kind=AlarmSubjectKind.SCOPE, scope_key=scope.key)','ScopeSubject(scope_key=scope.key)')
        s=imports(s,['LabelsEvidence','ReferencesEvidence','ScopeEvidence','ScopeSubject','TextEvidence'])
    elif name=='mandate_graph':
        s=s.replace('_REFS = TypeAdapter(tuple[str, ...])\n\n\n','')
        s=s.replace('value=empty.model_dump_json()','value=RulingsEvidence(value=empty)')
        s=s.replace('source_ref=source_ref, value=snapshot.model_dump_json(by_alias=True)','source_ref=source_ref, value=RulingsEvidence(value=snapshot)')
        s=s.replace('value=snapshot.model_dump_json(by_alias=True)','value=GraphEvidence(value=snapshot)')
        start=s.index('    try:\n        snapshot = LaneRulingSnapshot.model_validate_json');end=s.index('    criteria: list[TrackerIssue]',start)
        s=s[:start]+'''    snapshot = _read_value(current_rulings, RulingsEvidence, AlarmSignal.RULINGS_OUTPACE_CLOSURES)
'''+s[end:]
        s=s.replace('value=_REFS.dump_json(closed).decode("utf-8")','value=ReferencesEvidence(value=closed)')
        s=s.replace('from kodezart.domain.errors import RunShapeReadError','from kodezart.domain.errors import RunShapeReadError\nfrom kodezart.domain.run_shape import _read_value')
        s=imports(s,['CountEvidence','GraphEvidence','ReferencesEvidence','RulingsEvidence'])
    import re
    s=re.sub(r'\bstr\((config\.run_alarm_\w+|record\.(?:commits_ahead|files_changed))\)',r'CountEvidence(value=\1)',s)
    s=s.replace('the LaneEscalation JSON','the typed LaneEscalation')
    p.write_text(s)

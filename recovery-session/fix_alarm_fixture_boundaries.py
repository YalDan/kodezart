from pathlib import Path
import ast
root=Path('/private/tmp/kodezart-v03-recovery-alarm-owner/tests')
changes={
'escalation_ageing':['test_a_decision_does_not_supply_a_missing_tick_count','test_recorded_counts_are_nonnegative_integers','test_commit_identities_are_nonempty_opaque_strings'],
'record_consistency':['test_declared_count_must_be_a_nonnegative_integer','test_only_an_explicit_successful_lookup_boolean_can_answer_presence'],
'record_superseded':['test_assertion_projection_is_closed_and_carries_explicit_string_values'],
'surface_contention':['test_limit_is_a_recorded_nonnegative_integer','test_run_holders_cannot_be_inferred_from_invalid_or_vendor_shaped_rows'],
'barren_tick':['test_all_counts_are_nonnegative_integers','test_closure_does_not_supply_unreadable_growth_counts'],
'mandate_graph':['test_required_author_is_a_closed_recorded_field','test_omitted_authorship_cannot_inherit_machine_attribution'],
}
for name,names in changes.items():
 p=root/'domain'/f'test_{name}.py';s=p.read_text(); lines=s.splitlines(keepends=True)
 for n in reversed(ast.parse(s).body):
  if isinstance(n,ast.FunctionDef) and n.name in names:
   block=''.join(lines[n.lineno-1:n.end_lineno]).replace('pytest.raises(RunShapeReadError)', 'pytest.raises(ValidationError)')
   block=block.replace('    assert raised.value.source_ref == "record/walker#ticks-since-question"\n','')
   lines[n.lineno-1:n.end_lineno]=[block]
 s=''.join(lines).replace('assert isinstance(raised.value.__cause__, ValidationError)','assert raised.value.__cause__ is None')
 p.write_text(s)
p=root/'domain/test_escalation_ageing.py';s=p.read_text().replace('"kodezart.domain.errors",','"kodezart.domain.errors",\n        "kodezart.domain.run_alarm_record",');p.write_text(s)
p=root/'domain/test_surface_contention.py';s=p.read_text().replace('alarm.subject.member_id == surface_alarm_member_id(surface)','alarm.subject.surface == surface');p.write_text(s)
p=root/'domain/test_barren_tick.py';s=p.read_text().replace('    with pytest.raises(RunShapeReadError):\n        evaluate(readings(**{name: value}))\n\n\n@pytest.mark.parametrize("slot", [4, 5])','    expected = RunShapeReadError if value == ("same", "same") else ValidationError\n    with pytest.raises(expected):\n        evaluate(readings(**{name: value}))\n\n\n@pytest.mark.parametrize("slot", [4, 5])');p.write_text(s)
p=root/'domain/test_record_superseded.py';s=p.read_text().replace('    with pytest.raises(RunShapeReadError):\n        observe(replace(original, 2, value=ReferencesEvidence(value=order)))','    expected = RunShapeReadError if order in ((), ("sha-z",), ("sha-a",), (*ORDER, "sha-z")) else ValidationError\n    with pytest.raises(expected):\n        observe(replace(original, 2, value=ReferencesEvidence(value=order)))');p.write_text(s)
p=root/'domain/test_mandate_graph.py';s=p.read_text().replace('    inputs[1] = reading(\n        {','    inputs[1] = reading(RulingsEvidence(value=LaneRulingSnapshot.model_validate(\n        {',1).replace('"rulings": [ruling("a", issue="OTHER")],\n        }\n    )','"rulings": [ruling("a", issue="OTHER")],\n        }\n    )))');s=s.replace('    subject = AlarmSubject(\n        kind=kind,\n        scope_key="scope",\n        issue_id="FIRE" if kind is AlarmSubjectKind.ISSUE else None,\n    )','    subject = (IssueSubject(scope_key="scope", issue_id="FIRE")\n        if kind is AlarmSubjectKind.ISSUE else ScopeSubject(scope_key="scope"))');s=s.replace('    AlarmSubject,','    AlarmSubject,\n    IssueSubject,\n    ScopeSubject,');p.write_text(s)
p=root/'tracker/test_recorded_ruling_growth.py';s=p.read_text().replace('    LaneSubject,','    LaneSubject,\n    IssueSubject,');p.write_text(s)
p=root/'tracker/test_mandate_graph.py';s=p.read_text().replace('from kodezart.types.domain.run_alarm import AlarmSubject, AlarmSubjectKind','from kodezart.types.domain.run_alarm import LaneSubject, ReferencesEvidence, RulingsEvidence\nfrom kodezart.types.domain.mandate_graph import LaneRulingSnapshot');s=s.replace('reading(snapshot)','reading(RulingsEvidence(value=LaneRulingSnapshot.model_validate(snapshot)))').replace('reading(current)','reading(RulingsEvidence(value=LaneRulingSnapshot.model_validate(current)))').replace('reading(["criterion/open"])','reading(ReferencesEvidence(value=("criterion/open",)))');p.write_text(s)
# Exhaustive call guard migrations preserve the existing finite allowlists.
for file,names in [('test_barren_record_collector.py',['ReferencesEvidence']),('test_barren_tick.py',['CountEvidence']),('test_escalation_record_reader.py',['EscalationEvidence','ReferencesEvidence'])]:
 p=root/'tracker'/file;s=p.read_text();s=s.replace('        "AlarmReading",','        "AlarmReading",\n'+''.join(f'        "{n}",\n' for n in names));p.write_text(s)

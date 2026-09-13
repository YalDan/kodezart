from pathlib import Path
root=Path('/private/tmp/kodezart-v03-recovery-alarm-owner/tests/domain')
# Wrong variant remains a fully validated AlarmReading, preserving source refusal.
for name in ('escalation_ageing','surface_contention','barren_tick','record_superseded','mandate_graph'):
 p=root/f'test_{name}.py';s=p.read_text()
 if '    TextEvidence,' not in s:s=s.replace('    AlarmReading,','    AlarmReading,\n    TextEvidence,')
 s=s.replace('update={"value": value}', 'update={"value": TextEvidence(value=value)}').replace('update={"value": raw}', 'update={"value": TextEvidence(value=raw)}')
 s=s.replace('replace(original, slot, value=value)', 'replace(original, slot, value=TextEvidence(value=value))')
 s=s.replace('test_malformed_readings_raise_with_signal_source_and_validation_cause','test_wrong_evidence_arm_raises_with_signal_and_source')
 s=s.replace('test_malformed_readings_are_typed_errors_with_the_source','test_wrong_evidence_arm_is_a_typed_error_with_the_source')
 p.write_text(s)
p=root/'test_record_consistency.py';s=p.read_text();start=s.index('@pytest.mark.parametrize(\n    "rows",', s.index('def test_declared_count'));end=s.index('\n\n@pytest.mark.parametrize(',start+1);s=s[:start]+'''@pytest.mark.parametrize(
    "rows,expected",
    [
        ([{"sha": "x", "subject": "s"}], ValidationError),
        ([{"sha": "x", "subject": "s", "issue_id": "i", "explanation": "extra"}], ValidationError),
        ([{"sha": 3, "subject": "s", "issue_id": "i"}], ValidationError),
        ([{"sha": " ", "subject": "s", "issue_id": "i"}], RunShapeReadError),
        ([{"sha": "x", "subject": "s", "issue_id": "i"}] * 2, RunShapeReadError),
        (["x"], ValidationError),
        ({}, ValidationError),
        (None, ValidationError),
    ],
)
def test_unreadable_or_ambiguous_commit_rows_do_not_clear_the_signal(rows, expected):
    with pytest.raises(expected):
        evidence = CommitsEvidence.model_validate({"value": rows})
        readings = replace(commit_readings(count=0), 3, value=evidence)
        evaluate(commits_ahead_of_record, lane_subject(), readings)
'''+s[end:]
s=s.replace('@pytest.mark.parametrize("value", ["not-json", "null", \'""\', \'" "\', "7"])','@pytest.mark.parametrize("value", [CountEvidence(value=7), TextEvidence(value=""), TextEvidence(value=" ")])')
p.write_text(s)
p=root/'test_scope_tally.py';s=p.read_text().replace('from pydantic import ', 'from pydantic import ValidationError, ',1) if 'from pydantic import ' in p.read_text() else p.read_text().replace('import pytest','import pytest\nfrom pydantic import ValidationError');s=s.replace('    elif damage == "empty-member-key":\n        values[3] = reading(SCOPE.key, TextEvidence(value="wrong evidence arm"))','    elif damage == "empty-member-key":\n        with pytest.raises(ValidationError):\n            reading(SCOPE.key, ReferencesEvidence(value=("",)))\n        return');p.write_text(s)

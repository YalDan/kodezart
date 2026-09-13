from pathlib import Path
root=Path('/private/tmp/kodezart-v03-recovery-alarm-owner/tests/tracker')
for name in ['test_escalation_ageing','test_escalation_record_collector','test_barren_tick','test_barren_record_collector','test_recorded_ruling_growth']:
 p=root/(name+'.py');s=p.read_text()
 anchor='from kodezart.types.domain.run_alarm import '
 start=s.index(anchor);end=s.index('\n',start)
 old=s[start:end]
 names=['AlarmReading','CountEvidence','ReferencesEvidence','TextEvidence']
 if name.startswith('test_escalation'):
  names+=['EscalationEvidence']
 if name=='test_recorded_ruling_growth': names+=['LaneSubject']
 if 'AlarmSignal' in old:names+=['AlarmSignal']
 s=s[:start]+anchor+'('+', '.join(names)+')'+s[end:]
 s=s.replace('value=comment.body.partition("\\n")[2]','value=EscalationEvidence(value=record)')
 s=s.replace('value=json.dumps(["before", "raised", "after", "latest"])','value=ReferencesEvidence(value=("before", "raised", "after", "latest"))')
 s=s.replace('value="3"','value=CountEvidence(value=3)').replace('value="11"','value=CountEvidence(value=11)').replace('value="6"','value=CountEvidence(value=6)')
 s=s.replace('value="{}"','value=TextEvidence(value="wrong evidence kind")')
 s=s.replace('value=\'["previous/criterion"]\'','value=ReferencesEvidence(value=("previous/criterion",))').replace('value=\'["criterion/open"]\'','value=ReferencesEvidence(value=("criterion/open",))')
 s=s.replace('value=json.dumps([OLD])','value=ReferencesEvidence(value=(OLD,))').replace('value=json.dumps((OLD,))','value=ReferencesEvidence(value=(OLD,))')
 s=s.replace('LaneRulingSnapshot.model_validate_json(alarm.readings[1].value)','alarm.readings[1].value.value')
 s=s.replace('json.loads(baseline.value)["rulings"] == []','baseline.value.value.rulings == ()')
 s=s.replace('json.loads(result.readings[1].value) == [NEW]','result.readings[1].value.value == (NEW,)')
 s=s.replace('[item.value for item in result.readings[2:4]] == [str(files), str(commits)]','[item.value.value for item in result.readings[2:4]] == [files, commits]')
 s=s.replace('alarm.readings[0].value == question.body.partition("\\n")[2]','alarm.readings[0].value.value == LaneEscalation.model_validate_json(question.body.partition("\\n")[2])')
 if 'LaneEscalation.model_validate_json' in s and 'import LaneEscalation' not in s and '    LaneEscalation,' not in s:
  s=s.replace('from kodezart.types.domain.run_alarm import ','from kodezart.types.domain.run_state import LaneEscalation\nfrom kodezart.types.domain.run_alarm import ')
 s=s.replace('json.loads(alarm.readings[2].value) == ["first-sha", "head-full-identity"]','alarm.readings[2].value.value == ("first-sha", "head-full-identity")')
 p.write_text(s)

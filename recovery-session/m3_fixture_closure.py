import sys,ast,json,subprocess
from pathlib import Path
sys.path.insert(0,'/private/tmp/kodezart-recovery-session');import extract_m2 as h
h.T=Path('/private/tmp/kodezart-v03-m3-plan-walk'); O=Path('/private/tmp/kodezart-recovery-session')
for p in ['tests/prompts/test_prompt_wiring.py','tests/prompts/test_criteria_generation_prompts.py','tests/chains/test_criteria_validation.py','tests/integration/test_criteria_oracle.py','tests/workflow_factory.py','tests/adapters/test_ci_rerun.py']:
 h.whole(p)
# Preserve M1 config packaging while carrying all accepted CriterionClass removal.
p=h.T/'tests/prompts/test_prompt_wiring.py';p.write_text(p.read_text().replace('KODEZART_AGENT__MODEL','KODEZART_MODEL'))
p='tests/fakes.py'
for n in ['make_passing_evaluation_of_fake_criteria']:h.symbol(p,n)
s=(h.T/p).read_text().replace('        scope_label_members: Mapping[ScopeRef, frozenset[ScopeLabel]] | None = None,','        criteria_stage_label_key: str | None = None,\n        scope_label_members: Mapping[ScopeRef, frozenset[ScopeLabel]] | None = None,').replace('        self.scope_label_members = dict(scope_label_members or {})','        self.criteria_stage_label_key = criteria_stage_label_key\n        self.scope_label_members = dict(scope_label_members or {})');(h.T/p).write_text(s)
p='tests/test_forge_origin_selection.py'
for n in ['ForbiddenWorkflowEngine','_drive','_arm','RecordingForge']:h.symbol(p,n)
p='tests/domain/test_amendment.py';s=(h.T/p).read_text();n=h.node(s,'test_actual_scope_egress_roundtrips_required_nulls_and_rejects_bad_native_reports');lines=s.splitlines(keepends=True);del lines[h.start(n):n.end_lineno];s=''.join(lines).replace('from kodezart.handlers.agent_handler import _queued_event_payload\n','').replace('from kodezart.types.domain.scope_runtime import ScopeLaneEvent\n','');(h.T/p).write_text(s)
# Entire test module is M6 audit-consumer closure, no M3 consumer.
(h.T/'tests/tracker/test_criterion_resolution_consumers.py').unlink()
# Existing actual admission behavior is retained until M1 owns admission replacement.
p='tests/adapters/test_git_change_persister.py';(h.T/p).write_bytes(subprocess.check_output(['git','show','fadf6efe:'+p],cwd=h.T))
p='tests/services/test_fire_context.py'
for n in ['shipped_gate']: # restore only existing gate assembly, retain stronger new context assertions
 donor=h.D; h.D=h.T
 old=subprocess.check_output(['git','show','fadf6efe:'+p],cwd=h.T).decode();cur=(h.T/p).read_text();a=h.node(old,n);b=h.node(cur,n);ls=cur.splitlines(keepends=True);ls[h.start(b):b.end_lineno]=old.splitlines(keepends=True)[h.start(a):a.end_lineno];cur=''.join(ls).replace('from kodezart.adapters.outbound_admission import OutboundAdmission','from kodezart.adapters.pattern_outbound_gate import PatternOutboundContentGate\nfrom kodezart.adapters.regex_content_scanner import RegexContentScanner').replace('from tests.outbound import make_admission\n','');(h.T/p).write_text(cur);h.D=donor
log=O/'m3-extracted-hunks.json';log.write_text(json.dumps(json.loads(log.read_text())+h.records,indent=2)+'\n')

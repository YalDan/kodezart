import sys,json
from pathlib import Path
sys.path.insert(0,'/private/tmp/kodezart-recovery-session');import extract_m2 as h
h.T=Path('/private/tmp/kodezart-v03-m3-plan-walk')
p='tests/fakes.py'
for n in ['as_validated','make_minted_criteria','make_criteria','make_generated_criteria','FakeAgentExecutor','ScriptedFakeExecutor','FakeWorkspaceProvider','FakeChangePersister','FakeAgentRunner','FakeQualityGate','FakeRemediator','FakeTicketGenerator','FakeCIMonitor']:
 h.symbol(p,n)
s=(h.T/p).read_text().replace('    CriterionClass,\n','');(h.T/p).write_text(s)
for n in ['worktree_identity']:h.symbol(p,n,'FakeGitService')
for n in ['read_fire_spec','reset_criterion_pending']:h.symbol(p,n,'FakeTrackerPort')
log=Path('/private/tmp/kodezart-recovery-session/m3-extracted-hunks.json');log.write_text(json.dumps(json.loads(log.read_text())+h.records,indent=2)+'\n')

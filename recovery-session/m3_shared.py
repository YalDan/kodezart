import sys,json,ast
from pathlib import Path
sys.path.insert(0,'/private/tmp/kodezart-recovery-session')
import extract_m2 as h
h.T=Path('/private/tmp/kodezart-v03-m3-plan-walk')
original=h.key
def key(n):return n.name.id if isinstance(n,ast.TypeAlias) else original(n)
h.key=key
for p in ['core/node_sessions.py','types/domain/node_session.py','types/domain/check_observation.py','services/check_classification.py','adapters/git_change_persister.py','adapters/git_worktree_provider.py']:
 h.whole('src/kodezart/'+p)
p='src/kodezart/types/domain/delivery.py';(h.T/p).write_text('"""Typed check observations shared by authored execution and delivery."""\n')
for n in ['CheckRedClass','CheckRedObservation']:h.symbol(p,n)
for p,names in {
'core/protocols.py':['TrackerContextReader','FireCriteriaReader','FireCriteriaSource','NativeWriteGuard'],
'domain/errors.py':['CheckObservationError','CriterionResolutionError','EmptyFireCriteriaError','FireSpecEntryError','ScopePlanRefusalError','ScopeSupersessionReadError','ScopedExecutionUnavailableError'],
'domain/pr_body.py':['append_tracker_issue','require_tracker_issue'],
'domain/prompt_variables.py':['execution_criteria_variables','tracker_checks_section'],
}.items():
 for n in names:h.symbol('src/kodezart/'+p,n)
for parent,names in {'GitService':['worktree_identity'],'WorkspaceProvider':['capture','resume'],'ChangePersister':['persist'],'CIMonitor':['wait_for_checks','rerun_checks'],'AgentExecutor':['stream'],'AgentRunner':['stream'],'TrackerPort':['read_fire_spec']}.items():
 for n in names:
  try:h.symbol('src/kodezart/core/protocols.py',n,parent)
  except StopIteration:print('ABSENT METHOD',parent,n)
for n in ['CheckPrerequisite','CheckStep']:h.symbol('src/kodezart/types/domain/operation.py',n)
for n in ['checks','runner_environment']:h.symbol('src/kodezart/types/domain/operation.py',n,'RepoEntry')
for n in ['WorkRefLanding']:h.symbol('src/kodezart/types/domain/branch.py',n)
for n in ['TaskUsageInfo','NodeSessionStartedEvent','AuthoredWorkflowCompleteEvent','NativeAmendmentEvent','WorkflowCompleteEvent','WorkflowConsolidationEvent','CriterionResult','GeneratedCriteriaOutput','WorkflowIterationEvent','WorkflowReviewEvent','WorkflowTicketDraftEvent','WorkflowTicketReviewEvent','WorkflowTicketEvent','WorkflowPREvent','WorkflowCIEvent','RaiseSite']:
 h.symbol('src/kodezart/types/domain/agent.py',n)
for n in ['AMENDMENT_JUDGMENT_SCHEMA','AMENDMENT_TEXT_SCHEMA','NATIVE_WRITER_SCHEMA','REMEDIATION_SCHEMA']:
 h.symbol('src/kodezart/types/domain/agent.py',n)
# Narrow schema registry entries, constants must precede their actual registry use.
p=h.T/'src/kodezart/types/domain/agent.py';s=p.read_text();tree=ast.parse(s);names=['AMENDMENT_JUDGMENT_SCHEMA','AMENDMENT_TEXT_SCHEMA','NATIVE_WRITER_SCHEMA','REMEDIATION_SCHEMA'];chunks=[];lines=s.splitlines(keepends=True)
for n in sorted([n for n in tree.body if key(n) in names],key=lambda n:n.lineno,reverse=True):chunks.insert(0,''.join(lines[n.lineno-1:n.end_lineno]));del lines[n.lineno-1:n.end_lineno]
s=''.join(lines);at=s.index('WIRE_SCHEMAS:');s=s[:at]+ '\n'.join(chunks)+'\n'+s[at:];marker='WIRE_SCHEMAS: dict[str, dict[str, object]] = {'
assert marker in s;s=s.replace(marker,marker+'\n'+''.join('    "'+n+'": '+n+',\n' for n in names),1);p.write_text(s)
log=Path('/private/tmp/kodezart-recovery-session/m3-extracted-hunks.json');log.write_text(json.dumps(json.loads(log.read_text())+h.records,indent=2)+'\n')

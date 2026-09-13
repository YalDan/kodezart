import sys,json
from pathlib import Path
sys.path.insert(0,'/private/tmp/kodezart-recovery-session');import extract_m2 as h
h.T=Path('/private/tmp/kodezart-v03-m3-plan-walk')
for p in ['types/domain/outcome.py','core/stream_drain.py','composition/workspace.py','adapters/claude_agent_executor.py','adapters/claude_client_executor.py']:
 h.whole('src/kodezart/'+p)
p='src/kodezart/types/domain/run_event.py';(h.T/p).write_text('"""Shared native run-event names; publication belongs to its actual owner."""\n');h.symbol(p,'RunEventKind')
for p,names in {
'domain/errors.py':['PRTrackerIdentityError','GitOperationError','GitRepositoryError'],
'domain/stall_report.py':['stall_pr_body'],
'adapters/_mcp_mapping.py':['prompt_with_knowledge_map'],
}.items():
 for n in names:h.symbol('src/kodezart/'+p,n)
for parent,names in {'CIMonitor':['checks_declared'],'AgentRunner':['stream_in_workspace','stream_workflow'],'QualityGate':['run'],'Remediator':['run'],'TicketGenerator':['run'],'WorkflowEngine':['run'],'TrackerPort':['reset_criterion_pending']}.items():
 for n in names:h.symbol('src/kodezart/core/protocols.py',n,parent)
for n in ['forge_exempt']:h.symbol('src/kodezart/types/domain/operation.py',n,'RepoEntry')
for n in ['landing']:h.symbol('src/kodezart/types/domain/branch.py',n,'WorkRef')
for n in ['criterion_keys']:h.symbol('src/kodezart/types/domain/dispatch.py',n,'DispatchReport')
for n in ['WorkflowRemediationEvent']:h.symbol('src/kodezart/types/domain/agent.py',n)
for n in ['NATIVE_WRITER_CONTRACT','AMENDMENT_JUDGE','AMENDMENT_AUTHOR']:h.symbol('src/kodezart/types/domain/prompts.py',n,'PromptKey')
for n in ['_worktree_identity','validate_repo','_run','_run_output','_run_with_exit_codes','remote_branch_sha','_ref_exists']: 
 try:h.symbol('src/kodezart/adapters/subprocess_git_service.py',n,'SubprocessGitService')
 except StopIteration:print('ABSENT',n)
for n in ['read_fire_spec','reset_criterion_pending']:h.symbol('src/kodezart/adapters/linear_mcp_tracker.py',n,'LinearMcpTracker')
p=h.T/'src/kodezart/adapters/linear_mcp_tracker.py';s=p.read_text();s=s.replace('        scope_labels: Mapping[str, str],\n','        scope_labels: Mapping[str, str],\n        criteria_stage_label_key: str | None,\n',1);s=s.replace('        self._scope_labels = dict(scope_labels)\n','        self._scope_labels = dict(scope_labels)\n        self._criteria_stage_label_key = criteria_stage_label_key\n',1);p.write_text(s)
h.symbol('src/kodezart/composition/tracker.py','build_tracker')
log=Path('/private/tmp/kodezart-recovery-session/m3-extracted-hunks.json');log.write_text(json.dumps(json.loads(log.read_text())+h.records,indent=2)+'\n')

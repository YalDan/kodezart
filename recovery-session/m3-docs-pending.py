from pathlib import Path
D=Path('/private/tmp/kodezart-v03-recovery-integration');T=Path('/private/tmp/kodezart-v03-m3-plan-walk')
p=T/'docs/architecture.md';s=p.read_text();d=(D/'docs/architecture.md').read_text()
start=s.index('## Workflow Pipeline');end=s.index('## Ticket Generation Loop',start)
a=d.index('## Workflow Pipeline');b=d.index('## Ticket Generation Loop',a);section=d[a:b]
section=section[:section.index('This extraction does not wire')] + '''The native graph of `RalphWorkflowEngine` uses the same execution,
consolidation, review and remediation phases. Its input comes from
current tracker Checks through `TrackerCriteria`; its graph has no ticket or
criteria generation nodes. Current reads guard execution, retry, review,
remediation and checkpoint replay. Native amendment claims pass through the
independent judgment and canonical write-back services before persistence.
Workspace receipts retain native branch, worktree and Git identity; they support
same-host checkpoint continuation without repeating a completed writer or push.
They do not establish durable cross-job or public restart support.

Scope planning and readiness are available through `services/scope_planning.py`
and `chains/scope_walker.py`. Final container-scope orchestration consumes the
native delivery graph and belongs to the delivery composition. Until that
composition is supplied, the public router's addressed scope arm explicitly
refuses; an addressed request cannot fall through to authored execution.

'''
s=s[:start]+section+s[end:]
# Existing map gets actual first-consumer contracts only.
s=s.replace('| CIMonitor         | GitHubAPIClient          | Polls check runs for a pushed head                   |','| CIMonitor         | GitHubAPIClient          | Returns typed completed, absent or incomplete check observations; re-observes Actions attempts at one commit |')
s=s.replace('| WorkflowEngine    | RalphWorkflowEngine      | LangGraph outer pipeline                             |','| WorkflowEngine    | AuthoredDeliveryCoordinator | Authored orchestration around the shared fire graph |')
p.write_text(s)
p=T/'docs/configuration.md';s=p.read_text();s+='''
## Authored check observation bounds

`KODEZART_DELIVERY_MAX_CONCURRENT_WATCHES` bounds concurrent check watches
(default 4, inclusive range 1–32). `KODEZART_DELIVERY_RED_RERUN_MAX_ATTEMPTS`
bounds same-commit reruns before a red is treated as reproduced (default 1,
inclusive range 0–5). Both settings are consumed by the authored check phase.
Repository declarations can supply ordered `checks`, typed
`runner_environment` prerequisite facts, and `forge_exempt`; absent prerequisite
facts remain unavailable rather than implying capability.

The CI adapter resolves a rerun ref once, validates complete workflow-attempt
identity, and issues each rerun POST once. Lost responses and partial batches
remain errors. Follow-up observation stays on that same commit and asynchronous
task; old completed checks cannot satisfy a requested new attempt. Attempt
tracking is process-local and does not provide a durable rerun ledger.
''';p.write_text(s)

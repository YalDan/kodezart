from pathlib import Path
T=Path('/private/tmp/kodezart-v03-m5-delivery-termination');D=Path('/private/tmp/kodezart-v03-m5-union-roster-correction')
def section(s,start,end):return s[s.index(start):s.index(end,s.index(start))]
p=T/'docs/architecture.md';src=(D/'docs/architecture.md').read_text();chunk=section(src,'## Check-chain execution','## Current-head audit claim sessions').replace('Cleanup repeats group termination at its configured polling cadence until','Cleanup repeats group termination at its fixed adapter-owned cadence until');p.write_text(p.read_text()+'\n'+chunk)
p=T/'docs/configuration.md';src=(D/'docs/configuration.md').read_text();rows='\n'.join(l for l in src.splitlines() if l.startswith('| `KODEZART_UNION_'));retired=section(src,'The `union_check_cleanup_poll_interval_seconds`','The legacy aggregate-pattern scanner');p.write_text(p.read_text()+'\n## Scratch union verification\n\n| Variable | Type | Default | Bounds | Purpose |\n| --- | --- | --- | --- | --- |\n'+rows+'\n\n'+retired)
p=T/'.env.example';src=(D/'.env.example').read_text();chunk=section(src,'# Per-step deadline for scratch union checks.','#',) if False else '\n'.join(src.splitlines()[178:183]);p.write_text(p.read_text()+'\n'+chunk+'\n')
p=T/'docs/api.md';s=p.read_text();src=(D/'docs/api.md').read_text();row=next(l for l in src.splitlines() if l.startswith('| `scope`'));needle=next(l for l in s.splitlines() if l.startswith('| `baseBranch`'));s=s.replace(needle,row+'\n| `issueKey` | `string \\| null` | No | `null` | Native issue identity for an addressed request |\n'+needle,1);at=s.index('### Example',s.index('## POST /api/v1/agent/workflow'));intro=section(src,'`scope.kind` accepts','Scoped graph execution is not yet implemented.');s=s[:at]+intro+'A configured tracker scope is routed through the native scope controller. Without that capability, a valid scoped request refuses before the prompt workflow; it never silently drops its address.\n\n'+s[at:]
start=s.index('### Workflow Events');end=s.index('### Job Events',start);new=section(src,'### Workflow Events','### Job Events');s=s[:start]+new+s[end:];p.write_text(s)
p=T/'docs/delivery.md';s=(D/'docs/delivery.md').read_text();s=s.replace('Scoped execution currently refuses before preparation:\nthere is no active scope walker or independent delivery coordinator.','Scoped execution uses the actual native fire, lane delivery and scope controller\nconstructed by the same engine builder.');s=s.replace('The single `classify_red_checks` service also serves\n`AuditForgeVerifier`. The original failing set comes from `failed_check_names`\nat the watched branch; `CIObservationReader` supplies its retained SHA and check\nroster from the same native response. Neither read requests another check set.','The single `classify_red_checks` service also serves native lane delivery.\nThe watch returns a typed immutable observation carrying the commit, roster and\nfailing set together. No later read substitutes a different check set.');at=s.index('The retired alternative');s=s[:at]+'''## Native lane delivery and scoped requests

`build_native_lane_workflow` composes the real `NativeLaneWorkflow` with
`LaneDeliveryCoordinator`. The completed result carries the exact addressed PR,
head and base repository/branch identities, the final commit SHA, a coherent
check observation, structural red classification and existing workflow outcome.
Known open PRs are read before generation or creation. Head/base drift or an
unreadable native PR refuses instead of reporting an old observation.

Native delivery state is a typed pending/completed/skipped union. Pending repair
uses the existing native remediation graph and its budget. A skipped path names
its existing outcome and reason without inventing a PR; only completed/skipped
states become `lane_delivery` events inside the typed scope lane envelope.

`build_scope_runtime` binds the real delivery graphs to current native scope
reads. One request is one queue job; eligible approved lanes run serially and
retain their run identity and per-lane checkpoint. Resume validates current
criteria and the original scope, repository and resolved base. A controller
return is not a scope convergence verdict: unresolved criteria, unapproved and
skipped lanes remain visible in `scope_walk` observations.

## Retained union and terminal work

`ScopeUnionCoordinator` obtains the complete structurally valid retained lane
roster through the shared scope fact reader, including completed participants.
It consumes the planner's order and verifies exactly one recorded deliverable
head per participant. The existing `UnionTick` lock encloses roster/head checks,
cache reuse, scratch composition and final freshness. Future-stage admission
barriers remain in M3's separate `read_scope_plan` entry.

The scratch runner composes pinned commits, preserves every named ref and remote,
and removes its detached worktree on every exit. Its typed scope result names
heads, order, scratch observation and exactly one matching remediation entry for
a red result. The runner's fixed cleanup cadence is adapter mechanics; its
per-step timeout and the stale-head attempt bound are explicit settings.

The scope controller does not yet invoke the union at every walk tick or publish
a durable union residual. The current lane-report collector preserves an entry
for every dispatched lane, initialized as `UNREPORTED`; it does not implement
L6's required terminal vector, native residual readback, durable restart or
container status writer. Those are finite remaining implementation obligations.
The terminal stopping-rule contract still needs its recorded decision for a
nonexecutable residual when no configured numeric bound fired. No default bound,
record reference, tracker issue state write or PR merge is invented here.
''';p.write_text(s)

exec(open('/private/tmp/kodezart-recovery-session/extract_m1_scope.py').read().split("p='src/kodezart/types/domain/operation.py'")[0])
p='tests/fakes.py'

if '    ScopeLabel,' not in (R/p).read_text(): replace(p,'    QueueState,','    QueueState,\n    ScopeLabel,')
replace(p,'from kodezart.domain.surface_lease import live_conflict, surface_address','from kodezart.domain.scope_approval import resolve_execution_approval\nfrom kodezart.domain.surface_lease import live_conflict, surface_address')
replace(p,'        scope_memberships: Mapping[ScopeRef, Sequence[str]] | None = None,','        scope_memberships: Mapping[ScopeRef, Sequence[str]] | None = None,\n        scope_label_members: Mapping[ScopeRef, frozenset[ScopeLabel]] | None = None,')
replace(p,'        known_identifiers: Sequence[str] = (),','        known_identifiers: Sequence[str] = (),\n        scope_label_identifiers: Sequence[str] = (),')
replace(p,'        self.recorded_work_refs: dict[str, list[WorkRef]] = {','        self.scope_label_members = dict(scope_label_members or {})\n        self.recorded_work_refs: dict[str, list[WorkRef]] = {')
replace(p,'        self.known_identifiers: set[str] = set(known_identifiers)','        self.known_identifiers: set[str] = set(known_identifiers)\n        #: Scope definitions must exist in all native namespaces.\n        self.scope_label_identifiers: set[str] = set(scope_label_identifiers)')
replace(p,'            identifier: {None} for identifier in self.known_identifiers','            identifier: {None}\n            for identifier in self.known_identifiers | self.scope_label_identifiers')
add_units(p,['FakeTrackerPort.project_milestones','FakeTrackerPort.execution_approved','FakeTrackerPort._read_execution_approval','FakeTrackerPort.read_scope_labels'],'    async def container_metadata(')
for name in ['FakeTrackerPort.resolve_mappings','FakeTrackerPort.ensure_mappings','FakeLinearMcpServer._tool_list_issue_labels']:
 edit(p,lambda s,n=name:s.replace(symbol(s,n),symbol(source(p),n)))
add_units(p,['FakeTrackerPort._ensure_scope_label'],'    def _ensure_document(')
add_units(p,['FakeLinearMcpServer._label_page','FakeLinearMcpServer._tool_list_project_labels','FakeLinearMcpServer._tool_list_initiative_labels','FakeLinearMcpServer._tool_save_project_label','FakeLinearMcpServer._tool_create_initiative_label'],'    def _label_entry(')
replace(p,'        labels: Sequence[str] = (),','        labels: Sequence[str] = (),\n        project_labels: Sequence[str] = (),\n        initiative_labels: Sequence[str] = (),\n        label_page_size: int | None = None,')
replace(p,'        self.labels: list[str] = list(labels)','        self.labels: list[str] = list(labels)\n        self.project_labels: list[str] = list(project_labels)\n        self.initiative_labels: list[str] = list(initiative_labels)\n        self.label_page_size = label_page_size')
# Every actual direct constructor must receive an explicit mapping. Existing unrelated
# fixtures declare none; the approval helper passes the test-supplied mapping.
for path in (R/'tests').rglob('*.py'):
 s=path.read_text();tree=ast.parse(s);lines=s.splitlines(True);updates=[]
 for node in ast.walk(tree):
  if isinstance(node,ast.Call) and isinstance(node.func,ast.Name) and node.func.id=='LinearMcpTracker':
   if any(k.arg=='scope_labels' for k in node.keywords) or any(k.arg is None for k in node.keywords):continue
   k=next(k for k in node.keywords if k.arg=='issue_labels');indent=' '*(k.col_offset)
   updates.append((k.lineno-1,indent+'scope_labels={},\n'))
 for pos,line in sorted(updates,reverse=True):lines.insert(pos,line)
 if updates:path.write_text(''.join(lines))
p='tests/tracker/test_linear_mcp_tracker.py';replace(p,'        "issue_labels": {},','        "issue_labels": {},\n        "scope_labels": {},')
p='tests/tracker/conftest.py';replace(p,'    server: FakeLinearMcpServer,\n    *,\n    clock: Callable[[], datetime] = _frozen_now,','    server: FakeLinearMcpServer,\n    *,\n    scope_labels: Mapping[str, str] | None = None,\n    clock: Callable[[], datetime] = _frozen_now,');replace(p,'        scope_labels={},','        scope_labels=scope_labels if scope_labels is not None else {},')
p='docs/operation.example.toml';d=source(p);a=d.index('# Scope admission is separate');b=d.index('# Lifecycle states',a);replace(p,'# Lifecycle states, validated exactly parallel to queue_states.',d[a:b]+'# Lifecycle states, validated exactly parallel to queue_states.')
p='docs/cutover_mapping.md';replace(p,'| Queue and lifecycle vocabulary |','| Scope admission labels | `OperationConfig.scope_labels`; native label reads and workspace bootstrap |\n| Queue and lifecycle vocabulary |');replace(p,'| queue_states.triage | queue_states |','| scope_labels.triage | scope_labels |\n| scope_labels.proposed | scope_labels |\n| scope_labels.approved | scope_labels |\n| queue_states.triage | queue_states |')
p='docs/configuration.md';edit(p,lambda s:s+'\n### Scope admission labels\n\n`[scope_labels]` maps `triage`, `proposed`, and `approved` to tracker label\nnames. An absent or empty table is valid; a populated table requires all\nthree keys and may include additional keys. Templates receive the mapping\nthrough `scope_labels`, or `scope_labels_absent` when no mapping is declared.\n\nTracker bootstrap creates missing definitions in the issue, project, and\ninitiative namespaces and reads them back. It preserves existing definitions\nand does not apply approval to an entity. A conflicting team-scoped issue\nlabel is refused. Native approval reads use current issue ancestry and the\naddressed issue\'s project and initiative ancestry; a milestone inherits its\nproject\'s approval. An absent approved-label mapping refuses the approval\nread at use. These primitives do not yet add scoped HTTP workflow submission.\n')

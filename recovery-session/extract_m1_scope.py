import ast,subprocess
from pathlib import Path
R=Path('/private/tmp/kodezart-v03-m1-scope-bindings-extraction'); D='36083f83'; M='73cc5def'
def source(p,ref=D):return subprocess.check_output(['git','-C',str(R),'show',ref+':'+p],text=True)
def symbol(s,name):
 nodes=ast.parse(s).body
 for part in name.split('.'):
  node=next(n for n in nodes if getattr(n,'name',None)==part);nodes=node.body
 start=min([node.lineno]+[x.lineno for x in getattr(node,'decorator_list',[])])
 return ''.join(s.splitlines(True)[start-1:node.end_lineno])
def edit(p,fn):
 path=R/p;path.write_text(fn(path.read_text()))
def replace(p,a,b):
 def f(s):
  assert s.count(a)==1,(p,a,s.count(a));return s.replace(a,b)
 edit(p,f)
def copy(p):
 (R/p).parent.mkdir(parents=True,exist_ok=True);(R/p).write_text(source(p))
def add_units(p,names,anchor,ref=D):
 data='\n\n'.join(symbol(source(p,ref),n).rstrip() for n in names)+'\n\n'
 replace(p,anchor,data+anchor)
p='src/kodezart/types/domain/operation.py'
add_units(p,['ScopeLabel'],'class OperationConfig')
replace(p,'    marker_prefixes: dict[str, str] = Field(default_factory=dict)','    marker_prefixes: dict[str, str] = Field(default_factory=dict)\n\n    scope_labels: dict[str, str] = Field(default_factory=dict)')
replace(p,'        failures: list[str] = []\n\n        if self.principals:', '        failures: list[str] = []\n\n        if self.scope_labels:\n            for scope_label in ScopeLabel:\n                if scope_label.value not in self.scope_labels:\n                    failures.append(\n                        f"scope_labels is missing required key {scope_label.value!r}"\n                    )\n\n        if self.principals:')
replace(p,'    "issue_labels": ConfigOwnership.OWNED,','    "issue_labels": ConfigOwnership.OWNED,\n    "scope_labels": ConfigOwnership.OWNED,')
p='src/kodezart/types/domain/tracker.py'
replace(p,'    QUEUE_STATE = "queue_state"','    QUEUE_STATE = "queue_state"\n    SCOPE_LABEL = "scope_label"')
replace(p,'{MappingKind.QUEUE_STATE, MappingKind.ISSUE_LABEL, MappingKind.DOCUMENT}', '{MappingKind.QUEUE_STATE, MappingKind.SCOPE_LABEL, MappingKind.ISSUE_LABEL, MappingKind.DOCUMENT}')
for p in ['src/kodezart/services/tracker_boot.py','src/kodezart/adapters/linear_scope_reader.py','src/kodezart/adapters/linear_scope_types.py','src/kodezart/domain/scope_approval.py','src/kodezart/services/scope_resolution.py']:copy(p)
p='src/kodezart/core/prompt_namespaces.py'
replace(p,'    _bind_absentable(\n        bindings,\n        "queue_states",','    _bind_absentable(\n        bindings,\n        "scope_labels",\n        dict(config.scope_labels),\n        absent=not config.scope_labels,\n    )\n\n    _bind_absentable(\n        bindings,\n        "queue_states",')
p='src/kodezart/core/protocols.py'
s=(R/p).read_text(); print('operation import',s[s.index('from kodezart.types.domain.operation'):s.index('from kodezart.types.domain.operation')+150])
add_units(p,['TrackerPort.read_scope_labels','TrackerPort.execution_approved','TrackerPort.project_milestones'],'    async def scope_issues(')
# Add enum to existing multiline import, or simple line.
s=(R/p).read_text(); a=next(n for n in ast.parse(s).body if isinstance(n,ast.ImportFrom) and n.module=='kodezart.types.domain.operation');old=''.join(s.splitlines(True)[a.lineno-1:a.end_lineno]);new='from kodezart.types.domain.operation import '+', '.join([n.name for n in a.names]+['ScopeLabel'])+'\n';replace(p,old,new)
p='src/kodezart/composition/tracker.py';replace(p,'        issue_labels=operation.issue_labels,','        issue_labels=operation.issue_labels,\n        scope_labels=operation.scope_labels,')
p='src/kodezart/adapters/linear_mcp_types.py';edit(p,lambda s:s.replace(symbol(s,'LinearLabelListWire'),symbol(source(p),'LinearLabelListWire')))
p='src/kodezart/adapters/linear_mcp_tracker.py'
replace(p,'from kodezart.adapters.linear_scope_types import LinearScopeIssuesWire','from kodezart.adapters.linear_scope_types import LinearApprovalIssueWire, LinearScopeIssuesWire')
replace(p,'    CriterionReadError,','    CriterionReadError,\n    ScopeReadError,')
replace(p,'from kodezart.domain.surface_lease import (','from kodezart.domain.scope_approval import resolve_execution_approval\nfrom kodezart.domain.surface_lease import (')
replace(p,'    QueueState,','    QueueState,\n    ScopeLabel,')
replace(p,'_TOOL_CREATE_ISSUE_LABEL = "create_issue_label"','_TOOL_CREATE_ISSUE_LABEL = "create_issue_label"\n_TOOL_LIST_PROJECT_LABELS = "list_project_labels"\n_TOOL_SAVE_PROJECT_LABEL = "save_project_label"\n_TOOL_LIST_INITIATIVE_LABELS = "list_initiative_labels"\n_TOOL_CREATE_INITIATIVE_LABEL = "create_initiative_label"')
d=source(p);a=d.index('#: One configured scope label');b=d.index('#: The tools that change',a);replace(p,'#: The tools that change',d[a:b]+'#: The tools that change')
replace(p,'        _TOOL_LIST_ISSUE_LABELS,','        _TOOL_LIST_ISSUE_LABELS,\n        _TOOL_LIST_PROJECT_LABELS,\n        _TOOL_LIST_INITIATIVE_LABELS,')
replace(p,'        issue_labels: Mapping[str, str],','        issue_labels: Mapping[str, str],\n        scope_labels: Mapping[str, str],')
replace(p,'        self._issue_labels = dict(issue_labels)','        self._issue_labels = dict(issue_labels)\n        self._scope_labels = dict(scope_labels)')
add_units(p,['LinearMcpTracker._scope_label_members','LinearMcpTracker._read_scope_issue','LinearMcpTracker.read_scope_labels','LinearMcpTracker.execution_approved','LinearMcpTracker._read_execution_approval','LinearMcpTracker.project_milestones'],'    async def scope_issues(')
add_units(p,['LinearMcpTracker._scope_label_definitions','LinearMcpTracker._ensure_scope_label'],'    async def _ensure_document(')
edit(p,lambda s:s.replace(symbol(s,'LinearMcpTracker._label_entries'),symbol(source(p),'LinearMcpTracker._label_entries')))
replace(p,'        definitions = await self._label_definitions()\n        documents = (','        definitions = await self._label_definitions()\n        scope_definitions = (\n            await self._scope_label_definitions(definitions)\n            if any(ref.kind is MappingKind.SCOPE_LABEL for ref in refs)\n            else {}\n        )\n        documents = (')
replace(p,'                outcomes.append(await self._ensure_document(ref, documents))\n                continue','                outcomes.append(await self._ensure_document(ref, documents))\n                continue\n            if ref.kind is MappingKind.SCOPE_LABEL:\n                outcomes.append(\n                    await self._ensure_scope_label(ref, definitions, scope_definitions),\n                )\n                continue')
replace(p,'        for ref in refs:\n            if ref.kind is MappingKind.WORKFLOW_STATE:', '        for ref in refs:\n            if ref.kind is MappingKind.SCOPE_LABEL and ref.scope is not None:\n                unresolved.append(ref)\n                continue\n            if ref.kind is MappingKind.WORKFLOW_STATE:')
replace(p,'            case MappingKind.USER:\n', '            case MappingKind.SCOPE_LABEL:\n                issues = await self._label_definitions()\n                definitions = await self._scope_label_definitions(issues)\n                shared = set.intersection(*definitions.values())\n                return frozenset(\n                    name for name in shared if not issues.teams_holding(name)\n                )\n            case MappingKind.USER:\n')
for name in ['tests/domain/test_scope_label_config.py','tests/domain/test_scope_labels.py','tests/services/test_scope_resolution.py','tests/prompts/test_scope_label_bindings.py','tests/tracker/connected_app_label_contract.py','tests/tracker/test_scope_label_mappings.py','tests/tracker/test_scope_tool_arguments.py','tests/tracker/test_scope_approval.py','tests/tracker/test_scope_ancestor_identity.py','tests/tracker/test_milestone_approval.py','tests/tracker/test_native_approval_aliases.py']:copy(name)

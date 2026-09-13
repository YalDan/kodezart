import ast,json,subprocess
from pathlib import Path
R=Path('/private/tmp/kodezart-v03-m1-native-read-extraction')
DONOR='7892ca1a47adbcfd91fbcfb1e580dfc86f066a99'
MAP=[]
def src(path):return subprocess.check_output(['git','show',f'{DONOR}:{path}'],cwd=R,text=True)
def node(path,*names):
 s=src(path); t=ast.parse(s); n=None
 for name in names:
  n=next(n for n in t.body if getattr(n,'name',None)==name);t=n
 assert n is not None
 start=min([n.lineno,*[x.lineno for x in getattr(n,'decorator_list',[])]])
 MAP.append({'file':path,'symbols':names,'start':start,'end':n.end_lineno,'donor':DONOR})
 return ''.join(s.splitlines(keepends=True)[start-1:n.end_lineno])+'\n'
def edit(path,fn):
 p=R/path;p.write_text(fn(p.read_text()))
# Shared domain representation and mapping kind.
p='src/kodezart/types/domain/tracker.py'
edit(p,lambda s:s.replace('    team_key: str | None\n','    #: Configured semantic issue-label keys, never backend label spellings.\n    issue_labels: frozenset[str] = frozenset()\n    team_key: str | None\n',1).replace('    QUEUE_STATE = "queue_state"\n','    QUEUE_STATE = "queue_state"\n    ISSUE_LABEL = "issue_label"\n',1).replace('{MappingKind.QUEUE_STATE, MappingKind.DOCUMENT}', '{MappingKind.QUEUE_STATE, MappingKind.ISSUE_LABEL, MappingKind.DOCUMENT}'))
p='src/kodezart/types/domain/operation.py'
canonical=src(p);a=canonical.index('        label_names = list(self.issue_labels.values())');b=canonical.index('        prefixes = ',a);validation=canonical[a:b]
edit(p,lambda s:s.replace('    queue_states: dict[str, str] = Field(default_factory=dict)\n','    queue_states: dict[str, str] = Field(default_factory=dict)\n    issue_labels: dict[str, str] = Field(default_factory=dict)\n',1).replace('        if failures:\n',validation+'        if failures:\n',1).replace('    "queue_states": ConfigOwnership.OWNED,','    "queue_states": ConfigOwnership.OWNED,\n    "issue_labels": ConfigOwnership.OWNED,'))
p='src/kodezart/domain/errors.py';edit(p,lambda s:s+'\n\n'+node(p,'CriterionReadError'))
p='src/kodezart/core/protocols.py'
reader=node(p,'TrackerCriteriaReader')
methods='\n'.join(node(p,'TrackerPort',n) for n in ('read_planning_issue','require_scope_plan_reads','require_issue_classification_reads'))
edit(p,lambda s:s.replace('@runtime_checkable\nclass TrackerPort(Protocol):',reader+'\n\n@runtime_checkable\nclass TrackerPort(TrackerCriteriaReader, Protocol):').replace('    async def read_issue(self,',methods+'\n    async def read_issue(self,',1))
p='src/kodezart/adapters/linear_mcp_types.py';edit(p,lambda s:s+'\n\n'+node(p,'LinearCriterionIssueWire'))
p='src/kodezart/adapters/linear_mcp_tracker.py'
methods='\n'.join(node(p,'LinearMcpTracker',n) for n in ('read_planning_issue','_classification_label','require_scope_plan_reads','require_issue_classification_reads','read_criteria','_read_criterion_family','_read_criteria'))
def adapter(s):
 s=s.replace('    LinearCommentWire,','    LinearCommentWire,\n    LinearCriterionIssueWire,',1)
 s=s.replace('from kodezart.adapters.pagination import cursor_pages','from kodezart.adapters.linear_scope_types import LinearScopeIssuesWire\nfrom kodezart.adapters.pagination import cursor_pages',1)
 s=s.replace('    DuplicateWorkRefError,','    CriterionReadError,\n    DuplicateWorkRefError,',1)
 s=s.replace('from kodezart.types.domain.operation import LifecycleStage, QueueState','from kodezart.types.domain.operation import LifecycleStage, OperationMemberAbsentError, QueueState',1)
 s=s.replace('_MAPPING_TOOL_BY_KIND:', '_ISSUE_IDENTITY_PAGE_SIZE = 250\n\n_MAPPING_TOOL_BY_KIND:',1)
 s=s.replace('        queue_state_labels: Mapping[str, str],','        queue_state_labels: Mapping[str, str],\n        issue_labels: Mapping[str, str],',1)
 s=s.replace('        self._caller: McpToolCaller = caller','        self._caller: McpToolCaller = caller\n        self._issue_labels = dict(issue_labels)',1)
 s=s.replace('    async def read_issue(self,',methods+'\n    async def read_issue(self,',1)
 s=s.replace('            team_key=self._team_key_by_identifier.get(wire.team),','            issue_labels=frozenset(name for name, label in self._issue_labels.items() if label in wire.labels),\n            team_key=self._team_key_by_identifier.get(wire.team),',1)
 s=s.replace('            case MappingKind.QUEUE_STATE:', '            case MappingKind.QUEUE_STATE | MappingKind.ISSUE_LABEL:')
 return s
edit(p,adapter)
p='src/kodezart/composition/tracker.py';edit(p,lambda s:s.replace('                queue_state_labels=operation.queue_states,','                queue_state_labels=operation.queue_states,\n                issue_labels=operation.issue_labels,',1))
p='src/kodezart/services/tracker_boot.py'
ref=node(p,'_issue_label_refs')
edit(p,lambda s:s.replace('def _document_refs(',ref+'\n\ndef _document_refs(',1).replace('    "queue_states": _queue_state_refs,','    "queue_states": _queue_state_refs,\n    "issue_labels": _issue_label_refs,',1).replace('    refs.extend(\n        MappingRef(\n            kind=MappingKind.WORKFLOW_STATE,','    refs.extend(\n        MappingRef(kind=MappingKind.ISSUE_LABEL, name=name, identifier=identifier)\n        for name, identifier in sorted(config.issue_labels.items())\n    )\n    refs.extend(\n        MappingRef(\n            kind=MappingKind.WORKFLOW_STATE,',1))
p='src/kodezart/core/prompt_namespaces.py'
edit(p,lambda s:s.replace('    _bind_absentable(\n        bindings,\n        "marker_prefixes",','    _bind_absentable(\n        bindings,\n        "issue_labels",\n        dict(config.issue_labels),\n        absent=not config.issue_labels,\n    )\n    _bind_absentable(\n        bindings,\n        "marker_prefixes",',1))
p='tests/fakes.py'
methods='\n'.join(node(p,'FakeTrackerPort',n) for n in ('read_planning_issue','require_scope_plan_reads','require_issue_classification_reads','read_criteria','_read_criterion_family'))
edit(p,lambda s:s.replace('    DuplicateWorkRefError,','    CriterionReadError,\n    DuplicateWorkRefError,',1).replace('    async def read_issue(self,',methods+'\n    async def read_issue(self,',1).replace('        team = arguments.get("team")\n        selected = [','        team = arguments.get("team")\n        parent = arguments.get("parentId")\n        selected = [',1).replace('            and (team is None or issue.team == team)\n','            and (team is None or issue.team == team)\n            and (parent is None or issue.parent_id == parent)\n',1))
# Every existing actual adapter constructor explicitly declares absence of optional semantic mappings.
for p in sorted((R/'tests').rglob('*.py')):
 s=p.read_text();t=ast.parse(s);lines=s.splitlines(keepends=True);offsets=[0]
 for line in lines:offsets.append(offsets[-1]+len(line))
 inserts=[]
 for call in ast.walk(t):
  if isinstance(call,ast.Call) and isinstance(call.func,ast.Name) and call.func.id=='LinearMcpTracker' and not any(k.arg in ('issue_labels',None) for k in call.keywords):
   pos=offsets[call.func.end_lineno-1]+call.func.end_col_offset+1
   inserts.append(pos)
 for pos in sorted(inserts,reverse=True):s=s[:pos]+'issue_labels={}, '+s[pos:]
 if inserts:p.write_text(s);MAP.append({'file':str(p.relative_to(R)),'normalization':'existing constructor explicit empty semantic mapping','sites':len(inserts)})
# Existing kwargs-based fixture constructor, no new defaults in production.
p=R/'tests/tracker/test_linear_mcp_tracker.py';s=p.read_text().replace('        "caller": server,','        "caller": server,\n        "issue_labels": {},',1);p.write_text(s)
Path('/private/tmp/kodezart-recovery-session/m1-native-read-initial-hunks.json').write_text(json.dumps(MAP,indent=2)+'\n')
print('Extracted narrow native read and mapping closure')

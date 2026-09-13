import ast,hashlib,json,subprocess
from pathlib import Path
R='/private/tmp/kodezart-v03-m1-git-settings-extraction';O=Path('/private/tmp/kodezart-recovery-session');BASE='2bc237577da7a3db3101e67c324eea0eaa6e6abd';DONOR='36083f83f42c03240ebb5861fe284da2c9f04180'
def git(*a):return subprocess.run(['git','-C',R,*a],text=True,capture_output=True)
def source(ref,p):
 r=git('show',ref+':'+p);return None if r.returncode else r.stdout
SPECS={
'src/kodezart/types/domain/operation.py':['ScopeLabel','OperationConfig.scope_labels','OperationConfig._check_structure','FIELD_OWNERSHIP'],
'src/kodezart/types/domain/tracker.py':['MappingKind','INSTATABLE_MAPPING_KINDS'],
'src/kodezart/core/protocols.py':['TrackerPort.read_scope_labels','TrackerPort.execution_approved','TrackerPort.project_milestones'],
'src/kodezart/services/tracker_boot.py':['configured_mappings','_scope_label_refs','OWNED_REF_BUILDERS'],
'src/kodezart/core/prompt_namespaces.py':['operation_bindings'],
'src/kodezart/adapters/linear_mcp_tracker.py':['LinearMcpTracker.__init__','LinearMcpTracker._scope_label_members','LinearMcpTracker._read_scope_issue','LinearMcpTracker.read_scope_labels','LinearMcpTracker.execution_approved','LinearMcpTracker._read_execution_approval','LinearMcpTracker._scope_label_definitions','LinearMcpTracker._ensure_scope_label','LinearMcpTracker.project_milestones'],
'src/kodezart/adapters/linear_scope_reader.py':['LinearScopeReader.project_milestones'],
'src/kodezart/composition/tracker.py':['build_tracker'],
'src/kodezart/domain/scope_approval.py':['resolve_execution_approval'],
'src/kodezart/services/scope_resolution.py':['resolve_scope'],
'src/kodezart/types/requests/agent.py':['ScopeRefRequest','WorkflowRequest.scope','WorkflowRequest._check_trunk_base'],
'src/kodezart/types/domain/workflow.py':['WorkflowSubmission.scope'],
'src/kodezart/handlers/agent_handler.py':['AgentHandler.submit_workflow'],
}
def symbols(s):
 out={}
 if s is None:return out
 def walk(nodes,prefix=''):
  for n in nodes:
   if isinstance(n,(ast.ClassDef,ast.FunctionDef,ast.AsyncFunctionDef)):
    name=prefix+n.name;out[name]=n
    if isinstance(n,ast.ClassDef):walk(n.body,name+'.')
   if isinstance(n,(ast.Assign,ast.AnnAssign)):
    targets=n.targets if isinstance(n,ast.Assign) else [n.target]
    for t in targets:
     if isinstance(t,ast.Name):out[prefix+t.id]=n
 walk(ast.parse(s).body);return out
rows=[]
for path,names in SPECS.items():
 before=source(BASE,path);after=source(DONOR,path);bs=symbols(before);ds=symbols(after);units=[]
 for name in names:
  n=ds.get(name)
  if n is None:
   units.append({'symbol':name,'selector_missing':True,'available_names':[x for x in ds if any(y in x.lower() for y in ['reference','binding','mapping','validate','scope'])]});continue
  text='\n'.join(after.splitlines()[n.lineno-1:n.end_lineno]);old=bs.get(name);oldtext=None if old is None else '\n'.join(before.splitlines()[old.lineno-1:old.end_lineno]);units.append({'symbol':name,'donor_start':n.lineno,'donor_end':n.end_lineno,'donor_sha256':hashlib.sha256(text.encode()).hexdigest(),'base_present':old is not None,'base_same':oldtext==text,'donor_source':text})
 rows.append({'path':path,'base_exists':before is not None,'donor_blob':git('rev-parse',DONOR+':'+path).stdout.strip(),'units':units,'full_diff':git('diff','--unified=3',BASE,DONOR,'--',path).stdout})
tests=['tests/domain/test_scope_label_config.py','tests/domain/test_scope_labels.py','tests/services/test_scope_resolution.py','tests/prompts/test_scope_label_bindings.py','tests/tracker/connected_app_label_contract.py','tests/tracker/test_scope_label_mappings.py','tests/tracker/test_scope_tool_arguments.py','tests/api/v1/test_scope_request_boundary.py','tests/tracker/test_scope_approval.py','tests/tracker/test_scope_ancestor_identity.py','tests/tracker/test_milestone_approval.py','tests/tracker/test_native_approval_aliases.py']
testrows=[]
for p in tests:
 s=source(DONOR,p);testrows.append({'path':p,'base_exists':source(BASE,p) is not None,'donor_exists':s is not None,'donor_blob':git('rev-parse',DONOR+':'+p).stdout.strip() if s else None,'tests':[] if not s else [x for x in symbols(s) if x.rsplit('.',1)[-1].startswith('test_')]})
result={'base':BASE,'donor':DONOR,'scope':'Read-only remaining M1 inventory. Symbol bodies in mixed files are inspection evidence, not authorization to copy whole classes. M2 provisional scope_labels/shared readers are an active overlap: root must settle ownership after M2 freeze, once only. No new source edits performed.','source':rows,'tests':testrows};(O/'m1-remaining-scope-extraction-2bc.json').write_text(json.dumps(result,indent=2)+'\n')
print('selectors missing',[(r['path'],u['symbol'],u.get('available_names')) for r in rows for u in r['units'] if u.get('selector_missing')]);print('missing donor tests',[x['path'] for x in testrows if not x['donor_exists']]);print('source files',len(rows))

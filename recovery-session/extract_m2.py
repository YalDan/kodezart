import ast,pathlib,shutil,json
D=pathlib.Path('/private/tmp/kodezart-v03-recovery-integration')
T=pathlib.Path('/private/tmp/kodezart-v03-m2-organize')
records=[]
def read(p):return (D/p).read_text()
def key(n):
 if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef,ast.ClassDef)):return n.name
 if isinstance(n,ast.AnnAssign) and isinstance(n.target,ast.Name):return n.target.id
 if isinstance(n,ast.Assign):return next((t.id for t in n.targets if isinstance(t,ast.Name)),None)
def start(n):return min([n.lineno]+[d.lineno for d in getattr(n,'decorator_list',[])])-1
def node(source,name,parent=None):
 tree=ast.parse(source)
 if parent:tree=next(n for n in tree.body if key(n)==parent)
 return next(n for n in tree.body if key(n)==name)
def imports(p,n):
 src=read(p);loaded={a.id for a in ast.walk(n) if isinstance(a,ast.Name)}
 for i in ast.parse(src).body:
  if not isinstance(i,(ast.Import,ast.ImportFrom)):continue
  if isinstance(i,ast.ImportFrom) and i.module=='__future__':continue
  wanted=[a for a in i.names if (a.asname or (a.name.split('.')[0] if isinstance(i,ast.Import) else a.name)) in loaded]
  if not wanted:continue
  target=(T/p).read_text();tree=ast.parse(target)
  existing={a.asname or (a.name.split('.')[0] if isinstance(ii,ast.Import) else a.name) for ii in tree.body if isinstance(ii,(ast.Import,ast.ImportFrom)) for a in ii.names}
  wanted=[a for a in wanted if (a.asname or (a.name.split('.')[0] if isinstance(i,ast.Import) else a.name)) not in existing]
  if not wanted:continue
  new=ast.Import(names=wanted) if isinstance(i,ast.Import) else ast.ImportFrom(module=i.module,names=wanted,level=i.level)
  lines=target.splitlines(keepends=True);pos=tree.body[0].end_lineno if isinstance(tree.body[0],ast.Expr) and isinstance(tree.body[0].value,ast.Constant) else 0
  lines.insert(pos,'\n'+ast.unparse(new)+'\n');(T/p).write_text(''.join(lines))
def symbol(p,name,parent=None):
 src=read(p);n=node(src,name,parent);chunk=''.join(src.splitlines(keepends=True)[start(n):n.end_lineno]);target=(T/p).read_text();lines=target.splitlines(keepends=True)
 try:old=node(target,name,parent)
 except StopIteration:old=None
 if old:lines[start(old):old.end_lineno]=[chunk]
 elif parent:
  owner=node(target,parent);lines[owner.end_lineno:owner.end_lineno]=['\n'+chunk]
 else:lines.append('\n\n'+chunk)
 (T/p).write_text(''.join(lines));imports(p,n);records.append({'path':p,'symbol':name,'parent':parent,'donor_lines':[start(n)+1,n.end_lineno]})
def whole(p):
 (T/p).parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(D/p,T/p);records.append({'path':p,'whole':True})
if __name__=='__main__':
 for p in ['chains/organize.py','chains/organize_author.py','composition/organize.py','core/organize_settings.py','domain/criterion_creation.py','domain/organize.py','domain/organize_graph.py','domain/topology.py','services/organize_context.py','services/organize_owner.py','services/organize_tick.py','types/domain/organize.py','types/domain/organize_graph.py','types/domain/organize_owner.py','types/domain/topology.py','adapters/linear_issue_identity.py','types/domain/issue_identity.py']:
  whole('src/kodezart/'+p)
 for p,names in {
 'domain/errors.py':['OrganizeAdmissionIdentityError','OrganizeDecisionRequiredError','OrganizeHaltError','OrganizeWriteRefusalError','ScopeCycleError','DuplicateIssueIdentityError'],
 'domain/prompt_variables.py':['organize_variables'],
 'types/domain/tracker.py':['TrackerIssueRevision'],
 'types/domain/operation.py':['ScopeLabel','OrganizeScopeBinding'],
 'services/git_observations.py':['read_remote_head'],
 'adapters/linear_mcp_tracker.py':['_SplitCreation'],
 }.items():
  for name in names:symbol('src/kodezart/'+p,name)
 for name in ['read_issue_revision','read_scope_labels','execution_approved','project_milestones','update_issue_graph','read_split_children','create_split_if_absent','create_criterion_if_absent','read_issue_identity','edit_description']:
  symbol('src/kodezart/core/protocols.py',name,'TrackerPort')
 for name in ['read_issue_revision','read_scope_labels','execution_approved','_read_execution_approval','project_milestones','_read_unchanged_graph','update_issue_graph','_update_issue_graph_once','read_split_children','create_split_if_absent','_create_split_once','_unstarted_state_id','create_criterion_if_absent','read_issue_identity','_identity_issues','_find_issue_identity','edit_description']:
  symbol('src/kodezart/adapters/linear_mcp_tracker.py',name,'LinearMcpTracker')
 for name in ['scope_labels','organize_mandates','organize_scopes','resolve_organize_mandates']:
  symbol('src/kodezart/types/domain/operation.py',name,'OperationConfig')
 for name in ['ORGANIZE_ADMISSION_SCHEMA','ORGANIZE_PROPOSAL_SCHEMA']:
  symbol('src/kodezart/types/domain/agent.py',name)
 for name in ['ORGANIZE_ASSESS','ORGANIZE_AUTHOR','ORGANIZE_CRITERIA_AUTHOR','ORGANIZE_VERIFY']:
  symbol('src/kodezart/types/domain/prompts.py',name,'PromptKey')
 for name in ['ISSUE_GRAPH','ISSUE_SPLIT_SET']:
  symbol('src/kodezart/types/domain/surface.py',name,'SurfaceKind')
 for name in ['ORGANIZE_PASS']:
  symbol('src/kodezart/types/domain/session.py',name,'SessionType')
 for name in ['organize']:
  symbol('src/kodezart/core/config.py',name,'AppConfig')
 pathlib.Path('/private/tmp/kodezart-recovery-session/m2-extracted-hunks.json').write_text(json.dumps(records,indent=2)+'\n')

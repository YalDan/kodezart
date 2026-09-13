import ast,copy,json,subprocess
from pathlib import Path
root=Path('/private/tmp/kodezart-v03-m1-session-policy-extraction');base='fec7f28292975f0b4635a06ba84e4e35fdc3c2e8'
def get(ref,p):return subprocess.check_output(['git','show',f'{ref}:{p}'],cwd=root,text=True)
def methods(s):
 out={}
 def walk(n,prefix=''):
  for c in getattr(n,'body',[]):
   if isinstance(c,(ast.FunctionDef,ast.AsyncFunctionDef)):
    out[prefix+c.name]=c
   elif isinstance(c,ast.ClassDef):walk(c,prefix+c.name+'.')
 walk(ast.parse(s));return out
class ReversePolicy(ast.NodeTransformer):
 def __init__(self,path):self.path=path
 def visit_Name(self,n):
  replacements={'PermissionMode':'str','AllowedTools':'list[str]','WorkflowSubmission':'WorkflowRequest','map_permission_mode':'_validate_permission_mode'}
  return ast.parse(replacements[n.id],mode='eval').body if n.id in replacements else n
 def visit_Call(self,n):
  if isinstance(n.func,ast.Name) and n.func.id=='map_allowed_tools':return self.visit(n.args[0])
  return self.generic_visit(n)
 def visit_Attribute(self,n):
  if isinstance(n.value,ast.Name) and n.value.id=='PermissionMode':return ast.Constant({'PLAN':'plan','UNATTENDED':'bypassPermissions','INTERACTIVE':'default','ACCEPT_EDITS':'acceptEdits'}[n.attr])
  if isinstance(n.value,ast.Name) and n.value.id=='ToolPreset':
   if self.path.endswith('git_change_persister.py') and n.attr=='EVALUATION':return ast.parse('["Read", "Glob", "Grep", "Bash"]',mode='eval').body
   return ast.Name(id={'EVALUATION':'EVAL_TOOLS','DELEGATED_EVALUATION':'EVAL_TOOLS_WITH_AGENT','AUTHORING':'TICKET_TOOLS'}[n.attr],ctx=ast.Load())
  return self.generic_visit(n)
files=subprocess.check_output(['git','diff','--name-only',base,'HEAD','--','src'],cwd=root,text=True).splitlines();rows=[]
explicit={'src/kodezart/services/fire_dispatcher.py','src/kodezart/adapters/asyncio_job_queue.py','src/kodezart/handlers/agent_handler.py','src/kodezart/api/v1/endpoints/agent.py'}
for p in files:
 b=methods(get(base,p));d=methods((root/p).read_text())
 for name,n in d.items():
  if name not in b or ast.dump(n)==ast.dump(b[name]):continue
  if p in explicit:rows.append({'path':p,'symbol':name,'proof':'explicit domain-submission/HTTP conversion cut; compare recorded hunk map'});continue
  normalized=ReversePolicy(p).visit(copy.deepcopy(n));same=ast.dump(normalized)==ast.dump(b[name]);rows.append({'path':p,'symbol':name,'baseline_ast_after_reversing_only_policy':same});assert same,(p,name)
Path('/private/tmp/kodezart-recovery-session/session-policy-normalized-source-proof.json').write_text(json.dumps(rows,indent=2)+'\n');print('Normalized exact baseline source methods',sum(x.get('baseline_ast_after_reversing_only_policy') is True for x in rows),'explicit submission methods',sum('proof' in x for x in rows))

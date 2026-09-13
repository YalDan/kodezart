import ast,subprocess,difflib
R='/private/tmp/kodezart-v03-m1-scope-bindings-extraction'
def get(ref,p):return subprocess.check_output(['git','-C',R,'show',ref+':src/kodezart/'+p],text=True)
def unit(s,n):
 tree=ast.parse(s); nodes=tree.body
 for part in n.split('.'):
  node=next(x for x in nodes if getattr(x,'name',None)==part);nodes=getattr(node,'body',[])
 return ''.join(s.splitlines(True)[node.lineno-1:node.end_lineno])
for p,names in [('adapters/linear_mcp_tracker.py',['_LabelListings','LinearMcpTracker.ensure_mappings','LinearMcpTracker._label_entries','LinearMcpTracker._identifiers_of','LinearMcpTracker._read_scope_issue','LinearMcpTracker._read_execution_approval','LinearMcpTracker.execution_approved','LinearMcpTracker.read_scope_labels']),('core/protocols.py',['TrackerPort.read_scope_labels','TrackerPort.execution_approved','TrackerPort.project_milestones'])]:
 for name in names:
  ds=unit(get('36083f83',p),name)
  try:bs=unit(get('2bc2375',p),name)
  except StopIteration:bs=''
  print(p,name);print(''.join(difflib.unified_diff(bs.splitlines(True),ds.splitlines(True))))

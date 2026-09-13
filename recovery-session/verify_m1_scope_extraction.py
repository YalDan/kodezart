import ast,hashlib,json,subprocess
from pathlib import Path
R=Path('/private/tmp/kodezart-v03-m1-scope-bindings-extraction');O=Path('/private/tmp/kodezart-recovery-session');BASE='2bc237577da7a3db3101e67c324eea0eaa6e6abd';DONOR='36083f83f42c03240ebb5861fe284da2c9f04180';M2='73cc5def12a2daa4e0ba319660db76cdad781882';HEAD='ddb8cdef33f90f30aecc4343e36c2293bdf39a27'
def git(*args):return subprocess.run(['git','-C',str(R),*args],text=True,capture_output=True)
def source(ref,path):
 r=git('show',ref+':'+path);return None if r.returncode else r.stdout
def symbols(s):
 out={}
 if s is None:return out
 def walk(nodes,prefix=''):
  for n in nodes:
   if isinstance(n,(ast.ClassDef,ast.FunctionDef,ast.AsyncFunctionDef)):
    name=prefix+n.name;out[name]=n
    if isinstance(n,ast.ClassDef):walk(n.body,name+'.')
   elif isinstance(n,(ast.Assign,ast.AnnAssign)):
    for t in (n.targets if isinstance(n,ast.Assign) else [n.target]):
     if isinstance(t,ast.Name):out[prefix+t.id]=n
 walk(ast.parse(s).body);return out
def extracted(s,n):
 start=min([n.lineno]+[x.lineno for x in getattr(n,'decorator_list',[])]);return ''.join(s.splitlines(True)[start-1:n.end_lineno])
def normalized(n):return ast.dump(n,include_attributes=False)
paths=git('diff','--name-only',BASE,HEAD).stdout.splitlines();rows=[]
for p in paths:
 current=source(HEAD,p);base=source(BASE,p);donor=source(DONOR,p);m2=source(M2,p)
 units=[]
 if p.endswith('.py'):
  cs=symbols(current);bs=symbols(base);ds=symbols(donor);ms=symbols(m2)
  for name,n in cs.items():
   if isinstance(n,ast.ClassDef):continue
   old=bs.get(name)
   if old is not None and normalized(old)==normalized(n):continue
   text=extracted(current,n)
   def provenance(s,m):
    d=m.get(name)
    return None if d is None else {'start':d.lineno,'end':d.end_lineno,'text_same':extracted(s,d)==text,'ast_same':normalized(d)==normalized(n),'sha256':hashlib.sha256(extracted(s,d).encode()).hexdigest()}
   units.append({'symbol':name,'start':n.lineno,'end':n.end_lineno,'change':'added' if old is None else 'modified','source':text,'donor':provenance(donor,ds),'m2':provenance(m2,ms)})
 rows.append({'path':p,'head_blob':git('rev-parse',HEAD+':'+p).stdout.strip(),'donor_blob':git('rev-parse',DONOR+':'+p).stdout.strip() if donor else None,'donor_byte_equal':current==donor,'m2_byte_equal':current==m2,'units':units,'patch':git('diff','--unified=3',BASE,HEAD,'--',p).stdout})
# All donor new source files and mixed-file helper dependencies remain exact.
exact_files=['src/kodezart/domain/scope_approval.py','src/kodezart/services/scope_resolution.py','src/kodezart/services/tracker_boot.py','src/kodezart/adapters/linear_scope_reader.py','src/kodezart/adapters/linear_scope_types.py']
assert all(source(HEAD,p)==source(DONOR,p) for p in exact_files)
helpers=['_scope_label_members','_read_scope_issue','read_scope_labels','execution_approved','_read_execution_approval','project_milestones','_scope_label_definitions','_ensure_scope_label','_label_entries']
p='src/kodezart/adapters/linear_mcp_tracker.py';a=symbols(source(HEAD,p));d=symbols(source(DONOR,p));m=symbols(source(M2,p))
assert all(normalized(a['LinearMcpTracker.'+n])==normalized(d['LinearMcpTracker.'+n]) for n in helpers)
required=a['LinearMcpTracker.__init__'].args
assert 'scope_labels' in [x.arg for x in required.kwonlyargs]
assert required.kw_defaults[[x.arg for x in required.kwonlyargs].index('scope_labels')] is None
allowed={'LinearMcpTracker.__init__','LinearMcpTracker.resolve_mappings','LinearMcpTracker.ensure_mappings','LinearMcpTracker._identifiers_of','LinearMcpTracker._label_entries'}
changed_existing=[u['symbol'] for r in rows if r['path']==p for u in r['units'] if u['change']=='modified' and '(' not in u['symbol'] and u['symbol'].startswith('LinearMcpTracker.')]
assert set(changed_existing)==allowed,(changed_existing,allowed)
# Constructor source remains exact except the two scope mapping lines.
s=source(HEAD,p);b=source(BASE,p);cur=extracted(s,a['LinearMcpTracker.__init__']);old=extracted(b,symbols(b)['LinearMcpTracker.__init__']);cur=cur.replace('        scope_labels: Mapping[str, str],\n','').replace('        self._scope_labels = dict(scope_labels)\n','');assert cur==old
# No source escape hatch introduced. Existing ignores in tests remain unrelated.
patch=git('diff',BASE,HEAD,'--','src').stdout;added=[x[1:] for x in patch.splitlines() if x.startswith('+') and not x.startswith('+++')]
for line in added:
 assert not any(token in line for token in ['type: ignore','typing.cast','cast(','Any','noqa']),line
out={'base':BASE,'donor':DONOR,'m2':M2,'candidate':HEAD,'exact_donor_files':exact_files,'exact_donor_adapter_helpers':helpers,'changed_existing_adapter_methods':changed_existing,'required_constructor':True,'source_escape_hatches_added':False,'source_scope':'Generic scope label/approval/bootstrap only. M3 API/queue/spec-entry and M2 policy/writes excluded.','files':rows}
(O/'m1-scope-bindings-extraction-map.json').write_text(json.dumps(out,indent=2)+'\n');patch=git('diff','--binary',BASE,HEAD).stdout;(O/'m1-scope-bindings-extraction.patch').write_text(patch)
print('candidate',HEAD);print('changed files',len(rows));print('exact donor source files',len(exact_files));print('exact donor adapter helpers',len(helpers));print('only changed existing adapter methods',changed_existing);print('required scope_labels Mapping; no source escape hatch added');print('patch sha256',hashlib.sha256(patch.encode()).hexdigest());print('M2 overlap:');
for r in rows:
 if not r['path'].startswith('src/'):continue
 for u in r['units']:
  if u['m2'] and u['m2']['ast_same']:print(r['path'],u['symbol'],'exact AST at73cc')

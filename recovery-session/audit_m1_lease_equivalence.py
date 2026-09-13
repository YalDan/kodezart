from pathlib import Path
import ast,json,subprocess
ROOT=Path('/private/tmp/kodezart-v03-m1-lease-extraction'); BASE='a2ee4c6bebd438359b53fb7a9e11c3966d24ceb5'
REVS=['d2c6fceab762191d4e40b23c8cd349ef476e4b12','89b3751f0f1a7a20d0eb9c9d7c23001b9fce3735','76478e23bd1ebe7af9f35162a74971fdd0788aab','8fc655d2ddca93357f9fc9475b41839d62652037']
def read(p,r):
 v=subprocess.run(['git','show',r+':'+p],cwd=ROOT,text=True,capture_output=True);return v.stdout if v.returncode==0 else ''
def nodes(t):
 out={}
 def collect(body,prefix=''):
  for n in body:
   key=getattr(n,'name',None)
   if isinstance(key,ast.Name):key=key.id
   if isinstance(n,ast.AnnAssign) and isinstance(n.target,ast.Name):key=n.target.id
   if isinstance(n,ast.Assign) and len(n.targets)==1 and isinstance(n.targets[0],ast.Name):key=n.targets[0].id
   if key:
    out[prefix+key]=ast.dump(n,include_attributes=False)
    if isinstance(n,ast.ClassDef): collect(n.body,prefix+key+'.')
 collect(ast.parse(t).body);return out
paths=set(subprocess.check_output(['git','diff','--name-only',BASE],cwd=ROOT,text=True).splitlines())|set(subprocess.check_output(['git','ls-files','--others','--exclude-standard'],cwd=ROOT,text=True).splitlines())
rows=[]
for p in sorted(paths):
 if not p.startswith('src/') or not p.endswith('.py'):continue
 actual=nodes((ROOT/p).read_text()); old=nodes(read(p,BASE)); donors={r:nodes(read(p,r)) for r in REVS}
 for name,value in actual.items():
  if value==old.get(name):continue
  matches=[r for r,d in donors.items() if d.get(name)==value]
  rows.append({'file':p,'symbol':name,'matches':matches,'status':'exact AST' if matches else 'partial hunk normalization required'})
 for name in old.keys()-actual.keys():rows.append({'file':p,'symbol':name,'status':'removed base node','matches':[]})
Path('/private/tmp/kodezart-recovery-session/m1-lease-equivalence-audit.json').write_text(json.dumps(rows,indent=2)+'\n')
for r in rows:
 if r['status']=='partial hunk normalization required':print(r['file']+':'+r['symbol'])
print(f'{len(rows)} changed source nodes; {sum(r["status"]=="exact AST" for r in rows)} exact AST matches')

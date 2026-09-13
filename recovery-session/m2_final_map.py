import ast,json,subprocess
from pathlib import Path
T=Path('/private/tmp/kodezart-v03-m2-organize');D=Path('/private/tmp/kodezart-v03-recovery-integration');O=Path('/private/tmp/kodezart-recovery-session')
base='dddbcbb0df1e0171e8a770063958f85d16c0d917'
head=subprocess.check_output(['git','rev-parse','HEAD'],cwd=T,text=True).strip()
files=subprocess.check_output(['git','diff','--name-only',base,head],cwd=T,text=True).splitlines()
def dump(n):return ast.dump(n,include_attributes=False)
def symbols(s):
 tree=ast.parse(s);out={}
 for n in tree.body:
  if isinstance(n,(ast.ClassDef,ast.FunctionDef,ast.AsyncFunctionDef)):
   out[n.name]=n
   if isinstance(n,ast.ClassDef):
    for k in n.body:
     if isinstance(k,(ast.FunctionDef,ast.AsyncFunctionDef)):out[n.name+'.'+k.name]=k
 return out
rows=[];checks=[]
for name in files:
 p=T/name; dp=D/name;s=p.read_text();d=dp.read_text() if dp.exists() else ''
 row={'path':name,'byte_identical_to_donor':s==d}
 try:b=subprocess.check_output(['git','show',base+':'+name],cwd=T,text=True,stderr=subprocess.DEVNULL)
 except subprocess.CalledProcessError:b=''
 if name.endswith('.py'):
  actual,donor,previous=map(symbols,(s,d,b));changes=[]
  for key,n in actual.items():
   if key in previous and dump(n)==dump(previous[key]):continue
   dn=donor.get(key)
   changes.append({'symbol':key,'candidate_lines':[n.lineno,n.end_lineno],'donor_lines':[dn.lineno,dn.end_lineno] if dn else None,'ast_identical_to_donor':dn is not None and dump(n)==dump(dn)})
   if name.startswith('tests/') and key.split('.')[-1].startswith('test_') and dn:
    asserts=lambda x:[dump(a) for a in ast.walk(x) if isinstance(a,ast.Assert)]
    raises=lambda x:[dump(a) for a in ast.walk(x) if isinstance(a,ast.Call) and isinstance(a.func,ast.Attribute) and a.func.attr in ('raises','fail')]
    checks.append({'path':name,'symbol':key,'assertions_identical':asserts(n)==asserts(dn),'raises_identical':raises(n)==raises(dn)})
  row['changed_symbols']=changes
 rows.append(row)
(O/'m2-final-donor-hunk-map.json').write_text(json.dumps({'base':base,'candidate':head,'donor':'36083f83f42c03240ebb5861fe284da2c9f04180','files':rows},indent=2)+'\n')
(O/'m2-final-test-oracle-map.json').write_text(json.dumps(checks,indent=2)+'\n')
print('files',len(rows),'byte-identical',sum(r['byte_identical_to_donor'] for r in rows),'test oracle comparisons',len(checks))
print('nonidentical assertions or raises:',[r for r in checks if not r['assertions_identical'] or not r['raises_identical']])

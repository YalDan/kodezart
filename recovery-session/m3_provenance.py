import ast,json,subprocess,hashlib
from pathlib import Path
T=Path('/private/tmp/kodezart-v03-m3-plan-walk');D=Path('/private/tmp/kodezart-v03-recovery-integration');O=Path('/private/tmp/kodezart-recovery-session');BASE='fadf6efe29c7addcc608b22e8345cd9008ce4bab';DONOR='da39c439898aec1233aa8b6157b35e961df1e453'
paths=set(subprocess.check_output(['git','diff','--name-only',BASE],cwd=T,text=True).splitlines())|set(subprocess.check_output(['git','ls-files','--others','--exclude-standard'],cwd=T,text=True).splitlines())
rows=[]
def oracles(s):
 tree=ast.parse(s);result={}
 for n in ast.walk(tree):
  if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef)) and n.name.startswith('test_'):
   result[n.name]=[ast.dump(a,include_attributes=False) for a in ast.walk(n) if isinstance(a,ast.Assert) or isinstance(a,ast.Call) and isinstance(a.func,ast.Attribute) and isinstance(a.func.value,ast.Name) and a.func.value.id=='pytest' and a.func.attr in ('raises','fail')]
 return result
for p in sorted(paths):
 f=T/p;d=D/p
 if not f.is_file():continue
 row={'path':p,'base':BASE,'donor':DONOR,'sha256':hashlib.sha256(f.read_bytes()).hexdigest(),'donor_exists':d.exists(),'byte_identical':d.exists() and f.read_bytes()==d.read_bytes()}
 if p.startswith('tests/') and p.endswith('.py') and d.exists():
  local=oracles(f.read_text());original=oracles(d.read_text());row.update(tests=len(local),same_oracles=[k for k,v in local.items() if original.get(k)==v],changed_oracles=[k for k,v in local.items() if k in original and original[k]!=v],deferred_oracles=[k for k in original if k not in local])
 rows.append(row)
(O/'m3-current-donor-map.json').write_text(json.dumps(rows,indent=2)+'\n')
print(len(rows),'files;',sum(r['byte_identical'] for r in rows),'donor-identical; modified test oracle modules:',[(r['path'],r.get('changed_oracles')) for r in rows if r.get('changed_oracles')])

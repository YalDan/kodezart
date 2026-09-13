import ast,json,subprocess,hashlib
from pathlib import Path
T=Path('/private/tmp/kodezart-v03-m5-delivery-termination');O=Path('/private/tmp/kodezart-recovery-session');D=Path('/private/tmp/kodezart-v03-m5-union-roster-correction')
files=subprocess.check_output(['git','diff','--name-only','10c51a7'],cwd=T,text=True).splitlines()+subprocess.check_output(['git','ls-files','--others','--exclude-standard'],cwd=T,text=True).splitlines()
def oracles(s):
 return [ast.dump(n,include_attributes=False) for n in ast.walk(ast.parse(s)) if isinstance(n,ast.Assert) or isinstance(n,ast.Call) and isinstance(n.func,ast.Attribute) and isinstance(n.func.value,ast.Name) and n.func.value.id=='pytest' and n.func.attr in ('raises','fail')]
rows=[]
for f in sorted(set(files)):
 p=T/f;d=D/f
 r={'path':f,'sha256':hashlib.sha256(p.read_bytes()).hexdigest(),'exact_donor':d.exists() and p.read_bytes()==d.read_bytes()}
 if f.endswith('.py') and f.startswith('tests/') and d.exists():
  from collections import Counter
  cur,orig=Counter(oracles(p.read_text())),Counter(oracles(d.read_text()))
  r.update(original_oracles=sum(orig.values()),current_oracles=sum(cur.values()),missing_original_oracles=list((orig-cur).elements()),added_oracles=list((cur-orig).elements()))
 rows.append(r)
(O/'m5-current-donor-map.json').write_text(json.dumps(rows,indent=2)+'\n')
print('files',len(rows),'exact donor',sum(r['exact_donor'] for r in rows))
for r in rows:
 if r.get('missing_original_oracles'):print(r['path'],'missing',len(r['missing_original_oracles']))

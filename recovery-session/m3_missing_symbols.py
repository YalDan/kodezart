from pathlib import Path
import ast,json
T=Path('/private/tmp/kodezart-v03-m3-plan-walk');D=Path('/private/tmp/kodezart-v03-recovery-integration');O=Path('/private/tmp/kodezart-recovery-session')
imports=json.loads((O/'m3-seed-imports.json').read_text())
def defs(s):
 tree=ast.parse(s);names=set()
 for n in tree.body:
  if isinstance(n,(ast.ClassDef,ast.FunctionDef,ast.AsyncFunctionDef)):names.add(n.name)
  if isinstance(n,ast.ImportFrom):names.update(x.asname or x.name for x in n.names)
  if isinstance(n,ast.Assign):names.update(x.id for x in n.targets if isinstance(x,ast.Name))
  if isinstance(n,ast.AnnAssign) and isinstance(n.target,ast.Name):names.add(n.target.id)
  if isinstance(n,ast.TypeAlias):names.add(n.name.id)
 return names
missing={}
for path,names in imports.items():
 p=T/path;held=defs(p.read_text()) if p.exists() else set();needed=set(names)-held
 if needed:missing[path]=sorted(needed)
(O/'m3-missing-seed-symbols.json').write_text(json.dumps(missing,indent=2)+'\n')
print(json.dumps(missing,indent=2))

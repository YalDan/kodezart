import ast,json,pathlib,subprocess
repo=pathlib.Path('/private/tmp/kodezart-v03-recovery-integration')
base=pathlib.Path('/private/tmp/kodezart-v03-m4-classification-extraction')
seeds=['chains/organize.py','chains/organize_author.py','composition/organize.py','core/organize_settings.py','domain/criterion_creation.py','domain/organize.py','domain/organize_graph.py','services/organize_context.py','services/organize_owner.py','services/organize_tick.py','types/domain/organize.py','types/domain/organize_graph.py','types/domain/organize_owner.py']
files={f'src/kodezart/{p}' for p in seeds}
allmods={str(p.relative_to(repo)) for p in (repo/'src/kodezart').rglob('*.py')}
queued=list(files);imports={}
while queued:
 p=queued.pop();t=ast.parse((repo/p).read_text())
 for node in ast.walk(t):
  if not isinstance(node,ast.ImportFrom) or not node.module or not node.module.startswith('kodezart.'):continue
  q='src/'+node.module.replace('.','/')+'.py'
  if q not in allmods:continue
  imports.setdefault(q,set()).update(a.name for a in node.names)
  if not (base/q).exists() and q not in files:
   files.add(q);queued.append(q)
rows=[]
for q,names in sorted(imports.items()):
 if q in files:continue
 t=ast.parse((repo/q).read_text());b=ast.parse((base/q).read_text())
 def symbols(tree):
  result={}
  for n in tree.body:
   if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef,ast.ClassDef)):result[n.name]=n
   elif isinstance(n,ast.AnnAssign) and isinstance(n.target,ast.Name):result[n.target.id]=n
   elif isinstance(n,ast.Assign):
    for name in n.targets:
     if isinstance(name,ast.Name):result[name.id]=n
  return result
 ds,bs=symbols(t),symbols(b)
 out=[]
 for name in sorted(names):
  if name not in ds:continue
  if name not in bs:status='missing'
  elif ast.dump(ds[name])!=ast.dump(bs[name]):status='different'
  else:continue
  n=ds[name];out.append({'symbol':name,'status':status,'donor_lines':[n.lineno,n.end_lineno]})
 if out:rows.append({'path':q,'symbols':out})
result={'donor':'36083f83f42c03240ebb5861fe284da2c9f04180','comparison_base':'25c32f603f9c9cb59852304743e653f70be309ff','whole_file_import_closure':sorted(files),'shared_import_symbols':rows}
path=pathlib.Path('/private/tmp/kodezart-recovery-session/m2-source-hunk-map.json');path.write_text(json.dumps(result,indent=2)+'\n')
print(path.read_text())

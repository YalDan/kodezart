import ast
from pathlib import Path
for name in ['test_github_api.py','test_ci_rerun.py']:
 p=Path('tests/adapters')/name;s=p.read_text();tree=ast.parse(s);lines=s.splitlines(keepends=True);offsets=[0]
 for line in lines:offsets.append(offsets[-1]+len(line))
 edits=[]
 for n in ast.walk(tree):
  if not isinstance(n,ast.Assert):continue
  test=n.test
  if isinstance(test,ast.Compare) and len(test.ops)==1 and isinstance(test.ops[0],ast.Eq) and isinstance(test.comparators[0],ast.Tuple) and len(test.comparators[0].elts)==2:
   exp,summary=test.comparators[0].elts
   if not isinstance(exp,ast.Constant) or exp.value not in [True,False,None]:continue
   if not (isinstance(test.left,ast.Name) and test.left.id=='result' or isinstance(test.left,ast.Await)):continue
   before='';name='result'
   if isinstance(test.left,ast.Await):before='observed = '+ast.get_source_segment(s,test.left)+'\n'+' '*n.col_offset;name='observed'
   summary_src=ast.get_source_segment(s,summary)
   type_name='IncompleteChecks' if 'still running' in summary_src else 'AbsentChecks' if exp.value is None else 'ObservedChecks'
   clause=f'isinstance({name}, {type_name})'
   if type_name=='ObservedChecks':clause+=f' and {name}.checks_passed is {exp.value!r}'
   replacement=before+'assert '+clause+'\n'+' '*n.col_offset+f'assert {name}.summary == {summary_src}'
   edits.append((offsets[n.lineno-1]+n.col_offset,offsets[n.end_lineno-1]+n.end_col_offset,replacement))
 for a,b,r in sorted(edits,reverse=True):s=s[:a]+r+s[b:]
 s=s.replace('from kodezart.types.domain.check_observation import ObservedChecks','from kodezart.types.domain.check_observation import AbsentChecks, IncompleteChecks, ObservedChecks')
 p.write_text(s)
# External wire fixtures now declare an actual common check commit explicitly.
for name in ['test_github_api.py','test_ci_observation.py']:
 p=Path('tests/adapters')/name;s=p.read_text();tree=ast.parse(s);lines=s.splitlines(keepends=True);offsets=[0]
 for line in lines:offsets.append(offsets[-1]+len(line))
 edits=[]
 for n in ast.walk(tree):
  if not isinstance(n,ast.Dict):continue
  keys=[k.value for k in n.keys if isinstance(k,ast.Constant)]
  if {'name','status','conclusion'}<=set(keys) and 'head_sha' not in keys:
   pos=offsets[n.lineno-1]+n.col_offset+1
   edits.append((pos, '\"head_sha\": \"a\" * 40, '))
 for pos,r in sorted(edits,reverse=True):s=s[:pos]+r+s[pos:]
 p.write_text(s)

import sys,ast,json
from pathlib import Path
sys.path.insert(0,'/private/tmp/kodezart-recovery-session');import extract_m2 as h
h.T=Path('/private/tmp/kodezart-v03-m3-plan-walk-census');O=Path('/private/tmp/kodezart-recovery-session')
h.symbol('tests/fakes.py','FakeRaisingExecutor')
h.whole('tests/adapters/test_git_artifact_persister.py')
# Remove only obsolete provider constructor args, retaining real persister identity.
rows=[]
for p in (h.T/'tests').rglob('*.py'):
 s=p.read_text();tree=ast.parse(s);lines=s.splitlines(keepends=True);offset=[0]
 for line in lines:offset.append(offset[-1]+len(line))
 edits=[]
 for n in ast.walk(tree):
  if not isinstance(n,ast.Call) or not isinstance(n.func,ast.Name) or n.func.id!='GitWorktreeProvider':continue
  for kw in n.keywords:
   if kw.arg not in ['committer_name','committer_email']:continue
   start=offset[kw.lineno-1]+kw.col_offset;end=offset[kw.end_lineno-1]+kw.end_col_offset
   if s[end:end+1]==',':end+=1
   # A standalone keyword line can be removed in full.
   if not s[offset[kw.lineno-1]:start].strip() and not s[end:offset[kw.end_lineno]].strip():start=offset[kw.lineno-1];end=offset[kw.end_lineno]
   edits.append((start,end))
 for start,end in sorted(edits,reverse=True):s=s[:start]+s[end:]
 if edits:p.write_text(s);rows.append({'path':str(p.relative_to(h.T)),'obsolete_provider_args_removed':len(edits)})
(O/'m3-census-workspace-callers.json').write_text(json.dumps(rows,indent=2)+'\n')
log=O/'m3-census-extracted-hunks.json';log.write_text(json.dumps(json.loads(log.read_text())+h.records,indent=2)+'\n')

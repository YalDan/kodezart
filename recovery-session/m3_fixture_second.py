import sys,ast,json
from pathlib import Path
sys.path.insert(0,'/private/tmp/kodezart-recovery-session');import extract_m2 as h
h.T=Path('/private/tmp/kodezart-v03-m3-plan-walk');O=Path('/private/tmp/kodezart-recovery-session')
h.whole('tests/adapters/test_github_api.py')
p='tests/tracker/conftest.py'
for n in ['FIRE_STAGE_KEY','linear_over_fake_mcp','fake_port_over_fixture']:h.symbol(p,n)
# Constants must precede fixture consumers.
s=(h.T/p).read_text();ls=s.splitlines(keepends=True);n=h.node(s,'FIRE_STAGE_KEY');block=ls[h.start(n):n.end_lineno];del ls[h.start(n):n.end_lineno];s=''.join(ls);n=h.node(s,'linear_over_fake_mcp');ls=s.splitlines(keepends=True);ls[h.start(n):h.start(n)]=block+['\n'];(h.T/p).write_text(''.join(ls))
# New required source boundary is explicit at every existing construction site.
rows=[]
for p in (h.T/'tests').rglob('*.py'):
 s=p.read_text();tree=ast.parse(s);lines=s.splitlines(keepends=True);changes=[]
 for n in ast.walk(tree):
  if isinstance(n,ast.Call) and isinstance(n.func,ast.Name) and n.func.id=='LinearMcpTracker' and not any(k.arg=='criteria_stage_label_key' for k in n.keywords):
   if any(k.arg is None for k in n.keywords):continue
   if n.lineno==n.end_lineno:raise RuntimeError(str(p)+': one-line constructor needs explicit edit')
   changes.append(n.lineno)
 for pos in sorted(changes,reverse=True):
  indent=' '*(n.col_offset+4) if False else ' '*(len(lines[pos])-len(lines[pos].lstrip()))
  lines.insert(pos,indent+'criteria_stage_label_key=None,\n')
 if changes:p.write_text(''.join(lines));rows.append({'path':str(p.relative_to(h.T)),'explicit_none_lines':changes})
(O/'m3-stage-caller-map.json').write_text(json.dumps(rows,indent=2)+'\n')
p=h.T/'src/kodezart/types/domain/agent.py';s=p.read_text()
for n in ['audit_claim','audit_overclaim','audit_detection_removal','audit_mandate']:s=s.replace('    "'+n+'",\n','')
p.write_text(s)
log=O/'m3-extracted-hunks.json';log.write_text(json.dumps(json.loads(log.read_text())+h.records,indent=2)+'\n')
print('explicit stage argument callers',len(rows))

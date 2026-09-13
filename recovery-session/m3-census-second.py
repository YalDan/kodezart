import sys,ast,json
from pathlib import Path
sys.path.insert(0,'/private/tmp/kodezart-recovery-session');import extract_m2 as h
h.T=Path('/private/tmp/kodezart-v03-m3-plan-walk-census');O=Path('/private/tmp/kodezart-recovery-session')
for p in ['tests/integration/test_workflow_e2e.py','tests/integration/test_stacked_scope.py','tests/api/v1/test_agent.py','tests/chains/test_shorthand_url_resolution.py']:h.whole(p)
for n in ['make_engine','run_engine']:h.symbol('tests/chains/test_outbound_gating.py',n)
p='tests/api/v1/test_jobs.py'
for n in ['GatedWorkflowEngine','ChattyWorkflowEngine','GatedQualityGate','RaisingWorkflowEngine','BaseRecordingEngine','_request','_real_engine','_mid_run_engine']:
 h.symbol(p,n)
# Current required scope is explicit in old unscoped fixture inputs.
rows=[]
for p in (h.T/'tests').rglob('*.py'):
 s=p.read_text();tree=ast.parse(s);lines=s.splitlines(keepends=True);positions=[]
 for n in ast.walk(tree):
  if isinstance(n,ast.Call) and isinstance(n.func,ast.Name) and n.func.id=='WorkflowSubmission' and not any(k.arg=='scope' or k.arg is None for k in n.keywords):
   if n.lineno==n.end_lineno:raise RuntimeError(str(p))
   positions.append(n.lineno)
 for pos in sorted(positions,reverse=True):
  indent=' '*(len(lines[pos])-len(lines[pos].lstrip()));lines.insert(pos,indent+'scope=None,\n')
 if positions:p.write_text(''.join(lines));rows.append({'path':str(p.relative_to(h.T)),'scope_none_lines':positions})
p=h.T/'tests/prompt_census.py';p.write_text(p.read_text().replace('PROMPT_FUNCTION_COUNT: Final[int] = 23','PROMPT_FUNCTION_COUNT: Final[int] = 26'))
(O/'m3-census-submission-callers.json').write_text(json.dumps(rows,indent=2)+'\n')
log=O/'m3-census-extracted-hunks.json';log.write_text(json.dumps(json.loads(log.read_text())+h.records,indent=2)+'\n')

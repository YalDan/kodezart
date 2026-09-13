"""Read-only dependency evidence; writes only external session artifacts."""
import ast
import hashlib
import json
import subprocess
from pathlib import Path
R=Path('/private/tmp/kodezart-v03-recovery-integration')
C=Path('/private/tmp/kodezart-v03-recovery-semantic-applied-current')
S=Path('/private/tmp/kodezart-recovery-session')
MAIN='4661a24b599d75503a997f3ce122f3ad2da77048'
DONOR='d2c6fceab762191d4e40b23c8cd349ef476e4b12'
def git(path,*args):return subprocess.check_output(['git',*args],cwd=path)
def digest(raw):return hashlib.sha256(raw).hexdigest()
def census(raw):
 t=ast.parse(raw)
 out=[]
 for n in t.body:
  name=getattr(n,'name',None)
  if isinstance(n,ast.ImportFrom):
   out.append({'kind':'import','module':n.module,'names':[a.name for a in n.names],'start':n.lineno,'end':n.end_lineno})
  elif name:
   if not isinstance(name,str):name=ast.unparse(name)
   out.append({'kind':type(n).__name__,'name':name,'start':n.lineno,'end':n.end_lineno})
 return out
paths=[
 'src/kodezart/services/native_amendments.py',
 'src/kodezart/services/amendment_writeback.py',
 'src/kodezart/chains/native_amendment.py',
 'src/kodezart/types/domain/amendment.py',
 'src/kodezart/types/domain/amendment_write.py',
 'src/kodezart/domain/amendment.py',
 'src/kodezart/domain/criterion_amendment.py',
 'src/kodezart/domain/fire_spec.py',
 'src/kodezart/core/protocols.py',
 'src/kodezart/adapters/linear_mcp_tracker.py',
 'src/kodezart/composition/engine.py',
]
before=git(C,'diff','--binary')
rows=[]
folder=S/'extraction-native-candidate-snapshot';folder.mkdir(exist_ok=True)
for p in paths:
 raw=(C/p).read_bytes(); name=p.replace('/','__')
 (folder/name).write_bytes(raw)
 rows.append({'path':p,'sha256':digest(raw),'snapshot':name,'census':census(raw)})
after=git(C,'diff','--binary')
current={r['path']:digest((C/r['path']).read_bytes())==r['sha256'] for r in rows}
meta={'candidate_head':git(C,'rev-parse','HEAD').decode().strip(),'candidate_status':git(C,'status','--porcelain=v1').decode(),'tracked_diff_before_sha256':digest(before),'tracked_diff_after_sha256':digest(after),'captured_files_unchanged_at_end':current,'is_frozen':False,'acceptance_claim':False,'rows':rows}
(S/'extraction-native-candidate-closure.json').write_text(json.dumps(meta,indent=2)+'\n')
# Exact ticket-generation compatibility delta and main symbol evidence.
ticket='src/kodezart/chains/ticket_generation.py'
diff=git(R,'diff',MAIN,DONOR,'--',ticket)
(S/'extraction-ticket-compatibility-d2c6fce.patch').write_bytes(diff)
checks={
 'src/kodezart/chains/ticket_generation.py':['TicketGenerationLoop'],
 'src/kodezart/types/domain/run_records.py':['RunIdentity'],
 'src/kodezart/types/domain/agent.py':['CommitMessageOutput','COMMIT_MESSAGE_SCHEMA','WorkflowCompleteEvent','WorkflowScopeBaseEvent','WorkflowVisibilityEvent'],
 'src/kodezart/types/domain/branch.py':['WorkRef','WorkRefRole','BackupBranchName'],
 'src/kodezart/types/domain/criteria.py':['CriterionId','CriterionVerdict','FindingEvidence'],
 'src/kodezart/types/domain/outcome.py':['WorkflowOutcome'],
 'src/kodezart/domain/thread_id.py':['workflow_thread_id'],
}
base=[]
for p,names in checks.items():
 raw=git(R,'show',f'{MAIN}:{p}'); t=ast.parse(raw)
 for n in t.body:
  name=getattr(n,'name',None)
  if isinstance(n,ast.Assign):name=' '.join(i.id for i in n.targets if isinstance(i,ast.Name))
  if isinstance(n,ast.AnnAssign) and isinstance(n.target,ast.Name):name=n.target.id
  if name in names:base.append({'path':p,'symbol':name,'start':n.lineno,'end':n.end_lineno,'main_blob_sha256':digest(raw)})
(S/'extraction-baseline-symbol-evidence.json').write_text(json.dumps({'main':MAIN,'donor':DONOR,'ticket_patch_sha256':digest(diff),'baseline_symbols':base},indent=2)+'\n')
print('Candidate head:',meta['candidate_head'])
print('Tracked diff snapshot:',meta['tracked_diff_before_sha256'],meta['tracked_diff_after_sha256'])
print('Captured candidate files:',len(rows),'unchanged-at-end:',all(current.values()))
print('Main existing symbols:',len(base),'ticket patch:',digest(diff))
for p in ('src/kodezart/services/amendment_writeback.py','src/kodezart/chains/native_amendment.py'):
 row=next(r for r in rows if r['path']==p)
 imported=[x for x in row['census'] if x['kind']=='import']
 assert not any(x['module'] in ('kodezart.types.domain.workflow','kodezart.types.domain.fire_spec') for x in imported)
 print(p,'has no TrackerSpec/workflow import; captured hash',row['sha256'])

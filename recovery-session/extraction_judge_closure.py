import ast,json,subprocess
from pathlib import Path
from hashlib import sha256
R=Path('/private/tmp/kodezart-v03-recovery-integration');S=Path('/private/tmp/kodezart-recovery-session');H='d2c6fceab762191d4e40b23c8cd349ef476e4b12'
want={
 'src/kodezart/services/audit_sessions.py':{'judge_in_workspace','FreshAuditSession'},
 'src/kodezart/chains/write_back_verifier.py':{'WriteBackStep','WriteBackJudge','WriteBackVerifier','FreshWriteBackJudge'},
 'src/kodezart/services/tracker_artifacts.py':{'require_artifact_read','read_tracker_artifact','_SUPPORTED'},
 'src/kodezart/types/domain/audit.py':{'AuditVerdict','TrackerArtifact'},
 'src/kodezart/types/domain/write_back.py':{'WriteBackFinding','WriteBackResult'},
 'src/kodezart/types/domain/agent.py':{'WRITE_BACK_SCHEMA','RaiseSite'},
 'src/kodezart/domain/errors.py':{'WriteBackReadError'},
 'src/kodezart/types/domain/prompts.py':{'PromptKey'},
 'src/kodezart/domain/fire_spec.py':{'_CRITERION_ROW','_FENCE','_without_comments','criterion_field_bodies','tracker_spec_from_issues','criterion_check','require_fire_entry'},
 'src/kodezart/types/domain/assertion_drift.py':{'ProtectedTestRef','GitSourceBlob'},
 'src/kodezart/types/domain/branch.py':{'BranchRole','BranchAssociation','WorkRefLanding'},
 'src/kodezart/core/protocols.py':{'GitSourceReader'},
 'src/kodezart/services/fire_record_facts.py':{'observe_fire_facts'},
 'src/kodezart/adapters/subprocess_git_source_reader.py':{'SubprocessGitSourceReader'},
}
rows=[]
for path,names in want.items():
 raw=subprocess.check_output(['git','show',f'{H}:{path}'],cwd=R).decode();lines=raw.splitlines();tree=ast.parse(raw)
 imports=[]
 for node in tree.body:
  if isinstance(node,ast.ImportFrom):
   for alias in node.names: imports.append({'name':alias.asname or alias.name,'symbol':alias.name,'module':node.module,'start':node.lineno,'end':node.end_lineno})
 for node in tree.body:
  name=getattr(node,'name',None)
  if isinstance(node,ast.Assign): name=' '.join(t.id for t in node.targets if isinstance(t,ast.Name))
  if isinstance(node,ast.AnnAssign) and isinstance(node.target,ast.Name): name=node.target.id
  if name not in names: continue
  loaded={n.id for n in ast.walk(node) if isinstance(n,ast.Name) and isinstance(n.ctx,ast.Load)}
  rows.append({'path':path,'symbol':name,'start':node.lineno,'end':node.end_lineno,'source_sha256':sha256(('\n'.join(lines[node.lineno-1:node.end_lineno])+'\n').encode()).hexdigest(),'used_imports':[i for i in imports if i['name'] in loaded]})
(S/'extraction-judge-closure-d2c6fce.json').write_text(json.dumps({'source':H,'rows':rows},indent=2)+'\n')
for row in rows:
 print(row['path'],row['symbol'],f"{row['start']}-{row['end']}")
 for i in row['used_imports']: print(' ',i['module'],i['symbol'],f"{i['start']}-{i['end']}")

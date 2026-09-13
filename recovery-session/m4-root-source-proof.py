import ast,hashlib,json,subprocess
from pathlib import Path
R=Path('/private/tmp/kodezart-recovery-session');D=json.loads((R/'m4-verifier-provenance.json').read_text())
def blob(sha,p):return subprocess.check_output(['git','show',f'{sha}:{p}'])
def digest(b):return hashlib.sha256(b).hexdigest()
def symbol(raw,names):
 tree=ast.parse(raw)
 for name in names.split('.'):
  tree=next(x for x in tree.body if getattr(x,'name',None)==name)
 return ast.dump(tree,include_attributes=False)
rows=[]
for row in D['files']:
 data=blob('340ad8d',row['path']);assert digest(data)==row['sha256'],row['path']
 if row['whole_file_identical_to_donor']:
  assert data==blob(row['donor'],row['donor_path']),row['path']
 rows.append({'path':row['path'],'verified_file_hash':digest(data),'whole_donor_equality':row['whole_file_identical_to_donor']})
checks=[('src/kodezart/adapters/subprocess_git_service.py','SubprocessGitService.has_replace_refs','7892ca1'),('src/kodezart/core/protocols.py','GitService.has_replace_refs','7892ca1'),('src/kodezart/domain/errors.py','WriteBackReadError','7892ca1'),('src/kodezart/services/audit_sessions.py','judge_in_workspace','7042032'),('src/kodezart/services/git_observations.py','read_workspace_head','7892ca1'),('src/kodezart/types/domain/audit.py','AuditVerdict','7892ca1'),('src/kodezart/types/domain/audit.py','TrackerArtifact','7892ca1')]
for path,name,sha in checks:
 assert symbol(blob('340ad8d',path),name)==symbol(blob(sha,path),name),(path,name)
result={'source':'40b250c5c032c2a7ef9764c16f85bc7cf8d086ea','destination':'340ad8df3bcbf23882f930cd200ce3d8cf627753','files':rows,'exact_symbol_checks':checks,'tree':subprocess.check_output(['git','rev-parse','340ad8d^{tree}'],text=True).strip()}
assert subprocess.check_output(['git','diff','40b250c','340ad8d'])==b''
(R/'m4-root-source-proof.json').write_text(json.dumps(result,indent=2)+'\n')
print(f'{len(rows)} file hashes; {sum(r["whole_donor_equality"] for r in rows)} full donor files; {len(checks)} exact source symbols; full tree equivalence verified')

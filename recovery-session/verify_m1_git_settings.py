import ast,hashlib,json,subprocess
from pathlib import Path
R=Path('/private/tmp/kodezart-v03-m1-git-settings-extraction');OUT=Path('/private/tmp/kodezart-recovery-session');BASE='241e85cca03c963ec1ddd17e29f10700d499ce14';DONOR='36083f83f42c03240ebb5861fe284da2c9f04180'
def git(*args):return subprocess.check_output(['git','-C',str(R),*args],text=True)
def show(ref,p):return git('show',ref+':'+p)
head=git('rev-parse','HEAD').strip();fields={'git_remote':'remote','git_base_url':'base_url','clone_cache_dir':'clone_cache_dir','integration_workspace_dir':'integration_workspace_dir','git_committer_name':'committer_name','git_committer_email':'committer_email'}
settings='src/kodezart/core/git_settings.py';assert show(head,settings)==show(DONOR,settings)
cp='src/kodezart/core/config.py'
def config_tree(s):return next(n for n in ast.parse(s).body if isinstance(n,ast.ClassDef) and n.name=='AppConfig')
base=config_tree(show(BASE,cp));current=config_tree(show(head,cp));oldfields={n.target.id:n for n in base.body if isinstance(n,ast.AnnAssign) and isinstance(n.target,ast.Name)};newfields={n.target.id:n for n in current.body if isinstance(n,ast.AnnAssign) and isinstance(n.target,ast.Name)}
assert set(oldfields)-set(newfields)==set(fields);assert set(newfields)-set(oldfields)=={'git'}
for name in set(oldfields)&set(newfields):assert ast.dump(oldfields[name])==ast.dump(newfields[name]),name
oldmethods={n.name:n for n in base.body if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef))};newmethods={n.name:n for n in current.body if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef))};assert set(oldmethods)==set(newmethods)
for name in oldmethods:
 node=newmethods[name]
 if name=='settings_customise_sources':
  for a in ast.walk(node):
   if isinstance(a,ast.Set):a.elts=[v for v in a.elts if not isinstance(v,ast.Constant) or v.value not in fields]
 assert ast.dump(oldmethods[name])==ast.dump(node),name
sp='src/kodezart/adapters/subprocess_git_service.py';expected=show(BASE,sp).replace('["git", "clone", "--bare", effective_url, target]','["git", "clone", "--bare", "--origin", self._remote, effective_url, target]');assert show(head,sp)==expected
expected=show(BASE,'Makefile').replace('core/config.py','core/git_settings.py');assert show(head,'Makefile')==expected
stale=[]
for p in (R/'src').rglob('*.py'):
 for n in ast.walk(ast.parse(p.read_text())):
  if isinstance(n,ast.Attribute) and n.attr in fields and isinstance(n.value,ast.Name) and n.value.id.endswith('config'):stale.append((str(p.relative_to(R)),n.lineno,ast.unparse(n)))
assert not stale
patch=git('diff','--binary',BASE,head);(OUT/'m1-git-settings-extraction.patch').write_text(patch)
changed=git('diff','--name-only',BASE,head).splitlines();rows=[]
for p in changed:
 b=show(head,p);diff=git('diff','--unified=3',BASE,head,'--',p);rows.append({'path':p,'candidate_sha256':hashlib.sha256(b.encode()).hexdigest(),'candidate_blob':git('rev-parse',head+':'+p).strip(),'diff':diff})
report={'base':BASE,'donor':DONOR,'head':head,'branch':git('branch','--show-current').strip(),'fields':fields,'proofs':{'GitSettings_byte_identical_to_donor':True,'only_six_AppConfig_fields_replaced':True,'all_other_AppConfig_fields_unchanged_AST':True,'settings_sources_unchanged_except_six_retired_names_AST':True,'every_other_AppConfig_method_unchanged_AST':True,'Git_service_only_exact_donor_clone_remote_hunk':True,'Makefile_only_origin_owner_move':True,'stale_current_config_attribute_reads':stale},'paths':rows,'patch_sha256':hashlib.sha256(patch.encode()).hexdigest(),'review_notes':['GitWorktreeProvider retains current M1 configured committer parameters; donor removal is later/out of scope.','Donor test_git_settings.py adapted only for M1 build_dispatch_runtime without workspace keyword plus ten added secret/prefix controls.','Donor S108 git_settings.py per-file exemption preserved by root authorization as existing default-path packaging policy.','M2 consumes config.git.remote after root serialization; future M3/M5 callers are not copied.']}
(OUT/'m1-git-settings-extraction-map.json').write_text(json.dumps(report,indent=2)+'\n');print(json.dumps({k:v for k,v in report.items() if k!='paths'},indent=2));print('changed_paths',len(changed))

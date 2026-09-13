"""Index every frozen diff hunk; report provenance without replacing source."""
import ast
import hashlib
import json
import re
import subprocess
from pathlib import Path

ROOT = Path('/private/tmp/kodezart-v03-m1-session-policy-extraction')
OUT = Path('/private/tmp/kodezart-recovery-session')
BASE = 'fec7f28292975f0b4635a06ba84e4e35fdc3c2e8'
FINAL = 'ea1d91596cc862dbfaa48abb149d2480702bf1bb'
DONOR = '1f4296c8c36a7498625d2478f8ec7ae6ec187923'
def git(*args): return subprocess.check_output(['git',*args], cwd=ROOT, text=True)
def read(rev, path):
    r=subprocess.run(['git','show',f'{rev}:{path}'],cwd=ROOT,text=True,capture_output=True)
    return None if r.returncode else r.stdout

def nodes(source):
    if source is None: return {}
    result={}
    def visit(body,prefix=''):
        for n in body:
            if isinstance(n,(ast.ClassDef,ast.FunctionDef,ast.AsyncFunctionDef)):
                key=prefix+n.name;result[key]=n
                visit(n.body,key+'.')
            elif isinstance(n,ast.AnnAssign) and isinstance(n.target,ast.Name):
                result[prefix+n.target.id]=n
            elif isinstance(n,ast.Assign):
                for target in n.targets:
                    if isinstance(target,ast.Name):result[prefix+target.id]=n
            elif isinstance(n,ast.TypeAlias):result[prefix+n.name.id]=n
    visit(ast.parse(source).body)
    return result

cuts={x['path']:x['cut'] for x in json.loads((OUT/'session-policy-extraction-operations.json').read_text())}
proof=json.loads((OUT/'session-policy-normalized-source-proof.json').read_text())
normal={(x['path'],x['symbol']):x for x in proof}
newtests={'tests/chains/test_session_policy_composition.py':'New actual existing graph/real Git/real SDK-option composition oracle with external SDK transport doubles.', 'tests/api/v1/test_session_policy_base_boundary.py':'Existing donor empty/default/explicit-base HTTP contract adapted to current app.state composition; original six external assertions retained.'}
files=[]
for path in git('diff','--name-only',BASE,FINAL).splitlines():
    before,current,donor=read(BASE,path),read(FINAL,path),read(DONOR,path)
    ns,ds=nodes(current) if path.endswith('.py') else {},nodes(donor) if path.endswith('.py') else {}
    diff=git('diff','--no-ext-diff','-U0',BASE,FINAL,'--',path)
    parts=re.split(r'(?m)^(@@ .*? @@.*)\n',diff)
    hunks=[]
    for idx in range(1,len(parts),2):
        header,body=parts[idx],parts[idx+1]
        m=re.match(r'@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@',header)
        assert m,header
        old,nold,start,count=(int(m.group(1)),int(m.group(2) or 1),int(m.group(3)),int(m.group(4) or 1))
        end=start+max(count-1,0)
        touched=[(key,n) for key,n in ns.items() if n.lineno<=end and n.end_lineno>=start]
        # Most specific overlapping nodes retain separate function/field source addresses.
        selected=[(key,n) for key,n in touched if not any(other.startswith(key+'.') for other,_ in touched)]
        symbols=[]
        for key,n in selected:
            d=ds.get(key)
            symbols.append({'name':key,'new_lines':[n.lineno,n.end_lineno],'exact_donor_ast': d is not None and ast.dump(n)==ast.dump(d), 'baseline_normalization':normal.get((path,key))})
        if path in newtests:origin=newtests[path]
        elif path=='docs/architecture.md':origin='Exact donor Permission Modes paragraph only; neighboring docs untouched.'
        elif path.endswith('tool_request_schemas.json'):origin='Exact BASE HTTP request schemas, not later donor scope schema.'
        elif path.startswith('tests/'):
            origin='Existing donor acceptance oracle or baseline test fixture policy/domain-submission migration; see test-migrations ledger and source symbol identities. No unrelated fixture shape imported.'
        else:origin=cuts.get(path,'Exact donor HTTP alias/validator or explicit existing-handler/domain-command closure; later scope/phase fields deferred.')
        hunks.append({'index':len(hunks)+1,'header':header,'old_lines':[old,nold],'new_lines':[start,count], 'sha256':hashlib.sha256((header+'\n'+body).encode()).hexdigest(),'symbols':symbols,'origin':origin})
    files.append({'path':path,'baseline_sha256':None if before is None else hashlib.sha256(before.encode()).hexdigest(),'final_sha256':None if current is None else hashlib.sha256(current.encode()).hexdigest(),'whole_file_donor_equal':current==donor and current is not None,'hunks':hunks})
report={'base':BASE,'base_tree':git('rev-parse',BASE+'^{tree}').strip(),'donor':DONOR,'final':FINAL,'final_tree':git('rev-parse',FINAL+'^{tree}').strip(),'file_count':len(files),'hunk_count':sum(len(f['hunks']) for f in files),'source_file_count':sum(f['path'].startswith('src/') for f in files),'files':files,'explicit_deferred':['M3 native issue_key/scope/phase/workspace fields and new execution paths','M4 actual judge consumer','Later grouped session settings and typed knowledge transports','HTTP response/Depends migration','Cross-version checkpoint resume compatibility'],'policy_limit':'SDK allowed_tools are approval selectors; tools exposure remains unchanged; not a shell sandbox.'}
(OUT/'session-policy-final-hunk-map.json').write_text(json.dumps(report,indent=2)+'\n')
print({k:report[k] for k in ['final','final_tree','file_count','hunk_count','source_file_count']})

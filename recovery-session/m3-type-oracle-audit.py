import ast
import hashlib
import json
import subprocess
from pathlib import Path

ROOT = Path('/private/tmp/kodezart-v03-m3-type-review')
CANDIDATE = 'dad63c4f4b957323a58331e8416c2a542eb0a79c'
BASE = 'fadf6efe29c7addcc608b22e8345cd9008ce4bab'
DONOR = 'da39c439898aec1233aa8b6157b35e961df1e453'

def git(*args):
    return subprocess.check_output(['git', *args], cwd=ROOT, text=True)

def blob(sha, path):
    result = subprocess.run(['git', 'show', f'{sha}:{path}'], cwd=ROOT, text=True, capture_output=True)
    return result.stdout if result.returncode == 0 else None

def symbols(body):
    result = {}
    def walk(nodes, prefix=''):
        for node in nodes:
            if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                name = prefix + node.name
                result[name] = ast.dump(node, include_attributes=False)
                if isinstance(node, ast.ClassDef):
                    walk(node.body, name + '.')
    walk(ast.parse(body).body)
    return result

rows = []
paths = git('diff', '--name-only', BASE, CANDIDATE).splitlines()
for path in paths:
    if not path.endswith('.py'):
        continue
    current, donor, base = (blob(sha, path) for sha in [CANDIDATE, DONOR, BASE])
    row = {'path': path, 'donor_bytes': current == donor, 'candidate_exists': current is not None}
    if current is not None and donor is not None:
        ours, theirs, previous = symbols(current), symbols(donor), symbols(base or '')
        row.update(
            donor_exact_symbols=[k for k in ours if k in theirs and ours[k] == theirs[k]],
            non_donor_symbols=[k for k in ours if k not in theirs or ours[k] != theirs[k]],
            donor_missing_symbols=[k for k in theirs if k not in ours],
            base_missing_tests=[k for k in previous if k.split('.')[-1].startswith('test_') and k not in ours],
            changed_tests=[k for k in ours if k.split('.')[-1].startswith('test_') and k in previous and ours[k] != previous[k]],
        )
    rows.append(row)

out = Path('/private/tmp/kodezart-recovery-session/m3-type-oracle-audit.json')
out.write_text(json.dumps(rows, indent=2)+'\n')
print('files',len(rows),'donor byte identical',sum(r['donor_bytes'] for r in rows))
for r in rows:
    if not r['donor_bytes']:
        print(json.dumps({k:v for k,v in r.items() if k != 'donor_exact_symbols'}))

p='src/kodezart/types/domain/run_event.py'
symbols_by_sha = {sha: symbols(blob(sha,p)) for sha in [CANDIDATE,DONOR,'c855fb061209a7f20a3d10cd06f13f8476fba7dc']}
assert len({s['RunEventKind'] for s in symbols_by_sha.values()}) == 1
for sha in [CANDIDATE,'c855fb061209a7f20a3d10cd06f13f8476fba7dc']:
    assert set(symbols_by_sha[sha]) == {'RunEventKind'}
print('RunEventKind exact donor AST in M3 and M4; no other classes/functions in primitive.')

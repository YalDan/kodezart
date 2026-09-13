import ast
import hashlib
import json
import subprocess
from pathlib import Path

ARTIFACTS = Path('/private/tmp/kodezart-recovery-session')
manifest = json.loads((ARTIFACTS / 'm1-native-read-provenance.json').read_text())

def source(sha, path):
    return subprocess.check_output(['git', 'show', f'{sha}:{path}']).decode()

def symbols(text):
    found = {}
    def walk(nodes, prefix=''):
        for node in nodes:
            if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                name = prefix + node.name
                found[name] = ast.dump(node, include_attributes=False)
                walk(node.body, name + '.')
    walk(ast.parse(text).body)
    return found

actual = set(subprocess.check_output(['git', 'diff', '--name-only', manifest['base'], manifest['head']]).decode().splitlines())
assert actual == {row['path'] for row in manifest['files']}
proof = {'head': manifest['head'], 'tree': manifest['tree'], 'files': []}
for row in manifest['files']:
    current = source(manifest['head'], row['path'])
    assert hashlib.sha256(current.encode()).hexdigest() == row['sha256'], row['path']
    claims = row.get('unchanged_donor_symbols', [])
    if claims:
        before = symbols(source(manifest['donor'], row['path']))
        after = symbols(current)
        for claim in claims:
            assert claim['ast_identical']
            assert before[claim['symbol']] == after[claim['symbol']], (row['path'], claim['symbol'])
    proof['files'].append({'path': row['path'], 'verified_hash': row['sha256'], 'verified_donor_symbols': len(claims)})
assert subprocess.check_output(['git', 'rev-parse', manifest['head'] + '^{tree}']).decode().strip() == manifest['tree']
proof['file_count'] = len(proof['files'])
proof['donor_symbol_claim_count'] = sum(row['verified_donor_symbols'] for row in proof['files'])
(ARTIFACTS / 'm1-native-read-root-proof.json').write_text(json.dumps(proof, indent=2) + '\n')
print(json.dumps({k: v for k, v in proof.items() if k != 'files'}))

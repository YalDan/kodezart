import ast
import hashlib
import json
import re
import subprocess
from pathlib import Path

ROOT = Path('/private/tmp/kodezart-v03-m1-session-policy-root-review')
OUT = Path('/private/tmp/kodezart-recovery-session')
data = json.loads((OUT / 'session-policy-final-hunk-map.json').read_text())

def git(*args):
    return subprocess.check_output(['git', *args], cwd=ROOT)

def blob(ref, path):
    result = subprocess.run(['git', 'show', f'{ref}:{path}'], cwd=ROOT, capture_output=True)
    return result.stdout if result.returncode == 0 else None

def digest(value):
    return None if value is None else hashlib.sha256(value).hexdigest()

def symbols(raw):
    result = {}
    def walk(node, prefix=''):
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            prefix += node.name
            result[prefix] = ast.dump(node, include_attributes=False)
            prefix += '.'
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            result[prefix + node.target.id] = ast.dump(node, include_attributes=False)
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    result[prefix + target.id] = ast.dump(node, include_attributes=False)
        elif isinstance(node, ast.TypeAlias):
            result[prefix + node.name.id] = ast.dump(node, include_attributes=False)
        for child in ast.iter_child_nodes(node):
            walk(child, prefix)
    walk(ast.parse(raw))
    return result

proof = {'base': data['base'], 'final': data['final'], 'donor': data['donor'], 'files': [], 'exact_donor_symbols': []}
for item in data['files']:
    path = item['path']
    before, after, donor = (blob(data[key], path) for key in ['base', 'final', 'donor'])
    assert digest(before) == item['baseline_sha256'], (path, 'baseline hash')
    assert digest(after) == item['final_sha256'], (path, 'final hash')
    diff = git('diff', '--unified=0', data['base'], data['final'], '--', path).decode()
    starts = list(re.finditer(r'^@@ ', diff, re.M))
    chunks = [diff[m.start():starts[i + 1].start() if i + 1 < len(starts) else len(diff)] for i, m in enumerate(starts)]
    assert len(chunks) == len(item['hunks']), (path, 'hunk count')
    for actual, expected in zip(chunks, item['hunks']):
        assert digest(actual.encode()) == expected['sha256'], (path, expected['header'])
    if item['whole_file_donor_equal']:
        assert after == donor, (path, 'donor bytes')
    claims = {s['name'] for h in item['hunks'] for s in h['symbols'] if s.get('exact_donor_ast')}
    if claims:
        actual_symbols, donor_symbols = symbols(after), symbols(donor)
        for name in sorted(claims):
            assert actual_symbols[name] == donor_symbols[name], (path, name, 'donor AST')
            proof['exact_donor_symbols'].append({'path': path, 'symbol': name})
    proof['files'].append({'path': path, 'hunks': len(chunks), 'hashes_verified': True, 'donor_bytes_verified': item['whole_file_donor_equal']})
proof['file_count'] = len(proof['files'])
proof['hunk_count'] = sum(x['hunks'] for x in proof['files'])
proof['exact_donor_symbol_count'] = len(proof['exact_donor_symbols'])
(OUT / 'm1-session-policy-root-proof.json').write_text(json.dumps(proof, indent=2) + '\n')
print({k: proof[k] for k in ['file_count', 'hunk_count', 'exact_donor_symbol_count']})

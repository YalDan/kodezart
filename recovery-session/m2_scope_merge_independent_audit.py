"""Read immutable Git objects to audit the M2 scope-bootstrap merge."""
import ast
import collections
import hashlib
import json
import subprocess
from pathlib import Path

REPO = "/private/tmp/kodezart-v03-m2-scope-bootstrap-independent"
CANDIDATE = "37723cd8c660de5ad959de1f118b93287da48e64"
M2 = "fadf6efe29c7addcc608b22e8345cd9008ce4bab"
SHARED = "268be4057dbcd4d4edb79b35d7e2b60a30f2f3c8"
ROOT = Path("/private/tmp/kodezart-recovery-session")

def git(*args):
    return subprocess.check_output(["git", "-C", REPO, *args])

def content(ref, path):
    found = subprocess.run(["git", "-C", REPO, "show", f"{ref}:{path}"], capture_output=True)
    return None if found.returncode else found.stdout

def dump(node):
    return ast.dump(node, include_attributes=False)

def symbols(data):
    if data is None:
        return {}
    found = {}
    def walk(nodes, prefix=""):
        for node in nodes:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                found[prefix + node.name] = dump(node)
            elif isinstance(node, ast.ClassDef):
                walk(node.body, prefix + node.name + ".")
    walk(ast.parse(data).body)
    return found

paths = git("diff", "--name-only", M2, CANDIDATE).decode().splitlines()
all_source = [p for p in git("ls-tree", "-r", "--name-only", CANDIDATE, "src").decode().splitlines() if p.endswith(".py")]
production_changes = []
merge_authored_functions = []
duplicates = []
constructor_duplicates = []
for path in all_source + ["tests/fakes.py"]:
    current = content(CANDIDATE, path)
    previous = content(M2, path)
    shared = content(SHARED, path)
    current_symbols, previous_symbols, shared_symbols = map(symbols, (current, previous, shared))
    if path.startswith("src/"):
        for name, body in current_symbols.items():
            if previous_symbols.get(name) != body:
                record = {"path": path, "name": name, "new": name not in previous_symbols, "exact_shared_ast": shared_symbols.get(name) == body}
                production_changes.append(record)
                if not record["exact_shared_ast"]:
                    merge_authored_functions.append(record)
        for name in previous_symbols.keys() - current_symbols.keys():
            merge_authored_functions.append({"path": path, "name": name, "removed_from_m2": True})
    def scan(nodes, owner="module"):
        names = collections.Counter(n.name for n in nodes if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)))
        duplicates.extend({"path": path, "owner": owner, "name": name, "count": count} for name, count in names.items() if count > 1)
        for node in nodes:
            if isinstance(node, ast.ClassDef):
                scan(node.body, owner + "." + node.name)
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == "__init__":
                assignments = collections.defaultdict(list)
                for statement in node.body:
                    targets = statement.targets if isinstance(statement, ast.Assign) else [statement.target] if isinstance(statement, ast.AnnAssign) else []
                    for target in targets:
                        if isinstance(target, ast.Attribute) and isinstance(target.value, ast.Name) and target.value.id == "self":
                            assignments[target.attr].append(statement.lineno)
                constructor_duplicates.extend({"path": path, "owner": owner, "name": name, "lines": lines} for name, lines in assignments.items() if len(lines) > 1)
    scan(ast.parse(current).body)

manifest = json.loads((ROOT / "m2-scope-bootstrap-resolution.json").read_text())
duplicate_removal_proof = []
for path, cls, method, reason in manifest["ast_identical_duplicate_removal"]:
    qualified = f"{cls}.{method}"
    all_symbols = {ref: symbols(content(ref, path)) for ref in (CANDIDATE, M2, SHARED)}
    duplicate_removal_proof.append({"path": path, "name": qualified, "exact_m2_ast": all_symbols[CANDIDATE][qualified] == all_symbols[M2][qualified], "exact_shared_ast": all_symbols[CANDIDATE][qualified] == all_symbols[SHARED][qualified]})

test_changes = []
for path in paths:
    if not path.startswith("tests/") or not path.endswith(".py"):
        continue
    current, previous, shared = (content(ref, path) for ref in (CANDIDATE, M2, SHARED))
    cs, ps, ss = map(symbols, (current, previous, shared))
    changed = []
    for name, body in cs.items():
        if body != ps.get(name):
            changed.append({"name": name, "new": name not in ps, "exact_shared_ast": body == ss.get(name)})
    removed = sorted(ps.keys() - cs.keys())
    test_changes.append({"path": path, "exact_shared_file": current == shared, "changed": changed, "removed": removed})

proof = {
    "candidate": CANDIDATE, "tree": git("rev-parse", CANDIDATE + "^{tree}").decode().strip(), "parents": [M2, SHARED],
    "production_functions_changed_from_m2": production_changes,
    "production_functions_not_composed_exactly_from_parents": merge_authored_functions,
    "duplicate_definitions": duplicates,
    "constructor_duplicates": constructor_duplicates,
    "fourteen_removals": duplicate_removal_proof,
    "tests": test_changes,
}
(ROOT / "m2-scope-bootstrap-independent-provenance.json").write_text(json.dumps(proof, indent=2) + "\n")
print(json.dumps({k: v for k, v in proof.items() if k not in {"tests", "production_functions_changed_from_m2"}}, indent=2))
print("Production function deltas:", json.dumps(production_changes, indent=2))
print("Test deltas not exact shared AST:", json.dumps([{**r, "changed": [c for c in r["changed"] if not c["exact_shared_ast"]]} for r in test_changes if r["removed"] or any(not c["exact_shared_ast"] for c in r["changed"])], indent=2))

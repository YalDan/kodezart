"""Compare immutable candidate/source/test objects, never alter a reviewed tree."""

import ast
import hashlib
import json
import subprocess
import sys
from pathlib import Path

repo, candidate, base, donor, destination = sys.argv[1:]


def git(*arguments):
    return subprocess.check_output(["git", "-C", repo, *arguments])


def read(ref, path):
    result = subprocess.run(
        ["git", "-C", repo, "show", f"{ref}:{path}"],
        capture_output=True,
        check=False,
    )
    return result.stdout if result.returncode == 0 else None


def definitions(data):
    if data is None:
        return {}
    tree = ast.parse(data)
    result = {}

    def visit(nodes, prefix=""):
        for node in nodes:
            if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                key = prefix + node.name
                result[key] = ast.dump(node, include_attributes=False)
                if isinstance(node, ast.ClassDef):
                    visit(node.body, key + ".")

    visit(tree.body)
    return result


def tests(data):
    return {
        key: value
        for key, value in definitions(data).items()
        if key.split(".")[-1].startswith("test_")
    }


paths = git("diff", "--name-only", base, candidate).decode().splitlines()
rows = []
for path in paths:
    current, prior, original = (read(ref, path) for ref in (candidate, base, donor))
    row = {
        "path": path,
        "candidate_sha256": hashlib.sha256(current).hexdigest() if current else None,
        "donor_sha256": hashlib.sha256(original).hexdigest() if original else None,
        "byte_equal_donor": current == original,
    }
    if path.endswith(".py"):
        curr_defs, prior_defs, orig_defs = (
            definitions(data) for data in (current, prior, original)
        )
        row["candidate_definitions_different_from_donor"] = [
            name for name, value in curr_defs.items() if orig_defs.get(name) != value
        ]
        if path.startswith("tests/"):
            curr_tests, prior_tests, orig_tests = (
                tests(data) for data in (current, prior, original)
            )
            row["baseline_test_oracles_changed_or_removed"] = [
                name for name, value in prior_tests.items() if curr_tests.get(name) != value
            ]
            added = {name: value for name, value in curr_tests.items() if name not in prior_tests}
            row["added_test_count"] = len(added)
            row["added_tests_exact_donor"] = [
                name for name, value in added.items() if orig_tests.get(name) == value
            ]
            row["added_tests_different_or_not_in_donor_module"] = [
                name for name, value in added.items() if orig_tests.get(name) != value
            ]
    rows.append(row)

result = {
    "candidate": candidate,
    "candidate_tree": git("rev-parse", f"{candidate}^{{tree}}").decode().strip(),
    "comparison_base": base,
    "donor": donor,
    "changed_file_count": len(paths),
    "rows": rows,
}
Path(destination).write_text(json.dumps(result, indent=2) + "\n")
print(json.dumps({key: value for key, value in result.items() if key != "rows"}))
print(json.dumps({
    "original_test_changes": {
        row["path"]: row["baseline_test_oracles_changed_or_removed"]
        for row in rows if row.get("baseline_test_oracles_changed_or_removed")
    },
    "nonidentical_added_test_oracles": {
        row["path"]: row["added_tests_different_or_not_in_donor_module"]
        for row in rows if row.get("added_tests_different_or_not_in_donor_module")
    },
    "total_added_test_functions": sum(row.get("added_test_count", 0) for row in rows),
}, indent=2))

#!/usr/bin/env python3
"""Collect per-file ownership facts for the kodezart recovery split."""
import ast, json, subprocess, sys

REPO = "/Users/kodezart/Projects/kodezart"
BASE = "4661a24b599d75503a997f3ce122f3ad2da77048"
DONOR = "eae9a940e9682a09680b4adf49781f3743c12754"
MILESTONES = [("M1", "ddb8cdef33f90f30aecc4343e36c2293bdf39a27"),
              ("M4", "c855fb061209a7f20a3d10cd06f13f8476fba7dc"),
              ("M2", "cef0dff606500a76d5eb1eb9104a4862f9dbdd35"),
              ("M3", "10c51a7441b17163d8479acb4a31c8bf2833ff58")]
OUT = "/private/tmp/claude-503/-Users-kodezart-Projects-kodezart/f325c30b-eb65-4b5e-83ca-1ef5846ade2d/scratchpad/ownership_facts.json"


def git(*args, check=True):
    r = subprocess.run(["git", "-C", REPO, *args], capture_output=True, text=True)
    if check and r.returncode != 0:
        raise RuntimeError(" ".join(args) + ": " + r.stderr)
    return r


def kind_of(path):
    if path.startswith("tests/") and path.endswith(".py"):
        return "test_py"
    if path.endswith(".py") and (path.startswith("src/") or "/prompts/" not in path and path.startswith("src")):
        return "src_py"
    if path.endswith(".py"):
        return "other_py"
    if "prompt" in path and path.endswith((".md", ".txt", ".j2", ".jinja", ".jinja2")):
        return "prompt"
    if path.endswith((".md", ".rst", ".txt")):
        return "doc"
    if path.endswith((".toml", ".yaml", ".yml", ".json", ".cfg", ".ini", ".lock")):
        return "config"
    return "other"


def module_of(path):
    """src/kodezart/a/b.py -> kodezart.a.b ; __init__.py -> package."""
    if not path.startswith("src/") or not path.endswith(".py"):
        return None
    m = path[len("src/"):-len(".py")]
    if m.endswith("/__init__"):
        m = m[: -len("/__init__")]
    return m.replace("/", ".")


def imports_of(src, path):
    pkg = module_of(path)
    if pkg is None:
        pkg = ""
    # for relative resolution: package containing the module
    if path.endswith("/__init__.py"):
        parent = pkg
    else:
        parent = pkg.rsplit(".", 1)[0] if "." in pkg else pkg
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return []
    found = []
    for node in tree.body:  # module level only
        if isinstance(node, ast.Import):
            for a in node.names:
                if a.name.startswith("kodezart"):
                    found.append(a.name)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                base = parent.split(".")
                up = node.level - 1
                base = base[: len(base) - up] if up else base
                mod = ".".join(base + ([node.module] if node.module else []))
            else:
                mod = node.module or ""
            if mod.startswith("kodezart"):
                found.append(mod)
    return sorted(set(found))


def main():
    ns = git("diff", "--name-status", "-M", BASE, DONOR).stdout.splitlines()
    num = git("diff", "--numstat", "-M", BASE, DONOR).stdout.splitlines()
    stats = {}
    for line in num:
        if not line.strip():
            continue
        a, d, rest = line.split("\t", 2)
        p = rest.split("\t")[-1]
        stats[p] = (None if a == "-" else int(a), None if d == "-" else int(d))

    facts = []
    for line in ns:
        if not line.strip():
            continue
        parts = line.split("\t")
        status = parts[0]
        path = parts[-1]
        old = parts[1] if status.startswith("R") and len(parts) == 3 else None
        first_seen = "donor-only"
        for name, sha in MILESTONES:
            if git("cat-file", "-e", f"{sha}:{path}", check=False).returncode == 0:
                first_seen = name
                break
        kind = kind_of(path)
        imports = []
        if kind in ("src_py", "test_py", "other_py") and status != "D":
            r = git("show", f"{DONOR}:{path}", check=False)
            if r.returncode == 0:
                imports = imports_of(r.stdout, path)
        a, d = stats.get(path, (None, None))
        rec = {"path": path, "status": status, "added": a, "deleted": d,
               "kind": kind, "first_seen": first_seen, "imports": imports}
        if old:
            rec["old_path"] = old
        facts.append(rec)

    with open(OUT, "w") as f:
        json.dump(facts, f, indent=1)
    from collections import Counter
    c = Counter(x["kind"] for x in facts)
    print("total", len(facts), dict(c))
    print("first_seen", dict(Counter(x["first_seen"] for x in facts)))


if __name__ == "__main__":
    main()

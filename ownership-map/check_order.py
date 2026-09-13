#!/usr/bin/env python3
"""Check milestone import-order legality.

usage: check_order.py <ownership_map.json> [ownership_facts.json]
ownership_map.json: {path: owner}
"""
import json, sys, os

SD = os.path.dirname(os.path.abspath(__file__))
RANK = {"M1": 1, "M4": 2, "M3": 3, "M2": 4, "M5": 4, "M6": 5, "M7": 5}
FORBIDDEN = {("M2", "M5"), ("M5", "M2"), ("M6", "M7"), ("M7", "M6"),
             ("M6", "M2"), ("M7", "M2")}


def allowed(owner, dep):
    """May a file owned by `owner` import a file owned by `dep`?"""
    if dep == owner:
        return True
    if dep == "BASE":
        return True
    if (owner, dep) in FORBIDDEN:
        return False
    if owner not in RANK or dep not in RANK:
        return False
    return RANK[dep] < RANK[owner]


def module_paths(mod):
    p = "src/" + mod.replace(".", "/")
    return [p + ".py", p + "/__init__.py"]


def main():
    mp = sys.argv[1]
    fp = sys.argv[2] if len(sys.argv) > 2 else os.path.join(SD, "ownership_facts.json")
    owners = json.load(open(mp))
    facts = json.load(open(fp))
    delta = {f["path"] for f in facts}
    violations = []
    for f in facts:
        owner = owners.get(f["path"])
        if owner is None:
            continue
        for mod in f.get("imports", []):
            cands = [c for c in module_paths(mod) if c in delta]
            if not cands:
                continue  # unchanged since main -> rank 0, always allowed
            for c in cands:
                dep = owners.get(c, "BASE")
                if not allowed(owner, dep):
                    violations.append(f"{f['path']}({owner}) imports {mod}({dep}) [{c}]")
    for v in sorted(set(violations)):
        print(v)
    print(f"-- {len(set(violations))} violation(s); {len(owners)} mapped paths; {len(facts)} delta files")


if __name__ == "__main__":
    main()

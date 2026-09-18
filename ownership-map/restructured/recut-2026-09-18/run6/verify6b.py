#!/usr/bin/env python3.12
"""Kind-aware off-path member check + broken-import check + alias symbol coverage in osym."""
import ast
import json
import subprocess
from collections import Counter

REPO = "/Users/kodezart/Projects/kodezart"
S = "/private/tmp/claude-503/-Users-kodezart-Projects-kodezart/f2770f28-277d-4202-b936-349664afe907/scratchpad/recut/"
DONOR = "9d425ec4b2697fa8032e1f92287fb03b82d8603a"
BASE = "e1544ed749b994b2863b16b6318c61863fa4aa3e"
VIEWS = [
    ("M1", "4518c36328078972b320648f317c35eede0161fe"),
    ("M4", "ac85209e62a96a55c3af4b202ffc68b6493d0ed2"),
    ("M3", "4a5146e71388f3351bbf0ea6c782c54e0e45b153"),
    ("M2", "bf0c7de30bcda5f2183c9cf7f29a56cc32450be2"),
    ("M5", "91ce3b78a21a40b9b2fc079f0459b34e7ebda722"),
    ("M6", "3a23e284023e31b72a5b6e7f21161252ba6d297d"),
    ("M7", "e5b1493e21503ecebaed85f0f762218df1a2eecc"),
]
PATHS = {
    "M1": ["M1"], "M4": ["M1", "M4"], "M3": ["M1", "M4", "M3"], "M2": ["M1", "M4", "M3", "M2"],
    "M5": ["M1", "M4", "M3", "M5"], "M6": ["M1", "M4", "M3", "M5", "M6"], "M7": ["M1", "M4", "M3", "M5", "M7"],
}


def git(*args, binary=False):
    r = subprocess.run(["git", "-C", REPO, *args], capture_output=True, check=True)
    return r.stdout if binary else r.stdout.decode()


def tree(sha):
    out = {}
    for line in git("ls-tree", "-r", sha).splitlines():
        meta, path = line.split("\t", 1)
        out[path] = meta.split()[2]
    return out


_bc = {}


def blob(sha):
    if sha not in _bc:
        _bc[sha] = git("cat-file", "-p", sha, binary=True)
    return _bc[sha]


omap = json.load(open(S + "ownership_map.r.json"))
osym = json.load(open(S + "ownership_symbols.r.json"))
split_files = {f for f, v in omap.items() if v.get("split") is not None}
donor_tree, base_tree = tree(DONOR), tree(BASE)
trees = {m: tree(h) for m, h in VIEWS}


def defs_with_src(src_bytes):
    """name -> source segment for top-level and class-level definitions."""
    src = src_bytes.decode()
    mod = ast.parse(src)
    out = {}

    def names_of(node):
        if isinstance(node, ast.Assign):
            for t in node.targets:
                for n in ast.walk(t):
                    if isinstance(n, ast.Name):
                        yield n.id
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            yield node.target.id

    for node in mod.body:
        seg = ast.get_source_segment(src, node)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            out[node.name] = seg
            if isinstance(node, ast.ClassDef):
                for sub in node.body:
                    sseg = ast.get_source_segment(src, sub)
                    if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                        out[f"{node.name}.{sub.name}"] = sseg
                    else:
                        for n in names_of(sub):
                            out[f"{node.name}.{n}"] = sseg
        elif isinstance(node, ast.TypeAlias):
            out[node.name.id] = seg
        else:
            for n in names_of(node):
                out[n] = seg
    return out


def norm(seg):
    # compare by AST dump so comment/whitespace drift does not count
    try:
        return ast.dump(ast.parse(seg))
    except SyntaxError:
        return seg


offpath_added = []      # kind=added, owner off path, present in view  -> must be absent
offpath_modified = []   # kind=modified, owner off path, view text != base text (donor text leaked)
onpath_removed = []     # kind=removed, owner on path, still present (informational)
unknown = []
kind_counter = Counter()
for m, h in VIEWS:
    on_path = set(PATHS[m])
    t = trees[m]
    for f in sorted(split_files):
        if not f.endswith(".py") or f not in t:
            continue
        vd = defs_with_src(blob(t[f]))
        bd = defs_with_src(blob(base_tree[f])) if f in base_tree else {}
        dd = defs_with_src(blob(donor_tree[f])) if f in donor_tree else {}
        syms = osym.get(f, {})
        for name, seg in vd.items():
            e = syms.get(name)
            if e is None:
                unknown.append(f"{m}:{f}:{name}")
                continue
            owner, kind = e["owner"], e["kind"]
            kind_counter[(m, kind, owner in on_path)] += 1
            if owner in on_path:
                continue
            if kind == "added":
                offpath_added.append(f"{m}:{f}:{name} owner={owner}")
            elif kind == "modified":
                if name in bd and norm(seg) != norm(bd[name]):
                    tag = "==donor" if name in dd and norm(seg) == norm(dd[name]) else "!=donor,!=base"
                    offpath_modified.append(f"{m}:{f}:{name} owner={owner} {tag}")
        for name, e in syms.items():
            if e["kind"] == "removed" and e["owner"] in on_path and name in vd and not name.startswith("import:"):
                onpath_removed.append(f"{m}:{f}:{name} owner={e['owner']}")

# ---- broken imports: top-level (non TYPE_CHECKING) from-imports of kodezart/tests modules ----

def module_path(modname, t):
    rel = modname.replace(".", "/")
    for c in (f"src/{rel}.py", f"src/{rel}/__init__.py", f"{rel}.py", f"{rel}/__init__.py"):
        if c in t:
            return c
    return None


def module_defs(src_bytes):
    mod = ast.parse(src_bytes)
    d = set()
    for node in mod.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            d.add(node.name)
        elif isinstance(node, ast.TypeAlias):
            d.add(node.name.id)
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            tg = node.targets if isinstance(node, ast.Assign) else [node.target]
            for x in tg:
                for n in ast.walk(x):
                    if isinstance(n, ast.Name):
                        d.add(n.id)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            for a in node.names:
                d.add(a.asname or a.name.split(".")[0])
        elif isinstance(node, ast.If):  # TYPE_CHECKING blocks may define names too
            for sub in node.body:
                if isinstance(sub, (ast.Import, ast.ImportFrom)):
                    for a in sub.names:
                        d.add(a.asname or a.name.split(".")[0])
                elif isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    d.add(sub.name)
    return d


broken = {m: [] for m, _ in VIEWS}
for m, h in VIEWS:
    t = trees[m]
    for f, b in t.items():
        if not f.endswith(".py"):
            continue
        mod = ast.parse(blob(b))
        pkg_parts = f.removeprefix("src/").rsplit("/", 1)[0].split("/")
        for node in mod.body:  # top-level statements only (runtime imports)
            stmts = [node]
            if isinstance(node, ast.Try):
                stmts = node.body
            for st in stmts:
                if isinstance(st, ast.ImportFrom):
                    modname = st.module or ""
                    if st.level:
                        parts = pkg_parts[: len(pkg_parts) - (st.level - 1)]
                        modname = ".".join(parts + ([modname] if modname else []))
                    if modname.split(".")[0] not in ("kodezart", "tests"):
                        continue
                    target = module_path(modname, t)
                    if target is None:
                        broken[m].append(f"{f}: module {modname} missing")
                        continue
                    if target.endswith("__init__.py"):
                        continue  # package: names may be submodules
                    tdefs = module_defs(blob(t[target]))
                    for a in st.names:
                        if a.name != "*" and a.name not in tdefs:
                            broken[m].append(f"{f}: {a.name} not defined in {modname}")
                elif isinstance(st, ast.Import):
                    for a in st.names:
                        if a.name.split(".")[0] == "kodezart" and module_path(a.name, t) is None:
                            broken[m].append(f"{f}: module {a.name} missing")

# alias names modelled in osym?
alias_in_osym = Counter()
for f, syms in osym.items():
    for k, v in syms.items():
        alias_in_osym[v.get("sym_kind")] += 0
for m, h in VIEWS:
    pass
aliases_donor = {}
for f, b in donor_tree.items():
    if f.endswith(".py"):
        mod = ast.parse(blob(b))
        for n in mod.body:
            if isinstance(n, ast.TypeAlias):
                aliases_donor[(f, n.name.id)] = f in osym and n.name.id in osym[f]

res = {
    "offpath_added": len(offpath_added), "offpath_added_list": offpath_added[:40],
    "offpath_modified_leaks": len(offpath_modified), "offpath_modified_list": offpath_modified[:40],
    "onpath_removed_still_present": len(onpath_removed), "onpath_removed_list": onpath_removed[:20],
    "unknown_symbols": len(unknown), "unknown_list": unknown[:20],
    "broken_imports": {m: len(v) for m, v in broken.items()},
    "broken_import_lists": {m: v[:60] for m, v in broken.items()},
    "aliases_in_donor": {f"{f}:{n}": ("in-osym" if ok else ("split-file-not-in-osym" if f in osym else "whole-file")) for (f, n), ok in aliases_donor.items()},
}
json.dump(res, open(S + "../verify6b_result.json", "w"), indent=1)
for k, v in res.items():
    print(k, "=", json.dumps(v, indent=1) if isinstance(v, (dict, list)) else v)

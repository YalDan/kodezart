#!/usr/bin/env python3.12
"""Independent refutation of the recut6 views. Uses git plumbing only."""
import ast
import json
import subprocess
import sys
import tomllib
from collections import defaultdict

REPO = "/Users/kodezart/Projects/kodezart"
S = "/private/tmp/claude-503/-Users-kodezart-Projects-kodezart/f2770f28-277d-4202-b936-349664afe907/scratchpad/recut/"
DONOR = "9d425ec4b2697fa8032e1f92287fb03b82d8603a"
BASE = "e1544ed749b994b2863b16b6318c61863fa4aa3e"
VIEWS = [  # (milestone, parent, head)
    ("M1", "e1544ed749b994b2863b16b6318c61863fa4aa3e", "4518c36328078972b320648f317c35eede0161fe"),
    ("M4", "4518c36328078972b320648f317c35eede0161fe", "ac85209e62a96a55c3af4b202ffc68b6493d0ed2"),
    ("M3", "ac85209e62a96a55c3af4b202ffc68b6493d0ed2", "4a5146e71388f3351bbf0ea6c782c54e0e45b153"),
    ("M2", "4a5146e71388f3351bbf0ea6c782c54e0e45b153", "bf0c7de30bcda5f2183c9cf7f29a56cc32450be2"),
    ("M5", "4a5146e71388f3351bbf0ea6c782c54e0e45b153", "91ce3b78a21a40b9b2fc079f0459b34e7ebda722"),
    ("M6", "91ce3b78a21a40b9b2fc079f0459b34e7ebda722", "3a23e284023e31b72a5b6e7f21161252ba6d297d"),
    ("M7", "91ce3b78a21a40b9b2fc079f0459b34e7ebda722", "e5b1493e21503ecebaed85f0f762218df1a2eecc"),
]
PATHS = {
    "M1": ["M1"], "M4": ["M1", "M4"], "M3": ["M1", "M4", "M3"], "M2": ["M1", "M4", "M3", "M2"],
    "M5": ["M1", "M4", "M3", "M5"], "M6": ["M1", "M4", "M3", "M5", "M6"], "M7": ["M1", "M4", "M3", "M5", "M7"],
}


def git(*args, binary=False):
    r = subprocess.run(["git", "-C", REPO, *args], capture_output=True, check=True)
    return r.stdout if binary else r.stdout.decode()


def tree(sha):
    """path -> blob sha"""
    out = {}
    for line in git("ls-tree", "-r", sha).splitlines():
        meta, path = line.split("\t", 1)
        mode, typ, blob = meta.split()
        out[path] = blob
    return out


_blob_cache = {}


def blob(sha):
    if sha not in _blob_cache:
        _blob_cache[sha] = git("cat-file", "-p", sha, binary=True)
    return _blob_cache[sha]


omap = json.load(open(S + "ownership_map.r.json"))
osym = json.load(open(S + "ownership_symbols.r.json"))
split_files = {f for f, v in omap.items() if v.get("split") is not None}
whole_files = {f: v["owner"] for f, v in omap.items() if v.get("split") is None}

donor_tree = tree(DONOR)
base_tree = tree(BASE)
trees = {m: tree(h) for m, _, h in VIEWS}
parent_trees = {m: tree(p) for m, p, _ in VIEWS}

# Sanity: verify each head's first parent is the claimed parent sha
for m, p, h in VIEWS:
    actual = git("rev-parse", h + "^").strip()
    parents = git("log", "-1", "--format=%P", h).strip().split()
    assert parents == [p], (m, parents, p)

result = {"per_view": []}
donor_mismatch_list = []
ownership_violation_list = []
offpath_list = []
parse_fail_list = []
unknown_symbol_list = []
alias_gap_list = []
own_model_fail_list = []

# ---- symbol collection ------------------------------------------------------

def defs_of(src_bytes):
    """Return (top_level_names, member_names 'Class.attr') of a python source; raises SyntaxError."""
    tree_ = ast.parse(src_bytes)
    top, members = [], []

    def targets(node):
        if isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name):
                    yield t.id
                elif isinstance(t, ast.Tuple):
                    for e in t.elts:
                        if isinstance(e, ast.Name):
                            yield e.id
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            yield node.target.id

    for node in tree_.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            top.append(node.name)
            if isinstance(node, ast.ClassDef):
                for sub in node.body:
                    if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                        members.append(f"{node.name}.{sub.name}")
                    else:
                        for n in targets(sub):
                            members.append(f"{node.name}.{n}")
        elif isinstance(node, ast.TypeAlias):
            top.append(node.name.id)
        else:
            top.extend(targets(node))
    return top, members


def sym_owner(path, name):
    e = osym.get(path, {}).get(name)
    return e["owner"] if e else None


# ---- per view checks -------------------------------------------------------
for m, p, h in VIEWS:
    on_path = set(PATHS[m])
    t = trees[m]
    pt = parent_trees[m]
    changed = sorted(set(git("diff", "--name-only", p, h).splitlines()))
    view = {"milestone": m, "head_sha": h, "files_changed_vs_parent": len(changed),
            "checked_whole_files": 0, "mismatches": []}

    # 1. donor blob equality for whole files owned on path
    for f, owner in sorted(whole_files.items()):
        if owner not in on_path:
            continue
        view["checked_whole_files"] += 1
        vb, db = t.get(f), donor_tree.get(f)
        if vb != db:
            msg = f"{m}:{f} view={vb and vb[:8]} donor={db and db[:8]}"
            view["mismatches"].append(msg)
            donor_mismatch_list.append(msg)

    # 2. ownership of every changed file
    for f in changed:
        entry = omap.get(f)
        if entry is None:
            ownership_violation_list.append(f"{m}:{f} not in ownership map")
            continue
        if entry["owner"] in on_path:
            continue
        if entry.get("split") is not None:
            sym_owners = {v["owner"] for v in osym.get(f, {}).values()}
            split_owners = set(entry["split"].get("symbols_by_milestone", {}))
            if (sym_owners | split_owners) & on_path:
                continue
        ownership_violation_list.append(f"{m}:{f} owner={entry['owner']} split={entry.get('split') is not None}")

    # 3. off-path members in split python files + parse failures over all py files
    for f, b in t.items():
        if not f.endswith(".py"):
            continue
        try:
            top, members = defs_of(blob(b))
        except SyntaxError as exc:
            parse_fail_list.append(f"{m}:{f}: {exc}")
            continue
        if f not in split_files:
            continue
        for name in top + members:
            owner = sym_owner(f, name)
            if owner is None:
                unknown_symbol_list.append(f"{m}:{f}:{name}")
            elif owner not in on_path:
                offpath_list.append(f"{m}:{f}:{name} owner={owner}")

    result["per_view"].append(view)

# ---- 4. own-model oracle -----------------------------------------------------

def is_required(node):
    if node.value is None:
        return True
    v = node.value
    if isinstance(v, ast.Constant) and v.value is Ellipsis:
        return True
    if isinstance(v, ast.Call) and isinstance(v.func, ast.Name) and v.func.id == "Field":
        if v.args:
            a = v.args[0]
            return isinstance(a, ast.Constant) and a.value is Ellipsis
        kws = {k.arg for k in v.keywords}
        return not ({"default", "default_factory"} & kws)
    return False


own_model_detail = {}
for m, p, h in VIEWS:
    t = trees[m]
    toml_doc = tomllib.loads(blob(t["docs/operation.example.toml"]).decode())
    src = blob(t["src/kodezart/types/domain/operation.py"])
    mod = ast.parse(src)
    fields, required = [], []
    for node in mod.body:
        if isinstance(node, ast.ClassDef) and node.name == "OperationConfig":
            bases = [ast.unparse(b) for b in node.bases]
            for sub in node.body:
                if isinstance(sub, ast.AnnAssign) and isinstance(sub.target, ast.Name):
                    fields.append(sub.target.id)
                    if is_required(sub):
                        required.append(sub.target.id)
    keys = set(toml_doc)
    extra = sorted(keys - set(fields))
    missing_required = sorted(set(required) - keys)
    own_model_detail[m] = {"keys": len(keys), "fields": len(fields), "required": required,
                           "extra_keys": extra, "missing_required": missing_required, "bases": bases}
    for k in extra:
        own_model_fail_list.append(f"{m}: toml key {k} has no OperationConfig field")
    for k in missing_required:
        own_model_fail_list.append(f"{m}: required field {k} has no toml key")

# ---- 5. alias gaps ------------------------------------------------------------

def alias_defs(tree_map):
    """module path -> set of PEP 695 alias names"""
    out = {}
    for f, b in tree_map.items():
        if f.endswith(".py"):
            try:
                mod = ast.parse(blob(b))
            except SyntaxError:
                continue
            names = {n.name.id for n in mod.body if isinstance(n, ast.TypeAlias)}
            if names:
                out[f] = names
    return out


universe = set()
for tm in [donor_tree, base_tree, *trees.values()]:
    for names in alias_defs(tm).values():
        universe |= names


def module_to_path(modname, tree_map):
    rel = modname.replace(".", "/")
    for cand in (f"src/{rel}.py", f"src/{rel}/__init__.py", f"{rel}.py", f"{rel}/__init__.py"):
        if cand in tree_map:
            return cand
    return None


alias_detail = {}
for m, p, h in VIEWS:
    t = trees[m]
    defined_by_file = alias_defs(t)
    gaps = []
    for f, b in t.items():
        if not f.endswith(".py"):
            continue
        try:
            mod = ast.parse(blob(b))
        except SyntaxError:
            continue
        local_defs = set()
        imported = {}  # name -> (module, original)
        for node in ast.walk(mod):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                local_defs.add(node.name)
            elif isinstance(node, ast.TypeAlias):
                local_defs.add(node.name.id)
            elif isinstance(node, ast.Assign):
                for tg in node.targets:
                    for n in ast.walk(tg):
                        if isinstance(n, ast.Name):
                            local_defs.add(n.id)
            elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                local_defs.add(node.target.id)
            elif isinstance(node, ast.ImportFrom):
                for a in node.names:
                    imported[a.asname or a.name] = (node.module, a.name, node.level)
            elif isinstance(node, ast.arg):
                local_defs.add(node.arg)
            elif isinstance(node, (ast.For, ast.comprehension)):
                for n in ast.walk(node.target):
                    if isinstance(n, ast.Name):
                        local_defs.add(n.id)
        refs = {n.id for n in ast.walk(mod) if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)}
        # also string annotations referencing alias names
        for n in ast.walk(mod):
            if isinstance(n, ast.Constant) and isinstance(n.value, str) and n.value in universe:
                refs.add(n.value)
        for name in refs & universe:
            if name in local_defs:
                continue
            if name in imported:
                modname, orig, level = imported[name]
                if level:
                    # relative import: resolve against file's package
                    pkg = f.rsplit("/", 1)[0].removeprefix("src/").replace("/", ".")
                    parts = pkg.split(".")
                    if level > 1:
                        parts = parts[: len(parts) - (level - 1)]
                    modname = ".".join(parts + ([modname] if modname else []))
                target = module_to_path(modname, t)
                if target is None:
                    if modname and modname.split(".")[0] in ("kodezart", "tests"):
                        gaps.append(f"{m}:{f} imports {orig} from missing module {modname}")
                    continue
                try:
                    tmod = ast.parse(blob(t[target]))
                except SyntaxError:
                    continue
                tdefs = set()
                for node in tmod.body:
                    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                        tdefs.add(node.name)
                    elif isinstance(node, ast.TypeAlias):
                        tdefs.add(node.name.id)
                    elif isinstance(node, ast.Assign):
                        for tg in node.targets:
                            for n in ast.walk(tg):
                                if isinstance(n, ast.Name):
                                    tdefs.add(n.id)
                    elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                        tdefs.add(node.target.id)
                    elif isinstance(node, (ast.ImportFrom, ast.Import)):
                        for a in node.names:
                            tdefs.add(a.asname or a.name.split(".")[0])
                if orig not in tdefs:
                    gaps.append(f"{m}:{f} imports {orig} from {target} which does not define it")
                continue
            gaps.append(f"{m}:{f} references {name} (alias universe) with no local def or import")
    alias_detail[m] = gaps
    alias_gap_list.extend(gaps)

# ---- 6. coverage --------------------------------------------------------------
changed_vs_base = set(git("diff", "--name-only", BASE, DONOR).splitlines())
unmapped_changed = sorted(changed_vs_base - set(omap))
unmapped_literal = sorted(set(donor_tree) - set(omap))
map_not_changed = sorted(set(omap) - changed_vs_base)
whole_missing = []
for f, owner in whole_files.items():
    if f not in donor_tree:
        # deleted in donor: should be absent at owner head
        if f in trees[owner]:
            whole_missing.append(f"{owner}:{f} deleted in donor but present at head")
        continue
    if f not in trees[owner]:
        whole_missing.append(f"{owner}:{f} absent at owner head")

# extra invariant: files not in map must equal base blob at every head
stray = []
for m, _, h in VIEWS:
    for f, b in trees[m].items():
        if f not in omap and base_tree.get(f) != b:
            stray.append(f"{m}:{f}")
    for f in base_tree:
        if f not in omap and f not in trees[m]:
            stray.append(f"{m}:{f} (unmapped file missing vs base)")

summary = {
    "donor_mismatches": len(donor_mismatch_list), "donor_mismatch_list": donor_mismatch_list[:20],
    "ownership_violations": len(ownership_violation_list), "ownership_violation_list": ownership_violation_list[:20],
    "off_path_members_present": len(offpath_list), "offpath_list": offpath_list[:20],
    "unknown_symbols_in_split_files": len(unknown_symbol_list), "unknown_symbol_list": unknown_symbol_list[:20],
    "parse_failures": len(parse_fail_list), "parse_fail_list": parse_fail_list[:20],
    "own_model_failures": len(own_model_fail_list), "own_model_fail_list": own_model_fail_list,
    "own_model_detail": own_model_detail,
    "alias_universe": sorted(universe),
    "alias_gaps": len(alias_gap_list), "alias_gap_list": alias_gap_list[:20],
    "unmapped_paths_changed_vs_base": len(unmapped_changed), "unmapped_changed": unmapped_changed[:20],
    "unmapped_paths_literal_whole_tree": len(unmapped_literal),
    "map_entries_not_changed_vs_base": len(map_not_changed), "map_not_changed": map_not_changed[:20],
    "whole_missing": len(whole_missing), "whole_missing_list": whole_missing[:20],
    "stray_unmapped_edits": len(stray), "stray_list": stray[:20],
    "per_view": result["per_view"],
}
json.dump(summary, open(S + "../verify6_result.json", "w"), indent=1)
for k, v in summary.items():
    if k in ("per_view", "own_model_detail", "alias_universe"):
        continue
    print(k, "=", v if not isinstance(v, list) else v)
for v in summary["per_view"]:
    print(v["milestone"], v["head_sha"][:8], "changed", v["files_changed_vs_parent"], "whole checked", v["checked_whole_files"], "mismatches", len(v["mismatches"]))
print("own_model_detail", json.dumps(own_model_detail, indent=1))
print("alias_universe", sorted(universe))

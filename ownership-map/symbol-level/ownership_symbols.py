#!/usr/bin/env python3.12
"""Symbol-level ownership for the milestone cut.

Legitimate owner of a symbol = LCA of the owners of its consumers.
Inputs : ownership_map.json (file-level owner/reason/split)
Outputs: ownership_symbols.json, cut_specs.json, m1_adaptation_sites.json
"""
from __future__ import annotations
import ast, io, json, os, re, sys, tomllib
from collections import Counter, defaultdict

S = "/private/tmp/claude-503/-Users-kodezart-Projects-kodezart/f325c30b-eb65-4b5e-83ca-1ef5846ade2d/scratchpad/"
MAIN_TREE = S + "tree_main/"
DONOR_TREE = S + "tree_donor/"
MAIN_SHA = "4661a24b599d75503a997f3ce122f3ad2da77048"
DONOR_SHA = "eae9a940e9682a09680b4adf49781f3743c12754"

MPATHS = {
    "M1": ["M1"],
    "M4": ["M1", "M4"],
    "M3": ["M1", "M4", "M3"],
    "M2": ["M1", "M4", "M3", "M2"],
    "M5": ["M1", "M4", "M3", "M5"],
    "M6": ["M1", "M4", "M3", "M5", "M6"],
    "M7": ["M1", "M4", "M3", "M5", "M7"],
}
CUT_ORDER = ["M1", "M4", "M3", "M2", "M5", "M6", "M7"]


def lca(owners):
    owners = [o for o in owners if o in MPATHS]
    if not owners:
        return None
    paths = [MPATHS[o] for o in owners]
    pref = paths[0]
    for p in paths[1:]:
        n = 0
        while n < min(len(pref), len(p)) and pref[n] == p[n]:
            n += 1
        pref = pref[:n]
    return pref[-1] if pref else None


def on_one_chain(owners):
    """True if the owners' paths form a chain (one is a prefix of all others)."""
    owners = sorted(set(o for o in owners if o in MPATHS), key=lambda o: len(MPATHS[o]))
    if not owners:
        return None
    deepest = owners[-1]
    dp = MPATHS[deepest]
    for o in owners:
        if MPATHS[o] != dp[: len(MPATHS[o])]:
            return None
    return deepest


def divergent(a, b):
    pa, pb = MPATHS[a], MPATHS[b]
    return pa[: len(pb)] != pb and pb[: len(pa)] != pa


# ---------------------------------------------------------------- inputs
MAP = json.load(open(S + "ownership_map.json"))
delta = [l.split("\t") for l in open(S + "delta_namestatus.txt").read().splitlines() if l]
STATUS = {p[1]: p[0] for p in delta if len(p) > 1}
MODIFIED = [p[1] for p in delta if p[0] == "M"]

scope = set(k for k, v in MAP.items() if v.get("split"))
scope |= set(
    p for p in MODIFIED
    if p.endswith(".py") and (p.startswith("src/") or p.startswith("tests/"))
    and MAP.get(p, {}).get("owner") in ("M1", "M4", "M3")
)
SCOPE = sorted(scope)


def file_owner(path):
    e = MAP.get(path)
    return e["owner"] if e else None


# ---------------------------------------------------- word index (== git grep -w -l)
TOKEN = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def build_index(root):
    idx = defaultdict(set)
    texts = {}
    files = []
    for dirpath, _dirs, names in os.walk(root):
        for n in names:
            fp = os.path.join(dirpath, n)
            rel = os.path.relpath(fp, root)
            files.append(rel)
            try:
                txt = open(fp, "rb").read().decode("utf-8", "replace")
            except OSError:
                continue
            texts[rel] = txt
            for t in set(TOKEN.findall(txt)):
                idx[t].add(rel)
    return idx, files, texts


DONOR_IDX, DONOR_FILES, DONOR_TXT = build_index(DONOR_TREE)
MAIN_IDX, MAIN_FILES, MAIN_TXT = build_index(MAIN_TREE)


def blob(tree, path):
    fp = tree + path
    if not os.path.exists(fp):
        return None
    return open(fp, "rb").read().decode("utf-8", "replace")


def repo_blob(sha, path):
    import subprocess
    r = subprocess.run(["git", "show", f"{sha}:{path}"], cwd="/Users/kodezart/Projects/kodezart",
                       capture_output=True)
    if r.returncode != 0:
        return None
    return r.stdout.decode("utf-8", "replace")


def read_side(sha, tree, path):
    if path.startswith("src/") or path.startswith("tests/"):
        return blob(tree, path)
    return repo_blob(sha, path)


# ------------------------------------------------------------- symbol extraction
def seg(src_lines, node):
    start = node.lineno
    for d in getattr(node, "decorator_list", []) or []:
        start = min(start, d.lineno)
    end = getattr(node, "end_lineno", node.lineno)
    return "\n".join(src_lines[start - 1: end])


def fn_sig(node):
    return (ast.unparse(node.args) + " -> " +
            (ast.unparse(node.returns) if node.returns else "") + " @" +
            ",".join(ast.unparse(d) for d in node.decorator_list))


def cls_sig(node):
    bases = ",".join(ast.unparse(b) for b in node.bases)
    members = sorted(
        t.id if isinstance(t, ast.Name) else getattr(getattr(sub, "target", None), "id", "")
        for sub in node.body for t in (getattr(sub, "targets", []) or [getattr(sub, "target", None)])
        if isinstance(t, ast.Name))
    meth = sorted(sub.name for sub in node.body
                  if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)))
    return f"({bases})members={members}methods={meth}"


def assign_sig(node):
    ann = ast.unparse(node.annotation) if isinstance(node, ast.AnnAssign) and node.annotation else ""
    val = ast.unparse(node.value) if node.value else ""
    return ann + "=" + val


def py_symbols(src):
    """{symbol_key: {kind, body, name}} for one python source."""
    out = {}
    tree = ast.parse(src)
    lines = src.splitlines()
    for node in tree.body:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            body = seg(lines, node)
            names = []
            for a in node.names:
                names.append(a.asname or a.name.split(".")[0])
            key = "import:" + body.strip().replace("\n", " ")
            key = re.sub(r"\s+", " ", key)
            out[key] = {"kind_of": "import", "body": body, "names": names}
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            out[node.name] = {"kind_of": "function", "body": seg(lines, node), "names": [node.name], "sig": fn_sig(node)}
        elif isinstance(node, ast.ClassDef):
            out[node.name] = {"kind_of": "class", "body": seg(lines, node), "names": [node.name], "sig": cls_sig(node)}
            for sub in node.body:
                if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    out[f"{node.name}.{sub.name}"] = {
                        "kind_of": "method", "body": seg(lines, sub),
                        "names": [sub.name], "parent": node.name, "sig": fn_sig(sub)}
                elif isinstance(sub, ast.Assign):
                    for t in sub.targets:
                        if isinstance(t, ast.Name):
                            out[f"{node.name}.{t.id}"] = {
                                "kind_of": "class_attr", "body": seg(lines, sub),
                                "names": [t.id], "parent": node.name, "sig": assign_sig(sub)}
                elif isinstance(sub, ast.AnnAssign) and isinstance(sub.target, ast.Name):
                    out[f"{node.name}.{sub.target.id}"] = {
                        "kind_of": "class_attr", "body": seg(lines, sub),
                        "names": [sub.target.id], "parent": node.name, "sig": assign_sig(sub)}
        elif isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name):
                    out[t.id] = {"kind_of": "assignment", "body": seg(lines, node), "names": [t.id], "sig": assign_sig(node)}
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            out[node.target.id] = {"kind_of": "assignment", "body": seg(lines, node),
                                   "names": [node.target.id], "sig": assign_sig(node)}
    return out


def toml_symbols(src):
    data = tomllib.loads(src)
    out = {}

    def walk(d, prefix):
        for k, v in d.items():
            key = f"{prefix}{k}"
            if isinstance(v, dict):
                out["table:" + key] = {"kind_of": "toml_table", "body": json.dumps(v, sort_keys=True, default=str),
                                       "names": [k]}
                walk(v, key + ".")
            else:
                out[key] = {"kind_of": "toml_key", "body": json.dumps(v, sort_keys=True, default=str),
                            "names": [k]}
    walk(data, "")
    return out


def md_symbols(src):
    """H2/H3 sections: body runs to the next heading of the same or higher level."""
    out = {}
    lines = src.splitlines()
    heads = []
    fence = False
    for i, l in enumerate(lines):
        if l.strip().startswith("```"):
            fence = not fence
        if fence:
            continue
        m = re.match(r"^(#{1,6})\s+(.*)$", l)
        if m:
            heads.append((i, len(m.group(1)), m.group(2).strip()))
    for n, (i, lvl, title) in enumerate(heads):
        if lvl not in (2, 3):
            continue
        end = len(lines)
        for j, l2, _t in heads[n + 1:]:
            if l2 <= lvl:
                end = j
                break
        key = f"H{lvl}:{title}"
        out[key] = {"kind_of": f"md_h{lvl}", "body": "\n".join(lines[i:end]), "names": []}
    return out


def env_symbols(src):
    out = {}
    for l in src.splitlines():
        s = l.strip()
        if not s or s.startswith("#") or "=" not in s:
            continue
        k = s.split("=", 1)[0].strip()
        out[k] = {"kind_of": "env_key", "body": s, "names": [k]}
    return out


def extract(path, src):
    if src is None:
        return {}
    if path.endswith(".py"):
        return py_symbols(src)
    if path.endswith(".toml"):
        return toml_symbols(src)
    if path.endswith(".md"):
        return md_symbols(src)
    return env_symbols(src)


# ------------------------------------------------------------- consumers
GENERIC = set("""self cls args kwargs data value name path id type str int bool dict list set
tuple none true false test tests src main run get set new old max min key item items result
obj x y z i j n e error err msg text line file lines start end count total""".split())


def code_tokens(sym_key, info, idx):
    """Greppable identifier names for a symbol."""
    # a sub-symbol is grepped together with its class, so a common member name
    # (run, get, ...) is still specific enough; a bare top-level name is not.
    if info.get("parent"):
        names = [n for n in info.get("names", []) if n]
    else:
        names = [n for n in info.get("names", []) if n and n not in GENERIC]
    if names:
        return names, "name"
    # md/toml sections without an identifier name: use distinctive code tokens
    # that occur in the section body and exist in the donor tree.
    cand = []
    for t in set(TOKEN.findall(info["body"])):
        if len(t) < 4 or t.lower() in GENERIC:
            continue
        if "_" not in t and not (t[0].isupper() and any(c.islower() for c in t[1:])):
            continue
        hits = idx.get(t)
        if hits and len(hits) <= 10:
            cand.append(t)
    return sorted(cand), "body-tokens"


def modpath(path):
    mp = path
    if mp.startswith("src/"):
        mp = mp[4:]
    mp = mp[:-3] if mp.endswith(".py") else mp
    mp = mp.replace("/", ".")
    if mp.endswith(".__init__"):
        mp = mp[: -len(".__init__")]
    return mp


def qualified(c, defining_path, txt, idx):
    """Can consumer c actually see a symbol defined in defining_path?

    Reproduces Python's import reality so that low-specificity names
    (fixtures called `server`, `writer`, ...) do not collect the whole tree.
    """
    if not defining_path.endswith(".py"):
        return True
    dirn = os.path.dirname(defining_path)
    if os.path.basename(defining_path) == "conftest.py":
        return c.startswith(dirn + "/")            # pytest conftest visibility
    mp = modpath(defining_path)
    base = mp.rsplit(".", 1)[-1]
    pkg = mp.rsplit(".", 1)[0] if "." in mp else mp
    t = txt.get(c, "")
    if mp in t:
        return True
    if pkg in t and base in idx and c in idx[base]:
        return True
    if os.path.dirname(c) == dirn and re.search(
            rf"from\s+\.{re.escape(base)}\b|from\s+\.\s+import[^\n]*\b{re.escape(base)}\b", t):
        return True
    return False


def consumers_for(sym_key, info, defining_path, idx, txt):
    names, how = code_tokens(sym_key, info, idx)
    files = set()
    parent = info.get("parent")
    for n in names:
        hits = set(idx.get(n, ()))
        if parent:                      # Class.member -> require the class name too
            ph = set(idx.get(parent, ()))
            hits = (hits & ph) or hits
        files |= hits
    files.discard(defining_path)
    raw = sorted(files)
    qual = sorted(c for c in raw if qualified(c, defining_path, txt, idx))
    fell_back = False
    if raw and not qual:
        qual, fell_back = raw, True
    return qual, raw, names, how, fell_back


# ------------------------------------------------------------- main pass
symbols_out = {}
prop_violations = 0
removal_chain_exceptions = 0
unsplittable = []
parse_failures = []

for path in SCOPE:
    fowner = file_owner(path)
    main_src = read_side(MAIN_SHA, MAIN_TREE, path)
    donor_src = read_side(DONOR_SHA, DONOR_TREE, path)
    try:
        msyms = extract(path, main_src)
    except Exception as exc:
        parse_failures.append((path, "main", repr(exc)))
        msyms = {}
    try:
        dsyms = extract(path, donor_src)
    except Exception as exc:
        parse_failures.append((path, "donor", repr(exc)))
        dsyms = {}

    per_file = {}
    for key in sorted(set(msyms) | set(dsyms)):
        m, d = msyms.get(key), dsyms.get(key)
        if d and not m:
            kind = "added"
        elif m and not d:
            kind = "removed"
        elif m["body"] != d["body"]:
            kind = "modified"
        else:
            kind = "unchanged"
        info = d or m
        entry = {"kind": kind, "sym_kind": info["kind_of"]}
        if kind == "modified":
            entry["interface_change"] = bool(
                m.get("sig") is not None and d.get("sig") is not None and m["sig"] != d["sig"]
            ) or (m.get("sig") is None and d.get("sig") is None and info["kind_of"] != "import")

        if kind == "unchanged":
            entry["owner"] = fowner
            entry["consumers"] = {}
            entry["note"] = "unchanged between main and donor; stays as main has it"
            per_file[key] = entry
            continue

        idx = MAIN_IDX if kind == "removed" else DONOR_IDX
        txt = MAIN_TXT if kind == "removed" else DONOR_TXT
        cons, raw, names, how, fell_back = consumers_for(key, info, path, idx, txt)
        cmap = {c: file_owner(c) for c in cons if file_owner(c)}
        rawmap = {c: file_owner(c) for c in raw if file_owner(c)}
        entry["consumers_raw_count"] = len(raw)
        entry["owner_grep_only"] = lca(list(rawmap.values())) if rawmap else fowner
        unknown = [c for c in cons if not file_owner(c)]
        notes = [f"consumers via {how}={names}" if names else "no greppable identifier"]
        if len(raw) != len(cons):
            notes.append(f"{len(raw)-len(cons)} raw grep hit(s) dropped as not import-visible")
        if fell_back:
            notes.append("no import-qualified consumer; raw grep hits kept")
        if unknown:
            notes.append(f"{len(unknown)} consumer(s) not in file map, ignored")

        if info["kind_of"] == "import":
            # an import must exist as soon as the first in-file symbol using it lands
            users = [k2 for k2, i2 in dsyms.items()
                     if k2 != key and i2["kind_of"] != "import"
                     and any(re.search(rf"\b{re.escape(n)}\b", i2["body"]) for n in info["names"])]
            entry["in_file_users"] = sorted(users)
            cmap = {}
            notes = [f"import binding {info['names']}; owner = LCA of in-file donor symbols using it"]

        if kind == "removed":
            if cmap:
                deep = on_one_chain(list(cmap.values()))
                if deep:
                    owner = deep
                    notes.append("removal at deepest consumer (single chain)")
                else:
                    owner = "M7-final"
                    notes.append("consumers on divergent branches -> hygiene removal in the last cut")
            else:
                owner = fowner
                notes.append("no main-era consumer outside the defining file; removal at file owner")
        elif info["kind_of"] == "import":
            uowners = []
            for u in entry.get("in_file_users", []):
                ue = per_file.get(u)
                uowners.append(ue["owner"] if ue else None)
            uowners = [o for o in uowners if o]
            owner = lca(uowners) if uowners else fowner
            if not uowners:
                notes.append("no in-file user resolved; owner = file owner")
        else:
            owner = lca(list(cmap.values())) if cmap else fowner
            if not cmap:
                notes.append("no consumer outside the defining file; owner = file owner")

        entry["owner"] = owner
        entry["consumers"] = cmap
        entry["note"] = "; ".join(notes)

        # property: for a taken symbol, every consumer's path must contain the owner.
        # (Removals are deliberately placed at the DEEPEST consumer, so shallower
        #  consumers of a removed symbol are an expected exception, counted apart.)
        if cmap and owner in MPATHS:
            for c, co in cmap.items():
                if owner not in MPATHS.get(co, []):
                    if kind == "removed":
                        removal_chain_exceptions += 1
                        entry["removal_exception"] = True
                    else:
                        prop_violations += 1
                        entry["violation"] = True

        # unsplittable: modified symbol needed by milestones on divergent branches
        if kind == "modified" and cmap:
            os_ = sorted(set(cmap.values()))
            pairs = [(a, b) for i, a in enumerate(os_) for b in os_[i + 1:] if divergent(a, b)]
            if pairs:
                leaf = info["kind_of"] not in ("class", "toml_table", "md_h2", "md_h3")
                unsplittable.append({
                    "file": path, "symbol": key, "leaf": leaf,
                    "sym_kind": info["kind_of"], "owner": owner,
                    "consumer_owners": os_, "n_consumers": len(cmap),
                    "interface_change": entry.get("interface_change"),
                    "why": f"modified body is consumed by {os_} which sit on divergent branches "
                           f"({pairs[0][0]} vs {pairs[0][1]}); whole donor body lands at LCA {owner}, "
                           f"branch-specific hunks cannot be separated without an intra-symbol split"
                           + ("" if leaf else
                              " (container: its members carry their own owners, so the shell can "
                              "land at the LCA and the members can still be split)")})

        per_file[key] = entry

    # imports resolved after the rest of the file is known: second pass
    for key, entry in per_file.items():
        if entry.get("sym_kind") == "import" and entry["kind"] != "unchanged":
            uowners = sorted({per_file[u]["owner"] for u in entry.get("in_file_users", [])
                              if u in per_file and per_file[u]["owner"] in MPATHS})
            if uowners:
                entry["owner"] = lca(uowners)
                entry["owners_needed"] = uowners
                if len(uowners) > 1 and entry["owner"] not in uowners:
                    entry["derive_per_milestone"] = True
                    entry["note"] += ("; binding is needed only on divergent branches "
                                      f"{uowners}: materialise imports from the file body "
                                      "(ruff/autoflake) rather than copying by ownership")
        entry.pop("in_file_users", None)

    symbols_out[path] = per_file

json.dump(symbols_out, open(S + "ownership_symbols.json", "w"), indent=1, sort_keys=True)

# ------------------------------------------------------------- cut specs
cut = {}
for X in CUT_ORDER:
    pathset = MPATHS[X]
    earlier = pathset[:-1]
    per_m = {}
    for f, syms in symbols_out.items():
        take, rem, keep = [], [], []
        touched_earlier = False
        for k, e in syms.items():
            o = e["owner"]
            if e["kind"] in ("added", "modified") and o in pathset:
                take.append(k)
                if o in earlier:
                    touched_earlier = True
            elif e["kind"] == "removed" and o in pathset:
                rem.append(k)
                if o in earlier:
                    touched_earlier = True
            else:
                keep.append(k)
        if not take and not rem:
            continue
        per_m[f] = {
            "base": "previous" if touched_earlier else "main",
            "delta_status": STATUS.get(f, "?"),
            "creates_file": STATUS.get(f) == "A" and not touched_earlier,
            "file_map_owner": file_owner(f),
            "take_from_donor": sorted(take),
            "remove": sorted(rem),
            "keep_main": sorted(keep),
            "new_at_this_milestone": sorted(
                k for k in take + rem if syms[k]["owner"] == X),
        }
    cut[X] = per_m
json.dump(cut, open(S + "cut_specs.json", "w"), indent=1, sort_keys=True)

# ------------------------------------------------------------- M1 dry run
m1_take, m1_sites = [], []
for f, syms in symbols_out.items():
    for k, e in syms.items():
        if e["owner"] != "M1":
            continue
        if e["kind"] in ("added", "modified"):
            m1_take.append({"file": f, "symbol": k, "kind": e["kind"], "sym_kind": e["sym_kind"]})
        if e["kind"] in ("modified", "removed"):
            info_names = None
            src = read_side(MAIN_SHA, MAIN_TREE, f)
            try:
                ms = extract(f, src)
            except Exception:
                ms = {}
            if k in ms:
                cons, _raw, names, how, _fb = consumers_for(k, ms[k], f, MAIN_IDX, MAIN_TXT)
                for c in cons:
                    co = file_owner(c)
                    if co and co != "M1":
                        m1_sites.append({"symbol": k, "defining_file": f, "change": e["kind"],
                                         "consumer": c, "consumer_owner": co,
                                         "interface_change": e.get("interface_change", True)})
hard = [x for x in m1_sites if x["interface_change"]]
m1_out = {"n_sites": len(m1_sites), "n_interface_breaking_sites": len(hard),
          "n_consumer_files": len(set(x["consumer"] for x in m1_sites)),
          "n_interface_breaking_consumer_files": len(set(x["consumer"] for x in hard)),
          "take_from_donor": sorted(m1_take, key=lambda d: (d["file"], d["symbol"])),
          "adaptation_sites": sorted(m1_sites, key=lambda d: (d["consumer"], d["symbol"])),
          "distinct_consumer_files": sorted(set(s["consumer"] for s in m1_sites))}
json.dump(m1_out, open(S + "m1_adaptation_sites.json", "w"), indent=1)

# ------------------------------------------------------------- stats
all_syms = [(f, k, e) for f, s in symbols_out.items() for k, e in s.items()]
changed = [(f, k, e) for f, k, e in all_syms if e["kind"] != "unchanged"]
moved_off_m1 = sum(1 for f, k, e in changed
                   if file_owner(f) == "M1" and e["owner"] != "M1")
by_owner = Counter(e["owner"] for f, k, e in changed)
print("files_analysed", len(SCOPE))
print("symbols_total", len(all_syms))
print("symbols_changed", len(changed))
print("kinds", dict(Counter(e["kind"] for f, k, e in all_syms)))
print("counts_by_owner(changed)", dict(by_owner))
print("moved_off_m1", moved_off_m1)
print("owner_differs_from_grep_only",
      sum(1 for f, k, e in changed if e.get("owner_grep_only") and e["owner"] != e["owner_grep_only"]))
print("m1_under_grep_only_rule",
      sum(1 for f, k, e in changed if e.get("owner_grep_only") == "M1"))
assert prop_violations == 0, f"LCA property broken for {prop_violations} consumer edges"
print("property_violations", prop_violations)
print("removal_chain_exceptions", removal_chain_exceptions)
print("unsplittable", len(unsplittable),
      "leaf", sum(1 for x in unsplittable if x["leaf"]),
      "leaf_py_iface", sum(1 for x in unsplittable
                           if x["leaf"] and x["file"].endswith(".py") and x["interface_change"]))
print("m1_take", len(m1_take), "m1_sites", len(m1_sites),
      "m1_site_files", len(m1_out["distinct_consumer_files"]),
      "m1_hard_sites", m1_out["n_interface_breaking_sites"],
      "m1_hard_files", m1_out["n_interface_breaking_consumer_files"])
print("modified_with_interface_change",
      sum(1 for f, k, e in changed if e.get("interface_change")))
deriv = [(f, k) for f, k, e in changed if e.get("derive_per_milestone")]
print("imports_needing_per_milestone_derivation", len(deriv))
created_early = sorted({f for X in CUT_ORDER for f, spec in cut[X].items()
                        if spec["creates_file"] and
                        len(MPATHS[X]) < len(MPATHS[file_owner(f)])})
print("added_files_created_before_their_map_owner", len(created_early))
json.dump(created_early, open(S + "files_created_early.json", "w"), indent=1)
print("cut_spec_files_per_milestone", {X: len(cut[X]) for X in CUT_ORDER})
print("parse_failures", parse_failures)
json.dump(unsplittable, open(S + "unsplittable.json", "w"), indent=1)

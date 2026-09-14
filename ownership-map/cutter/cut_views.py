#!/usr/bin/env python3.12
"""Materialise one milestone's REVIEW VIEW on top of its parent branch.

  * whole files  : donor blob / deletion / rename, driven by ownership_map.json
  * shared files : member-wise splice of ONLY this milestone's symbols,
                   driven by cut_specs.json + ownership_symbols.json

MEMBER-WISE CONTAINERS (this revision):
  when a container symbol (python class, TOML table, markdown H2 section) is
  new_at_this_milestone for X but owns members whose owner is NOT on X's path,
  the container is materialised at X with ONLY its on-path members (shell,
  decorators and docstring kept); the off-path members are inserted later, at
  the milestone that owns them, at the donor position (or at the end of the
  container when the donor neighbours are absent).

Usage: cut_views.py <MILESTONE> <worktree-dir>
"""
from __future__ import annotations

import ast
import json
import os
import re
import subprocess
import sys

S = "/private/tmp/claude-503/-Users-kodezart-Projects-kodezart/f325c30b-eb65-4b5e-83ca-1ef5846ade2d/scratchpad/"
REPO = "/Users/kodezart/Projects/kodezart"
DONOR = "eae9a940e9682a09680b4adf49781f3743c12754"
MAIN = "4661a24b599d75503a997f3ce122f3ad2da77048"

MPATHS = {
    "M1": ["M1"],
    "M4": ["M1", "M4"],
    "M3": ["M1", "M4", "M3"],
    "M2": ["M1", "M4", "M3", "M2"],
    "M5": ["M1", "M4", "M3", "M5"],
    "M6": ["M1", "M4", "M3", "M5", "M6"],
    "M7": ["M1", "M4", "M3", "M5", "M7"],
}

MAP = json.load(open(S + "ownership_map.json"))
FACTS = {f["path"]: f for f in json.load(open(S + "ownership_facts_full.json"))}
SYMS = json.load(open(S + "ownership_symbols.json"))
SPECS = json.load(open(S + "cut_specs.json"))

SPLIT = {p for p, v in MAP.items() if v.get("split")}
LIVE = ("added", "modified")

# populated per file/milestone by run(); read by the render helpers
CTX = {"path": None, "mile": None, "restricted": [], "preserved": []}


def blob(sha: str, path: str):
    r = subprocess.run(["git", "-C", REPO, "show", f"{sha}:{path}"], capture_output=True)
    if r.returncode != 0:
        return None
    return r.stdout.decode("utf-8", "replace")


# ------------------------------------------------------------------ activity
def active(m: str, p: str) -> bool:
    e = SPECS.get(m, {}).get(p)
    return bool(e and (e["new_at_this_milestone"] or e.get("whole_file")))


def first_touch(p: str, m: str) -> bool:
    chain = [x for x in MPATHS[m] if active(x, p)]
    return bool(chain) and chain[0] == m


# --------------------------------------------------------- generic restrict
def pull_comments(lines, start):
    """Extend a 1-based start line back over the contiguous comment block above."""
    s = start
    while s - 2 >= 0 and lines[s - 2].strip().startswith("#"):
        s -= 1
    return s


def restrict_region(lines, rstart, rend, drop_spans):
    """Return region [rstart,rend] (1-based incl) minus drop_spans, tidy blanks.

    With an empty drop_spans this is byte-identical to the donor region.
    """
    if not drop_spans:
        return list(lines[rstart - 1:rend])
    dropped = set()
    for a, b in drop_spans:
        for i in range(max(a, rstart), min(b, rend) + 1):
            dropped.add(i)
    out = []
    prev_dropped = False
    for i in range(rstart, rend + 1):
        if i in dropped:
            prev_dropped = True
            continue
        ln = lines[i - 1]
        if prev_dropped and not ln.strip() and (not out or not out[-1].strip()):
            continue
        out.append(ln)
        if ln.strip():
            prev_dropped = False
    while out and not out[-1].strip():
        out.pop()
    return out


NEWAT = {}
for _m, _d in SPECS.items():
    for _p, _e in _d.items():
        for _k in _e["new_at_this_milestone"]:
            NEWAT[(_p, _k)] = _m


def off_path_members(path, container, mile, prefix=None):
    """Member keys of `container` in `path` whose owner is off `mile`'s path."""
    info = SYMS.get(path, {})
    pre = (prefix if prefix is not None else container) + "."
    res = set()
    for k, v in info.items():
        if k == container or not k.startswith(pre):
            continue
        if v.get("owner") in MPATHS[mile] or v.get("kind") not in LIVE:
            continue
        res.add(k)
    return res


def preserved_removals(path, container, mile):
    """Main-era members of `container` that a milestone OFF this path removes.

    They stay untouched in this view: the lane that owns the removal shows it.
    A removal that no milestone claims (no cut_specs entry) is NOT preserved -
    nothing downstream would delete it and the union would drift from the donor.
    """
    info = SYMS.get(path, {})
    pre = container + "."
    res = set()
    for k, v in info.items():
        if k == container or not k.startswith(pre):
            continue
        if v.get("kind") != "removed" or v.get("owner") in MPATHS[mile]:
            continue
        if (path, k) not in NEWAT:
            continue
        res.add(k)
    return res


# ------------------------------------------------------------ python regions
def _seg_bounds(node):
    start = node.lineno
    for d in getattr(node, "decorator_list", []) or []:
        start = min(start, d.lineno)
    return start, getattr(node, "end_lineno", node.lineno)


def _import_key(lines, node):
    a, b = _seg_bounds(node)
    return re.sub(r"\s+", " ", "import:" + "\n".join(lines[a - 1:b]).strip().replace("\n", " "))


def py_model(src: str):
    lines = src.splitlines()
    tree = ast.parse(src)
    out, imports, order = {}, [], []

    def add(key, node, cls=None):
        a, b = _seg_bounds(node)
        out[key] = {"start": a, "end": b, "text": "\n".join(lines[a - 1:b]), "cls": cls}
        order.append(key)

    for node in tree.body:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            k = _import_key(lines, node)
            add(k, node)
            imports.append(k)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            add(node.name, node)
        elif isinstance(node, ast.ClassDef):
            add(node.name, node)
            body_start = node.body[0].lineno
            for d in getattr(node.body[0], "decorator_list", []) or []:
                body_start = min(body_start, d.lineno)
            out[node.name]["body_start"] = body_start
            out[node.name]["is_class"] = True
            for sub in node.body:
                if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    add(f"{node.name}.{sub.name}", sub, node.name)
                elif isinstance(sub, ast.Assign):
                    for t in sub.targets:
                        if isinstance(t, ast.Name):
                            add(f"{node.name}.{t.id}", sub, node.name)
                elif isinstance(sub, ast.AnnAssign) and isinstance(sub.target, ast.Name):
                    add(f"{node.name}.{sub.target.id}", sub, node.name)
        elif isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name):
                    add(t.id, node)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            add(node.target.id, node)
    return {"lines": lines, "sym": out, "imports": imports, "order": order, "tree": tree}


def py_class_text(don, key, mile, path, extra_drop=()):
    """Donor text of class `key` restricted to members on `mile`'s path."""
    s = don["sym"][key]
    lines = don["lines"]
    drops = set(off_path_members(path, key, mile)) | set(extra_drop)
    spans = []
    for mk in sorted(drops):
        ms = don["sym"].get(mk)
        if not ms or ms.get("cls") != key:
            continue
        spans.append((pull_comments(lines, ms["start"]), ms["end"]))
        CTX["restricted"].append([path, mk, mile])
    if not spans:
        return s["text"].splitlines()
    out = restrict_region(lines, s["start"], s["end"], spans)
    try:
        ast.parse("\n".join(out))
    except SyntaxError:
        head = lines[s["start"] - 1:s.get("body_start", s["start"] + 1) - 1]
        indent = " " * 4
        out = head + [indent + "pass"]
    return out


def _apply_edits(lines, edits):
    """edits: (start, end, replacement|None) 1-based inclusive; end<start => insert."""
    spans = [(a, b) for a, b, _ in edits if b >= a]
    kept, seen_span = [], set()
    for e in edits:
        a, b, _ = e
        if b >= a:
            if any(a2 <= a and b <= b2 and (a2, b2) != (a, b) for a2, b2 in spans):
                continue
            if (a, b) in seen_span:      # never replace the same span twice
                continue
            seen_span.add((a, b))
        else:
            if any(a2 < a <= b2 for a2, b2 in spans):
                continue
        kept.append(e)
    merged, ins = [], {}
    for a, b, rep in kept:
        if b >= a:
            merged.append((a, b, rep))
        else:
            ins.setdefault(a, []).extend(rep or [])   # append order == donor order
    merged.extend((a, a - 1, rep) for a, rep in ins.items())
    merged.sort(key=lambda e: (e[0], e[1]), reverse=True)
    out = list(lines)
    for a, b, rep in merged:
        if b >= a:
            out[a - 1:b] = rep if rep is not None else []
        else:
            out[a - 1:a - 1] = rep or []
    return out


def py_render(cur_src, donor_src, take, drop, do_imports, mile, path):
    cur = py_model(cur_src)
    don = py_model(donor_src)
    edits = []
    handled = set()

    if do_imports:
        for k in cur["imports"]:
            s = cur["sym"][k]
            edits.append((s["start"], s["end"], None))
        blk = []
        for k in don["imports"]:
            blk.extend(don["sym"][k]["text"].splitlines())
        if cur["imports"]:
            at = cur["sym"][cur["imports"][0]]["start"]
        else:
            at = 1
            for node in cur["tree"].body:
                if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant):
                    at = node.end_lineno + 1
                break
        edits.append((at, at - 1, blk + [""]))
        handled |= {k for k in take if k.startswith("import:")}
        handled |= {k for k in drop if k.startswith("import:")}

    def donor_gap(a_key, b_key):
        return don["lines"][don["sym"][a_key]["end"]:don["sym"][b_key]["start"] - 1]

    def insertion(key):
        """(line, prefix_lines, suffix_lines) for a donor symbol absent from cur.

        The donor's own inter-symbol gap is reproduced ONLY when the anchor is
        the immediately adjacent donor symbol; otherwise the gap would swallow
        every donor symbol in between.
        """
        idx = don["order"].index(key)
        cls = don["sym"][key]["cls"]
        default = [] if cls else [""]

        def clean(gap):
            if len(gap) > 4 or any(x.strip() and not x.strip().startswith("#") for x in gap):
                return list(default)
            return gap

        for j in range(idx - 1, -1, -1):
            prev = don["order"][j]
            if prev in cur["sym"] and don["sym"][prev]["cls"] == cls:
                gap = clean(donor_gap(prev, key)) if j == idx - 1 else list(default)
                return cur["sym"][prev]["end"] + 1, gap, []
        for j in range(idx + 1, len(don["order"])):
            nxt = don["order"][j]
            if nxt in cur["sym"] and don["sym"][nxt]["cls"] == cls:
                gap = clean(donor_gap(key, nxt)) if j == idx + 1 else list(default)
                return cur["sym"][nxt]["start"], [], gap
        if cls and cls in cur["sym"]:
            return cur["sym"][cls]["end"] + 1, [""], []
        return len(cur["lines"]) + 1, [""], []

    # ---- which containers are handled member-wise, which are replaced whole
    memberwise, whole_cls = set(), set()
    for k in (take | drop) - handled:
        d = don["sym"].get(k) or cur["sym"].get(k) or {}
        if not d.get("is_class"):
            continue
        if k in take and k in cur["sym"] and k in don["sym"] and preserved_removals(path, k, mile):
            memberwise.add(k)
        else:
            whole_cls.add(k)

    def member_of_replaced(k):
        c = (don["sym"].get(k) or cur["sym"].get(k) or {}).get("cls")
        return c is not None and c in whole_cls

    # ---- member-wise container: donor shell + on-path donor members, with the
    #      main-era members that a later lane removes left exactly where they are
    for k in sorted(memberwise):
        ck, dk = cur["sym"][k], don["sym"][k]
        cur_mems = [x for x in cur["order"] if cur["sym"][x]["cls"] == k]
        don_mems = [x for x in don["order"] if don["sym"][x]["cls"] == k]
        off = off_path_members(path, k, mile)
        keep_don = [x for x in don_mems if x not in off]
        for x in don_mems:
            if x in off:
                CTX["restricted"].append([path, x, mile])
        preserved = preserved_removals(path, k, mile)
        c_first = min([pull_comments(cur["lines"], cur["sym"][x]["start"]) for x in cur_mems],
                      default=ck["end"] + 1)
        d_first = min([pull_comments(don["lines"], don["sym"][x]["start"]) for x in keep_don],
                      default=dk["end"] + 1)
        edits.append((ck["start"], c_first - 1, don["lines"][dk["start"] - 1:d_first - 1]))
        for x in cur_mems:
            handled.add(x)
            if x in keep_don or x in preserved or x in off:
                continue
            s = cur["sym"][x]
            edits.append((pull_comments(cur["lines"], s["start"]), s["end"], None))
        for x in preserved:
            CTX["preserved"].append([path, x, mile, NEWAT[(path, x)]])
        for x in keep_don:
            handled.add(x)
            dtext = don["sym"][x]["text"].splitlines()
            if x in cur["sym"] and cur["sym"][x]["cls"] == k:
                s = cur["sym"][x]
                edits.append((s["start"], s["end"], dtext))
            else:
                at, pre, suf = insertion(x)
                edits.append((at, at - 1, list(pre) + dtext + list(suf)))
        handled.add(k)

    for k in sorted(drop - handled):
        if k in cur["sym"] and not member_of_replaced(k):
            s = cur["sym"][k]
            edits.append((s["start"], s["end"], None))

    ordered = [x for x in don["order"] if x in take] + \
              sorted(x for x in take if x not in don["sym"])
    for k in ordered:
        if k in handled or k not in don["sym"] or member_of_replaced(k):
            continue
        if don["sym"][k].get("is_class"):
            dtext = py_class_text(don, k, mile, path)
        else:
            dtext = don["sym"][k]["text"].splitlines()
        if k in cur["sym"]:
            s = cur["sym"][k]
            edits.append((s["start"], s["end"], dtext))
        else:
            cls = don["sym"][k]["cls"]
            if cls and cls not in cur["sym"]:
                continue  # container shell not here yet; its owner brings it
            at, pre, suf = insertion(k)
            edits.append((at, at - 1, list(pre) + dtext + list(suf)))

    return "\n".join(_apply_edits(cur["lines"], edits)).rstrip("\n") + "\n"


def py_create(donor_src, take, mile, path):
    """Create a donor-only file carrying only this milestone's top-level symbols."""
    don = py_model(donor_src)
    lines = don["lines"]
    keep = []
    tree = don["tree"]
    doc = None
    if tree.body and isinstance(tree.body[0], ast.Expr) and isinstance(tree.body[0].value, ast.Constant):
        doc = "\n".join(lines[tree.body[0].lineno - 1:tree.body[0].end_lineno])
    for k in don["order"]:
        s = don["sym"][k]
        if s["cls"] is not None:
            continue
        if k.startswith("import:"):
            keep.append(s["text"])
            continue
        if k not in take:
            continue
        if s.get("is_class"):
            keep.append("\n".join(py_class_text(don, k, mile, path)))
        else:
            keep.append(s["text"])
    parts = ([doc] if doc else []) + keep
    return "\n\n".join(p for p in parts if p.strip()) + "\n"


# ------------------------------------------------------------------ markdown
def md_model(src):
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
    sym, order = {}, []
    for n, (i, lvl, title) in enumerate(heads):
        if lvl not in (2, 3):
            continue
        end = len(lines)
        for j, l2, _t in heads[n + 1:]:
            if l2 <= lvl:
                end = j
                break
        k = f"H{lvl}:{title}"
        sym[k] = {"start": i + 1, "end": end, "text": "\n".join(lines[i:end]), "level": lvl}
        order.append(k)
    return {"lines": lines, "sym": sym, "order": order}


_HDR = re.compile(r"^\s*(\[\[|\[)\s*([^\]]+?)\s*(\]\]|\])\s*(#.*)?$")


def toml_model(src):
    lines = src.splitlines()
    n = len(lines)

    def cstart(i):
        j, st = i - 1, i
        while j >= 0 and lines[j].strip().startswith("#"):
            st = j
            j -= 1
        return st

    hdrs = []
    for i, raw in enumerate(lines):
        m = _HDR.match(raw)
        if m and not raw.strip().startswith("#"):
            hdrs.append((cstart(i), i, m.group(2).strip(), m.group(1) == "[["))

    def block_end(k):
        stop = hdrs[k + 1][0] if k + 1 < len(hdrs) else n
        while stop > 0 and not lines[stop - 1].strip():
            stop -= 1
        return stop

    sym = {}

    def put(key, a, b):
        if key in sym:
            sym[key]["start"] = min(sym[key]["start"], a)
            sym[key]["end"] = max(sym[key]["end"], b)
        else:
            sym[key] = {"start": a, "end": b}

    for k, (bs, hi, path, arr) in enumerate(hdrs):
        a, b = bs + 1, block_end(k)
        put(path if arr else "table:" + path, a, b)
        parts = path.split(".")
        for d in range(1, len(parts)):
            pre = ".".join(parts[:d])
            owner = [x for x in hdrs if x[2] == pre]
            put((pre if (owner and owner[0][3]) else "table:" + pre), a, b)

    cur, i = "", 0
    while i < n:
        raw = lines[i]
        m = _HDR.match(raw)
        if m and not raw.strip().startswith("#"):
            cur = m.group(2).strip()
            i += 1
            continue
        s2 = raw.strip()
        if s2 and not s2.startswith("#") and "=" in s2:
            kname = s2.split("=", 1)[0].strip().strip('"').strip("'")
            full = f"{cur}.{kname}" if cur else kname
            a = cstart(i) + 1
            body = s2.split("=", 1)[1]
            e = i + 1
            while (body.count("[") > body.count("]") or body.count("{") > body.count("}")
                   or body.count('"""') % 2 == 1) and e < n:
                body += "\n" + lines[e]
                e += 1
            if full not in sym:
                sym[full] = {"start": a, "end": e}
            i = e
            continue
        i += 1

    for k, v in sym.items():
        v["text"] = "\n".join(lines[v["start"] - 1:v["end"]])
    order = sorted(sym, key=lambda k: (sym[k]["start"], -sym[k]["end"]))
    return {"lines": lines, "sym": sym, "order": order}


def env_model(src):
    lines = src.splitlines()
    sym, order = {}, []
    for i, l in enumerate(lines):
        s = l.strip()
        if not s or s.startswith("#") or "=" not in s:
            continue
        k = s.split("=", 1)[0].strip()
        sym[k] = {"start": i + 1, "end": i + 1, "text": l}
        order.append(k)
    return {"lines": lines, "sym": sym, "order": order}


def _outermost(keys, m):
    ks = [k for k in keys if k in m["sym"]]
    seen, uniq = set(), []
    for k in sorted(ks, key=lambda k: (m["sym"][k]["start"], -m["sym"][k]["end"], k)):
        span = (m["sym"][k]["start"], m["sym"][k]["end"])
        if span in seen:
            continue
        seen.add(span)
        uniq.append(k)
    out = []
    for k in uniq:
        a, b = m["sym"][k]["start"], m["sym"][k]["end"]
        if any(m["sym"][j]["start"] <= a and b <= m["sym"][j]["end"]
               and (m["sym"][j]["start"], m["sym"][j]["end"]) != (a, b)
               for j in uniq if j != k):
            continue
        out.append(k)
    return set(out)


def struct_text(don, k, mile, path, kind):
    """Donor region of container `k`, restricted to `mile`'s on-path members."""
    s = don["sym"][k]
    lines = don["lines"]
    info = SYMS.get(path, {})
    spans = []
    if kind is toml_model and info.get(k, {}).get("sym_kind") == "toml_table":
        tpath = k[len("table:"):] if k.startswith("table:") else k
        for mk in sorted(off_path_members(path, k, mile, prefix=tpath)):
            if info.get(mk, {}).get("sym_kind") != "toml_key":
                continue
            ms = don["sym"].get(mk)
            if ms and s["start"] <= ms["start"] and ms["end"] <= s["end"] and ms["end"] - ms["start"] < s["end"] - s["start"]:
                spans.append((ms["start"], ms["end"]))
                CTX["restricted"].append([path, mk, mile])
    elif kind is md_model and s.get("level") == 2:
        for mk, mv in sorted(info.items()):
            if mv.get("sym_kind") != "md_h3" or mv.get("kind") not in LIVE:
                continue
            if mv.get("owner") in MPATHS[mile]:
                continue
            ms = don["sym"].get(mk)
            if ms and ms.get("level") == 3 and s["start"] < ms["start"] and ms["end"] <= s["end"]:
                spans.append((ms["start"], ms["end"]))
                CTX["restricted"].append([path, mk, mile])
    if not spans:
        return s["text"].splitlines()
    return restrict_region(lines, s["start"], s["end"], spans)


def struct_render(cur_src, donor_src, take, drop, model, mile, path):
    cur = model(cur_src)
    don = model(donor_src)
    take = _outermost(take, don)
    drop = _outermost(drop, cur)
    edits = []
    for k in sorted(drop):
        if k in cur["sym"]:
            edits.append((cur["sym"][k]["start"], cur["sym"][k]["end"], None))
    for k in sorted(take):
        if k not in don["sym"]:
            continue
        t = struct_text(don, k, mile, path, model)
        if k in cur["sym"]:
            edits.append((cur["sym"][k]["start"], cur["sym"][k]["end"], t))
        else:
            idx = don["order"].index(k)
            at = len(cur["lines"]) + 1
            for prev in reversed(don["order"][:idx]):
                if prev in cur["sym"]:
                    at = cur["sym"][prev]["end"] + 1
                    break
            else:
                for nxt in don["order"][idx + 1:]:
                    if nxt in cur["sym"]:
                        at = cur["sym"][nxt]["start"]
                        break
            edits.append((at, at - 1, t + [""]))
    return "\n".join(_apply_edits(cur["lines"], edits)).rstrip("\n") + "\n"


def struct_create(donor_src, take, model, mile, path):
    don = model(donor_src)
    take = _outermost(take, don)
    pre_end = min((don["sym"][k]["start"] for k in don["order"]), default=len(don["lines"]) + 1)
    out = don["lines"][:pre_end - 1]
    for k in don["order"]:
        if k in take:
            out.extend(struct_text(don, k, mile, path, model))
    return "\n".join(out).rstrip("\n") + "\n"


MODELS = {".md": md_model, ".toml": toml_model}


def model_for(path):
    if path.endswith(".py"):
        return "py"
    for ext, m in MODELS.items():
        if path.endswith(ext):
            return m
    return env_model


# ------------------------------------------------------------------ driver
def run(mile, wt):
    report = {"whole": [], "deleted": [], "renamed": [], "shared": [],
              "import_blocks": [], "skipped": [], "created": [], "restricted": [],
              "preserved": []}
    spec = SPECS[mile]

    for p, e in sorted(MAP.items()):
        if e.get("split") or e["owner"] != mile:
            continue
        f = FACTS.get(p, {})
        st = f.get("status", "M")
        if st == "D":
            subprocess.run(["git", "-C", wt, "rm", "-q", "--ignore-unmatch", "--", p], check=False)
            report["deleted"].append(p)
            continue
        if st.startswith("R"):
            old = f.get("old_path")
            if old and os.path.exists(os.path.join(wt, old)):
                subprocess.run(["git", "-C", wt, "rm", "-q", "--", old], check=False)
            report["renamed"].append([old, p])
        subprocess.run(["git", "-C", wt, "checkout", DONOR, "--", p], check=True)
        report["whole"].append(p)

    whole_now = set(report["whole"])

    for p in sorted(SPLIT):
        if not active(mile, p):
            continue
        sp = spec[p]
        if sp.get("whole_file"):
            subprocess.run(["git", "-C", wt, "checkout", DONOR, "--", p], check=True)
            report["whole"].append(p)
            whole_now.add(p)
            continue
        newsyms = set(sp["new_at_this_milestone"])
        info = SYMS.get(p, {})
        take = {k for k in newsyms if info.get(k, {}).get("kind") in LIVE}
        drop = {k for k in newsyms if info.get(k, {}).get("kind") == "removed"}
        dsrc = blob(DONOR, p)
        if dsrc is None:
            report["skipped"].append([p, "no donor blob"])
            continue
        fp = os.path.join(wt, p)
        cur = open(fp).read() if os.path.exists(fp) else None
        kind = model_for(p)
        CTX["path"], CTX["mile"] = p, mile
        try:
            if cur is None:
                if kind == "py":
                    out = py_create(dsrc, take, mile, p)
                else:
                    out = struct_create(dsrc, take, kind, mile, p)
                report["created"].append(p)
            else:
                if kind == "py":
                    imp = first_touch(p, mile) and p not in whole_now
                    out = py_render(cur, dsrc, take, drop, imp, mile, p)
                    if imp:
                        report["import_blocks"].append(p)
                else:
                    out = struct_render(cur, dsrc, take, drop, kind, mile, p)
        except SyntaxError as exc:
            report["skipped"].append([p, f"parse: {exc}"])
            continue
        os.makedirs(os.path.dirname(fp), exist_ok=True)
        open(fp, "w").write(out)
        subprocess.run(["git", "-C", wt, "add", "--", p], check=True)
        report["shared"].append(p)

    report["restricted"] = CTX["restricted"]
    report["preserved"] = CTX["preserved"]
    return report


if __name__ == "__main__":
    mile, wt = sys.argv[1], sys.argv[2]
    rep = run(mile, wt)
    json.dump(rep, open(S + f"cut_report_{mile}.json", "w"), indent=1)
    print(json.dumps({k: (len(v) if isinstance(v, list) else v) for k, v in rep.items()}))
    for x in rep["skipped"]:
        print("  SKIP", x)

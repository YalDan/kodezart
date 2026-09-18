#!/usr/bin/env python3.12
"""Extend the translated ownership maps for a range of union commits.

usage: CUT_SUFFIX=.r python3.12 extend_maps.py <old> <new>
Owner of a new file / new symbol = the lane tag of the commit that introduced it
(commit subject prefix like 'feat(m2):' / 'test(M4):' -> M2 / M4; otherwise M1).
"""
import json, os, re, subprocess, sys
S = "<scratch>/recut"
sys.path.insert(0, S)
os.environ.setdefault("CUT_SUFFIX", ".r")
import cut_views_r as C
import sym_kinds as K   # derives the real kind from cut_views_r's own models
OLD, NEW = sys.argv[1], sys.argv[2]
MAIN = C.MAIN
def git(*a): return subprocess.run(["git", "-C", C.REPO, *a], capture_output=True, text=True).stdout
def lane_of_commit(sha):
    subj = git("show", "-s", "--format=%s", sha)
    m = re.match(r"\w+\((m[1-7]|M[1-7])[^)]*\)", subj)
    return m.group(1).upper() if m else "M1"
om = json.load(open(S + "/ownership_map.r.json")); facts = json.load(open(S + "/ownership_facts_full.r.json"))
syms = json.load(open(S + "/ownership_symbols.r.json")); specs = json.load(open(S + "/cut_specs.r.json"))
commits = git("rev-list", "--reverse", f"{OLD}..{NEW}").split()
file_lane = {}
for c in commits:
    lane = lane_of_commit(c)
    for line in git("diff-tree", "--no-commit-id", "--name-status", "-r", c).splitlines():
        st, p = line.split("\t")[0], line.split("\t")[-1]
        file_lane.setdefault(p, lane)  # first commit that touched it in the range
changed = [l.split("\t")[-1] for l in git("diff", "--name-only", OLD, NEW).splitlines()]
new_files = [p for p in changed if p not in om and C.blob(NEW, p) is not None]
for p in new_files:
    owner = file_lane.get(p, "M1")
    om[p] = {"owner": owner, "reason": f"introduced {OLD[:8]}..{NEW[:8]} by a {owner} commit", "split": None}
    if not any(f["path"] == p for f in facts):
        facts.append({"path": p, "status": "A", "added": len(C.blob(NEW, p).splitlines()), "deleted": 0, "kind": "test_py" if p.startswith("tests/") else "src_py", "first_seen": f"union-{NEW[:8]}", "imports": []})
print("new files mapped:", [(p, om[p]["owner"]) for p in new_files])
split = [p for p in changed if om.get(p, {}).get("split")]
def model(rev, p):
    src = C.blob(rev, p)
    if src is None: return None
    kind = C.model_for(p)
    return C.py_model(src) if kind == "py" else kind(src)
added = remarked = 0
for p in split:
    mo, mn, mm = model(OLD, p), model(NEW, p), model(MAIN, p)
    kinds_new, kinds_old = K.kinds_at(NEW, p), K.kinds_at(OLD, p)
    def sym_kind_of(key):
        # the model entry's own kind (py: import/function/method/class_attr/assignment/class,
        # toml: toml_table/toml_key, md: md_h2/md_h3, env: env_key) - never a placeholder
        return kinds_new.get(key) or kinds_old.get(key) or K.kind_from_shape(p, key)
    lane = file_lane.get(p, om[p]["owner"])
    for k, v in mn["sym"].items():
        told = (mo["sym"].get(k) or {}).get("text") if mo else None
        if v["text"] == told: continue
        tmain = (mm["sym"].get(k) or {}).get("text") if mm else None
        kind = "added" if tmain is None else ("modified" if tmain != v["text"] else "unchanged")
        entry = syms.setdefault(p, {}).get(k)
        if entry is None:
            syms[p][k] = {"consumers": {}, "kind": kind, "note": f"introduced {OLD[:8]}..{NEW[:8]}", "owner": lane, "sym_kind": sym_kind_of(k)}
            owner = lane; added += 1
        else:
            owner = entry.get("owner") or lane; entry["owner"] = owner
            if entry.get("sym_kind") in (None, "symbol"): entry["sym_kind"] = sym_kind_of(k)
            if entry.get("kind") not in ("added", "modified"): entry["kind"] = kind
            remarked += 1
        sp = specs.setdefault(owner, {}).setdefault(p, {"base": "main", "creates_file": False, "delta_status": "M", "file_map_owner": om[p]["owner"], "keep_main": [], "new_at_this_milestone": [], "remove": [], "take_from_donor": []})
        for key in ("new_at_this_milestone", "take_from_donor"):
            if k not in sp[key]: sp[key].append(k)
        if k in sp.get("keep_main", []): sp["keep_main"].remove(k)
print("split files:", split); print("symbols added:", added, "re-marked:", remarked)
for name, obj in [("ownership_map", om), ("ownership_facts_full", facts), ("ownership_symbols", syms), ("cut_specs", specs)]:
    json.dump(obj, open(f"{S}/{name}.r.json", "w"), indent=1)
print("maps updated")

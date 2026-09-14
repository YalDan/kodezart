#!/usr/bin/env python3.12
"""Coverage: every file the union changes vs the base is carried, whole or symbol-wise, by the views."""
import json, os, subprocess, sys
S = "/private/tmp/claude-503/-Users-kodezart-Projects-kodezart/f325c30b-eb65-4b5e-83ca-1ef5846ade2d/scratchpad"
sys.path.insert(0, S)
import cut_views_r as C
UNION, BASE = C.DONOR, C.MAIN
REPO = C.REPO
PATHS = C.MPATHS  # milestone -> path list
heads = {m: subprocess.run(["git", "-C", f"{S}/wt3-view-{m.lower()}", "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip() for m in PATHS}
def blob(rev, p): return C.blob(rev, p)
changed = [l.split("\t")[-1] for l in subprocess.run(["git", "-C", REPO, "diff", "--name-only", BASE, UNION], capture_output=True, text=True).stdout.splitlines()]
om = C.MAP; syms = C.SYMS
unmapped, whole_missing, split_missing, ok = [], [], [], 0
for p in changed:
    e = om.get(p)
    if e is None:
        unmapped.append(p); continue
    ub = blob(UNION, p)
    if not e.get("split"):
        owner = e["owner"]
        views_with = [m for m in PATHS if owner in PATHS[m] and blob(heads[m], p) == ub]
        if ub is None:  # deleted in union
            if all(blob(heads[m], p) is None for m in PATHS if owner in PATHS[m]): ok += 1
            else: whole_missing.append((p, owner, "deletion not carried"))
        elif views_with: ok += 1
        else: whole_missing.append((p, owner, "union blob absent from every view on the owner's path"))
        continue
    # split: every symbol in the union model must appear (union-identical text) in some view whose path includes the symbol's owner
    try:
        kind = C.model_for(p); um = C.py_model(ub) if kind == "py" else kind(ub)
    except Exception as ex:
        split_missing.append((p, "?", f"model error {ex}")); continue
    models = {}
    for m in PATHS:
        b = blob(heads[m], p)
        if b is None: models[m] = None; continue
        try: models[m] = C.py_model(b) if kind == "py" else kind(b)
        except Exception: models[m] = None
    for k, v in um["sym"].items():
        owner = (syms.get(p, {}).get(k) or {}).get("owner")
        cands = [m for m in PATHS if (owner is None or owner in PATHS[m])]
        if any(models[m] and k in models[m]["sym"] and models[m]["sym"][k]["text"] == v["text"] for m in cands):
            ok += 1
        else:
            split_missing.append((p, k, f"owner {owner}: union text of this symbol in no view on the owner's path"))
print("changed files vs base:", len(changed), "| covered units:", ok)
print("UNMAPPED files:", len(unmapped)); [print("  ", p) for p in unmapped]
print("WHOLE files missing:", len(whole_missing)); [print("  ", x) for x in whole_missing[:30]]
print("SPLIT symbols missing:", len(split_missing)); [print("  ", x) for x in split_missing[:40]]
json.dump({"unmapped": unmapped, "whole_missing": whole_missing, "split_missing": split_missing, "covered": ok}, open(S + "/coverage_report.json", "w"), indent=1)

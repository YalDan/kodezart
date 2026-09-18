#!/usr/bin/env python3.12
"""Per-view annotations: later-lane members shown here / own members shown earlier."""
import json, subprocess, sys, collections
sys.path.insert(0, "/private/tmp/claude-503/-Users-kodezart-Projects-kodezart/f2770f28-277d-4202-b936-349664afe907/scratchpad/recut")
import cut_views_r as C

BR = {"M1": "4518c36328078972b320648f317c35eede0161fe", "M4": "ac85209e62a96a55c3af4b202ffc68b6493d0ed2", "M3": "4a5146e71388f3351bbf0ea6c782c54e0e45b153", "M2": "bf0c7de30bcda5f2183c9cf7f29a56cc32450be2", "M5": "91ce3b78a21a40b9b2fc079f0459b34e7ebda722", "M6": "3a23e284023e31b72a5b6e7f21161252ba6d297d", "M7": "e5b1493e21503ecebaed85f0f762218df1a2eecc"}
PR = {"M1": 133, "M4": 124, "M3": 125, "M2": 126, "M5": 127, "M6": 128, "M7": 129}
BASE = {"M1": "origin/v03/restructure", "M4": BR["M1"], "M3": BR["M4"], "M2": BR["M3"],
        "M5": BR["M3"], "M6": BR["M5"], "M7": BR["M5"]}
LIVE = ("added", "modified")

cache = {}
def model(ref, p):
    key = (ref, p)
    if key in cache:
        return cache[key]
    src = C.blob(ref, p)
    m = None
    if src is not None:
        kind = C.model_for(p)
        try:
            m = C.py_model(src) if kind == "py" else kind(src)
        except SyntaxError:
            m = None
    cache[key] = m
    return m

def txt(m, k):
    if not m or k not in m["sym"]:
        return None
    return m["sym"][k]["text"]

files = [p for p in C.SYMS if C.blob(C.DONOR, p) is not None]
print("files with symbol ownership:", len(files))

# donor-identical presence of every live symbol at every ref we care about
refs = {"main": "origin/v03/restructure"}
refs.update({m: BR[m] for m in BR})

present = collections.defaultdict(dict)   # (p,k) -> ref_name -> bool
for p in files:
    dm = model(C.DONOR, p)
    if dm is None:
        continue
    for k, v in C.SYMS[p].items():
        if v.get("kind") not in LIVE:
            continue
        dt = txt(dm, k)
        if dt is None:
            continue
        for rn, rr in refs.items():
            present[(p, k)][rn] = (txt(model(rr, p), k) == dt)

ORDER = ["M1", "M4", "M3", "M2", "M5", "M6", "M7"]
res = {}
for X in ORDER:
    path = C.MPATHS[X]
    anc = path[:-1]
    later, earlier = [], []
    for (p, k), pres in present.items():
        owner = C.SYMS[p][k].get("owner")
        here = pres.get(X, False)
        base_name = anc[-1] if anc else "main"
        inbase = pres.get(base_name, False)
        introduced_here = here and not inbase
        if introduced_here and owner not in path:
            later.append({"file": p, "symbol": k, "owner": owner,
                          "owner_pr": PR.get(owner), "sym_kind": C.SYMS[p][k].get("sym_kind"),
                          "kind": C.SYMS[p][k].get("kind")})
        if owner == X and inbase:
            src_m = None
            prev = "main"
            for a in anc:
                if pres.get(a) and not (prev != "main" and pres.get(prev)):
                    src_m = a
                    break
                prev = a
            if src_m is None:
                for a in anc:
                    if pres.get(a):
                        src_m = a
                        break
            earlier.append({"file": p, "symbol": k, "shown_at": src_m,
                            "shown_pr": PR.get(src_m), "sym_kind": C.SYMS[p][k].get("sym_kind"),
                            "kind": C.SYMS[p][k].get("kind")})
    later.sort(key=lambda x: (x["file"], x["symbol"]))
    earlier.sort(key=lambda x: (x["file"], x["symbol"]))
    obj = {"milestone": X, "branch": BR[X], "base": BASE[X],
           "head": subprocess.run(["git", "-C", C.REPO, "rev-parse", BR[X]],
                                  capture_output=True, text=True).stdout.strip(),
           "later_members_shown_here": later,
           "own_members_shown_earlier": earlier,
           "counts": {"later_members_shown_here": len(later),
                      "own_members_shown_earlier": len(earlier)}}
    json.dump(obj, open(C.S + f"view_annotations_r_{X}.json", "w"), indent=1)
    res[X] = obj["counts"]
    print(X, obj["counts"])
json.dump(res, open(C.S + "annotation_counts_r.json", "w"), indent=1)

#!/usr/bin/env python3.12
"""Replace every placeholder sym_kind "symbol" in ownership_symbols<SUF>.json with
the real kind, derived from the model of that file (union first, base second,
name shape last). Backs the four maps up as *.r.json.run1 and rewrites the
symbols map in place.
"""
import json, os, shutil, sys

S = "<scratch>/recut/"
sys.path.insert(0, S)
import cut_views_r as C          # noqa: E402
import sym_kinds as K            # noqa: E402

for name in ("ownership_map", "ownership_facts_full", "ownership_symbols", "cut_specs"):
    src = S + name + ".r.json"
    dst = src + ".run1"
    if not os.path.exists(dst):
        shutil.copy2(src, dst)
        print("backup", dst)

path_syms = S + "ownership_symbols.r.json"
syms = json.load(open(path_syms))

cache = {}
def kinds(rev, p):
    key = (rev, p)
    if key not in cache:
        cache[key] = K.kinds_at(rev, p)
    return cache[key]

repaired, by_source, unresolved = [], {}, []
for p in sorted(syms):
    for k in sorted(syms[p]):
        v = syms[p][k]
        if v.get("sym_kind") != "symbol":
            continue
        ku, kb = kinds(C.DONOR, p), kinds(C.MAIN, p)
        if k in ku:
            new, source = ku[k], "union model"
        elif k in kb:
            new, source = kb[k], "base model (absent at union)"
        else:
            new, source = K.kind_from_shape(p, k), "NAME SHAPE (absent from both models)"
            unresolved.append([p, k, new])
        v["sym_kind"] = new
        by_source[source] = by_source.get(source, 0) + 1
        repaired.append([p, k, "symbol", new, source])

print(f"repaired entries: {len(repaired)}")
for p, k, old, new, source in repaired:
    print(f"  {p} :: {k}   {old} -> {new}   [{source}]")
print("by source:", by_source)
if unresolved:
    print("DERIVED FROM NAME SHAPE ONLY:")
    for row in unresolved:
        print("  ", row)

json.dump(syms, open(path_syms, "w"), indent=1)
json.dump({"repaired": repaired, "by_source": by_source, "shape_only": unresolved},
          open(S + "kind_repair_report.json", "w"), indent=1)
print("wrote", path_syms)
left = sum(1 for p in syms for k in syms[p] if syms[p][k].get("sym_kind") == "symbol")
print("remaining placeholder entries:", left)

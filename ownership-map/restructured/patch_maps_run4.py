#!/usr/bin/env python3.12
"""Run-4 map fixes: four removal/arrival owners corrected so that every view's
own docs/operation.example.toml is accepted by that view's own OperationConfig,
and so that the two removals the map misattributes name the milestone the cut
actually performs them at.

(a) docs/operation.example.toml 'initiatives'      removal  M2 -> M3
    (OperationConfig.initiatives is removed at M3; the key must go with it)
(b) docs/operation.example.toml 'audit_scopes'     addition M1 -> M4
    (OperationConfig.audit_scopes arrives at M4; the key must arrive with it)
(c) docs/operation.example.toml 'private_surface'  removal  M6 -> M1
    (the [private_surface] table arrives at M1 and TOML evicts the scalar there)
(d) docs/api.md 'H3:Workflow Events (15)'          removal  M7 -> M1
    (M1 owns H2:SSE Event Types, which carries the renamed 'Workflow Events (18)')

Edits, exactly as patch_maps_run3.py made its edits: the ownership_symbols owner
plus the matching cut_specs lists, so the cutter actually applies them.

cut_specs invariants restored for each moved symbol, per milestone M
(MPATHS[M] is the lane path of view M):
  new_at_this_milestone : k iff M == owner and kind != 'unchanged'
  take_from_donor       : k iff owner in MPATHS[M] and kind in (added, modified)
  remove                : k iff M == owner and kind == 'removed'
  keep_main             : k iff owner not in MPATHS[M] (for a live/removed kind)
"""
import json

S = "<scratch>/recut/"

MPATHS = {
    "M1": ["M1"],
    "M4": ["M1", "M4"],
    "M3": ["M1", "M4", "M3"],
    "M2": ["M1", "M4", "M3", "M2"],
    "M5": ["M1", "M4", "M3", "M5"],
    "M6": ["M1", "M4", "M3", "M5", "M6"],
    "M7": ["M1", "M4", "M3", "M5", "M7"],
}
LIVE = ("added", "modified")

# (path, symbol, expected old owner, new owner, reason tag)
MOVES = [
    ("docs/operation.example.toml", "initiatives", "M2", "M3",
     "removal follows OperationConfig.initiatives, removed at M3"),
    ("docs/operation.example.toml", "audit_scopes", "M1", "M4",
     "key follows OperationConfig.audit_scopes, added at M4"),
    ("docs/operation.example.toml", "private_surface", "M6", "M1",
     "TOML evicts the scalar where [private_surface] arrives, at M1"),
    ("docs/api.md", "H3:Workflow Events (15)", "M7", "M1",
     "M1 owns H2:SSE Event Types, which renames the heading to (18)"),
]

syms = json.load(open(S + "ownership_symbols.r.json"))
specs = json.load(open(S + "cut_specs.r.json"))
om = json.load(open(S + "ownership_map.r.json"))
log = {"moved": [], "spec_edits": []}


def spec_entry(m, p):
    e = specs.setdefault(m, {}).get(p)
    if e is None:
        e = specs[m][p] = {"base": "main" if m == "M1" else "previous", "creates_file": False,
                           "delta_status": "M", "file_map_owner": om[p]["owner"],
                           "keep_main": [], "new_at_this_milestone": [], "remove": [],
                           "take_from_donor": []}
        log["spec_edits"].append(f"created cut_specs[{m}][{p}]")
    return e


def want(m, p, k, owner, kind):
    on_path = owner in MPATHS[m]
    return {
        "new_at_this_milestone": (m == owner and kind != "unchanged"),
        "take_from_donor": (on_path and kind in LIVE),
        "remove": (m == owner and kind == "removed"),
        "keep_main": (not on_path) if kind != "unchanged" else True,
    }


def apply_lists(p, k, owner, kind, tag):
    for m in ("M1", "M2", "M3", "M4", "M5", "M6", "M7"):
        e = spec_entry(m, p)
        for field, need in want(m, p, k, owner, kind).items():
            lst = e.setdefault(field, [])
            has = k in lst
            if need and not has:
                lst.append(k)
                lst.sort()
                log["spec_edits"].append(f"cut_specs[{m}][{p}].{field} += {k!r}   ({tag})")
            elif has and not need:
                lst.remove(k)
                log["spec_edits"].append(f"cut_specs[{m}][{p}].{field} -= {k!r}   ({tag})")


for p, k, old, new, why in MOVES:
    v = syms[p][k]
    assert v["owner"] == old, (p, k, "owner is", v["owner"], "expected", old)
    kind = v["kind"]
    v["owner"] = new
    v["note"] = (v.get("note", "") + f"; owner {old} -> {new} (run 4): {why}").lstrip("; ")
    log["moved"].append({"path": p, "symbol": k, "kind": kind,
                         "owner": f"{old} -> {new}", "sym_kind": v.get("sym_kind"),
                         "reason": why, "note": v["note"]})
    apply_lists(p, k, new, kind, f"{k} {old}->{new}")

json.dump(syms, open(S + "ownership_symbols.r.json", "w"), indent=1)
json.dump(specs, open(S + "cut_specs.r.json", "w"), indent=1)
print(json.dumps(log, indent=1))
print("\n=== resulting per-milestone lists for the four symbols ===")
for p, k, old, new, why in MOVES:
    for m in ("M1", "M4", "M3", "M2", "M5", "M6", "M7"):
        e = specs[m][p]
        fields = [f for f in ("new_at_this_milestone", "take_from_donor", "remove", "keep_main")
                  if k in e[f]]
        print(f"  {p} {k!r} @ {m}: {fields}")

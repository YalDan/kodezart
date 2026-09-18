#!/usr/bin/env python3.12
"""Run-3 map fixes: the seven PEP 695 aliases, and the five "M7-final" owners.

B: model ast.TypeAlias as a module-level assignment (cut_views_r/sym_kinds) and
   map each alias the extender would now see: owner = lane tag of the commit that
   introduced it (all seven: 358a1db1, tag-less -> M1), kind "added".
C: five removals carry the owner "M7-final", which is no view; relabel to M7 and
   give M7 the cut_specs entry that makes the leaf view apply the removal.
"""
import json

S = "<scratch>/recut/"

ALIASES = [
    ("src/kodezart/types/domain/session.py", "AllowedTools", "M1", "358a1db1"),
    ("src/kodezart/types/domain/native_execution.py", "NativeExecutionPhase", "M1", "358a1db1"),
    ("src/kodezart/types/domain/native_execution.py", "ActiveNativeExecution", "M1", "358a1db1"),
    ("src/kodezart/types/domain/audit.py", "MandateJudgment", "M1", "358a1db1"),
    ("src/kodezart/types/domain/audit.py", "MandateObservation", "M1", "358a1db1"),
    ("src/kodezart/types/domain/agent.py", "NativeFireProgressEvent", "M1", "358a1db1"),
    ("tests/fakes.py", "_FakeCIObservation", "M1", "358a1db1"),
]

RELABEL = [
    ("docs/api.md", "H3:Workflow Events (15)"),
    ("src/kodezart/config/app.py", "AppConfig.git_base_url"),
    ("src/kodezart/config/app.py", "AppConfig.git_remote"),
    ("src/kodezart/config/app.py", "AppConfig.model"),
    ("tests/fakes.py", "make_passing_evaluation"),
]

syms = json.load(open(S + "ownership_symbols.r.json"))
specs = json.load(open(S + "cut_specs.r.json"))
om = json.load(open(S + "ownership_map.r.json"))
log = {"aliases": [], "relabelled": [], "spec_edits": []}


def spec_entry(m, p):
    e = specs.setdefault(m, {}).get(p)
    if e is None:
        e = specs[m][p] = {"base": "main" if m == "M1" else "previous", "creates_file": False,
                           "delta_status": "M", "file_map_owner": om[p]["owner"],
                           "keep_main": [], "new_at_this_milestone": [], "remove": [],
                           "take_from_donor": []}
        log["spec_edits"].append(f"created cut_specs[{m}][{p}]")
    return e


# ---- B: the seven aliases
for p, k, owner, sha in ALIASES:
    assert k not in syms.get(p, {}), (p, k)
    syms.setdefault(p, {})[k] = {
        "consumers": {}, "kind": "added",
        "note": f"PEP 695 type alias; introduced by {sha} (tag-less subject -> M1)",
        "owner": owner, "sym_kind": "assignment"}
    e = spec_entry(owner, p)
    for field in ("new_at_this_milestone", "take_from_donor"):
        if k not in e[field]:
            e[field].append(k)
            e[field].sort()
    if k in e.get("keep_main", []):
        e["keep_main"].remove(k)
    log["aliases"].append([p, k, owner])

# ---- C: M7-final -> M7, with the removal spec at M7
for p, k in RELABEL:
    v = syms[p][k]
    assert v["owner"] == "M7-final", (p, k, v["owner"])
    assert v["kind"] == "removed", (p, k, v["kind"])
    v["owner"] = "M7"
    v["note"] = v.get("note", "") + "; owner relabelled M7-final -> M7 (M7-final is no view)"
    e = spec_entry("M7", p)
    for field in ("new_at_this_milestone", "remove"):
        if k not in e[field]:
            e[field].append(k)
            e[field].sort()
    if k in e.get("keep_main", []):
        e["keep_main"].remove(k)
        log["spec_edits"].append(f"cut_specs[M7][{p}].keep_main -= {k}")
    log["relabelled"].append([p, k])

json.dump(syms, open(S + "ownership_symbols.r.json", "w"), indent=1)
json.dump(specs, open(S + "cut_specs.r.json", "w"), indent=1)
print(json.dumps(log, indent=1))

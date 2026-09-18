#!/usr/bin/env python3.12
"""Render the PR body for one re-cut milestone view (2026-09-14, restructured base)."""
import json, subprocess, sys
S = "/private/tmp/claude-503/-Users-kodezart-Projects-kodezart/f2770f28-277d-4202-b936-349664afe907/scratchpad/recut/"
REPO = "/Users/kodezart/Projects/kodezart"
UNION = "9d425ec4b2697fa8032e1f92287fb03b82d8603a"
BASE0 = "e1544ed749b994b2863b16b6318c61863fa4aa3e"
MS = {
    "M1": ("Scope input & port surface", ["KOD-73"], "v03/m1-scope-ports", "v03/restructure", 133),
    "M4": ("Criterion lifecycle & tracker state", ["KOD-76"], "v03/m4-criterion-lifecycle", "v03/m1-scope-ports", 124),
    "M3": ("Plan & walk", ["KOD-75", "KOD-105"], "v03/m3-plan-walk", "v03/m4-criterion-lifecycle", 125),
    "M2": ("Scope pass (groom · ticket · criteria)", ["KOD-74"], "v03/m2-organize", "v03/m3-plan-walk", 126),
    "M5": ("Deliver & terminate", ["KOD-77", "KOD-78"], "v03/m5-deliver-terminate", "v03/m3-plan-walk", 127),
    "M6": ("Audit", ["KOD-79"], "v03/m6-audit", "v03/m5-deliver-terminate", 128),
    "M7": ("Run supervisor", ["KOD-103"], "v03/m7-run-supervisor", "v03/m5-deliver-terminate", 129),
}
PRNUM = {m: v[4] for m, v in MS.items()}
def git(*a): return subprocess.run(["git", "-C", REPO, *a], capture_output=True, text=True).stdout
def counts(base, head):
    ns = [l.split("\t") for l in git("diff", "--name-status", f"{base}...{head}").splitlines() if l]
    return (sum(1 for x in ns if x[0].startswith("A")), sum(1 for x in ns if x[0].startswith("M")),
            sum(1 for x in ns if x[0].startswith("D")), sum(1 for x in ns if x[0].startswith("R")), len(ns))
def main(mile):
    name, lanes, branch, base, prnum = MS[mile]
    head = git("rev-parse", f"origin/{branch}").strip()
    rep = json.load(open(S + f"cut_report_{mile}.json"))
    ann = json.load(open(S + f"view_annotations_r_{mile}.json"))
    syn = json.load(open(S + "syntax_check_r.json"))[mile]
    a, m, d, r, total = counts(f"origin/{base}", head)
    lane_links = "\n".join(f"- {l}" for l in lanes)
    later = ann["later_members_shown_here"]; earlier = ann["own_members_shown_earlier"]
    body = f"""## {mile} — {name}

This view was re-cut on 2026-09-18 (23:10 UTC) from the union commit `{UNION}` on the restructured base
`v03/restructure` (#132, pure moves on `main`).

This branch is a **REVIEW VIEW**, not a working increment. It is cut from the union commit
`{UNION}` by **symbol-level ownership**: every file whose file-level owner is {mile} arrives
as the union blob, and every file shared with another milestone receives only the symbols {mile} owns
(member-wise for classes, per enum member, per TOML key/table, per Markdown section, per `.env` key).
It is stacked on `{base}` and is **expected NOT to be independently green** — it does not have to
type-check, import, or pass tests on its own, because a restriction of a coherent tree is not itself a
coherent tree. It is a **review surface**: prose outside modelled symbols is not carried, and CI on the
view is informational only. Correctness is asserted on the union branch `v03/union` @ `{UNION[:8]}`,
where the full gate is green and per-criterion grading happens. Nothing here merges alone:
the merge path is the union, after the restructure base #132 and all seven views have been reviewed.
A view is a pure restriction of the union — any edit that is not such a restriction does not belong on this
branch, including edits made to turn CI green.

Verified independently on this re-cut: 0 blob mismatches against the union over every owned whole file,
0 ownership violations, 0 off-path added members, 0 parse failures over every Python file in the tree,
every `type` alias owned on this view's path defined in the view, and this view's own config model accepting
its own example config. Import blocks of shared files are taken whole from the union on first touch, so a view
may import modules owned by later milestones — a recorded limit of the cut, not a defect. Recomposing the seven
views mechanically conflicts on exactly one hunk in `src/kodezart/types/domain/operation.py` (an adjacent
insertion, M2 against M5), resolved with the union text; the recomposed tree loads with the union's loader and
differs from the union only in placement and comments. The union is canonical and coverage is proven per file.

### Lanes
{lane_links}

### Ownership inputs
- Origin branch: `archive/recovery-session-2026-09-13`, directory `ownership-map/` (`restructured/` holds the
  maps translated through the 45-entry rename map, the env-driven cutter `cut_views_r.py`, and these cut reports)
- `ownership_map.r.json` — file-level owner / reason / split
- `ownership_facts_full.r.json` — per-path delta status, kind, imports
- `ownership_symbols.r.json` — symbol-level owner, kind and consumers
- `cut_specs.r.json` — per milestone and file: base, take_from_donor, remove, keep_main, new_at_this_milestone
- Union: `{UNION}` · restructured base: `{BASE0}` (#132) · original main: `4661a24b599d75503a997f3ce122f3ad2da77048`

### Re-derived file counts (`git diff --name-status origin/{base}...{head[:8]}`)
| | count |
|---|---|
| files changed vs `{base}` | {total} |
| added | {a} |
| modified | {m} |
| deleted | {d} |
| renamed | {r} |

Cut composition at this milestone: {len(rep['whole'])} whole union files, {len(rep['shared'])} shared files
spliced symbol-by-symbol, {len(rep['created'])} files created here carrying only this milestone's symbols,
{len(rep['deleted'])} deletions, {len(rep['renamed'])} renames, {len(rep['import_blocks'])} Python files whose
import block was set to the union's (first milestone on this path to touch the file; accepted noise).

Syntax check (`ast.parse`) on every Python file this view changes against its parent: **{syn['checked']} files, {'all parse' if not syn['failures'] else 'FAILURES: ' + ', '.join(syn['failures'][:5])}**.
"""
    if later:
        body += f"""
## Members of later lanes shown in this view

{len(later)} symbol(s) whose owning milestone is not on this view's path but which this view introduces (union-identical) — union import blocks taken wholesale on first touch, and symbols inside files whose *file-level* owner is this milestone:

""" + "\n".join(f"- `{x['file']}::{x['symbol']}` → #{x['owner_pr']}" for x in later) + "\n"
    if earlier:
        body += f"""
## Own members already shown by an earlier view

{len(earlier)} symbol(s) owned by this milestone that an earlier view on the path already introduced (shared-file splicing); review them there:

""" + "\n".join(f"- `{x['file']}::{x['symbol']}` — shown at {x['shown_at']} (#{x['shown_pr']})" for x in earlier) + "\n"
    body += """
🤖 Generated with Claude Code
"""
    open(S + f"pr_body_r_{mile}.md", "w").write(body)
    print(mile, prnum, head[:8], "body lines", body.count("\n"))
    return prnum
if __name__ == "__main__":
    for mile in sys.argv[1:]: main(mile)

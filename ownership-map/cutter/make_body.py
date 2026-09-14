#!/usr/bin/env python3.12
"""Render the draft-PR body for one milestone view branch."""
import json
import subprocess
import sys

S = "/private/tmp/claude-503/-Users-kodezart-Projects-kodezart/f325c30b-eb65-4b5e-83ca-1ef5846ade2d/scratchpad/"
REPO = "/Users/kodezart/Projects/kodezart"
DONOR = "eae9a940e9682a09680b4adf49781f3743c12754"
INITIATIVE = ("https://linear.app/duckburg/initiative/kodezart-v03-loop-orchestration-scopes-as-input-"
              "the-tracker-as-live-97c2509ef1b8/activity#initiative-update-0a70962b")

MS = {
    "M1": ("Scope input & port surface", ["KOD-73"], "v03/m1-scope-ports", "main", None),
    "M4": ("Criterion lifecycle & tracker state", ["KOD-76"], "v03/m4-criterion-lifecycle", "v03/m1-scope-ports", "M1"),
    "M3": ("Plan & walk", ["KOD-75", "KOD-105"], "v03/m3-plan-walk", "v03/m4-criterion-lifecycle", "M4"),
    "M2": ("Scope pass (groom · ticket · criteria)", ["KOD-74"], "v03/m2-organize", "v03/m3-plan-walk", "M3"),
    "M5": ("Deliver & terminate", ["KOD-77", "KOD-78"], "v03/m5-deliver-terminate", "v03/m3-plan-walk", "M3"),
    "M6": ("Audit", ["KOD-79"], "v03/m6-audit", "v03/m5-deliver-terminate", "M5"),
    "M7": ("Run supervisor", ["KOD-103"], "v03/m7-run-supervisor", "v03/m5-deliver-terminate", "M5"),
}


def git(*a):
    return subprocess.run(["git", "-C", REPO, *a], capture_output=True, text=True).stdout


def counts(base, head):
    ns = [l.split("\t") for l in git("diff", "--name-status", f"{base}...{head}").splitlines() if l]
    a = sum(1 for x in ns if x[0].startswith("A"))
    m = sum(1 for x in ns if x[0].startswith("M"))
    d = sum(1 for x in ns if x[0].startswith("D"))
    r = sum(1 for x in ns if x[0].startswith("R"))
    return a, m, d, r, len(ns)


def main(mile, head_sha, syntax_ok, checked):
    name, lanes, branch, base, parent = MS[mile]
    rep = json.load(open(S + f"cut_report_{mile}.json"))
    a, m, d, r, total = counts(f"origin/{base}" if base != "main" else "origin/main", head_sha)
    lane_links = "\n".join(f"- [{l}](https://linear.app/duckburg/issue/{l})" for l in lanes)
    body = f"""## {mile} — {name}

This branch is a **REVIEW VIEW**, not a working increment. It is cut from the donor commit
`eae9a940e9682a09680b4adf49781f3743c12754` by **symbol-level ownership**: every file whose file-level
owner is {mile} arrives as the donor blob, and every file shared with another milestone receives only the
symbols {mile} owns (member-wise for classes, per enum member, per TOML key/table, per Markdown section,
per `.env` key). It is stacked on `{base}` and is **expected NOT to be independently green** — it does not
have to type-check, import, or pass tests on its own, because a restriction of a coherent tree is not itself
a coherent tree. **CI on this branch is informational only.** Correctness is asserted on the union branch
`v03/union` (the merge of the leaves M2, M6 and M7), which is **tree-identical to the donor**; CI and
per-criterion grading happen there. Nothing here merges alone: the merge path is the union, after all seven
views have been reviewed. A view is a pure restriction of the donor — any edit that is not such a
restriction does not belong on this branch, including edits made to turn CI green.

### Lanes
{lane_links}

### Ownership inputs
- Origin branch: `archive/recovery-session-2026-09-13`, directory `ownership-map/`
- `ownership_map.json` — file-level owner / reason / split
- `ownership_facts_full.json` — per-path delta status, kind, imports
- `ownership_symbols.json` — symbol-level owner, kind and consumers
- `cut_specs.json` — per milestone and file: base, take_from_donor, remove, keep_main, new_at_this_milestone
- `unsplittable.json`, `files_created_early.json`
- Donor: `eae9a940e9682a09680b4adf49781f3743c12754` · baseline main: `4661a24b599d75503a997f3ce122f3ad2da77048`

### Re-derived file counts (`git diff --name-status origin/{base}...{head_sha[:8]}`)
| | count |
|---|---|
| files changed vs `{base}` | {total} |
| added | {a} |
| modified | {m} |
| deleted | {d} |
| renamed | {r} |

Cut composition at this milestone: {len(rep['whole'])} whole donor files, {len(rep['shared'])} shared files
spliced symbol-by-symbol, {len(rep['created'])} files created here carrying only this milestone's symbols,
{len(rep['deleted'])} deletions, {len(rep['renamed'])} renames, {len(rep['import_blocks'])} Python files whose
import block was set to the donor's (first milestone on this path to touch the file; accepted noise).

Syntax check (`ast.parse`) on every Python file written by this cut: **{checked} files, {'all parse' if syntax_ok else 'FAILURES — see notes'}**.

### Initiative
{INITIATIVE}

🤖 Generated with [Claude Code](https://claude.com/claude-code)
"""
    open(S + f"pr_body_{mile}.md", "w").write(body)
    print(S + f"pr_body_{mile}.md")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2], sys.argv[3] == "1", int(sys.argv[4]))

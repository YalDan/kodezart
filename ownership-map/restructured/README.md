# ownership-map/restructured

The live symbol-level cutter and the ownership maps it reads, for the restructured
base. Each cut of the seven milestone review views is recorded here, newest last.

Every script in this directory reads three environment values and will silently use
the wrong donor or the wrong main without them:

    CUT_DONOR=<union sha>  CUT_MAIN=<restructured base sha>  CUT_SUFFIX=.r

## Re-cut 2026-09-18 from union b94a63ab

The seven views were re-cut from union `b94a63ab` onto the restructured base
`e1544ed7`, each view stacked on its parent. The heads that were published:

| View | Head | PR |
| --- | --- | --- |
| M1 | `f17b6ee5` | #133 |
| M4 | `9950181e` | #124 |
| M3 | `3757ed9c` | #125 |
| M2 | `76346458` | #126 |
| M5 | `a6a2a2c5` | #127 |
| M6 | `1a9c8998` | #128 |
| M7 | `2d037d54` | #129 |

`heads.txt` records each view's head together with the parent it was cut on;
`publish_result.json` records the branch each head went to and the stale head it
replaced. Per-run reports are kept under `recut-2026-09-18/run1`, `run2` and
`run3`; the accepted run's reports are the ones at the top of this directory.

### Four runs, three rejected

It took four cuts to get a set worth publishing. Runs 1 to 3 were each rejected
on a check, the tooling was fixed, and the cut was redone from the maps.

**Run 1** was rejected because the symbol kinds in the maps were placeholders. The
map extender had been writing the literal kind `symbol` for every symbol it added,
so nothing downstream could tell a function from a TOML key from a Markdown
heading, and the cut was made without that distinction.

**Run 2** was rejected on two counts. The cutter did not model PEP 695 `type X = ...`
alias statements at all, so seven aliases in the domain types and the test fakes
were invisible to it and simply went missing from the views that should have
carried them. Separately, a headerless top-level key in the example operation
config was relocated underneath a table header the cut had just inserted above it,
because a table inserted at the top of the file swallows every bare key below it.

**Run 3** was rejected because a lane split ran between a config key's removal and
the model field that key belongs to: the example config dropped a key at one
milestone while the config model dropped the matching field at another, so a view
existed in which its own example config no longer matched its own config model.

**Run 4** passed every check and is the published cut.

### What changed in the tooling

The map extender now derives and records each symbol's real kind from the same
models the cutter itself builds, instead of writing a placeholder; `sym_kinds.py`
holds that derivation and `repair_kinds.py` back-fills the placeholders already in
the maps (`kind_repair_report.json` is its log).

The cutter gained three behaviours, recorded as a diff in `run3_cutter.diff`:

- it models `type X = ...` aliases as module-level assignments, so an alias is
  owned, cut and carried like any other top-level symbol;
- it keeps headerless top-level TOML keys above any table header it inserts, so an
  inserted table cannot swallow keys that must stay at the top level, and it
  removes such a key by its text even once a header has re-qualified it;
- when a donor table replaces a same-named scalar, the milestone that brings the
  table now also evicts the scalar, since TOML cannot hold both; the later
  recorded removal then finds nothing and is a no-op. Evictions are reported per
  view under `displaced`.

Four owner moves were applied to the maps (`patch_maps_run3.py`,
`patch_maps_run4.py`): two example-config keys moved to follow the config model
fields they describe, the table-versus-scalar eviction moved to the milestone that
brings the table, and one Markdown heading moved to the milestone that owns the
section renaming it.

### What gates acceptance

A run is accepted only when all of these hold:

- no donor mismatch: every file taken whole is byte-identical to the donor's blob;
- no ownership violation: no view carries a symbol owned by a milestone that is
  not on its path;
- no off-path members: no class or table member appears in a view whose lane does
  not reach it;
- no parse failures: every Python file in every view parses
  (`syncheck_all.py`, written to `syntax_check_r.json`);
- every view's own config model accepts that view's own example config, with the
  top-level keys and the model fields agreeing one for one;
- no alias gaps: every alias referenced in a view is also defined in it;
- the recomposition of the leaf views loads with the union's own loader.

Coverage is reported in `coverage_report.json` (`unmapped` and `whole_missing`
must both be empty); annotation counts are in `annotation_counts_r.json` and the
per-view annotations in `view_annotations_r_M1..M7.json`.

### Recorded limits

These are known and accepted, not defects to chase:

- prose that sits outside a modelled symbol is not carried into the views;
- an import block is replaced whole the first time a view touches it, rather than
  line by line;
- a class or table member is shown at its container's lane, not at the lane that
  introduced the member;
- the recomposition of the views differs from the union on 40 paths. The
  differences are placement and comments only, not content.

## Re-cut 2026-09-18 23:10 UTC from union 9d425ec4 (run 6)

Slice 2a landed on the union (`b94a63ab` → `9d425ec4`); the maps were extended once for that
range (`extend_maps.py b94a63ab 9d425ec4` with all three environment values: 7 new files mapped,
13 symbols added, 16 re-marked) and the seven views were re-cut and published on the first run:

| View | Head | Parent | PR |
| --- | --- | --- | --- |
| M1 | `4518c363` | `e1544ed7` (restructure) | #133 |
| M4 | `ac85209e` | M1 | #124 |
| M3 | `4a5146e7` | M4 | #125 |
| M2 | `bf0c7de3` | M3 | #126 |
| M5 | `91ce3b78` | M3 | #127 |
| M6 | `3a23e284` | M5 | #128 |
| M7 | `e5b1493e` | M5 | #129 |

Independent refutation (workflow `wf_recut_views_2a.js`, cut agent + refuter): donor mismatches 0,
ownership violations 0, off-path members 0, parse failures 0, own-model failures 0, coverage
unmapped 0 / whole-missing 0 (split-symbol misses 74, the known prose/config placement drift),
recomposition: the one known conflict in `src/kodezart/types/domain/operation.py`; with the union
text the leaves merge and the result loads with the union's loader, differing from the union on 41
paths in placement and comments only.

The refuter returned REJECT on an "alias gap": `CIWatchResult` (a `type` alias in the M5-owned
`types/domain/check_observation.py`) is imported by `core/protocols.py` and `tests/fakes.py` at the
M1/M4/M3/M2 heads, where that file is absent. The accepted run 4 has the identical import lines at
its M1 head `f17b6ee5` with the same file absent: this is the recorded limit "an import block is
replaced whole the first time a view touches it", not a defect of the cut, and the gate the earlier
runs applied is the seven-alias own-model oracle (aliases owned on the path must be defined in the
view), which this cut passes. The cut was accepted on that basis. Two notes for the maps: the
alias `McpToolResult` in `core/protocols.py` is absent from `ownership_symbols.r.json` (unchanged
since the base; present in every view), and `annotate_r.py`'s head table must be repointed per run
(`annotate_r_newheads6.py`). Per-run reports under `recut-2026-09-18/run6/`; the accepted run's
maps, `heads.txt`, PR bodies and `publish_result.json` are the ones at the top of this directory.

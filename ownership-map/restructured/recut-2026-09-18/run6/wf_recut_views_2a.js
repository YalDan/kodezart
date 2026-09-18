export const meta = {
  name: 'recut-review-views-2a',
  description: 'Re-cut the seven v0.3 review views from the new union head against the restructured base with the extended ownership maps; independent refutation (blob equality, ownership, off-path members, parse, own-model oracle, alias gaps, recomposition); no pushes, no tracker writes',
  phases: [
    { title: 'Cut', detail: 'one opus worker materialises M1→M4→M3→{M2,M5}→{M6,M7} sequentially, commits each view, runs the four local checks' },
    { title: 'Verify', detail: 'independent refuter re-derives every acceptance gate against the union tree' },
  ],
}
const { donor, base, scratch, repo, suffix, previous } = args
const PY = '/Users/kodezart/.local/bin/python3.12'
const ORDER = [['M1', 'v03/restructure', 'v03/m1-scope-ports'], ['M4', 'v03/m1-scope-ports', 'v03/m4-criterion-lifecycle'], ['M3', 'v03/m4-criterion-lifecycle', 'v03/m3-plan-walk'], ['M2', 'v03/m3-plan-walk', 'v03/m2-organize'], ['M5', 'v03/m3-plan-walk', 'v03/m5-deliver-terminate'], ['M6', 'v03/m5-deliver-terminate', 'v03/m6-audit'], ['M7', 'v03/m5-deliver-terminate', 'v03/m7-run-supervisor']]
const CUT_SCHEMA = { type: 'object', properties: {
  views: { type: 'array', items: { type: 'object', properties: { milestone: { type: 'string' }, branch: { type: 'string' }, parent_branch: { type: 'string' }, parent_sha: { type: 'string' }, head_sha: { type: 'string' }, report_summary: { type: 'string' }, skipped: { type: 'array', items: { type: 'string' } } }, required: ['milestone', 'branch', 'parent_branch', 'parent_sha', 'head_sha', 'report_summary', 'skipped'] } },
  syncheck: { type: 'string' }, coverage: { type: 'string' }, own_model: { type: 'string' }, annotate: { type: 'string' },
  union_recomposed_sha: { type: 'string' }, recomposition_tree_identical: { type: 'boolean' }, recomposition_diff_count: { type: 'number' }, notes: { type: 'string' } },
  required: ['views', 'syncheck', 'coverage', 'own_model', 'annotate', 'union_recomposed_sha', 'recomposition_tree_identical', 'recomposition_diff_count', 'notes'] }
const VERIFY_SCHEMA = { type: 'object', properties: {
  donor_mismatches: { type: 'number' }, ownership_violations: { type: 'number' }, off_path_members_present: { type: 'number' }, parse_failures: { type: 'number' },
  own_model_failures: { type: 'number' }, alias_gaps: { type: 'number' }, unmapped_paths: { type: 'number' }, whole_missing: { type: 'number' },
  recomposition_tree_identical: { type: 'boolean' }, recomposition_diff_files: { type: 'array', items: { type: 'string' } }, recomposition_loads: { type: 'boolean' },
  per_view: { type: 'array', items: { type: 'object', properties: { milestone: { type: 'string' }, head_sha: { type: 'string' }, files_changed_vs_parent: { type: 'number' }, checked_whole_files: { type: 'number' }, mismatches: { type: 'array', items: { type: 'string' } } }, required: ['milestone', 'head_sha', 'files_changed_vs_parent', 'checked_whole_files', 'mismatches'] } },
  verdict: { type: 'string', enum: ['ACCEPT', 'REJECT'] }, reason: { type: 'string' } },
  required: ['donor_mismatches', 'ownership_violations', 'off_path_members_present', 'parse_failures', 'own_model_failures', 'alias_gaps', 'unmapped_paths', 'whole_missing', 'recomposition_tree_identical', 'recomposition_diff_files', 'recomposition_loads', 'per_view', 'verdict', 'reason'] }

phase('Cut')
const cut = await agent(`You re-cut the seven v0.3 REVIEW VIEWS. A review view is a restriction of the union tree to one milestone's files and symbols, stacked on its parent view; views are not independently green by design and must never be edited to become green. Repository ${repo}; scratch directory ${scratch}. Do not push. Do not write to Linear, GitHub or Notion. Do not touch any worktree under ${scratch} other than the ones you create below, and do not touch the worktrees gate-1a, gate-1b, w1, w2, w3 under the parent scratchpad. Do not run extend_maps.py (the maps are already extended to the donor).

Inputs (all already prepared):
- Union (donor) commit: ${donor}. Restructured base commit (branch v03/restructure): ${base}. Previous union the maps were extended from: ${previous}.
- Cutter: ${scratch}/cut_views_r.py — run it as \`CUT_DONOR=${donor} CUT_MAIN=${base} CUT_SUFFIX=.r ${PY} ${scratch}/cut_views_r.py <MILESTONE> <worktree>\`. EVERY script in ${scratch} needs those three environment values, every time. The cutter reads ownership_map.r.json, ownership_facts_full.r.json, ownership_symbols.r.json and cut_specs.r.json and materialises the milestone's view INTO the given worktree, which must already be checked out at the parent's head. It writes cut_report_<MILESTONE>.json into ${scratch}; capture its stdout to ${scratch}/cutlog_<MILESTONE>.txt.

Procedure, strictly in this order because each view stacks on its parent:
${ORDER.map(([m, p, b]) => `- ${m}: parent branch ${p} → view branch ${b}`).join('\n')}
For each milestone: \`git -C ${repo} worktree add --detach ${scratch}/wt3-view-<m lowercase> <parent head sha>\` (for M1 the parent is v03/restructure at ${base}; for later ones the parent is the NEW view head you just created). Then \`git -C <wt> checkout -b <view branch>${suffix}\`, run the cutter, inspect its printed summary and cut_report (any "SKIP" lines must be reported verbatim in skipped), then \`git -C <wt> add -A && git -C <wt> commit -q -m "<M> review view re-cut from union ${donor.slice(0, 8)} on restructured base"\` with the trailer line "Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>". Record parent sha and new head sha. Do not run tests; do not edit any file by hand; if the cutter raises, stop and report the traceback in notes.

After all seven, run the four local checks with the same three environment values and report each one's printed result verbatim (truncate to 600 characters each):
1. \`${PY} ${scratch}/syncheck_all.py\` (every Python file in every view parses; expect bad: 0) → syncheck.
2. \`${PY} ${scratch}/coverage_check.py\` (expect unmapped: 0 and whole_missing: 0) → coverage. It reads the worktrees ${scratch}/wt3-view-m1..m7 by those exact names.
3. \`${PY} ${scratch}/own_model_check.py\` (each view's own config model accepts its own example config; the M4 marker prefixes; the type aliases) → own_model.
4. \`${PY} ${scratch}/annotate_r.py\` → annotate. If it fails because its BR table names stale heads, copy it to ${scratch}/annotate_r_newheads6.py, edit ONLY the head table in the copy to the seven new heads, run the copy, and say so in notes.
Then write ${scratch}/heads.txt with PARENT_<M>=<sha> / HEAD_<M>=<sha> lines for the seven views (same shape as the existing file; overwrite it).

Recomposition check: \`git -C ${repo} worktree add --detach ${scratch}/wt3-union <M2 head>\`, then \`git -C ${scratch}/wt3-union merge --no-edit <M6 head>\` and \`merge --no-edit <M7 head>\`. If a merge conflicts, do not resolve by hand: record the conflicting paths in notes and set recomposition_tree_identical=false. Otherwise \`git -C ${repo} diff --name-only <wt3-union head> ${donor} | wc -l\` → recomposition_diff_count and recomposition_tree_identical = (count == 0). Placement/comment drift on some paths is a known, accepted property of member-wise splicing — report the count, do not chase it.

Return the structured result only.`, { label: 'cut:views', phase: 'Cut', schema: CUT_SCHEMA, model: 'opus', effort: 'high' })
if (!cut) return { error: 'cut agent returned nothing' }
log(`cut: ${cut.views.length} views; recomposition diff count=${cut.recomposition_diff_count}`)

phase('Verify')
const verdict = await agent(`You are an independent refuter for a re-cut of the seven v0.3 review views. Repository ${repo}; scratch ${scratch}; union commit ${donor}; restructured base ${base}. Read-only except for creating your own scratch worktrees under ${scratch}/wt3-verify-*; remove them when done; do not push; do not write to Linear, GitHub or Notion; do not touch the wt3-view-* or wt3-union worktrees the cutter made (read their commits by sha instead). Every script in ${scratch} needs CUT_DONOR=${donor} CUT_MAIN=${base} CUT_SUFFIX=.r in its environment; use ${PY}.

Claim: ${JSON.stringify(cut, null, 1)}

Ownership inputs: ${scratch}/ownership_map.r.json (file → owner milestone, "split" for shared files), ${scratch}/ownership_symbols.r.json (path → symbol → owner/kind), ${scratch}/cut_specs.r.json (milestone → path → keep_main/take_from_donor/remove/new_at_this_milestone). Milestone paths (which milestones are "on path" for a view): M1:[M1], M4:[M1,M4], M3:[M1,M4,M3], M2:[M1,M4,M3,M2], M5:[M1,M4,M3,M5], M6:[M1,M4,M3,M5,M6], M7:[M1,M4,M3,M5,M7].

Re-derive with git plumbing and your own code (do not merely re-run the cutter's own checks; where you do run ${scratch}/own_model_check.py or ${scratch}/syncheck_all.py, ALSO re-derive the same fact another way and report both):
1. Donor mismatches: for every view head, for every file whose file-level owner is on the view's path and which is not a split file, the blob at the view head must equal the blob at ${donor} (git rev-parse <sha>:<path>). Count mismatches and list up to 20.
2. Ownership violations: for every view head, every file changed versus its parent sha (git diff --name-only parent head) must be either owned by a milestone on the path, or a split file with symbols owned on the path. Count violations, list up to 20.
3. Off-path members: for each view and each split file, parse the view's blob and the donor's blob with ast (Python files only; model PEP 695 \`type X = ...\` statements as module-level definitions) and check that no top-level or class-level definition whose owner (per ownership_symbols.r.json) is NOT on the view's path is present in the view blob. Count, list up to 20. Count Python files that fail to parse separately (parse_failures) over ALL Python files of every view, not only split ones.
4. Own-model oracle: for each view, load docs/operation.example.toml with tomllib and compare its top-level keys with the fields of OperationConfig in src/kodezart/types/domain/operation.py at that view head (a key with no field, or a required field with no key, is a failure); count own_model_failures.
5. Alias gaps: for each view, every PEP 695 alias name referenced in any Python file of the view must be defined in the view; count alias_gaps.
6. Coverage: every path in ${donor}'s tree (git ls-tree -r --name-only) that is not in ownership_map.r.json → unmapped_paths; every path that the map owns whole (no split) but that is absent from the head of the view that should carry it → whole_missing.
7. Recomposition: in your own worktree, merge the M2, M6 and M7 heads as the claim describes and compare the resulting tree with ${donor}: list every differing path (git diff --name-only, up to 60) in recomposition_diff_files and set recomposition_tree_identical accordingly. Then check that the recomposed tree LOADS: run \`uv run python -c "import kodezart.composition.engine"\` inside that worktree (uv sync --all-groups --locked first) and set recomposition_loads. Placement or comment drift from member-wise splicing is NOT by itself a rejection; a conflicting merge is reported the same way with the conflicting paths.
8. Per view: files changed vs parent, number of whole files checked, mismatches.
ACCEPT only if donor_mismatches == 0, ownership_violations == 0, off_path_members_present == 0, parse_failures == 0, own_model_failures == 0, alias_gaps == 0, unmapped_paths == 0, whole_missing == 0 and recomposition_loads == true; the recomposition tree-identity result is reported for the orchestrator's decision and does not by itself change the verdict. Return the structured verdict only.`, { label: 'verify:views', phase: 'Verify', schema: VERIFY_SCHEMA, effort: 'high' })
return { cut, verdict }

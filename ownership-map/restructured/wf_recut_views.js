export const meta = {
  name: 'recut-review-views',
  description: 'Re-cut the seven v0.3 review views from the new union head against the restructured base with the translated ownership maps; independent verification of tree consistency and recomposition; no pushes, no tracker writes',
  phases: [
    { title: 'Cut', detail: 'one opus worker materialises M1→M4→M3→{M2,M5}→{M6,M7} sequentially and commits each view' },
    { title: 'Verify', detail: 'independent refuter re-derives blob equality, ownership, and recomposition against the union tree' },
  ],
}
const { donor, base, scratch, repo } = args
const ORDER = [['M1', 'v03/restructure', 'v03/m1-scope-ports'], ['M4', 'v03/m1-scope-ports', 'v03/m4-criterion-lifecycle'], ['M3', 'v03/m4-criterion-lifecycle', 'v03/m3-plan-walk'], ['M2', 'v03/m3-plan-walk', 'v03/m2-organize'], ['M5', 'v03/m3-plan-walk', 'v03/m5-deliver-terminate'], ['M6', 'v03/m5-deliver-terminate', 'v03/m6-audit'], ['M7', 'v03/m5-deliver-terminate', 'v03/m7-run-supervisor']]
const CUT_SCHEMA = { type: 'object', properties: {
  views: { type: 'array', items: { type: 'object', properties: { milestone: { type: 'string' }, branch: { type: 'string' }, parent_branch: { type: 'string' }, parent_sha: { type: 'string' }, head_sha: { type: 'string' }, report_summary: { type: 'string' }, skipped: { type: 'array', items: { type: 'string' } } }, required: ['milestone', 'branch', 'parent_branch', 'parent_sha', 'head_sha', 'report_summary', 'skipped'] } },
  union_recomposed_sha: { type: 'string' }, recomposition_tree_identical: { type: 'boolean' }, notes: { type: 'string' } },
  required: ['views', 'union_recomposed_sha', 'recomposition_tree_identical', 'notes'] }
const VERIFY_SCHEMA = { type: 'object', properties: {
  donor_mismatches: { type: 'number' }, ownership_violations: { type: 'number' }, off_path_members_present: { type: 'number' },
  recomposition_tree_identical: { type: 'boolean' }, recomposition_diff_files: { type: 'array', items: { type: 'string' } }, parse_failures: { type: 'number' },
  per_view: { type: 'array', items: { type: 'object', properties: { milestone: { type: 'string' }, head_sha: { type: 'string' }, files_changed_vs_parent: { type: 'number' }, checked_whole_files: { type: 'number' }, mismatches: { type: 'array', items: { type: 'string' } } }, required: ['milestone', 'head_sha', 'files_changed_vs_parent', 'checked_whole_files', 'mismatches'] } },
  verdict: { type: 'string', enum: ['ACCEPT', 'REJECT'] }, reason: { type: 'string' } },
  required: ['donor_mismatches', 'ownership_violations', 'off_path_members_present', 'recomposition_tree_identical', 'recomposition_diff_files', 'parse_failures', 'per_view', 'verdict', 'reason'] }

phase('Cut')
const cut = await agent(`You re-cut the seven v0.3 REVIEW VIEWS. A review view is a restriction of the union tree to one milestone's files and symbols, stacked on its parent view; views are not independently green by design and must never be edited to become green. Repository ${repo}; scratch directory ${scratch}. Do not push. Do not write to Linear, GitHub or Notion. Do not touch the worktrees h-union, wt2-union, wt-restructure or any wt2-view-* worktree.

Inputs (all already prepared):
- Union (donor) commit: ${donor}. Restructured base commit (branch v03/restructure): ${base}.
- Cutter: ${scratch}/cut_views_r.py — run it as \`CUT_DONOR=${donor} CUT_MAIN=${base} CUT_SUFFIX=.r /Users/kodezart/.local/bin/python3.12 ${scratch}/cut_views_r.py <MILESTONE> <worktree>\`. It reads ownership_map.r.json, ownership_facts_full.r.json, ownership_symbols.r.json and cut_specs.r.json (paths already translated to the new package layout) and materialises the milestone's view INTO the given worktree, which must already be checked out at the parent's head. It writes cut_report_<MILESTONE>.json into ${scratch}.

Procedure, strictly in this order because each view stacks on its parent:
${ORDER.map(([m, p, b]) => `- ${m}: parent branch ${p} → view branch ${b}`).join('\n')}
For each milestone: \`git -C ${repo} worktree add --detach ${scratch}/wt3-view-<m> <parent branch head sha>\` (for M1 the parent is v03/restructure at ${base}; for later ones the parent is the NEW view branch you just created, at its new head). Then \`git -C <wt> checkout -b <view branch>-recut\` (suffix -recut; the orchestrator renames on push), run the cutter, inspect its printed summary and cut_report (any "SKIP" lines must be reported verbatim), then \`git -C <wt> add -A && git -C <wt> commit -q -m "<M> review view re-cut from union ${donor.slice(0, 8)} on restructured base"\` with the trailer "Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>". Record parent sha and new head sha. Do not run tests; do not edit any file by hand; if the cutter raises, stop and report the traceback in notes.

After all seven: recomposition check. Create \`git -C ${repo} worktree add --detach ${scratch}/wt3-union <M2 head>\`, then \`git -C ${scratch}/wt3-union merge --no-edit <M6 head>\` and then \`merge --no-edit <M7 head>\` (this mirrors how v03/union was first built: the merge of the leaves M2, M6, M7). If a merge conflicts, do not resolve by hand: record the conflicting paths in notes and set recomposition_tree_identical=false. Otherwise compare trees: \`git -C ${repo} diff --stat <wt3-union head> ${donor}\` must be empty; report the sha and whether it is tree-identical (\`git diff --quiet\` exit 0).

Return the structured result only.`, { label: 'cut:views', phase: 'Cut', schema: CUT_SCHEMA, model: 'opus', effort: 'high' })
if (!cut) return { error: 'cut agent returned nothing' }
log(`cut: ${cut.views.length} views; recomposition identical=${cut.recomposition_tree_identical}`)

phase('Verify')
const verdict = await agent(`You are an independent refuter for a re-cut of the seven v0.3 review views. Repository ${repo}; scratch ${scratch}; union commit ${donor}; restructured base ${base}. Read-only except for creating your own scratch worktrees under ${scratch}/wt3-verify-*; do not push; do not write to Linear, GitHub or Notion; do not touch the wt3-view-* or wt3-union worktrees the cutter made (read their commits by sha instead).

Claim: ${JSON.stringify(cut, null, 1)}

Ownership inputs: ${scratch}/ownership_map.r.json (file → owner milestone, "split" for shared files), ${scratch}/ownership_symbols.r.json (path → symbol → owner/kind), ${scratch}/cut_specs.r.json (milestone → path → keep_main/take_from_donor/remove/new_at_this_milestone). Milestone paths (which milestones are "on path" for a view): M1:[M1], M4:[M1,M4], M3:[M1,M4,M3], M2:[M1,M4,M3,M2], M5:[M1,M4,M3,M5], M6:[M1,M4,M3,M5,M6], M7:[M1,M4,M3,M5,M7].

Re-derive, with git plumbing and /Users/kodezart/.local/bin/python3.12:
1. Donor mismatches: for every view head, for every file whose file-level owner is on the view's path and which is not a split file, the blob at the view head must equal the blob at ${donor} (git rev-parse <sha>:<path>). Count mismatches and list up to 20.
2. Ownership violations: for every view head, every file changed versus its parent sha (git diff --name-only parent head) must be either owned by a milestone on the path, or a split file with symbols owned on the path. Count violations, list up to 20.
3. Off-path members: for each view and each split file, parse the view's blob and the donor's blob with ast (Python files only) and check that no top-level or class-level definition whose owner (per ownership_symbols.r.json) is NOT on the view's path is present in the view blob. Count, list up to 20. Count Python files that fail to parse separately (parse_failures).
4. Recomposition: in your own worktree, merge the M2, M6 and M7 heads as the claim describes and compare the resulting tree with ${donor}: list every differing path (git diff --name-only, up to 60) in recomposition_diff_files and set recomposition_tree_identical accordingly. Ordering or comment drift from member-wise splicing is expected to show up here and is NOT by itself a rejection; a conflicting merge is reported the same way with the conflicting paths.
5. Per view: files changed vs parent, number of whole files checked, mismatches.
ACCEPT only if donor_mismatches == 0, ownership_violations == 0, off_path_members_present == 0 and parse_failures == 0; the recomposition result is reported for the orchestrator's decision and does not by itself change the verdict. Return the structured verdict only.`, { label: 'verify:views', phase: 'Verify', schema: VERIFY_SCHEMA, model: 'opus', effort: 'high' })
return { cut, verdict }

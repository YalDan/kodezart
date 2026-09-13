# Independent criterion-label ownership review

Verdict: **REQUEST CHANGES**, exact source `246da869a5847a513932d4fc2ca3782af2782385`, parent `6f0fbff599f75d2fbd20e89a89e0505ffdfc98bc`.

Review worktree: `/private/tmp/kodezart-v03-criterion-label-independent-review`, detached source unchanged. Only independent test file added. No canonical, source, state, PR or initiative writes. Astra ultra requested/inherited; effective metadata unverified.

Requirements independently fetched: KOD-383 current Do covers criterion body/state/labels with one surface; KOD-386 requires typed refusal for unheld or expired writes. Existing unheld compatibility is not claimed complete.

## Findings

1. Missing `issue_labels['criterion']` lets an actual criterion be projected as an ordinary issue. `classification_surface` then selects the caller's ISSUE_LABEL_SET while another job holds the native CRITERION_SUB_ISSUE grant. The actual backend save succeeds. Mapping only the requested decision classification is insufficient to establish the surface kind.
2. Native `get_issue.labels` omission has the same authority failure with mappings configured. `read_current` calls lenient `read_issue`; the original wire defaults labels empty. The save is issued and mutates the criterion before final readback raises. Unknown classification cannot safely mean ordinary issue.

Minimal existing-source closure: use strict `read_planning_issue` (LinearPlanningIssueWire requires labels and relations) plus the already present identity check; establish the configured criterion mapping before choosing a surface. Apply that premise to escalation's pre-acquisition read too. `require_scope_plan_reads` already checks criterion+decision mappings (presence); `require_issue_classification_reads` is broader and also requires tracker. Do not globally change read_issue or add another lease algorithm. A mapping must be nonblank if consumed directly. Root owns correction.

## Executable evidence

Independent test: `tests/tracker/test_criterion_label_independent.py`, SHA256 `c2aaeb2a4fe79351cca3c20785d05a20031ebadb0b1b49c349d63ec338c7e5e0`.

- First diagnostic: `uv run pytest -q tests/tracker/test_criterion_label_independent.py tests/tracker/test_criterion_label_surface.py tests/tracker/test_classification_authority.py` — 4 failed, 13 passed, 1.88s. Two were review-harness mistakes accessing error.surface; exact original saved as `criterion-label-independent-original-diagnostic.py`. Functional findings are the two metadata cases, not all four failures.
- Corrected command: same three modules plus `tests/tracker/test_lane_records.py tests/services/test_run_surface_lease.py` — **2 failed, 111 passed, 6.32s**. Metadata assertions corrected only to existing primitive surface_kind/scope_kind/scope_key fields. Both negative authority oracles unchanged. Log `criterion-label-independent-corrected-246da86.log`.
- `uv run ruff check tests/tracker/test_criterion_label_independent.py` — passed, `criterion-label-independent-ruff.log`.
- `git diff --check` — passed; git status shows only the independent new test.
- Strict source check dispatched separately; result in `criterion-label-independent-mypy-246da86.log` (pending at initial envelope).

All probes use actual LinearMcpTracker + RunSurfaceLease, with only MCP external input/clock interception. Four paired controls cover criterion/noncriterion legitimate writers and expiry during the final awaited issue read. Existing seven late lease/retry/readback controls and four candidate controls retained and executed.

## Eight lenses and type impact

- SOLID: single pure surface-selection function is appropriate, but producer observation contract is insufficient.
- DRY: adapter, fake and escalation share that selector; existing strict reader is the smallest repair.
- Hexagonal architecture: external MCP boundary is doubled; actual owner and adapter execute.
- KISS: no additional ownership machinery needed.
- Typed agent calls instead of semantic heuristics: no agent calls or prose inference introduced; configured semantic label mapping must be known.
- Official framework practices (version-matched): no new framework mechanism; executable checks use the locked Python 3.12/Pydantic environment.
- Type safety: interface annotations remain typed; the behavioral projection currently changes unknown native classification into an unjustified known surface. Acceptance withheld.
- Repository hygiene: immutable source, isolated probes, all diagnostics retained, no unrelated edits.

Residual limits: snapshot reads do not provide CAS/fencing; cached grant observations with a final synchronous clock check cannot establish absence of a new unseen rival. Holder-less classification compatibility is inherited and outside this corrective acceptance. No whole L1 completion claim.

Own public evidence: https://linear.app/duckburg/issue/KOD-386#comment-9a49dd56-8d3c-4bc7-b94b-ac2bd2c32235

Integration: hold 246da86 until correction is frozen and the unchanged two negative/four paired probes plus original controls are independently rerun. Do not count downstream consumption of this prerequisite as acceptance.


Corrective review ce2751e95d7cf62051f3d257f0e02dd2d822dd2c

Accepted bounded correction. Isolated cherry 008ff18 has identical candidate tree; source remains untouched. Original six probes SHA256 c2aaeb2a4fe79351cca3c20785d05a20031ebadb0b1b49c349d63ec338c7e5e0 unchanged. Same affected command: 113 passed in 3.99s (criterion-label-independent-ce2751e.log). Strict three-file check: Success no issues found (criterion-label-independent-mypy-ce2751e.log); baseline also clean. Existing strict planning reader + nonblank mapping guard eliminate both observable unsafe saves. Three prior fixture mapping additions, assertion AST unchanged. Global read_issue unchanged. No new public type or port. Inherited optional-holder and observed-snapshot/nonfencing residuals remain. Root may serialize both commits; no review source changes.

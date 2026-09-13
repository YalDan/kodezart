L2 independent architecture/type and consumer review — REQUEST CHANGES

Exact reviewed source: 161ca93fd9fb83d64516ee11b8ed53944a9a517e, parent fd8686d7e3ae68f9159c26b723b2ef58f698c30d. Delta: 49 files, 3339 insertions / 171 deletions. git binary diff SHA256: 423e90ff9746afb06ed3c38602b092490b97e180debaf9a1ee38551e2f833305. Donor was inspected clean before a detached checkout was created at /private/tmp/kodezart-v03-organize-native-review. No source or existing oracle edits. The only review checkout addition is tests/chains/test_organize_native_independent.py, SHA256 1004b7841ccf8d32e72e3e1c3767357120a2923ebe9c409c47c0b6beeb2104b9. Requested Astra ultra inherited; effective runtime configuration unverified.

Requirements first

Read live KOD-74, KOD-368, KOD-820 and KOD-475 bodies and current comments before inspecting the implementation diff and executable tests. KOD-74's September 1 amendments make Organize one pre-approval breadth-first preparation pass: scope approval ends it, it creates native criterion children and never ticks completion. The parent remains current specification; a leaf must not contradict the parent's Fix. KOD-368 requires exact replay and real structural capabilities; absent graph capabilities cannot be represented by prose or invented decisions. KOD-820's September 12 superseding implementation-scope comment authorizes the existing canonical reread → fresh independent WriteBackFinding → bounded repair contract, preserving cited refutation evidence. KOD-475 requires actual configured bound identity/value/rounds and surviving findings to remain data for the terminal owner. No issue state is evidence of completion.

The author envelope organize-owner-review.md was read only after the source, existing tests and independent counterexamples, as explanation rather than an oracle. The author's explicit partial-capability and scheduler-registration limitations are retained below.

Blocking findings

F1 — P1: criterion creation does not validate the parent revision used by its author.

services/organize_owner.py:374-440 derives missing children from a proposed criteria set and later checks the phase gate/approval and child-set lease, but never compares the current parent revision with ProposedWrite.revision. OrganizeAuthor has read and retained that exact revision. Body writes correctly use expected=proposal.revision.issue.body; criterion writes have no equivalent provenance guard. Changing the parent's body while the criteria-author session is awaited still mints the criterion authored against the old parent. The subsequent semantic verifier does not make that stale write authorized.

Independent production regression test_criterion_mint_requires_the_parent_revision_its_author_read[True] records the actual author prompt, mutates only the external native parent description before returning the proposal, and observes a save_issue create. The unchanged-parent control passes with exactly one create. Required repair: validate the proposal's actual source revision at the covered criterion write boundary, after awaited gate/lease operations, and refuse/re-author on drift without creating stale children. Preserve actual current child-set replay; this is not a request for synthetic artifacts, a second ledger or a backend fencing guarantee.

F2 — P1: the Organize halt writer can mutate an already-approved scope.

services/organize_owner.py:500-570 sends admission escalation through LaneEscalationWriter without the Organize approval/phase guard used by ordinary body/criterion/marker writes. Approval arriving while the initial admission session is awaited does not invalidate a human_decision route. The run then writes marker comments and the decision classification after approval has ended Organize.

Independent production regression test_scope_approval_ends_organize_before_escalation_writes[True] adds only the external human approval label during the awaited assessment and returns an explicit legal human-decision admission arm. It observes save_comment calls and save_issue classification. The unapproved positive control correctly records its decision. Required repair: retain the scope/phase write authority at this real writer boundary, including relevant awaited gating/lease work. Do not weaken the shared escalation writer's other authorized callers or silently manufacture a human decision for an unavailable capability.

F3 — P2: exhausted write-back discards the finding that exhausted it.

services/organize_owner.py:779-794 keeps len(verified_write.rounds) for the bound but calls _halt with the earlier admission result and no findings. The exact reread artifact, latest independent verdict/evidence and cited_refs disappear from StageHaltReport and the escalation basis. types/domain/organize_owner.py:102-129 provides admission/spec/question slots but no actual write-back outcome, so a consumer receives an admission explanation even when independent landed-content verification caused the stop.

Independent production regression test_writeback_exhaustion_retains_its_actual_cited_refutation runs with bound 1. It returns a real typed refutation of the landed body citing tests/absent.py; escalation verification itself holds. The halt truthfully names loop=write_back and rounds=1, but neither its evidence nor its sole citation survives in report.halt JSON. Required repair: preserve the actual typed write-back artifact/findings and cited provenance in the bounded halt/raise handoff. Do not translate buildability into claim truth, synthesize citations, or only copy the round count.

Actual execution

All commands ran in the detached review tree at exact 161ca93. All logs below are in /private/tmp/kodezart-recovery-session. Overlapping selections are not additive unique coverage; no full suite run.

1. organize-independent-native-selection.log — 351 passed in 19.82s:
/Users/kodezart/.local/bin/uv run pytest -q tests/chains/test_organize_owner.py tests/chains/test_fresh_write_back_judge.py tests/chains/test_organize.py tests/chains/test_write_back_verifier.py tests/core/test_organize_settings.py tests/tracker/test_criterion_creation.py tests/domain/test_organize.py tests/domain/test_organize_routing.py tests/types/test_wire_schemas.py

2. organize-independent-native-adversarial.log — original first two counterexamples: 2 failed / 1 positive control passed in 0.85s:
/Users/kodezart/.local/bin/uv run pytest -q tests/chains/test_organize_native_independent.py

3. organize-independent-native-adversarial-final.log — after adding the approval boundary and its positive control: 3 failed / 2 passed in 1.04s. Same command as above. Assertions fail on real native write calls / actual returned report. No mocked Organize owner, admission, author, canonical verifier, agent service or tracker adapter is used; only executor responses and external board facts are controlled.

4. organize-independent-native-mypy.log — strict mypy passes in 11 source files:
/Users/kodezart/.local/bin/uv run mypy src/kodezart/chains/organize.py src/kodezart/chains/organize_author.py src/kodezart/chains/write_back_verifier.py src/kodezart/composition/organize.py src/kodezart/services/organize_owner.py src/kodezart/services/organize_tick.py src/kodezart/types/domain/organize.py src/kodezart/types/domain/organize_owner.py src/kodezart/types/domain/write_back.py src/kodezart/types/domain/scope_address.py src/kodezart/core/organize_settings.py

5. organize-independent-native-static.log — Ruff and format checks pass over every changed Python file (38 paths). Exact fully expanded commands are retained in that log; paths are the .py members of git diff --name-only 161ca93^..161ca93. git diff --check clean, tracked review source unchanged.

Eight scoped lenses

1. Contract/correctness: one owner implements assessment, read-only proposal, actual tracker write, canonical readback, independent judgment and dry verification. Current spec and approval authority are not maintained at every new writer (F1/F2). The halt loses the judgment behind its stop (F3). These are concrete failures, not unmet hypothetical full-L2 requirements.

2. SOLID/hexagonal: transport/state vocabulary and child creation live at the tracker adapter; sessions and selected workspaces are composed; domain identity/gap/routing remain pure. OrganizeOwner consumes existing admission, author, canonical verifier and escalation owners. No native MCP exception catch was added to orchestration. The owner is substantial (922 lines), but the review requests bounded boundary repairs rather than another orchestrator or generic framework.

3. DRY: existing organize_gap, three configured mandates, phase-role lookup and WriteBackVerifier are reused. FreshWriteBackJudge is a concrete implementation of the existing port, not a parallel loop. Tracker label reading shares approval identity hydration. Source reuse is appropriate; copying or reinterpreting write-back evidence at halt would undermine it.

4. KISS: explicit scope-to-repository binding and two required positive settings replace speculative defaults. No new run kind, scheduling cadence, queue or repository/scope cross-product. Unsupported hygiene remains an explicit unavailable variant rather than disguised prose mutation. Adding current-source checks and preserving typed halt evidence are the narrow repairs.

5. Type safety: admission improves from a permissive optional bag into Buildable/Refused/Unverifiable discriminated variants. Flat RootModel compatibility retains existing callers while the pure router narrows concrete refusal/unverifiable arms. OrganizeProposal is closed and names unavailable capabilities. ScopeRef extraction is a leaf dependency and its reexport preserves class identity/schema. WriteBackFinding requires nonblank cited references on refutation and rejects admission verdicts. Halt cause/bound consistency is runtime-validated, but the halt is still one model with optional payload slots; F3 demonstrates that it lacks the typed outcome needed by a real producer. This review does not claim all invalid halt states are unrepresentable merely because mypy passes.

6. Prompt/schema/framework: each added role has templates in both prompt sets and exact registered output/census bindings. The actual shared dispatch supplies session_id=None and evaluation tools, and the fresh write-back judge checks clean exact commit before and after its session. Refutation stays distinct from buildability. The actual tests exercise empty/blank-citation rejection, wrong semantic type, repository drift, fresh author repair, and genuine global schema absence. No decorative schema exception or digest refresh.

7. Lifecycle/concurrency/production interfaces: replay returns an existing child unchanged, including state/evidence; new children use the actual team's unique unstarted state and configured criterion label. The explicit child-set surface and holder are checked. Unknown create responses are not resent. Existing tests deliberately characterize the backend limit: a sent create may land after lease expiry/successor acquisition; this is not an atomic fence. F1/F2 concern avoidable stale authority before new writes, independent of that known backend limit. Scheduled registration remains a root-owned isolated passes/main integration task. The existing grooming identity and current trunk SHA are retained by OrganizeTick.

8. Evidence/hygiene: donor was clean and immutable when reviewed; detached source stayed unchanged; exact requirements preceded diff/tests, and author prose followed them. No canonical edits, issue states, Notion, pushes or repair-oracle changes. All three red probes have meaningful positive or mechanism controls. Review model/effort is requested only, not attested.

Explicit integration dependencies / limits

- Do not integrate 161ca93 as accepted until the three corrections are frozen and independently rerun with the unchanged probes above plus affected source/compatibility checks. Root assigned these repairs to the original source owner; this reviewer edits no source.
- Root separately owns passes.py/main.py and the narrow composition.organize preflight helper. Required actual boot wiring must validate settings, bindings, mandates and tracker before queue startup and use the same owner under the existing grooming schedule. Its tests/source are not in this reviewed commit.
- OrganizeTick.run currently logs the complete report, then raises OrganizeWriteRefusalError carrying only issue_key and a cause string (services/organize_tick.py:75-85). It does not expose the typed bound/findings through PassRun or its error. KOD-475/L6 typed halt consumption therefore remains incomplete unless an owned handoff preserves that report. Logging is not terminal arithmetic or a substitute for the typed handoff. This is communicated to the root integration owner; no extra scheduler source change is made here.
- KOD-368 graph parent/blockedBy/relatedTo/priority/milestone/split mutations and existing criterion edits remain unavailable. The rubric PromptKey path does not implement the native rubric-subissue source requirement. Do not mark full KOD-74/KOD-368 complete on this candidate.
- Native criterion key and current Check identity follow the existing nonblank shared identity ruling; no AC-number identity or criterion class is reintroduced. Existing children are replayed by current Check and are never ticked by Organize.
- Full live model judgment quality, vendor-atomic creation/fencing, public restart, durable terminal reporting and root's later scheduler boot are outside the executed evidence.

Authority links
https://linear.app/duckburg/issue/KOD-74
https://linear.app/duckburg/issue/KOD-74#comment-8b426002-6552-4d74-9d4d-091a4c8ae2f9
https://linear.app/duckburg/issue/KOD-74#comment-69978aaa-659f-463e-b91d-d8664620d26e
https://linear.app/duckburg/issue/KOD-368
https://linear.app/duckburg/issue/KOD-820#comment-3f13d3fc-e118-485b-bba2-7674a53b2d29
https://linear.app/duckburg/issue/KOD-475

Superseding corrective review — ee1d44bd540304f5effce4021f547768cfe5d82d

Verdict: APPROVE the bounded correction of F1/F2/F3. This supersedes the request-changes verdict for these three defects only. Full L2 completion, scheduled composition and typed terminal consumption remain outside this source approval.

The detached review source remained unchanged. Original independent test bytes were preserved under tests/chains/test_organize_native_review_original.py; both that copy and the saved original have SHA256 1004b7841ccf8d32e72e3e1c3767357120a2923ebe9c409c47c0b6beeb2104b9. Corrective diff 161ca93..ee1d44b SHA256: a1043a17246d53f24d774e5524f51442a08e126e6d3745001f4fea816f5618b1.

Actual commands/results:
- uv run pytest -q tests/chains/test_organize_native_review_original.py tests/chains/test_organize_owner.py tests/chains/test_organize_correction.py tests/chains/test_fresh_write_back_judge.py tests/chains/test_write_back_verifier.py: 46 passed in 1.53s. Log organize-independent-native-corrected-final.log.
- uv run pytest -q tests/tracker/test_lane_escalation.py: 30 passed in 0.49s. Log organize-independent-native-corrected-escalation.log.
- Changed production modules mypy: Success, no issues in 3 source files. Log organize-independent-native-corrected-types.log.
- git diff --check: passed. No full suite. An initial collection command named a nonexistent tests/services/test_lane_escalation.py and collected zero tests; its log organize-independent-native-corrected.log is retained as a harness error, not evidence for the product.

Eight-lens delta:
1. Correctness: stale ProposedWrite parent revision is refused after author return and before each actual body/criterion write; all original controls now pass.
2. Architecture: the existing escalation writer owns the post-gate/lease boundary and invokes a narrow awaited policy guard, preserving transport ownership.
3. DRY: actual canonical WriteBackResult is retained, including artifact, rounds, citations and final refutation; no parallel reconstruction.
4. Simplicity: bounded changes in the existing owners; no new scheduler, lease system or authority table.
5. Type safety: StageHaltReport gains typed WriteBackResult tuples and rejects write-back exhaustion without the matching actual rounds or with a HOLDS result. Other optional halt slots retain the earlier documented limits; this is not a claim that every unrelated invalid combination is unrepresentable.
6. Prompt/schema/framework: no judge/schema semantics were weakened. The original independent outputs and citation oracle are unchanged.
7. Concurrency/lifecycle: current membership, phase and final approval are checked at each issueable write, including both awaited escalation writes. This guards new requests; it does not claim backend atomic fencing after a request has been sent.
8. Evidence: frozen source, unchanged independent probes and meaningful positive controls; no donor/canonical edits, issue states or source repairs.

Integration: root may serially integrate 161ca93 then ee1d44b with the separately owned scheduler composition after that composition's own checks. Preserve the explicit unavailable hygiene/rubric and typed report handoff limits listed above. Requested Astra ultra configuration remains unverified, not an execution attestation.

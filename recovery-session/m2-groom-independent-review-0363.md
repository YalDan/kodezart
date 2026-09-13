# Independent review: request changes on 0363cc9

Reviewed candidate 0363cc9faa84ef90f61e9db38bc62864f5ee4fa3, tree 80442d0d6805d688daedc3dc40855c59554b3b35, exact parent e99c9d105bdf5d20fec8b976dce58156b7c4fb72. Reviewer authored no production code. Isolated worktree: /private/tmp/kodezart-v03-m2-groom-independent-review. Test-only commit 980b179 supersedes 41812b2 by an AST-identical Ruff formatting change.

## Findings

1. **P2: dynamic rubric is stale after a real repair.** `src/kodezart/services/organize_owner.py:251` renders a selected rubric with current native inputs once; `run` reuses that request at `verify(request)` near line 1140 after the native body write and fresh snapshot. With the explicitly supported rubric `Grade this current goal: {{issue_body}}`, both sets produce an organize_verify prompt containing the repaired `<issue_body>` but the original vague activity inside `<mandate_rubric>`. This contradicts the new documented per-call current-input guarantee and the fresh-context verification contract. It can make the fresh judgment grade a previous specification. The test uses a typed initial refusal, body proposal and write-back observation to reach the actual native adapter write; the verify executor deliberately supplies no result. It asserts current source/rubric bytes without inventing model grading. Fix using existing request/current-read machinery at actual consumer boundaries; preserve one existing mandate_rubric carriage. Re-read the current subject after repair/membership refresh before constructing the verification request, and cover subsequent author iterations as well.

2. **P2: boot validation accepts native value shapes that cannot render.** `src/kodezart/composition/organize.py:34` subtracts native names before rendering anything. An unconditional `{{refusal_evidence}}` rubric is therefore accepted although `_request` always supplies None; `{{#each issue_body}}{{this}}{{/each}}` is accepted although issue_body is a string. Both then raise PromptRenderError before any executor call or save in the actual owner. This leaves the advertised boot guarantee incomplete for supported inputs. Validate against actual typed native shapes through the existing renderer, keeping legal conditional absent values legal. The independent positive control uses a guarded absent refusal_evidence and the native linked_issue_bodies sequence and reaches dispatch.

Neither failure affects the shipped constant rubric bodies directly; both affect the newly supported and documented customizable native rubric source feature. No production correction is authored by this reviewer.

## Evidence

All commands use `/Users/kodezart/.local/bin/uv run` in the isolated worktree unless stated otherwise.

- `pytest -q tests/chains/test_organize_rubric.py tests/prompts/test_organize_rubric_sources.py tests/chains/test_organize_owner.py tests/integration/test_organize_scheduler.py`: **49 passed, 8.32s**, `m2-groom-independent-candidate-valid.log`.
- Exact unmodified candidate `test_organize_rubric.py` copied as a test-only addition to an isolated exact-parent worktree; `pytest -q tests/chains/test_organize_rubric.py -k 'real_factory_dispatches or incompatible_native_admission or each_organizational_refusal'`: **8 failed, 7 deselected, 8.56s**, `m2-groom-independent-parent-eight.log`. Failures are six missing-record_title dispatches and two accepted incompatible roles. No goldens or original assertions changed.
- `pytest -q tests/chains/test_groom_independent_review.py`: **4 failed, 1 passed, 2.04s**, `m2-groom-independent-final-oracles.log`. Two set-specific freshness failures, two boot failures, one valid conditional/sequence control. Final commit has the same executable AST; last change was formatting only.
- Before removing diagnostic-only assertions that intentionally observe broken runtime behavior, the expanded probe produced **4 failed, 3 passed, 2.12s**, `m2-groom-independent-counterexamples-final.log`. The two passing diagnostic-only controls prove the invalid shapes fail at actual dispatch and cause no write; they are preserved in `test_groom_independent_diagnostics.py`, outside the mergeable test commit because their expected behavior must disappear after correction. Every red oracle is retained unchanged.
- `mypy src/`: **216 source files pass**, `m2-groom-independent-types.log`. Changed production files and final probe pass Ruff; `m2-groom-independent-lint.log`, `m2-groom-independent-final-lint.log`. Initial probe E501 and subsequent one-line format diagnostic received no acceptance credit.
- `m2-groom-independent-source-proof.json` independently hashes all six original scheduled/authored bodies as unchanged; all **146** assertions/raises/fail calls in the three adjusted original fixture modules retain identical ASTs. Only `_request` changes among OrganizeOwner methods. PromptKey, SessionType, MandateSpec, dependency files and retry/authorization methods are unchanged.
- First relative `uv` invocation returned exit127 and performed no tests; `m2-groom-independent-candidate.log` retains that diagnostic. The successful invocation used the absolute executable. All test/type runner sessions settled.

No repetition of the author's 1229-test selection or full gate; no live LLM or whole-M2 acceptance claim.

## Requirements and eight lenses

Read current KOD-74, KOD-127, KOD-556 and KOD-557 plus all returned comments with provenance, repository CONTRIBUTING/Makefile/pyproject and the Notion engineering parent. No applicable AGENTS found. Agent-authored comments claiming founder rulings are historical claims, not independent human authority. The explicit criterion bodies establish existing carriage and the four organizational conditions; no policy ruling is added.

1. SOLID: existing registry owns source selection, composition owns validation, owner owns orchestration. Boundary ownership is sound; the freshness handoff is incomplete.
2. DRY: single existing renderer, phase table and rubric carriage reused. Per-set rubric prose is intentional corpus ownership; no duplicate retry or authority implementation.
3. Hexagonal: tests traverse actual composition, AgentService and native tracker adapter with executor/MCP boundary doubles. No new external dependency or domain I/O.
4. KISS: additive metadata and derived PromptTemplate are bounded. Correct the two existing handoffs rather than adding a cache, ledger or parallel rubric mechanism.
5. Typed LLM orchestration: same AdmissionJudgment/OrganizeProposal/WriteBackFinding contracts and ORGANIZE_PASS, no resumed judging transcript; rubric directs the four-part GROOM condition. Stale embedded evidence still compromises the fresh-context guarantee. Four organizational fixtures are typed convergence input oracles, not evidence of actual LLM grading or completed class-for-class parity.
6. Framework practice: dependency locks unchanged; installed source strict check passes. Pydantic Annotated constraints on mapping values follow the documented annotated-container pattern: https://docs.pydantic.dev/latest/concepts/fields/ . FastAPI and LangGraph behavior are untouched by this bounded change.
7. **Type-safety impact: improvement at the metadata boundary**, with PromptKey keys and nonblank constrained string values; no Any/cast/ignore added. Static construction still permits optional rubric_body until runtime selection, and native value-shape correctness remains incomplete as finding 2 shows. No claim that strict mypy proves template validity.
8. Hygiene: isolated source and original oracle proof retained, no source/golden/config modification, independently reproduced exact parent failures, no PR writes or initiative/Notion mutation. All live runner sessions settled before return.

## Integration and dependencies

Root alone should give the original author the final test-only `980b179` commit on candidate036, preserving its red assertions. After a minimal source successor, rerun these five probes and the 49-test bounded selection; runtime-diagnostic historical tests are not acceptance tests. Independently review the correction, then root integrates the accepted successor into maintained PR121 and tests the actual composed revision. Published cef0dff also carries the accepted M4 enum ancestry; retain it.

Existing accepted common write authority is unchanged. Whole KOD-74/L2, KOD-127, dated behavior parity, reserved human approval/ruling ownership, live grading and full-stack release remain outside this bounded review.

Owning references: https://linear.app/duckburg/issue/KOD-74 ; https://linear.app/duckburg/issue/KOD-127 ; https://linear.app/duckburg/issue/KOD-556 ; https://linear.app/duckburg/issue/KOD-557 . Engineering parent: https://app.notion.com/p/3abf89e34d10812d9b3fd3551cc2208a .

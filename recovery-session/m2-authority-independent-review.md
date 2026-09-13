# Independent Organize write-authorization review

Decision: **ACCEPT the bounded correction at `38956bb04f70b5cbd5d6ffbc5dbc9b08a29e369f`**, tree `38e10468599b6c041206d1dfe1f88b2d8312d120`, exact parent `9e387b4a830adf87a3cebcbe705c17f1569101c7`. No blocking correctness, concurrency, architecture, type or oracle finding was demonstrated in this delta. This is not whole KOD-74/L2 or M2 acceptance.

Reviewer: `/root/m2_authority_review`. The requested review role was Astra ultra; effective runtime metadata was not exposed, so this report makes no independent claim about it. Reviewed current KOD-74 description and all returned comments before relying on source-author evidence. The existing approval rulings remain human owned; this review creates no replacement approval rule. CONTRIBUTING.md was read; no applicable AGENTS.md was found in the inspected ancestor paths or tracked review tree.

## Isolation and exact attribution

- Candidate review tree: `/private/tmp/kodezart-v03-m2-authority-review`, detached at the reviewed SHA before adding probes.
- Baseline review tree: `/private/tmp/kodezart-v03-m2-authority-review-before`, detached at the exact parent. Only probe files were added there.
- No source-author, maintained, canonical, or other worker tree was mutated. All seven candidate files match their committed bytes; `m2-authority-independent-proof.json` records SHA-256 values and exact tree identity.
- Independent probes only: commit `ad9ba4e` (full SHA recorded below), file `tests/chains/test_authority_independent_review.py`, 36 tests. Production source still equals `38956bb` exactly. The two original author probe files match baseline/candidate byte-for-byte. The independent probe executable AST also matches between baseline and candidate; differences are formatting only.
- Initial new-probe diagnostic run was invalid: its external project stub omitted the required native `description` field. `m2-authority-independent-extra-first.log` is preserved and receives no acceptance credit. The correction supplied that external field only; no assertion was weakened. Two subsequent probe-only E501 observations were fixed by formatting/adjacent string literals preserving the executable AST. The formatter's initial subprocess yielded before being explicitly joined; the final 36-probe run occurred after all prior runners and formatting had settled. Source files were never edited.

## Executable evidence

All test commands used `/Users/kodezart/.local/bin/uv run --locked --python 3.12 pytest -q` in the indicated isolated tree. No live service or agent output was used. Only external MCP answers/state and structured executor outputs were controlled; owner, source/context reader, adapter, retry owner, actual grant arbitration, lease lifetime and canonical verifier remained production code.

1. Exact original probes at parent `9e387b4`: `tests/chains/test_organize_description_authority.py tests/chains/test_organize_write_revalidation.py`. **6 failed, 2 passed in 3.95s**. Three body failures independently reproduce expired grant, external source-body preservation and approval refusal; the other three reproduce graph/split/criterion approval loss. Log: `m2-authority-independent-before.log`.
2. Candidate originals plus native retry/receipt/final boundary controls: the two files above plus `tests/tracker/test_organize_graph_retry_boundary.py`, `test_organize_graph_receipt_boundary.py`, `test_organize_graph_final_boundary.py`, `test_criterion_retry_independent.py`, `test_criterion_receipt_boundary.py`. **31 passed in 3.80s**. Log: `m2-authority-independent-after-original.log`.
3. Added independent actual-owner matrix: all four authoring surfaces against parent-only approval, project-only approval, graph-context revision, scope membership addition, configured phase-gate removal, and expired actual lease; issued unknown responses and successful receipts followed by unreadable native readback on all four; repeated cancellation while the retry authorization callback is awaiting a native read, with both live authority and refusal. **34 passed in 3.92s**. Log: `m2-authority-independent-extra.log`.
4. Exactly the project-only approval subset of the same independent probe at parent `9e387b4`: `tests/chains/test_authority_independent_review.py -k project_approval`. **4 failed, 32 deselected in 1.45s**. The target's own body, labels, parent, project and priority remain unchanged; approval changes solely on its native project. Log: `m2-authority-independent-project-before.log`.
5. Added two positive controls: a known-unsent retry on the second split/criterion creation retains the context containing the first owned creation; both complete the actual configured owner and replay without further writes. The final independent file has **36 passed in 5.69s**; log `m2-authority-independent-extra-final.log`. Its final formatting preserves the same AST and emitted strings; final SHA-256 is in the proof JSON.
6. Broader actual-owner and ownership/verifier controls: `tests/chains/test_authority_independent_review.py tests/chains/test_organize_owner.py tests/chains/test_organize_graph_owner.py tests/chains/test_organize_native_independent.py tests/chains/test_organize_ownership.py tests/services/test_run_surface_lease.py tests/chains/test_fresh_write_back_judge.py tests/chains/test_write_back_tracker_boundary.py tests/chains/test_write_back_verifier.py tests/chains/test_write_back_workspace_ownership.py`. **137 passed in 47.33s**, including the 36 independent cases. Log: `m2-authority-independent-owner-controls.log`.
7. `/Users/kodezart/.local/bin/uv run --locked --python 3.12 mypy src`: **216 source files clean**. Log: `m2-authority-independent-types.log`.
8. Ruff check of the seven changed files plus the independent probe: **passed**. Log: `m2-authority-independent-lint-pass.log`. The preceding two lint logs are preserved as probe-only formatting diagnostics, not green evidence.

The counts above overlap and must not be summed into an inflated total. This reviewer did not repeat the entire repository gate and does not attribute the published predecessor's full gate to this candidate.

## Boundary assessment

The actual owner binds `authorize(proposal, peers)` in every graph, split, body and criterion writing arm. A source census finds no other production graph/split/criterion callers. Body writes now pass the existing DescriptionWriteAuthority with the queue's actual job ID and declared issue-description surface. No agent chooses identity, a lease holder, the phase gate, current membership, or native approval.

The callback is invoked by the existing `_retry_call` before each complete safe-to-repeat attempt. Its existing owner routine checks current phase and membership, reads the source revision and complete graph/ruling/milestone context, and consults fresh native inherited execution approval. Each adapter attempt still owns native identity/snapshot checks and grant verification. Revalidation does not renew an expired grant, turn failure into reacquisition, or construct another retry policy.

Callback refusal escapes before the next mutation; outages fail closed. Existing unknown-issued-write classification prevents blind mutation resends. Graph/split/criterion readback remains outside mutation retry scope; description's receipt parser performs no awaited readback inside that scope. Canonical WriteBackVerifier retains independent artifact reread, fresh structured judge, and bounded repair. Repair obtains a fresh proposal and reuses the same authorization path. The two-child controls demonstrate that updated owned context is bound to the correct next operation, rather than retaining the pre-creation snapshot or capturing a mutable loop variable.

Cancellation during the callback remains inside the existing `settle` operation. Repeated caller cancellation leaves the grant held until that owned operation settles, then release occurs and CancelledError propagates. With still-current authority exactly one mutation may finish before cancellation returns, consistent with the existing settlement contract; with newly approved context it refuses and writes nothing. Neither case fabricates a successful owner report or leaves a grant in the tested backend.

The optional callback's absence is valid for the existing native grant-only port contract: those callers do not carry a cached Organize judgment. Supplying a callback is now required by the actual Organize behavior and is present at every production authoring call site. The optional API still permits a future caller to omit it; the type system alone does not prove freshness. That is a stated existing layering limit, not a new authorization fallback in the actual owner. No repository-wide API migration is needed to accept this focused repair.

## Eight review lenses

| Lens | Assessment |
| --- | --- |
| SOLID | Positive: source/phase/approval policy stays with OrganizeOwner, native transport/identity/arbitration stays with its adapter; the small callback carries an existing collaborator through the retry boundary. |
| DRY | Positive: one existing authorize routine and one existing retry owner; no copied approval arithmetic, new ledger, timer or retry framework. Repeated authorization observations serve different await boundaries rather than duplicate policy. |
| Hexagonal architecture | Positive: vendor-neutral callable type crosses TrackerPort; application code learns no Linear wire format. New independent probes cross the actual adapter using external MCP state. |
| KISS | Positive: additive callback and existing description authority; no new actor, store, rule engine or orchestration layer. |
| Typed agent calls | Neutral/preserved: author output remains discriminated OrganizeProposal validated against the source issue; judgments remain fresh read-only structured calls, separate from deterministic write authorization. |
| Official framework practice | Neutral: uv.lock remains FastAPI 0.135.1 / Pydantic 2.12.5 / LangGraph 1.0.10; no framework API/configuration or checkpoint-state change. Existing discriminated unions align with [Pydantic 2.12.5 guidance](https://github.com/pydantic/pydantic/blob/v2.12.5/docs/concepts/unions.md). Deterministic workflow ordering remains separate from agent judgment, as distinguished in [LangGraph workflow guidance](https://docs.langchain.com/oss/python/langgraph/workflows-agents). Awaited callbacks add no blocking I/O to async orchestration ([FastAPI async guidance](https://fastapi.tiangolo.com/async/)); cancellation ownership retains explicit settlement and propagation ([Python 3.12 task/shield guidance](https://docs.python.org/3.12/library/asyncio-task.html#shielding-from-cancellation)). These are bounded consistency observations, not blanket framework certification. |
| Type safety | **Neutral static impact, improved runtime authorization.** Named `Callable[[], Awaitable[None]]`, explicit partials and keyword-only port extensions introduce no Any, cast, ignore, permissive schema or unchecked agent Boolean. Strict 216-source check passes. Optional omission remains representable and is not claimed statically impossible. |
| Hygiene | Positive: seven focused candidate files, immutable production proof, original failing probes preserved, independent native controls, no weakened oracle. Reviewer probe-only fixture and lint diagnostics are expressly uncredited. |

## Limits and integration

This patch establishes fresh observed preconditions on retry; it does not provide backend compare-and-set, transactional uniqueness or fencing against a change after the final observation or a write already issued. Transport unavailability and release behavior still depend on existing adapter/backend contracts. Broad operational performance and live service correctness were not measured here.

Root may integrate the exact reviewed candidate onto its exact parent in the maintained milestone stream and run the required maintained gate. The reviewer-only test commit is available for deliberate cherry-pick if desired; it contains no production change. Do not cherry-pick the whole reviewer worktree or treat review artifacts as application source.

Native GROOM rubric acquisition, reserved approval/actor rulings, remaining criterion/capability/census obligations, full nine-lane/eight-lens recomposition, live demonstrations and release remain separately open. Preserve KOD-74 and initiative state. This acceptance closes only the independently reproduced description/source authorization retry correction.

Full reviewer-only probe commit: `ad9ba4e618723100af1f361af1024729d52cf62f`.

Published bounded review: https://linear.app/duckburg/issue/KOD-74#comment-1e5093a9-6ac4-490f-b5de-32b6143c2b46 . All test/mypy/formatter subprocesses were settled before final handoff; the reviewer tree is clean.

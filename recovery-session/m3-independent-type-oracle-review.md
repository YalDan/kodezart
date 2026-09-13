# Independent M3 extraction review

Bounded disposition: the inspected M3 native/fire extraction preserves the accepted donor's implementation and improves the pre-extraction type model. Acceptance requires transfer of the original oracle restoration and the census owner's remaining finite correction. This review does not accept final L3/L9 scope composition, live operation, or release readiness.

## Immutable sources and ownership

- Initial source: `dad63c4f4b957323a58331e8416c2a542eb0a79c`, tree `c8ed973b44b499ae6334fc2842e71c3263513881`, parent `fadf6efe29c7addcc608b22e8345cd9008ce4bab`.
- Corrective source: `7675c0b461acb9e77aa41c9c9829f2359ebc83c0`, tree `7523c681b77a852ba14ed198b321046346f4cb11`, parent dad.
- Exact donor: `da39c439898aec1233aa8b6157b35e961df1e453`.
- Reviewer test-only correction: `cbbe5dd1d18822867c5556b6117040463681b0bc`, tree `75e873cc063090add14e4dfeea496c7a60e7ee54`, parent 7675.
- Shared M4 vocabulary: `c855fb061209a7f20a3d10cd06f13f8476fba7dc`.
- Isolated source review: `/private/tmp/kodezart-v03-m3-type-review`; isolated test-only correction: `/private/tmp/kodezart-v03-m3-oracle-review`. No production edits, no edits to author/root trees, no further delegation. `CONTRIBUTING.md` read and locked Python 3.12 used.

## Findings and original evidence

1. **Original oracle omission, corrected in cbbe5dd.** Both dad and 7675 omit five donor files for source they extract: `tests/chains/test_fire_spec_prompt_consumers.py`, `tests/chains/fire_spec_prompt_goldens.json`, `tests/domain/test_fire_spec_formatter.py`, `tests/chains/test_fire_extraction.py`, and `tests/chains/test_workflow_phase_composition.py`. This loses KOD-416/412 authored prompt/formatter and phase ownership evidence. It is an evidence regression, not by itself proof of a runtime bug. The correction copies all five exact donor blobs, including the unchanged full-prompt hashes. All 108 original tests pass; there is no golden regeneration or assertion rebaseline.
2. **Finite dispatch inventory still incorrect in 7675.** The independent broader dad run passes 266 tests and fails only `test_house_rules_delivered_as_system_prompt_append`: `KEYED_DISPATCH_COUNTS` requires `lane_delivery.py: 1` although that M5 source is deferred. The test is unchanged in 7675. The census owner explicitly owns the single-row correction and eventual restoration when the actual lane source lands. Preserve the test's real prompt and session-policy assertions. No correction to this file is included in cbbe5dd.
3. **Two known production omissions corrected.** 7675 explicitly supplies `scope=None` at the authored HTTP constructor and restores the donor's active immediate-child checkpoint reader byte-for-byte. Four original HTTP/SSE and committed/in-flight progress tests pass on cbbe5dd (source identical to 7675). Root/census owner supplies the earlier failure reproduction; this reviewer does not claim independently rerunning that earlier reproduction.
4. **No additional blocking native/fire source defect found in the bounded extraction review.** Inspected phase, loop, frozen spec, criterion, amendment, and service state remains the exact accepted donor. Prior bounded native replay evidence is reusable for those unchanged paths only. Parent separately reviews the three non-donor composition cuts and final integration.

## Eight review lenses

| Lens | Outcome and evidence |
| --- | --- |
| SOLID | Improvement over the previous monolithic workflow: specification, shared fire execution/review, authored delivery and native execution each have a concrete responsibility. Typed runtime collaborators remain outside serializable native phase state. No additional ownership framework introduced by this extraction. |
| DRY | Shared downstream fire nodes and `FireSpec` consumers remain one implementation across the two compiled compositions. Original consumer corpus tests now transfer. M4's 18-member `RunEventKind` class is AST-identical to donor/M3 in names, values, order and class body; its only source is stdlib `StrEnum`. Suitable as shared vocabulary, with no event-effects or publisher policy decision. |
| Hexagonal boundaries | Current tracker/Git/workspace facts cross explicit ports; semantic agent output does not decide lease ownership or native identity. LangGraph checkpoint reading remains isolated in its adapter. The restored reader selects exact immediate-parent metadata and rejects competing active namespaces instead of inventing a backend-specific fallback. |
| KISS | Existing graph/checkpointer, source guards and receipts are reused. The M5 runtime ownership cut remains explicit, without a dummy runtime. The test correction is exact blob restoration, not a new oracle abstraction. |
| Typed agent calls | Native writer, amendment judgment and amendment text are parsed through their concrete Pydantic output models at actual call sites. The corrected schema census includes those bindings. `RalphOutcome` distinguishes evaluated native/authored/refused/pending cases and validates actual criterion roster/head evidence. No added type ignores, casts, `Any`, reflection or raw variadic callable escape was found in the source diff scan. |
| Official framework practices | Pydantic 2.12.5 phase discriminators select a closed concrete variant and validators relate output, receipts and workspace evidence. LangGraph 1.0.10 typed state/checkpoint separation is preserved; the immediate-child reader uses the real checkpoint API. FastAPI 0.135.1's authored construction now supplies the required nullable scope value, confirmed through original HTTP tests. No framework upgrade is implied. |
| Type safety | Improvement from pre-extraction parent: closed native phases, `TrackerSpec`/`AuthoredSpec`, typed execution criteria and outcome variants replace loose native state. Neutral against accepted donor: these definitions are preserved. `RalphLoopContext` rejects mixing tracker criteria with an authored context, and graph phase routing ends in `assert_never`. Static source check passes 255 files. Test-only cbbe5dd has no production type impact. |
| Hygiene | Source and test pins/logs retained, original goldens preserved, portable test-only commit clean. One low-impact donor comment-placement issue remains: the `McpToolResult` explanatory comment sits near the top of `core/protocols.py`, away from the alias it describes. It has no behavioral effect and is not an acceptance blocker. No suppression/rebaseline was introduced to hide the observed failures. |

Official references: [Pydantic v2.12.5 union documentation](https://github.com/pydantic/pydantic/blob/v2.12.5/docs/concepts/unions.md), [LangGraph persistence](https://docs.langchain.com/oss/python/langgraph/persistence). The latter is current guidance; exact installed behavior is bounded by the resolved 1.0.10 source and executable original tests, not inferred from latest documentation alone.

## Commands actually run

All commands use `/Users/kodezart/.local/bin/uv run --locked --python 3.12`.

- On dad: `mypy src` — success, 255 source files. Log `m3-independent-types-before.log`.
- On dad: `pytest -q tests/domain/test_fire_spec.py tests/domain/test_authored_feasibility_compatibility.py tests/integration/test_criteria_oracle.py tests/chains/test_native_fire.py tests/chains/test_native_amendment_runtime.py tests/chains/test_native_fresh_boundaries.py tests/chains/test_ralph_workflow.py tests/chains/test_authored_check_routing.py tests/domain/test_gap.py tests/domain/test_subtree_closure.py` — 266 passed, one finite inventory failure, 306.26s. Log `m3-independent-extraction-controls.log`.
- On 7675 plus exact donor oracle blobs (committed unchanged as cbbe5dd): `pytest -q tests/chains/test_fire_spec_prompt_consumers.py tests/domain/test_fire_spec_formatter.py tests/chains/test_fire_extraction.py tests/chains/test_workflow_phase_composition.py` — 108 passed in 82.80s. Log `m3-independent-restored-oracles.log`.
- On the same four `.py` files: `ruff check` — all checks passed.
- On cbbe5dd: `pytest -q tests/api/v1/test_agent.py::test_stream_workflow_sse tests/api/v1/test_agent.py::test_workflow_streams_criteria_event_via_sse tests/api/v1/test_jobs.py::test_status_of_a_running_job_reports_checkpointed_progress tests/api/v1/test_jobs.py::test_status_reports_progress_while_the_graph_is_paused_mid_run` — four passed in 2.43s. Log `m3-independent-corrected-boundaries.log`.

The initial restoration attempt preceded completion of asynchronous worktree creation. Copying the first file failed before any file mutation; the premature pytest invocation failed collection. It has no acceptance credit and its diagnostic remains `m3-independent-restored-oracles-initial-invalid.log`. All credited edits/tests followed settlement of those processes. No source/test edit occurred during an active test or mypy invocation in its tree.

## Parity, integration and limits

`m3-type-oracle-audit.py` / `.json` record independent immutable source/test symbol comparisons. Root separately supplies `m3-root-source-function-proof.json` for 343 changed functions: 340 exact donor AST, three composition cuts under its ownership. `m3-independent-restored-oracle-proof.json` records all five original blob hashes and the portable commit. `m3-independent-framework-version-proof.json` confirms donor/candidate resolved package entries are identical except local-project dependency metadata: the candidate omits donor's direct `typing-extensions` declaration, while the resolved transitive version remains 4.15.0 and candidate source has no import of it. FastAPI 0.135.1, Pydantic 2.12.5, LangGraph 1.0.10 and langgraph-checkpoint 4.0.1 are unchanged.

Root should cherry-pick cbbe5dd once after 7675, then transfer the census owner's distinct correction. Preserve these five donor files and original golden data in the integrated candidate. A merged source needs its actual integration gate; these results do not attest to subsequent unreviewed source changes. No expensive previously accepted donor native replay suite was rerun without a changed-dependency reason.

Native/human approval remains human-owned. No backend CAS/fencing, cross-host restart or unreceipted effect replay claim is added. Prior same-host `InMemorySaver`/real-Git phase evidence does not establish database/host/public-HTTP queue restart durability. Final M5 scope runtime, scoped API/composition, full L3/L9 matrix, live verification and release remain dependencies.

Linear evidence: [KOD-75 comment](https://linear.app/duckburg/issue/KOD-75#comment-85f646fe-1e92-4847-b2bf-9a70dbcd1df1), [KOD-105 comment](https://linear.app/duckburg/issue/KOD-105#comment-03b2e35c-942e-41bf-8a18-a0bb744df467).

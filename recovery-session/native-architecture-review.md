# Native architecture and type review

**Verdict: integrate from the bounded architecture/type perspective; no blocking source finding.** Independent native correctness, canonical integration, public composition and release gates remain separate. Canonical tracker-port failure normalization is a known integration dependency, not part of the isolated native commit approved here.

Reviewed final SHA: `19e5d870ddcd1f602cbfe402519d1c11e444835a`, parent `10fa10d4f6af4ce844503145d36fcf6dd464f6d4`. Worktree: `/private/tmp/kodezart-v03-recovery-native-fire`. Requested comparison base: `55a87a5`; already independently reviewed class-removal ancestry was accounted for using `abfb0c8..10fa10d4` to isolate the native changes, followed by the complete nine-file `10fa10d4..19e5d870` corrective delta. Final HEAD and clean worktree were independently reconfirmed. Reviewer source files changed: **none**; reviewer source commit SHA: **none**. Requested Astra/high runtime settings are not independently attested.

## Contract and source review

Read current KOD-815, KOD-105, KOD-763 and KOD-814 descriptions/comments before source, then inspected immutable source/tests with git show/diff, then the writer evidence index and actual log endings. No actively edited source was used as final proof. The native identity ruling explicitly settles exact tracker subissue keys as CriterionId and nonblank shared wire identities; authored AC-n minting remains. Persistence remains held; retaining the branch's existing absence of native artifact writes does not select a side of KOD-814.

Original native architecture correctly replaces authored-only execution inputs with one FireSpec slot and one criterion_set partition. TrackerCriterion has exact ID and Check text with no authored feasibility field. ExecutionCriterion is the union of that native value and ValidatedCriterion; shared grading's authored-only feasibility access is guarded by the actual type. No fake feasible result, authored TicketDraftOutput conversion, AC-n key map, parallel native ledger or second execution engine was introduced.

FireCriteriaSource captures the frozen subject and extends the narrower FireCriteriaReader used by downstream consumers. Runtime source/tracker objects are constructor dependencies, not checkpoint values. The actual production constructor supplies one capability to the loop, implementation, review, engine and, after the corrective change, remediation. The source implementation still consumes the existing wide TrackerPort because subtree/spec reading requires several capabilities; this review does not claim the entire tracker interface has been split.

RalphLoopContext validates agreement between the native tracker_spec and criterion variants when reconstructing loop configuration. Missing native reader/spec refuses instead of selecting cached authored behavior. Current subject accessors preserve the original spec and use replacement tickets only for authored remediation. The outer state remains an existing TypedDict with separately typed optional fields, not a fully discriminated typestate machine: this repair does not make every contradictory state mechanically unrepresentable. Collection uniqueness is established by native mapping construction, not a new global model validator. The source contains no new Any, cast or ignore bypass in this delta; these bounded runtime checks avoid inventing a new schema framework.

Remediation uses the existing node/session/budget and typed FireSpec input. Its native output is RemediationPlan, registered in WIRE_SCHEMAS as REMEDIATION_SCHEMA; its authored output remains TicketDraftOutput. The same source-arm decision selects dispatch schema and model_validate; FireRemediation additionally rejects a returned event from the wrong arm. Authored prompt goldens were not changed by the native-only diff; earlier golden changes belong to separately approved class removal. The default evaluation/review prompts condition feasibility language on its actual source rather than assuming every criterion has the authored field.

## Corrective architecture delta

The four original correctness findings were read and treated as existing review findings, not independently reproduced in this architecture task. The corrective design addresses their shared placement problem without duplicating shared execution:

- `require_current_native_snapshot` reads through the existing narrow capability and compares the current native set with the recorded evaluated set. Existing merge, best-iteration publication and terminal nodes call it before their effects; the authored path returns immediately from the helper. Node names, downstream consolidation implementation and graph retry policy are preserved.
- Each evaluator/reviewer fan-in attempt now reads current obligations inside its dispatch closure, renders its prompt, validates its output and computes one IterationGrade before returning. The existing generic `until_permutation` consumes that grade through `require_permutation`; no later grade can accidentally use another attempt's snapshot. The review's sequential closure retains the same final criterion_set returned with the grade.
- Remediation keeps the existing historical summary of what was graded and appends separately titled current obligations after a live read. One pure `tracker_checks_section` renderer serves implementation and remediation. Subject text is not recaptured.

Snapshot comparison is intentionally conservative and is not a tracker-wide transaction spanning an agent call. A mismatch refuses; it does not silently auto-approve, reacquire authority or redefine obligations. The corrective change does not solve the pre-existing unknown-ID plus complete-known-results fan-in arithmetic, which is separately owned and explicitly excluded by the correctness review.

## Actual independent checks

Executed on clean final `19e5d870`, using `/Users/kodezart/.local/bin/uv run --locked`:

1. `mypy src` — **Success: no issues found in 289 source files**, exit 0. Log: `/private/tmp/kodezart-recovery-session/native-architecture-mypy-19e5d87.log`.
2. `python -m pytest -q tests/types/test_wire_schemas.py tests/domain/test_criterion_identity.py` — **82 passed in 11.98s**, exit 0. Log: `/private/tmp/kodezart-recovery-session/native-architecture-schema-19e5d87.log`.
3. `git diff --check abfb0c8..19e5d870` — clean. `git rev-parse HEAD` returned the exact final SHA; `git status --short` was empty.

No native correctness probes or full suite were rerun. Type/schema/identity tests include shared nonblank ID rejection/preservation, schema registration/census and matching remediation dispatch/validation arms. The AST-based remediation source audit is an architecture guard, not a substitute for execution evidence; its deliberate damaged-source controls establish only that the audit detects those source mutations. The separately inspected native tests use real shared consumers and boundary executors. Corrective fixture changes distinguish raw evaluator text from the QualityGate's promised reconciled source text; no correctness assertion was removed to hide the earlier mismatch.

Inspected writer logs, not counted as my executions: `native-corrective-focused-final.log` reports 419 passed in 63.29s; `native-corrective-adversarial-final.log` reports 9 passed / 1 deselected in 4.64s; final mypy reports 289 source files; Ruff passes and format reports 666 files. These overlap other selections and are not unique-test totals. Existing Makefile excludes tests from strict mypy, so no test-tree type cleanliness is claimed. Two exploratory source reads guessed nonexistent helper filenames; git reported those paths absent, after which `git grep` located the actual unchanged `core/redispatch.py`. No test execution or source conclusion relied on those failed reads.

## Eight lenses

| Lens | Bounded assessment |
| --- | --- |
| SOLID | Shared components consume narrow runtime read capabilities; source capture and current-obligation reading have distinct real consumers. Existing phase responsibilities stay recognizable. |
| DRY | One shared engine/node set, identity representation, grading implementation, current snapshot guard and current-Checks renderer. Existing bounded redispatch remains the retry mechanism. |
| Hexagonal | Tracker I/O stays outside domain formatting/grading and outside checkpoint state; constructors inject the source. TrackerCriteria remains an orchestration/source implementation over the existing tracker port. |
| KISS | Two data variants and a narrow native remediation output extend existing paths. No service container, parallel graph, version ledger or artifact policy introduced. |
| Typed agent calls | Same typed AgentRunner boundary and registered wire-schema path. Native/authored remediation dispatch and runtime validation select the same model; evaluator retries return a typed per-attempt grade. |
| Official version match | Lock contains Pydantic 2.12.5, LangGraph 1.0.10, checkpoint 4.0.1 and langchain-core 1.2.17. Official tagged source supports the Field constraints, four-parameter StateGraph/CompiledStateGraph and typed Pydantic checkpoint representation used here. |
| Type safety | Native provenance no longer pretends to be authored feasibility; loop source agreement and remediation output agreement are checked. Independent strict source typing and schema/identity checks pass. TypedDict typestate and hostile/corrupt checkpoint validation are not universally solved. |
| Hygiene | Clean immutable source review, no native golden rebaseline, no reviewer code edits/commits, no full-suite duplication. Existing public composition and persistence limitations remain explicit. |

Official sources: [Pydantic 2.12.5 Field implementation](https://raw.githubusercontent.com/pydantic/pydantic/v2.12.5/pydantic/fields.py), [LangGraph 1.0.10 StateGraph/CompiledStateGraph](https://raw.githubusercontent.com/langchain-ai/langgraph/1.0.10/libs/langgraph/langgraph/graph/state.py), and [checkpoint 4.0.1 serializer](https://raw.githubusercontent.com/langchain-ai/langgraph/checkpoint%3D%3D4.0.1/libs/checkpoint/langgraph/checkpoint/serde/jsonplus.py). The serializer can fall back to model_construct after validation failure, so the valid strict-msgpack replay tests do not establish validation of arbitrary corrupt historical payloads. No such broader claim is made here.

## Dependencies and disposition

Canonical integration must normalize root's new TrackerUnavailableError / TrackerAccessDeniedError at TrackerCriteria.read_spec/read_current if it promises FireSpecEntryError to consumers. This isolated ancestry does not yet define those types. Root and writer explicitly acknowledged ownership of that integration correction; the final canonical patch has not been reviewed in this report. Current uncaught failures still stop execution, so this is an exception-contract integration requirement, not evidence of stale-success fallback.

Public scoped routing/lifespan native-source injection, public restart API, live evaluator tracker write-back, attribution/lease enforcement, KOD-814 persistence and full L9 remain separate. Direct production-constructor native capability is not public application composition. Root owns canonical integration and its final checks.

Own public-safe review comment: [KOD-815 architecture/type review](https://linear.app/duckburg/issue/KOD-815#comment-60398add-944c-44fc-acaa-a94f159fdcfa). Governing decisions: [KOD-763 settled identity](https://linear.app/duckburg/issue/KOD-763#comment-24370a9c-aed8-4285-ae2b-9cd4ffadd1ee), [KOD-814 held persistence](https://linear.app/duckburg/issue/KOD-814#comment-5bd2dfdc-f8a5-4dc8-99da-7e9e2865e8c6). No issue states, Notion content, integrations or pushes were changed by this reviewer.

## Canonical neutral-port normalization addendum

Independently reviewed exact cc7b82940769aea59924852f65b73a9fb46a15fe using git show, source before/after and tests/chains/test_native_port_failures.py. **Approve this bounded integration correction**; it satisfies the previously pending exception-contract dependency. TrackerCriteria.read_spec and read_current add exactly TrackerUnavailableError and TrackerAccessDeniedError to their existing narrow failure translation, raising FireSpecEntryError with the original exception as cause. No MCP transport exception leaks into this boundary, retry/replay policy is unchanged, and cancellation/programming errors are not swallowed. Read-current uses the captured subject for the typed error address.

The four-case regression uses the production TrackerCriteria and doubles only its tracker-port read seam, covering both error classes at read_spec and read_current. It checks the exact issue key and cause object identity; it does not replace the implementation under test or broaden to Exception. Inspected root native-port-before.log (four expected failures, 0.49s) and native-port-after.log (51 passed, 8.51s). No redundant test execution was performed in this bounded source/oracle follow-up, as requested. Files changed by root: src/kodezart/chains/criteria.py and tests/chains/test_native_port_failures.py; reviewer source changes/SHA: none. The eight-lens assessment above is unchanged: narrow typed translation preserves hexagonal placement, existing source responsibilities and simple shared error handling, and introduces no new framework APIs or state authority. This addendum does not close public composition, persistence or other held lane criteria.

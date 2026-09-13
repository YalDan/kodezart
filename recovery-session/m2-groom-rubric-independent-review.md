# M2 native GROOM rubric and admission contract — independent findings

Review scope: published M2 `37723cd8c660de5ad959de1f118b93287da48e64`, tree `965ce3a5833741293a7405cfed50bc6d2ab960ac`, detached clean worktree `/private/tmp/kodezart-v03-m2-groom-rubric-independent`. Description-authority successor `38956bb` belongs to a different review and is not reviewed here. No production edits were made for this investigation. Requested highest effort remains requested, with effective runtime metadata unverified.

## Findings

1. **The documented registered-key contract accepts native configurations which cannot run.** `MandateSpec` accepts every `PromptKey` for rubric and admission; `docs/configuration.md` documents that rule. The preserved `mandate_fields()` declares `grooming_pass` and `ticket_review`. Both shipped prompt sets pass `OperationConfig`, `verify_organize_configuration` and actual `build_organize_owner` construction with those fields. Public `owner.run` then fails rendering the rubric: `PromptRenderError: Unbound template placeholders: record_title`. Replacing only the rubric key for a diagnostic control exposes the second independent failure: the authored-ticket admission template requires `task, draft_md`. Neither path starts an executor call. This is not evidence of a live deployment with that table: the shipped operation example declares no mandates; it is evidence that the documented accepted configuration and original declared test fixture are invalid at the native consumer.

2. **Renderability alone cannot validate an admission supplier.** Selecting `commit_message` as admission key passes the same configuration/construction checks and reaches the real agent service in both sets. The actual call asks for a conventional commit message while supplying the `AdmissionJudgment` schema under `ORGANIZE_PASS`. A boot check must validate the known role contract, not merely test that all placeholders render.

3. **The built-in native roles still narrow every mandate to ticket buildability.** `organize_assess.md` unconditionally asks whether the issue can be implemented; `organize_verify.md` unconditionally asks for dry implementation. KOD-557 requires GROOM's four-part organizational predicate and explicitly excludes that test. Both files also say to use the supplied rubric, so the shipped instructions conflict for GROOM. This is a source-contract finding, not a claim about observed LLM judgments. Existing actual-owner tests replace every declared rubric and admission key with `organize_assess`, which bypasses finding 1 and does not supply the authorized organizational rubric.

## Authorized contract and provenance

The current owners are KOD-74 (shared D11/D12), KOD-127 (GROOM), KOD-556 (shared carriage), and KOD-557 (organizational acceptance). KOD-788, KOD-781, and KOD-797 concern distinct authority/terminal/event questions; their unresolved decisions do not supply this rubric.

KOD-556 requires the parent D12 session/prompt machinery, no GROOM-specific session type or prompt key, and the existing per-call `mandate_rubric` binding. KOD-557 requires blockers organized across containers, open decisions asked of their owners, dates/order verified, and measurable issue goals. A fixture missing any one must not converge. Parent D11 fixes exactly five `MandateSpec` fields, including the two `PromptKey` references. D12 keeps role prompts separate and prose outside Python.

The historical corrected third initiative ruling is [8fd570ce-f70c-4434-997c-3dc95da35e3d](https://linear.app/duckburg/initiative/kodezart-v03-loop-orchestration-scopes-as-input-the-tracker-as-live-97c2509ef1b8#comment-8fd570ce-f70c-4434-997c-3dc95da35e3d). It describes founder direction but its observed author is the agent account Goofy. All 70 returned initiative comments (complete, `hasNextPage=false`) and all eight KOD-127 comments observed in this read are agent authored. This is an attribution limit, not a claim that a founder instruction never existed. The current issue bodies preserve an actionable predicate; no missing human signature is needed to repair routine native prompt bindings. The one-shot approval delegation in the fifth August 11 ruling was explicitly spent and authorizes no current approval write.

“GROOM's rubric is a sub-issue” points to its specification owner KOD-127. The inspected contracts do not say that every runtime must fetch that exact Linear issue as an application rubric, and no such concrete operation binding is shipped. Adding an implicit live dependency on this workspace's issue would invent runtime configuration.

## Actual execution and oracles

External probe: `/private/tmp/kodezart-recovery-session/test_m2_groom_configured_prompts_independent.py`. It uses the actual production constructor, `OperationConfig`, operation namespace bindings, the actual native tracker adapter over the existing in-process board, actual agent service, actual prompt registry and both shipped sets. It deliberately uses `RecordingExecutor([])`: no structured verdict, authored ticket artifact, approval act, or simulated semantic grader is supplied.

Commands run from the detached review tree:

```sh
PYTHONPATH=.:src .venv/bin/python -m pytest -c pyproject.toml -q -s /private/tmp/kodezart-recovery-session/test_m2_groom_configured_prompts_independent.py
PYTHONPATH=.:src .venv/bin/python -m pytest -c pyproject.toml -q -s /private/tmp/kodezart-recovery-session/test_m2_groom_configured_prompts_independent.py -k wrong_renderable_role
```

The first command ran the original six-case version: **6 passed in 4.08s** in `m2-groom-configured-prompts-independent-before-configured.log`. Four assert the concrete rendering failures and no dispatch; two native-input controls reach exactly one `AdmissionJudgment` dispatch and then the expected `NoStructuredOutputError`. The later extension adds only two wrong-role controls: **2 passed, 6 deselected in 3.71s**, `m2-groom-wrong-renderable-role-independent.log`. All controls require unchanged original body/labels and no tracker save operation. These are passing counterexample probes, not passing GROOM functionality. The initial invocation omitted `-c pyproject.toml`; all six async cases failed before execution due to absent pytest configuration. That setup-only failure is preserved separately in `m2-groom-configured-prompts-independent-before.log` and supplies no production evidence.

No source or original test oracle was changed. Existing broad gates are not rerun here because this review adds no production source. The tests do not establish that a real model grades the four organizational conditions correctly.

## Eight bounded review lenses

| Lens | Result |
|---|---|
| Correctness | Three findings above remain open; no whole-M2 acceptance. |
| Concurrency/resource ownership | Probe reads current native inputs; empty executor and owned workspaces settle. No new mutation/lease behavior; 389 authority is separate. |
| Types/contracts | Registered `PromptKey` is too broad for this specific native admission contract; the five-field shape can remain intact with explicit consumer validation. |
| Architecture/hexagonal | Keep native tracker facts and typed prompt provider. Do not synthesize authored ticket/pass artifacts or add a live Linear-issue dependency. |
| DRY/KISS | Reuse D12 role prompts, existing renderer and per-call rubric binding; no extra session/key, GROOM-specific orchestration, semantic classifier, or fallback. |
| Security/authority | Zero writes and no approval supplied in reproduction. Routine supplier repair cannot consume a historical spent delegation or close reserved lease questions. |
| Oracle fidelity | Original owner tests override both keys; counterexamples retain original declarations and independently isolate each boundary. Semantic model correctness remains untested. |
| Scope/integration | Confine correction to rubric supplier metadata/rendering, native admission validation, role prose and matching tests/docs. Serialize the owner render-site edit after root releases 389 ownership. |

## Finite correction proposal — pending root agreement, not authored

Retain five-field mandates and existing key/session enums. Supply explicit rubric data through the existing prompt infrastructure, selected by `rubric_prompt_key`, rendered from available native inputs, and carried solely in `mandate_rubric`. Provide the three already-authorized predicates as data for both sets. Validate the native admission role and required rubric/input contract before scheduling. Make assess/verify follow the selected rubric instead of always demanding dry implementation. Prove old declarations fail at boot with named keys and missing inputs; prove corrected configuration supplies the actual organizational rubric through the production constructor; retain all original authority/retry oracles. Exact supplier representation requires root agreement before editing shared source.

No integration is authorized from this investigation yet. Production source remains clean at the reviewed SHA.

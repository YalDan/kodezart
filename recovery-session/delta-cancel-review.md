# Scheduled gate cancellation — separate corrective candidate

Frozen CLEAN864bc0ab314183fed0deba95037f4b247e224552, tree73223dd073e0224116b5f1b0a576a239b0dbc121, parent ea1d91596cc862dbfaa48abb149d2480702bf1bb. Writer /private/tmp/kodezart-v03-m1-delta-cancel, branch codex/v03-m1-delta-cancel. Provenance correction after root review: both source try/rearm protections already exist in the declared1f4296c donor and current canonical3528bd4351885332e910b158eae8fb26344623d7. This fixes an M1 extraction omission, not a new canonical defect. The original M1 baseline/current counterexamples remain genuine. Prompt source matches donor; dispatch differs only by moving the effect-free changed tuple initialization inside the try. No canonical source patch is needed; root may import only the meaningful regression tests there.

## Requirement and reproduction

Live KOD164 requires both scheduled consumers to retain their gate marks when a pass fails, so the next tick reads the same window; success advances them. Current actual source has exactly two PassGate.delta consumers: GatedDispatchPass.run and run_prompt_pass. Both previously awaited delta outside their existing BaseException/rearm boundary. PassGate.delta advances marks and observations in _observe/_issue_delta, then awaits async logging. Cancellation at that log consumed a wake-up even though no dispatcher/session started.

The original full typed-policy gate at990 failed the existing10ms dispatch budget control (dispatcher.calls=0). Unchanged dispatch module reran29passed0.98s; this did not dismiss the failure. An event-synchronized real-gate probe at990 instead showed the actual lost mark1red3.06s; identical source at extraction baseline fec reproduced1red3.05s. The prompt consumer's analogous source probe reproduced1red15.32s at ea1d915. No test timeout was increased.

## Change

Only src/kodezart/services/dispatch_pass.py and src/kodezart/services/prompt_pass.py change production behavior: move gate.delta and the immediate quiet-pass logging into each caller's existing try/except BaseException which already calls the gate's canonical rearm method before re-raising. Same catch, same exception identity, same clocks, same marks/replay algorithm, same scheduler and existing effect/session behavior. Production source delta10added/10removed lines.

Three new tests/services modules retain the original synchronized dispatch/prompt probes and add paired caller controls. They prove: canceled observed window is asked again; no session/dispatch preceded cancellation; a separate pass advances a newer mark while the first is paused and retains it after cancellation; failure before any observed page preserves either absence or a previous completed mark; cancellation while the next quiet delta is logging preserves the completed window and remains skipped on retry. The original entire dispatch/prompt/gate/scheduler files and10ms/50ms deadline controls are byte-identical to parent.

Production ownership is serial: build_dispatch_passes builds a separate PassGate per scheduled dispatch pass; prompt pass configuration similarly owns its gate; PassScheduler._drive awaits each _tick before scheduling its next. PassGate's mutable _advanced/_observations state is not a general concurrent shared-instance API. No new lock or ledger was invented, and no guarantee for two overlapping calls on the same instance is claimed. Both actual consumers are covered; no new caller was created.

## Executed evidence

Logs and preserved original probes: /private/tmp/kodezart-recovery-session.

- session-policy-delta-cancel-990652c.log: original dispatch counterexample1red3.06s.
- session-policy-delta-cancel-fec7f28.log: same probe against untouched baseline1red3.05s.
- prompt-delta-cancel-before-ea1d915.log: original prompt counterexample1red15.32s.
- delta-cancel-final-controls-before-ea1d915.log: final exact ten tests against unmodified policy source,4failed6passed1.42s. These are the two original cancellation controls plus the two canceled-window retry variants; controls for unchanged prior marks already pass.
- delta-cancel-affected-after.log: first eight-control plus existing full corpus120passed2.94s.
- delta-cancel-final-affected.log: final ten plus existing full dispatch/prompt/gate/scheduler corpus122passed4.58s.
- delta-cancel-final-types.log: strict mypy two source files clean.
- delta-cancel-final-ruff.log: Ruff all five files clean; final format check five files clean.
- delta-cancel-source-test-proof.json: original external probe ASTs identical after Ruff formatting; original four existing source/test oracle files byte-identical. Initial three long-line Ruff diagnostics were fixed by formatting before tests; no assertion change.

Actual final command: uv run --locked pytest -q tests/services/test_dispatch_delta_cancel_independent.py tests/services/test_prompt_delta_cancel_independent.py tests/services/test_pass_delta_cancellation.py tests/services/test_dispatch_pass.py tests/services/test_prompt_pass.py tests/services/test_pass_gate.py tests/services/test_pass_scheduler.py . Parent before command runs those same first three modules externally with PYTHONPATH=.:src and -c pyproject.toml.

## Eight lenses

| Lens | Bounded assessment |
|---|---|
| SOLID | Each existing consumer keeps ownership of its gate unwind; canonical PassGate owns restoration arithmetic. |
| DRY | Reuses both existing catch/rearm blocks; no second mark store, replay mechanism or cancellation wrapper. |
| Hexagonal architecture | Real gate and actual consumers, controlled external tracker/logger/session boundaries; no production protocol change. |
| KISS | Only the protected statement scope changes. |
| Typed agent calls instead of semantic heuristics | No policy or prompt interpretation; cancellation and unchanged exception objects retain their existing semantics. |
| Official framework practices (version-matched) | Python3.12 actual Task.cancel and wait_for deadline controls; synchronous rearm completes during unwind before re-raise. No new shielding behavior. |
| Type safety | Neutral: existing typed fields, return types and BaseException boundary preserved. |
| Repository hygiene | Isolated separate commit, unchanged original timeout tests, before/after logs and hashes, no maintained/canonical source mutation. |

Root may independently review and cherry-pick only864bc0a after accepted policy closure onto M1; canonical has the same two source statement blocks and may receive the same separately reviewed hunk. No other files/dependencies needed beyond existing gate implementation. Fresh integrated full gate remains root responsibility. This is not release completion or general shared-gate fencing.

Owning73 source/correction checkpoint:https://linear.app/duckburg/issue/KOD-73#comment-9051e0ef-6b7a-405c-bace-716620695e98 .

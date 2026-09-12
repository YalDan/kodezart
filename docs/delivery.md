# Authored delivery

`build_workflow_engine` composes `AuthoredDeliveryCoordinator` around the shared
fire graph. The authored HTTP path creates the PR, watches checks, routes a
reproduced work defect through the existing remediation entry, and emits its
existing terminal event. Scoped execution currently refuses before preparation:
there is no active scope walker or independent delivery coordinator.

The accepted and stalled PR-opening nodes use the same watch route. The existing
acceptance/outcome classifier retains `stalled_pr_opened` when checks recover;
green checks do not establish that stalled acceptance criteria passed. PR bodies
retain the existing total FireSpec formatter, artifact cleanup, title/body gate
and fixed issue-line validation. No bytes are appended after the gate. The three
active authored prompt consumers retain their dispatch-base prompt digests.

`KODEZART_DELIVERY_MAX_CONCURRENT_WATCHES` bounds simultaneous watches per
coordinator (default 4, range 1–32). Its semaphore encloses the initial watch and
all same-commit reruns. Failure or cancellation releases the slot. Existing
adapter polling bounds still control each watch.

`KODEZART_DELIVERY_RED_RERUN_MAX_ATTEMPTS` bounds reruns at one observed commit
(default 1, range 0–5). The single `classify_red_checks` service also serves
`AuditForgeVerifier`. The original failing set comes from `failed_check_names`
at the watched branch; `CIObservationReader` supplies its retained SHA and check
roster from the same native response. Neither read requests another check set.
Reruns use that immutable SHA, including a cleanup commit different from the
fire's original tip. An incomplete, missing or contradictory observation refuses
before rerun or remediation. The native adapter correlates each requested Actions
attempt and keeps task-local observation identity.

Repository declarations are supplied directly from the loaded operation.
Matching `CheckStep.forge_check` and explicit `runner_environment=False` facts
establish an unmet prerequisite before any rerun. Missing repository or
prerequisite declarations establish no exemption; duplicate repository addresses
refuse. The check summary is never parsed to infer a class.

| Observation | Active authored behavior |
| --- | --- |
| Same-SHA rerun becomes nonred | `RUNNER_FLAKE`; no remediation round |
| Explicitly unmet prerequisite of a failing check | `ci_failed_environment_prerequisite`; no rerun or fix |
| Every rerun stays red with the original failing set | `WORK_DEFECT`; the shared remediation entry, within its existing budget |
| Every rerun stays red and a failing set differs | `ci_failed_unclassified`; no fix |
| No checks and no declared workflow | `ci_not_configured` |
| No run despite declared workflows | `ci_no_run_at_ref`, unless the repository explicitly declares `forge_exempt` |

A zero rerun bound reproduces a red vacuously and reaches the work-defect route
unless an explicit unmet prerequisite takes precedence. The four red classes use
one vocabulary. The historical root/cascade check-chain classifier and authored
terminal outcome classifier address different facts and remain separate.

The retired alternative's `LaneDispatch`, `DeliveryContext`, `LaneDelivery`, PR
content editor/query capabilities and their exclusive fixtures are removed.
The actual PR-state audit reader and native lane reports remain. Unused scope
terminal/residual wrappers are removed without deleting public outcome values.

This change does not implement scoped dispatch, native terminal/residual
publication, tracker criterion state writes, or PR content replay. The authored
PR creation behavior remains its existing API; the retired alternative's
optimistic replay checks are not claimed as active behavior. No delivery path
merges a PR or writes tracker issue state.

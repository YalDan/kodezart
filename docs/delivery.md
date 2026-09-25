# Authored delivery

`build_workflow_engine` composes `AuthoredDeliveryCoordinator` around the shared
fire graph. The authored HTTP path creates the PR, watches checks, routes a
reproduced work defect through the existing remediation entry, and emits its
existing terminal event.

The scope path runs inside the same coordinator. Its fire graph is the third
composition `RalphWorkflowEngine` compiles in `chains/ralph_workflow.py`: an
engine given `ScopeStages` holds `_build_scope_graph` (groom, prep, the loop,
the merge, the board's "is it done" answer, the review) in place of the
authored graph. `composition/engine.py` builds a forge arm and a forge-less arm
of it behind the scope entry (`services/scope_entry.py`). On a scope run the
coordinator opens one pull request per declared repository the deliverable
branch gained commits in, each against that repository's trunk, and watches
the checks of every one of them: the run's checks fail when any repository's
fail, with the first red repository's class. See
[the two workflows side by side](workflows-v02-v03.md) and
[running a scope](running-a-scope.md).

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
The actual PR-state audit reader remains; the native lane report
collection was retired in favour of `ScopeLaneEntry.done` (KOD-866). Unused scope
terminal/residual wrappers are removed without deleting public outcome values.

This change does not implement scoped dispatch, native terminal/residual
publication, tracker criterion state writes, or PR content replay. The authored
PR creation behavior remains its existing API; the retired alternative's
optimistic replay checks are not claimed as active behavior. No delivery path
merges a PR or writes tracker issue state.

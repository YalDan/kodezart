# Delivery coordinator boundary

`DeliveryCoordinator.deliver(dispatch, feature_branch=..., final_commit_sha=...,
context=...)` opens an accepted lane's pull request and observes its checks.
`LaneDispatch` contains the lane key, issue identity, head branch and recorded
`BaseSpec`. `DeliveryContext` supplies the existing execution context, fire
outcome, ticket, validated criteria, iteration count, flags and repository
visibility used by the PR-description session. These are caller inputs; the
coordinator does not synthesize a legacy ticket from tracker issue text.

The common route accepts `handed_off_for_delivery`. It verifies that the
execution carries the dispatched issue's FIRE run identity, that the
terminal head and recorded base agree with the call, that both branches exist
on the configured remote, and that the remote head still matches the fire's
final SHA. A missing ref raises `BaseResolutionError`; an inconsistent handoff
raises `DeliveryContextError`. A dependent lane can open against its blocker's
branch before that blocker has a PR.

If an artifact cleaner is supplied, it runs before description generation and
both remote refs are checked again afterward. Cleanup may advance the branch;
the caller's original fire SHA is preserved. The description session uses the
existing PR-description prompt and output schema in a fresh read-only session,
with the original run identity. The harness appends recorded flags and issue
identity before sending both title and body through the shared outbound gate.

The coordinator creates the PR and calls `wait_for_checks` with its head branch.
One semaphore per coordinator limits concurrent watches using
`KODEZART_DELIVERY_MAX_CONCURRENT_WATCHES`. A failed or canceled watch releases
its slot. The existing CI poll budgets remain adapter configuration.

| Observation | Result |
| --- | --- |
| Checks pass | Open PR and `ci_passed` |
| No checks, and the adapter confirms no active workflow declaration | Open PR and `ci_not_configured`, retaining `checks_passed=None` |
| Red checks, or no checks despite an active declaration | `DeliveryRouteUnavailableError` carrying the observed PR and check facts |
| Any other fire outcome | `DeliveryRouteUnavailableError` before writes |
| Failed forge or declaration read | The adapter's typed refusal propagates |

The coordinator has no merge capability or tracker issue-state writer. An
unavailable route does not produce a successful `LaneDelivery` or claim that
a residual was published.

This boundary is callable independently; scope-walker dispatch and application
composition are not connected yet. The legacy fire graph still owns its prior
PR/check nodes until that extraction is completed. Stalled-fire handoff,
check-before-create replay, same-SHA linkage between the initial branch watch
and red re-observation, the shared remediation loop, durable residual
publication, and declared-no-run exemption/close-out remain unfinished. The
existing pure red classifier is not invoked by this common route.

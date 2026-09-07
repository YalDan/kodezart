# Delivery coordinator boundary

`DeliveryCoordinator.deliver(dispatch, feature_branch=..., final_commit_sha=...,
context=...)` creates or edits an accepted lane's pull request and observes its checks.
`LaneDispatch` contains the lane key, issue identity, head branch and recorded
`BaseSpec`. `DeliveryContext` supplies the existing execution context, fire
outcome, `FireSpec`, criteria, iteration count, flags and repository visibility
used by the PR-description session. Authored calls supply
`AuthoredSpec(ticket=...)` with validated authored criteria. Tracker calls supply
the captured `TrackerSpec` and the criterion issues themselves, in its recorded
key order; each must retain its owning subject and configured criterion
membership. Empty, mixed, duplicate or mismatched tracker criteria refuse at
context construction, and a subject differing from the dispatch refuses before
side effects. These are caller inputs; the coordinator neither constructs a
legacy ticket nor re-reads the tracker. The one total formatter preserves
authored subject bytes and renders a tracker subject's body verbatim. Both
shipped PR prompts retain tracker criterion keys, owning issue references and
bodies without minting legacy criterion identities or classes.

The existing authored implementation, remediation and workflow PR bindings also
use the total formatter. A generated ticket corpus exercises the actual
consumers in both shipped prompt sets against 192 prompt digests captured on the
dispatch base, preserving the authored bytes without changing existing goldens.
The branch-name input still belongs to its earlier dispatch stage.

The common route accepts `handed_off_for_delivery`. It verifies that the
execution carries the dispatched issue's FIRE run identity, that the
terminal head and recorded base agree with the call, that both branches exist
on the configured remote, and that the remote head still matches the fire's
final SHA. An existing-PR replay also accepts a descendant whose nonempty diff
is confined to the cleaner's owned `.kodezart/` directory: it fetches the native
Git objects and proves ancestry and changed paths. This permits cold replay
after cleanup without replacing the original fire SHA or accepting later code
changes. A missing ref raises `BaseResolutionError`; an inconsistent handoff
raises `DeliveryContextError`. A dependent lane can open against its blocker's
branch before that blocker has a PR.

The existing `ForgeQuery` is a separate required read dependency. After the
handoff identity is validated, the coordinator looks up the open PR for that
repository and head. Only a successful empty lookup permits creation. An
existing PR is read through the separate `PRContentEditor`: URL, number, head,
base, title and body. The native adapter requires a unique open PR for the exact
repository and head; ambiguity or a conflicting identity raises
`PRContentConflictError`, while malformed or unreadable responses remain typed
adapter errors. Successful replay retains that PR's identity.

After authoring and gating, the editor re-reads the expected content snapshot
and refuses an intervening change. Matching fields cause no write. Otherwise,
the GitHub adapter sends one PATCH containing only differing title, body and
base fields and verifies the response. It never retries that mutation after an
uncertain transport result. Both adapters share content conformance tests;
native coordinator tests cover creation followed by replay and existing-PR
editing followed by a replay with no additional content write.

These are optimistic checks. The [GitHub pull-request REST contract](https://docs.github.com/en/rest/pulls/pulls)
does not provide the content editor with an atomic compare-and-swap guarantee.
Concurrent edits can still arrive between the read and PATCH, and simultaneous
calls can still race on an absent PR. These limitations are not treated as
successful exclusion.

If an artifact cleaner is supplied, it runs before description generation and
both remote refs are checked again afterward. Cleanup may advance the branch;
the caller's original fire SHA is preserved. The description session uses the
existing PR-description prompt and output schema in a fresh read-only session,
with the original run identity. The harness appends recorded flags and issue
identity before sending both title and body through the shared outbound gate.
Every PR writer then validates the fixed tracker-issue line in the gated body.
If rewriting removed or changed that identity, `PRTrackerIdentityError`
refuses publication; the writer never appends bytes after the gate. Permitted
redaction of other prose remains publishable, and legacy calls without an
issue key retain their existing behavior.

Retargeting an existing PR sends the dispatch-resolved base through the shared
identifier gate before PATCH. A blocked or rewritten reference refuses the
update; it never substitutes another branch. `PRCreator` remains exactly
`create_pr` and `comment_on_pr`; content reads and edits expose no merge or
mergedness operation.
The composition module's `pr_content_editor_for_origin` selects this capability
using the shared origin predicate, returning no editor for a `file://` origin or
an absent client.

The coordinator creates or edits the PR and calls `wait_for_checks` with its head branch.
One semaphore per coordinator limits concurrent watches using
`KODEZART_DELIVERY_MAX_CONCURRENT_WATCHES`. A failed or canceled watch releases
its slot. The existing CI poll budgets remain adapter configuration.

A completed red now reaches the existing structural classifier. The separate
`CIObservationReader` returns the original watch's commit SHA, verdict and
failing names, using the native check run's `head_sha` from the
[GitHub Checks response](https://docs.github.com/en/rest/checks/runs#list-check-runs-for-a-git-reference).
It performs no second query: a moving branch cannot replace the original
failing set. Missing or mixed commit identities, incomplete or nonterminal
sets, and absent observations raise `CheckObservationError`. Each async task
owns its observations; starting another watch clears the previous result
before that new watch can fail or be canceled. The normal monitor retains its
four methods and its existing timeout/no-CI behavior; a timeout cannot supply
the completed red evidence this reader requires.

The observed commit must match the delivered remote head, including a cleanup
commit when present. The classifier receives that observed SHA and failing
set, then follows the declared prerequisite and bounded rerun order. Its
repository declarations come from the required `OperationConfig`, resolved
against the delivered URL using the existing clone URL resolver. Missing or
ambiguous repository declarations refuse classification; an undeclared
environment prerequisite never establishes that it is unmet.
After recovery the coordinator re-reads the remote head and refuses if it
moved away from the commit that was checked. These are observations, not an
atomic lock on a branch another writer can move.

A not-red rerun establishes `RUNNER_FLAKE`. Green returns through the ordinary
successful PR validation; `None` still needs a successful declaration read
establishing no CI. Rerun watches retain the same semaphore slot and create no
remediation session. Other red classes remain typed unavailable routes with
their observed check facts. The per-origin `ci_observation_reader_for_origin`
selector supplies no capability for a `file://` origin or an absent client.

Before returning success it re-observes the same unique open PR, its recorded
base and its fixed issue line. Closure, ambiguity or a changed identity/base
during watching cannot produce a stale successful result.

| Observation | Result |
| --- | --- |
| Checks pass | Open PR and `ci_passed` |
| No checks, and the adapter confirms no active workflow declaration | Open PR and `ci_not_configured`, retaining `checks_passed=None` |
| Red checks that recover at the same commit | Ordinary green/no-CI result after bounded rerun |
| Reproduced, prerequisite-unmet or unclassified red, or no checks despite an active declaration | `DeliveryRouteUnavailableError` carrying the observed PR and check facts |
| Any other fire outcome | `DeliveryRouteUnavailableError` before writes |
| Failed forge or declaration read | The adapter's typed refusal propagates |

The coordinator has no merge capability or tracker issue-state writer. An
unavailable route does not produce a successful `LaneDelivery` or claim that
a residual was published.

This boundary is callable independently; scope-walker dispatch and application
composition are not connected yet. The legacy fire graph still owns its prior
PR/check nodes until that extraction is completed. Stalled-fire handoff,
the shared remediation loop, durable residual publication, and declared-no-run
exemption/close-out remain unfinished.
Tracker fire entry, approval/state eligibility, per-iteration criterion queries
and the write-only artifact projection remain separate integration work. This
delivery input path does not establish any of those producer behaviors.

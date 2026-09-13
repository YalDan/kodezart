# Organize corrective owner freeze

Exact corrective SHA: `ee1d44bd540304f5effce4021f547768cfe5d82d`, parent `161ca93fd9fb83d64516ee11b8ed53944a9a517e`, in `/private/tmp/kodezart-v03-recovery-organize-owner`. Tree clean. Requested Astra/high; effective runtime unverified. Author evidence only; independent reviewers have been notified to repeat their original probes. No issue states, initiative, Notion, push or integration changed.

## Findings corrected

1. Criterion creation ignored the author's captured parent revision. The owner now rereads and validates exact issue identity/body digest after the author, before any proposal disposition, and again after outbound/lease waits before every body write or child creation. A stale proposal refuses before the adapter operation. The body-write regression now expects that earlier exact `OrganizeWriteRefusalError`, rather than the former downstream adapter `StaleWriteError`; body preservation and no-write assertions remain unchanged.
2. Approval arriving during assessment could still trigger escalation. Organize's guard now verifies current scope membership, the configured phase gate, then approval as the last authority read. It runs before escalation starts (so known approval avoids even lease publication). Root authorized the existing LaneEscalationWriter's optional `before_write: Callable[[], Awaitable[None]] | None` callback: it runs after internal gate/lease waits before the actual comment write and again after renewal before classification. Existing callers preserve behavior; no duplicate writer or proxy was introduced. The callback checks captured source revision and, when applicable, admission-result liveness as well as current authority.
3. A subject leaving its configured scope during authorship could still receive description and marker writes. All guarded target writes now require the target in the current canonical `scope_issues` result. Phase-marker authority checks follow fresh phase-proof reads, including the proof after lease acquisition.
4. Exhausted canonical writeback discarded the actual independent refutation in favor of prior admission evidence. `StageHaltReport.write_back_results` now retains complete typed `WriteBackResult` values: exact artifact, each finding, citations and ordered rounds. The existing actual nested-setting bound is retained; validation ties a writeback exhaustion bound to the retained round counts and refuses a settled result as halted evidence. The escalation carries the actual verified-write evidence, not a buildability translation. Escalation-recording failure also retains these typed results without fabricating an exhaustion bound.

## Files

- `src/kodezart/services/organize_owner.py`
- `src/kodezart/services/lane_escalation.py` (root-authorized callback hunk only)
- `src/kodezart/types/domain/organize_owner.py`
- `tests/chains/test_organize_owner.py` (precise earlier refusal oracle)
- `tests/chains/test_organize_native_independent.py` (reviewer regression retained; one long string wrapped without changing bytes)
- `tests/chains/test_organize_delivery_independent.py` (reviewer regression retained unchanged)
- `tests/chains/test_organize_correction.py` (actual escalation comment accepted, approval arrives, second label write refused; paired unapproved success)

No composition, scheduler, main, generic protocol, adapter, prompt or schema-census source edits belong to this correction.

## Actual validation

All Python commands used `/Users/kodezart/.local/bin/uv run --locked`.

- Before source changes, copied original independent native test: **3 failed, 2 passed in 1.01s**, `organize-correction-before.log`. The three failures are stale criterion creation, approval-during-assessment escalation writes and missing `tests/absent.py` refutation from halt JSON.
- Intermediate first correction: **2 failed, 19 passed**. One failure correctly exposed that guarding only inside escalation occurred after lease-comment writes; added the pre-call guard while retaining both internal guards. The other was the original owner test expecting the later adapter exception instead of the new earlier owner refusal. No broad exception acceptance was added.
- Broader Organize/schema/prompt/writeback/escalation selection: **349 passed, 2 deselected in 10.99s**, `organize-correction-final-tests.log`. This preceded the final narrow escalation-admission liveness check.
- Exact final affected owner/native/delivery/callback/escalation selection: **61 passed, 2 deselected in 1.77s**, `organize-correction-exact-final.log`:

  `uv run --locked pytest -q tests/chains/test_organize_native_independent.py tests/chains/test_organize_delivery_independent.py tests/chains/test_organize_correction.py tests/chains/test_organize_owner.py tests/tracker/test_lane_escalation.py -k 'not halted_first_binding'`

- Final `uv run --locked mypy src`: **301 source files clean**, `organize-correction-final-mypy.log`.
- Final Ruff and format check for all seven files: clean, `organize-correction-final-ruff.log`; `git diff --check` clean.

The two deselected tests are the independent delivery review's `test_halted_first_binding_does_not_starve_second_binding[False/True]`. They remain in the repository and are owned by root's separate tick correction. This donor does not claim them green or the whole suite green. No live agent sessions or real external tracker writes were used by these tests; production owner/factory/adapter paths are exercised with executor/board boundary doubles. Reviewer originals outside the donor remain untouched.

## Eight lenses and limits

- SOLID: Organize owns authorization and source lineage; the existing escalation writer owns its two writes and accepts a narrow policy guard.
- DRY: one current-authority helper applies to body/child/marker/escalation writes; canonical verification and escalation publication remain single implementations.
- Hexagonal: existing scope/revision/approval ports provide observations, adapter transport/error behavior is unchanged; no backend data interpretation moved into the owner.
- KISS: three source files; no new service, generic proxy, retry framework or authority cache.
- Typed agent calls: no wire schema changes. Actual fresh writeback results remain typed through halt rather than collapsed to strings or admission verdicts; the escalation's existing evidence field renders that retained result for publication.
- Official framework/version lens: no framework API or dependency change. Existing Pydantic validation and async callback patterns are retained; the baseline L2 framework evidence remains in `organize-owner-review.md`. No new independent official-doc lookup was run for this correction.
- Type safety: strict whole-source mypy passes; callback is `Callable[[], Awaitable[None]]`; halt validates actual round correspondence. Cancellation and programming errors are not caught as policy success.
- Hygiene: clean frozen commit, bounded ownership, original independent counterexamples retained, one exact error-oracle migration documented, no source or assertion broadening to erase failures.

These are fresh observation guards, not transactions. Reads can be followed by external changes and the backend can still accept already-issued writes after lease expiry or approval. The known backend fencing limitation remains. The supplemental second-write test proves a newly observed approval prevents the next write; it does not claim rollback of the accepted first comment. Strict compare-and-write body behavior remains at the adapter, while membership/approval/child creation have no backend atomic predicate.

## Dependencies and integration

Owning public issue: [KOD74](https://linear.app/duckburg/issue/KOD-74); baseline evidence [b2ad3029](https://linear.app/duckburg/issue/KOD-74#comment-b2ad3029-049d-436d-80fd-8ef9543202d9). KOD97 read-only reconnaissance was completed first in `semantic-amendment-reconnaissance.md` and made no source changes.

Corrective public evidence: [ef63962d](https://linear.app/duckburg/issue/KOD-74#comment-ef63962d-43a5-468f-bd6b-e275b12d0b21), explicitly author evidence with independent review pending.

Integrate only after independent corrective review: apply `161ca93` and then `ee1d44b` onto the canonical dependency stack, preserving root's separately owned pure-preflight, tick starvation/typed halt and scheduler integration. Run the retained full delivery regression once root's tick patch is stacked. This is a correction to the bounded owner slice, not full KOD74 or founder-owned capability acceptance. Missing graph-authoring capabilities and backend inflight limits remain as recorded in the baseline envelope.

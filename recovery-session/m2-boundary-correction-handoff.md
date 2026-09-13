# Bounded M2 correction

Frozen `a15b33484bc0f88975c910c1706edf20805d98f8`, tree `c756178d9dc8cb4fd80c9b0eaa6b69343c51590b`, parent `73cc5def12a2daa4e0ba319660db76cdad781882`. Exclusive writer `/private/tmp/kodezart-v03-m2-organize-boundaries`, branch `codex/v03-m2-organize-boundaries`; original73cc fullgate tree remained untouched.

Independent baseline evidence: native criterion create under expired grant FAIL and live control PASS (`m2-independent-criterion-retry-daae.log`,16.08s). Reviewer original probe copied byte-identical into `tests/tracker/test_criterion_retry_independent.py`. Review comment https://linear.app/duckburg/issue/KOD-74#comment-cc8a848c-74cc-48d6-b4aa-8da4f46cb39f . Actual prompt execution diagnostic `m2-independent-prompt-contract-daae.log` proves criteria role promised body writes the owner refuses and unavailable capability values outside schema.

Only five files changed:
1. `adapters/linear_mcp_tracker.py`: new frozen `_CriterionCreation` receipt, and `create_criterion_if_absent` wraps its original child identity lookup, current parent/team/state preparation, configured classification checks, and final lease check inside existing `_retry_call` complete attempt. The attempt uses `_send` once. Existing identity returns retain original early-return behavior. Completed receipts pass to original readback outside resend scope. No retry policy, transport taxonomy, backend grant protocol or graph/split algorithm changed.
2–3. Both shipped `organize_criteria_author.md` files replace their stale capability paragraph with actual criteria-only role, distinct structural author, `unavailable` only `criterion_edit`, and unresolved only real human decision. No runtime schema/capability expansion.
4. Exact peer expired/live probe preserved.
5. `test_criterion_receipt_boundary.py` adds actual successful native creation followed by injected known-unsent readback failure, asserting one create and complete grant release. This protects the newly structured receipt boundary.

Validation on immutable correction content:
- `uv run --locked --python3.12 pytest -q tests/tracker/test_criterion_retry_independent.py tests/tracker/test_criterion_creation.py tests/tracker/test_organize_graph*.py tests/prompts/test_organize_roles.py tests/chains/test_organize_native_independent.py tests/chains/test_organize_owner.py`:129 passed8.85s (`m2-boundary-correction-tests.log`). Existing original graph/split expiry/source retry, unknown response, final lease and receipt controls unchanged.
- Actual receipt boundary1 passed0.18s (`m2-criterion-receipt-boundary.log`).
- `UV_PYTHON=3.12 UV_LOCKED=1 PATH=/Users/kodezart/.local/bin:$PATH make format-check lint type-check`:470 files formatted/Ruff clean;215 strict source files passed (`m2-boundary-static.log`).
- `git diff --check` clean.

Peer independently reviewing exact frozen a15b. Parent integrates reviewed correction into existing maintained M2 only; no new PR. A subsequent separate fixture-only correction adds explicit workspace to two existing composed-dispatch test helpers (required actual M2 collaborator), with assertions untouched. Its tests are running after a15b freeze; do not confuse it with frozen corrective source.

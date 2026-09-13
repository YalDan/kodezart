# Organize halt cause/evidence correction

Frozen clean implementation `64d64b315596b3a60ad8f6a05d134e7aa926634c`, parent `68eabb5157bbebd82ed181914bd39e32c62ea21c`, isolated `/private/tmp/kodezart-v03-recovery-organize-halt-types`. Requested high; effective runtime unverified. Author evidence; independent review still required. No adapter, protocol, composition, scheduler, RulingId, L8, issue-state, initiative or Notion changes.

## Findings and change

Root's new counterexample is real: `StageHaltReport.model_validate({'cause': 'human_decision'})` accepted a halt with no actual choice or evidence. No runtime producer was observed emitting that shape. This is a deterministic model-contract gap, not a refutation of the independently approved scheduling path.

Before editing source, a 21-case matrix reproduced **10 failed / 11 passed in 0.39s**. Besides the empty human halt, the model accepted human halts supported only by a spec-gap/buildable admission, a generic finding or writeback result; accepted human questions on exhausted causes; accepted writeback results against admission/convergence bounds; and accepted a whitespace-only unrecorded issue ID. Existing valid bound and unrecorded controls were preserved.

`StageHaltReport` now wraps four cause-discriminated named variants:

- `AdmissionExhaustedHalt` requires the actual bound; a writeback loop requires its actual unsettled results and matching round counts, while admission-loop bounds cannot carry writeback results. Human questions and unrecorded IDs are forbidden.
- `ConvergenceExhaustedHalt` requires its convergence bound; human questions, writeback results and unrecorded IDs are forbidden.
- `HumanDecisionHalt` requires at least one actual author `UnresolvedProposal` or explicitly human-decision-classified refused admission. Those existing payload models require nonblank question/choice and evidence. Buildable/spec-gap admissions and general findings cannot stand in for a human decision; bounds/writeback/unrecorded IDs are forbidden.
- `EscalationUnrecordedHalt` requires nonblank affected issue IDs and forbids a fabricated bound. It retains any actual admission/findings/questions/writeback evidence that was being recorded. IDs alone remain a valid recording-failure carrier, preserving the existing contract.

The JSON remains the same flat object with its original seven fields and camel-case aliases. Existing consumers retain `.cause`, `.bound`, `.admission_results`, `.surviving_findings`, `.write_back_results`, `.questions`, and `.unrecorded_escalation_issue_ids`. Downstream code can narrow `.root` to a cause variant when it needs statically required bound/evidence fields. The owner's direct unrecorded constructor now constructs its named typed variant. The other owner boundary continues validating the completed flat payload; no model_copy, Any, cast or ignored type error was introduced.

This validates structural evidence and reason classification. It does not judge whether an asserted human choice is semantically necessary, establish truth of citations, or introduce a new stopping rule or founder decision.

## Files and validation

Changed files:

1. `src/kodezart/types/domain/organize_owner.py`
2. `src/kodezart/services/organize_owner.py` (unrecorded constructor migration only)
3. `tests/types/test_organize_halt.py`

All commands used `uv run --locked`:

- Before: `pytest -q tests/types/test_organize_halt.py` — **10 failed, 11 passed**, `organize-halt-types-before.log`.
- First after: type matrix plus owner/native/correction/typed-tick controls — **46 passed in 3.97s**, `organize-halt-types-after.log`.
- Final: `pytest -q tests/types/test_organize_halt.py tests/chains/test_organize_owner.py tests/chains/test_organize_correction.py tests/chains/test_organize_native_independent.py tests/chains/test_organize_delivery_independent.py tests/integration/test_organize_tick_halt.py tests/integration/test_organize_scheduler.py tests/types/test_wire_schemas.py` — **137 passed in 14.49s**, `organize-halt-types-final-tests.log`. This includes two additional missing/mismatched writeback-round negatives, flat JSON validation/construction roundtrips, and unchanged cited refs/ordered rounds.
- `mypy src` — **301 source files clean**, `organize-halt-types-mypy.log`.
- Ruff check and format for the three files — clean, `organize-halt-types-ruff.log`; `git diff --check` clean. A formatting-only wrap followed the full source mypy and preserved the exact validation message.

No full suite or live agent semantic evaluation was run. Production owner/scheduler controls use real collaborators with boundary doubles as previously reviewed.

## Eight lenses and type impact

SOLID: each cause owns its legal payload; the service only constructs the actual result. DRY: shared serialized evidence fields and unsettled-writeback invariant; no duplicate verifier or stop calculation. Hexagonal: no I/O in models and no transport/backend changes. KISS: four concrete variants, one flat wrapper, existing public read properties; no framework or new configuration. Typed agent calls: this is not an agent-output schema; the existing exact wire-schema census passes unchanged. Type safety: cause literals, required bounds and forbidden empty-tuple payloads narrow valid states; semantic human alternatives use one named validator because the unchanged flat JSON has no second discriminator. No casts/Any/defaulted stopping policy. Hygiene: three-file commit, clean donor, before/after counterexamples, exact fixture migration note below.

Framework/version: checked official **Pydantic v2.12.5** source documentation for [RootModel and root serialization](https://raw.githubusercontent.com/pydantic/pydantic/v2.12.5/docs/concepts/models.md) and [string-discriminated unions](https://raw.githubusercontent.com/pydantic/pydantic/v2.12.5/docs/concepts/unions.md), matching the locked installed dependency. RootModel preserves root serialization and a Literal cause selects the one variant to validate; actual snake/camel JSON roundtrips and constructor controls verify those properties here. Initial docs-site URL access failed; the exact-tag official repository documentation supplied the evidence.

## Integration and risks

Cherry-pick `64d64b3` after the already-reviewed owner/scheduler stack, pending independent source/oracle review. No source overlap beyond the two owned owner modules is expected.

The canonical retained `tests/integration/test_organize_scheduler_independent.py` (not present at the isolated68 base) used an empty human halt as the fixture for its project/initiative identity test. Root was notified to replace that invalid fixture with a real UnresolvedProposal carrying a question and evidence, leaving the identity assertions unchanged. This is a required fixture migration to the new contract, not a reason to allow empty human halts. That canonical file was not overwritten from this donor.

Existing JSON keys/values for valid reports remain stable, but introspection changes: StageHaltReport is now a RootModel, its actual fields live in `.root`, and its generated JSON schema is a cause-discriminated union. No production consumer at this base introspects the old `model_fields`; all current source consumers passed strict checking. Historical invalid empty/wrong-cause reports will now refuse, as intended. Existing backend inflight and full L6 aggregation limitations are unaffected.

Owning contract [KOD74](https://linear.app/duckburg/issue/KOD-74); [KOD475](https://linear.app/duckburg/issue/KOD-475) still owns terminal interpretation of actual configured bound evidence. This correction does not claim either whole issue complete.

Public bounded author evidence: [fbadfa8f](https://linear.app/duckburg/issue/KOD-74#comment-fbadfa8f-e4ec-4db3-9a79-5bf645ac6648).

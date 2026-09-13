# Corrective review candidate —179ef5acfad52758fc48ffe6fd0d32b2753931f2

This **supersedes76125c3's acceptance candidate**. Parent76125c36d83ad88c362978140502be1495b24670, same clean isolated branch/worktree. Fresh independent corrective review remains required. Ten-file corrective commit; no new dependency commits.

Independent reviewer found a real pre-write defect in761: record_run_alarm parsed one initial record, but the universal writer then read a newly damaged record and overwrote it without parsing that actual snapshot. This was distinct from the already documented unseen backend/CAS limitation. Original independent probe was copied byte-identically as tests/tracker/test_alarm_independent.py; reproduced1failed1passed before fix (`alarm-correction-before.log`).

Root-approved correction retains exactly one existing universal algorithm in private _upsert_comment, reached by unchanged public upsert_comment and by the alarm method. Both paths still unconditionally enforce attribution and live holder. Alarm supplies a typed synchronous validate_existing(TrackerComment) precondition. It parses the exact existing comment snapshot after awaited attribution/holder checks immediately before the no-op/save branch. The per-adapter _parse_alarm_comment helper is shared by read_run_alarm and this precondition. Public TrackerPort contract is unchanged. No second writer, retry loop, owner, verifier or lease implementation.

The shared typed extraction function is public read_alarm_value in the same domain module; all consumer imports migrated. Obsolete escalation_comment.body.partition/json.dumps call-allowlist entries were removed, retaining native reader JSON parsing where it actually belongs.

Expanded dependency inventory exposed two collector modules omitted from the initial925-test selection: tests/tracker/test_lane_record_signals.py and test_scope_tally.py. Old JSON-reading assertions failed38cases while68passed (`alarm-correction-missed-fixture-before.log`). Their exact values now compare typed LaneCommit rows, reference tuples and actual ScopeRef; arithmetic, source stamps, no-write/fresh-read guards and all other assertions remain unchanged. The earlier925 was an actual result but was not the entire affected collector inventory; this correction explicitly withdraws that broader scope claim.

Final executed command: uv run pytest -q tests/domain/test_{run_alarm,alarm_typed_evidence,escalation_ageing,record_consistency,record_superseded,surface_contention,barren_tick,mandate_graph,scope_tally}.py tests/tracker/test_{barren*,escalation*,mandate_graph,recorded_ruling_growth,run_alarm_records,alarm_independent,lane_record_signals,scope_tally}.py tests/services/test_run_surface_lease.py => **1052 passed27.25s**, `alarm-correction-expanded-final.log`.

Focused actual P1/owner/original independent controls60passed0.87s (`alarm-correction-current-snapshot.log`). Existing universal writer/pagination command: tests/tracker/test_idempotent_writes.py::TestCommentUpsert tests/tracker/test_comment_pages.py => **27passed0.44s**, `alarm-correction-universal-writer-executed.log`. Initial mistaken selection test_linear_mcp_tracker.py -k upsert selected0tests/62deselected (`alarm-correction-universal-writer.log`); no passing execution was attributed to it.

Strict mypy300source clean (`alarm-correction-mypy.log`); final Ruff clean and305source/selected fixture files formatted (`alarm-correction-ruff-final.log`, `alarm-correction-format-final.log`); git diff --check clean. All product source froze before final1052 run. No full suite run.

Eight lenses corrective delta: SOLID record-specific precondition inside existing writer; DRY same algorithm and parser per adapter, no duplicate lease; Hexagonal stable public port and synchronous typed callback confined to adapter; KISS one bounded precondition; Typed agent calls unchanged, typed record parse rather than heuristics; Official framework practices unchanged installed Pydantic validation and asyncio resource settlement; Type safety improved public extraction/API preserved; Repository hygiene isolated corrective commit with unchanged independent probe and two exact missed fixture migrations.

Snapshot validation is not backend CAS: an external mutation after the latest observed snapshot can remain unseen, and an already admitted save may land after expiry. Existing explicit nonfencing probe remains green. Scope/tick alarm publisher, signal totality and event806 application remain outside this type/P1 slice. Integrate76125c3 then179ef5a only after independent corrective review; preserve neighboring L2/native port/adapter/fake additions during root serialization.

---

# L8 typed alarm / P1 native persistence — frozen review envelope

Source SHA: **76125c36d83ad88c362978140502be1495b24670**. Exact parent **593fdc1d9231767491493160d6a9455faadee2e3**. Clean isolated author branch `codex/v03-recovery-alarm-owner`, worktree `/private/tmp/kodezart-v03-recovery-alarm-owner`. Requested Astra ultra inherited; effective model/effort unverified. No delegation.

## Bounded result and provenance

Six closed subject variants replace one conditional record. SURFACE carries actual WritableSurface; SCOPE has no lane/issue fields. AlarmReading now carries one of thirteen explicit typed evidence shapes actually consumed by the existing predicates. The exact six RunAlarm fields and all twelve AlarmSignal values remain unchanged. The eight existing domain/service consumers inspect typed values; JSON encode/decode is confined to the address/native record boundary. No JSON string domain payload reparsing, semantic text inference, guessed subject carrier or SHA was introduced.

Useful donor PR116 commit **1e36993355cf01fa17194c5f2c05c57bbb6e438a** was inspected, not wholesale imported. Its full `(subject, signal)` P1 identity and strict canonical codec were adapted to current typed subjects, neutral tracker failures, universal leased upsert and current fake fidelity. New `record_run_alarm(issue_key, alarm, holder)` and `read_run_alarm(issue_key, subject, signal)` require explicit native carrier identity. Missing configured `run_alarm` marker purpose refuses before mutations; no fallback prefix. The optional native port has no boot-wide marker requirement because no composed alarm publisher is added in this slice.

Live governing contracts: KOD103 current body and all comments, including 7c60c539-a110-4f62-a4a8-43d3c16029d7 (six typed subject variants) and 7b6966f1-fe5a-4545-962c-a49935d8576d (typed evidence), current KOD493 P1 Check, and KOD775 all comments including 37e7d7cd-6d8f-4b93-bb5a-b9c51e30cdd7. The latter is agent-authored explicitly delegated ORGANIZE mapping grounded in current493 Check; it is not attributed as a founder quote. Source research in `l8-harvest-review.md` precedes this implementation.

## Executed evidence

Reproduction before implementation: three focused controls failed in0.92s: missing six-arm discriminator, opaque JSON reading accepted, missing native alarm port. `alarm-owner-before.log`.

Final affected command (uv run pytest):
`tests/domain/test_{run_alarm,alarm_typed_evidence,escalation_ageing,record_consistency,record_superseded,surface_contention,barren_tick,mandate_graph,scope_tally}.py tests/tracker/test_{barren*,escalation*,mandate_graph,recorded_ruling_growth,run_alarm_records}.py`
**925 passed in11.43s**, `alarm-affected-final.log`. This includes source identity, fresh membership/approval reads, refusal arithmetic, recorded counts, closure and current escalation controls as well as new persistence conformance. No full suite run.

Focused new typed/P1 evidence: `tests/domain/test_alarm_typed_evidence.py tests/tracker/test_run_alarm_records.py`: **43 passed in0.50s**, `alarm-p1-expanded-final.log`. Real LinearMcpTracker talks to external fake MCP; the same native roundtrip contract runs FakeTrackerPort. No owner/admission replacement. Controls cover full six-field equality and readings order, two surfaces/same lane/signal, different signals, scope without lane, fresh adapter cold read, repeat zero native writes, same-address update, malformed framing/foreign subject/signal/duplicate JSON key/seventh field/duplicate address, missing/foreign/expired/implicit holder, concurrent disjoint addresses, explicit configured-duration renewal and permanent loss, cancellation settles in-flight write before release, exceptional exit release, and explicitly measured backend nonfencing.

Last static checks: `uv run mypy src` **300 source files clean**, `alarm-source-final-mypy.log`; `uv run ruff check src` plus affected tests and fakes clean, `alarm-final-ruff.log`; format check **321 files already formatted**, `alarm-final-format.log`; git diff --check clean. Public type docstrings were the only change after final925 test run; static checks ran after those docstrings.

Diagnostic history retained, not represented as acceptance: domain migration136failed346passed (`alarm-domain-fixture-diagnostic.log`); tracker migration25failed216passed (`alarm-tracker-fixture-diagnostic.log`); second fixture20failed868passed (`alarm-fixture-second-diagnostic.log`); first P1 fixture16failed14passed due missing existing claim-prefix fixture config plus model_fields deprecation (`alarm-p1-first-diagnostic.log`); corrected P1one failure29passed because a refused renewal may update/retract its existing grant, corrected oracle now checks no reacquisition/new alarm and no second retry (`alarm-p1-corrected.log`); expanded two failures41passed from a missing required-null resolution field and an expiry probe which had not crossed the explicitly renewed native deadline (`alarm-p1-expanded-diagnostic.log`). Corrections retain the actual semantic oracles: invalid model projections reject at construction; wrong typed evidence arm refuses with source metadata; ambiguous valid histories/commit rows still reach domain refusal. The final corpus above supersedes all those diagnostics.

## Limits and dependencies

This is **type/P1 persistence only**, not complete KOD103/L8 runtime. No partial nine-signal supervisor, new tick/exit publisher, event806 applier, seventh active field, fake Scope-as-lane holder, tracker workflow mutation, or new lease implementation. Future caller must supply the actual carrier issue_key, observed SHA and queue job holder, compose the complete write set, explicitly renew existing RunSurfaceLease, and settle writes before release. Existing port refuses expired ownership at admission; the backend cannot conditionally fence a save already in flight. `test_in_flight_backend_write_is_explicitly_not_fenced_by_lease_expiry` confirms a previously admitted request can land after a new owner acquires. No stronger guarantee claimed.

Old opaque-reading alarm payloads will fail the new closed shape; there was no composed native alarm writer on this baseline to migrate. No compatibility parser silently upgrades or invents missing evidence. The marker prefix must be configured by the actual future publisher. Scope terminal alarm mapping/totality and806 remain separately governed. Existing RunAlarm raised_by remains observed producer identity, distinct from explicit port writer holder; neither is inferred from a scope/lane name.

## Eight lenses

- SOLID: subject/evidence invariants live in native values; pure predicates retain arithmetic; adapter owns comment persistence and current ownership admission.
- DRY: one canonical full-address codec shared by real/fake, existing comment marker helper, existing RunSurfaceLease and universal upsert; no event/lease duplication.
- Hexagonal architecture: explicit TrackerPort methods, vendor transport remains inside Linear adapter; real component tested with external doubles.
- KISS: six subject variants and thirteen evidence wrappers correspond to existing actual consumer shapes; no new supervisor or event authority.
- Typed agent calls instead of semantic heuristics: no agent behavior added; explicit discriminators, observed IDs, attribution, typed rows and references replace string payload parsing.
- Official framework practices (version matched): installed Pydantic2.12.5 discriminated unions, TypeAdapter for the union/dataclass, frozen CamelCaseModel, strict native JSON validation and roundtrip tests; repository Python3.12 generic typing.
- Type safety: improvement. Closed subjects and evidence prevent mixed conditional fields/opaque JSON payloads; six-field RunAlarm stays stable. Missing required metadata is refused, never synthesized.
- Repository hygiene: exact authorized baseline, single frozen commit,31 cohesive source/test files, no canonical/source-owned neighboring hunk edits, no statuses/Notion/initiative/push/full-suite run.

## Integration

Fresh reviewers inspect immutable76125c3 against593fdc1. Root cherry-picks only76125c3 into canonical after independent review, resolving narrow imports/new method hunks in core/protocols.py, LinearMcpTracker and tests/fakes.py while preserving L2/native additions. No dependency commits are embedded in this branch. Re-run final affected command and shared conformance/source gates after integration; whole CI gate belongs to root.

Changed files:
- src/kodezart/adapters/linear_mcp_tracker.py
- src/kodezart/core/protocols.py
- src/kodezart/domain/mandate_graph.py
- src/kodezart/domain/run_alarm_record.py
- src/kodezart/domain/run_shape.py
- src/kodezart/services/barren_record_signals.py
- src/kodezart/services/escalation_signals.py
- src/kodezart/services/lane_record_signals.py
- src/kodezart/services/mandate_graph.py
- src/kodezart/services/run_shape.py
- src/kodezart/services/scope_tally.py
- src/kodezart/types/domain/run_alarm.py
- tests/domain/test_alarm_typed_evidence.py
- tests/domain/test_barren_tick.py
- tests/domain/test_escalation_ageing.py
- tests/domain/test_mandate_graph.py
- tests/domain/test_record_consistency.py
- tests/domain/test_record_superseded.py
- tests/domain/test_run_alarm.py
- tests/domain/test_scope_tally.py
- tests/domain/test_surface_contention.py
- tests/fakes.py
- tests/tracker/test_barren_record_collector.py
- tests/tracker/test_barren_tick.py
- tests/tracker/test_escalation_ageing.py
- tests/tracker/test_escalation_record_collector.py
- tests/tracker/test_escalation_record_reader.py
- tests/tracker/test_escalation_resolution_drift.py
- tests/tracker/test_mandate_graph.py
- tests/tracker/test_recorded_ruling_growth.py
- tests/tracker/test_run_alarm_records.py

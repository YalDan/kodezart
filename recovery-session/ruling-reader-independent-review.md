# Independent shared ruling-reader review

## Corrective review — accepted 2026-09-12

**Accept `03b99acb76a08b4c7000f052d90ef8a691496f95` together with the issue-wide reader dependency.** Review checkout is now detached at this exact source commit, clean. Before changing its head, the prior untracked independent probe was backed up and compared byte-for-byte to the committed copy. Original eighteen probes retain SHA256 `b535a73669b496fd6f95706a925768f0e1a2af86a5e5d02f5265446f01f43092`.

Reviewed the complete four-file corrective diff: one required-nullable native `parent_id` on the canonical wire model, one generic page loop rejecting unequal repeated native IDs, removal of redundant thread-only models and flag, and the previous permissive legacy test replaced with paired omitted-field refusal / explicit-null success while retaining its original escalation event. This closes the actual information-loss boundary rather than adding a ruling-only facade. Identical native page overlap still deduplicates; differing records refuse before consumer projection. Existing reply-aware reads retain their stricter behavior through the same unified path. No new write path or authority was introduced.

Executed on exact03b99:

- `uv run pytest -q tests/tracker/test_ruling_reader_independent.py tests/tracker/test_issue_ruling_records.py tests/tracker/test_ruling_records.py tests/tracker/test_comment_pages.py tests/tracker/test_escalation_resolution_boundary.py tests/tracker/test_port_failure_boundary.py`: **102 passed in 1.12s**, including all original18 unchanged. Log `ruling-reader-corrective-independent-03b99.log`.
- `uv run mypy src`: **309 source files clean**, log `ruling-reader-corrective-mypy-03b99.log`.
- Changed four-file Ruff and format check: **pass**, log `ruling-reader-corrective-ruff-03b99.log` (format direct tool output: four files already formatted).

Eight-lens corrective verdict: SOLID improved by enforcing provenance at the adapter that owns it; DRY improved by deleting thread-only duplicate models; Hexagonal boundary preserved; KISS improved by one unconditional wire/page contract; typed-agent-versus-heuristic lens neutral (no judgment introduced); framework practice uses the existing measured nullable field with required Pydantic validation; type safety improved (unknown is no longer converted into explicit absence); repository hygiene good (two concise source edits, original independent tests retained, no reviewer source edits).

Integration: root may integrate the issue-wide reader plus this correction, preserving shared adapter changes from other slices, then rerun the canonical gate. This acceptance covers the factual native reader and provenance correction only, not whole KOD97/KOD368. Original red evidence below remains historical rather than erased. Native precommit review continues separately.

Candidate: `68187b03a32fc894dec1741d527404925caf3972`, parent `44fd521b593f2dd9120d303379babc055f48728e`.
Review checkout: `/private/tmp/kodezart-v03-recovery-ruling-reader-review` (detached, source immutable).
Parent checkout: `/private/tmp/kodezart-v03-recovery-ruling-reader-before` (detached, source immutable).
Reviewer: independent surface-owner agent; requested inherited Astra ultra, effective setting unverified. No delegation.

## Findings and disposition

**Request changes at the shared native comment boundary; do not integrate the dependency as complete yet.** No lane-decoding regression was found. Two inherited adapter gaps defeat the query's advertised native provenance and conflict refusal guarantees:

1. `LinearMcpTracker._comment_wires` deduplicates by native ID and detects unequal repeated rows only when `require_reply_links=True`. Generic `list_comments` uses false. Two cursor pages reporting one native ID with different ruling resolutions or different question identities are silently collapsed before `RulingRecordReader` can detect either native-key or ruling-ID conflicts. Both actual-adapter refusal probes fail.
2. `LinearCommentWire.parent_id` has a `None` default. Omitted native reply metadata becomes an asserted top-level comment and passes the ruling reader's top-level check. Actual-adapter refusal probe fails. Explicit null is the valid positive control. The existing thread-specific wire already requires this nullable native field, and live connected-app KOD811 comments inspected during this review include explicit `parentId: null`.

Separate explicit-lane probes against the immutable parent fail for the same three inputs. These defects predate this change; they are not attributed to the new issue-wide lane decoder. Root owns the bounded shared adapter correction. No reviewer source correction was made.

Own evidence: https://linear.app/duckburg/issue/KOD-811#comment-2e19c500-3f9a-4b23-b654-8fce5bd5b98c

## Requirements and source evidence

Read current KOD97, KOD811, KOD368 bodies and all returned comments, with all comment cursors exhausted. KOD97 current registry/readback contract distinguishes a confirmed empty ruling set from failed retrieval; its Sep12 semantic review permits deterministic identity/evidence collection but rejects heuristic semantic judgment. KOD811 Sep12 coordinator comment `2a0b9033-c120-4c59-ba02-235b7b8eb5c1` grants coherent engineering ownership, not a founder behavioral ruling. KOD368 requires actual recorded rulings as admission evidence; this query does not itself perform graph admission, reconciliation, or any graph/state/event write.

Reviewed all three candidate files: `services/ruling_records.py`, `domain/errors.py`, and `tests/tracker/test_issue_ruling_records.py`, plus the current marker codec/config grammar and actual Linear pagination/wire boundary. Config already rejects delimiter-containing prefixes; no prefix-format defect was invented. The reader uses the existing strict `parse_ruling` JSON codec and canonical re-encoding, retains typed Ruling alongside the original TrackerComment, discovers only configured native first-line marker lanes, and refuses duplicate selected ruling IDs/native keys still visible at its port. Error lane `None` truthfully represents the issue-wide request; the observed issue remains exact. Existing explicit-lane namespace behavior remains unchanged.

## Executed evidence

All commands use `/Users/kodezart/.local/bin/uv run` per CONTRIBUTING. Logs are under `/private/tmp/kodezart-recovery-session`.

- Candidate `pytest -q tests/tracker/test_ruling_reader_independent.py`: **3 failed, 12 passed, 0.94s**, `ruling-reader-independent-681.log`. Original fifteen cases are frozen; only formatting was later normalized, with no oracle changes. Three added explicit-lane parent controls bring the retained file to eighteen cases.
- Parent `pytest -q tests/tracker/test_ruling_reader_independent.py -k inherited_explicit_lane`: **3 failed, 15 deselected, 0.58s**, `ruling-reader-inherited-before-44fd521.log`.
- `mypy src`: **308 source files clean**, `ruling-reader-mypy-681.log`.
- `ruff check src/kodezart/services/ruling_records.py src/kodezart/domain/errors.py tests/tracker/test_issue_ruling_records.py tests/tracker/test_ruling_reader_independent.py`: **pass**, `ruling-reader-ruff-681.log`.
- Affected corpus: `pytest -q tests/domain/test_rulings.py tests/tracker/test_issue_ruling_records.py tests/tracker/test_ruling_records.py tests/tracker/test_comment_pages.py tests/tracker/test_consumer_read_ports.py tests/tracker/test_port_failure_boundary.py tests/tracker/test_recorded_assertion_drift.py tests/tracker/test_recorded_ruling_growth.py`: **207 passed in 194.26s**, `ruling-reader-affected-681.log`. It excludes the retained red independent probes listed above; the existing corpus does not invalidate those counterexamples.

Original independent probes use the actual adapter and actual ruling reader with only an external MCP page double. Positives assert exact native keys/body/issue, unknown native author preserved separately from typed ruling authorship, actual encoded lanes, complete page calls and zero writes. Paired controls cover identical overlap versus changed native identity; malformed later page versus partial success; inability to advance versus absence; duplicate questions under one/two lanes; outage cause; cancellation propagation; explicit-lane isolation versus issue-wide damage.

Frozen independent probe: `test_ruling_reader_independent-frozen.py`. Frozen source snapshots: `ruling_records-68187b0.py`, `linear_mcp_types-ruling-68187b0.py`, `linear_mcp_tracker-ruling-68187b0.py`. Measured connected-app response: `ruling-reader-live-KOD811-comments.json`.

## Eight lenses

| Lens | Bounded verdict |
| --- | --- |
| SOLID | Good: one reader shares one internal enumeration path; actual caller receives precise native records. Adapter normalization prevents reader-enforced conflict guarantees until repaired. |
| DRY | Good: canonical marker composition and strict ruling codec are reused; no second ruling parser or lease implementation. |
| Hexagonal architecture | Good: reader consumes narrow TrackerCommentReader; backend-specific faults were reproduced at the actual adapter boundary. No vendor types enter the reader. |
| KISS | Good: one explicit issue-wide method and optional requested lane in the shared read error; no fabricated lane or speculative semantic policy. |
| Typed agent calls instead of semantic heuristics | Neutral/preserved: this is deterministic identity collection, not an agent judgment; no semantic string heuristic was added. |
| Official framework practices (version-matched) | Standard Python3.12 `unquote(errors='strict')` plus existing canonical re-encoding and Pydantic strict codec; actual local framework/adapter tests execute. No live model/provider execution is claimed. Missing versus explicit-null provenance remains a material validation gap. |
| Type safety | Improvement in truthful optional requested-lane metadata; typed return preserved. Overall native provenance guarantee remains incomplete until the shared wire default is removed for this read. |
| Repository hygiene | Three concise author files, isolated immutable review source, independent probes/logs only, no full suite or canonical mutations. |

## Integration instructions and limits

Hold candidate acceptance pending root's frozen comment-boundary correction and independent rerun of the original fifteen probes plus the three parent characterization controls. Preserve identical page repeats, require conflicting native versions to refuse, and preserve explicit-null thread metadata. No second reader or alternate ruling codec is needed. Root must serialize the shared adapter/wire hunks with its current work and rerun affected canonical consumers; worker evidence is not integrated evidence.

This review does not complete KOD97 semantic reconciler wiring, KOD368 graph hygiene, or any tracker state/event implementation. No source write, graph/event write, issue-state change, Notion/initiative update, commit, or push was performed by the reviewer.

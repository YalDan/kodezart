# Shared comment provenance correction

Frozen commit03b99acb76a08b4c7000f052d90ef8a691496f95, parentba1050d (canonical25b3 plus unchanged reader681 cherry). Root implementation isolated in /private/tmp/kodezart-v03-recovery-comment-provenance. Surface independent reviewer owns original tests and re-review.

Actual18 reviewer probes unchanged SHA256 b535a73669b496fd6f95706a925768f0e1a2af86a5e5d02f5265446f01f43092. Before:6 failed12 passed0.41s (3 issue-wide +3 explicit lane variants); after selection296 passed3.13s. Wider firstconformance478 passed1 failed18.18s exposed old permissive generic-reader oracle. It is explicitly superseded by required linkage refusal plus explicit-null successful enumeration and unresolved escalation. Finalconformance498 passed28.99s. Two interim fixture author errors (wrong reply attribute, positive path still expected escalation refusal) are diagnostics only; fixed before freeze. Source has not changed after strict mypy309/Ruff/format718/literal guard pass. No broad/full-suite pass claimed.

Logs: comment-provenance-before-ba1050d.log, comment-provenance-after.log, comment-provenance-conformance.log, comment-provenance-conformance-final.log, comment-provenance-mypy.log. Commands in tool transcript and published811 comment830c2f96. Original live MCP response saved by independent reviewer ruling-reader-live-KOD811-comments.json; current root live fetch also has explicit parentId null. Pydantic locked2.12.5 required nullable model semantics are documented at https://raw.githubusercontent.com/pydantic/pydantic/v2.12.5/docs/concepts/models.md.

## Eight lenses
- SOLID: native completeness/provenance enforced by existing adapter; issue-wide and explicit-lane readers retain own identity selection, no writer authority added.
- DRY: one validated listing and one duplicate-native-row policy; redundant thread-specific subclasses and private switch removed.
- Hexagonal: all vendor shape enforcement stays inside adapters; existing typed TrackerProtocolError crosses port, reader wraps its precise RulingRecordReadError.
- KISS: two-source correction removes30 net lines; no second parser, semantic reconciler or snapshot cache.
- Typed agent calls: deterministic identity/provenance enforcement is not model judgment; actual semantic judgment remains separately owned.
- Framework: required nullable Pydantic field distinguishes missing evidence from native null; no validation bypass, parallel mutation, or new replay mechanism. Existing cursor_pages retains complete traversal/cancellation rules.
- Type safety: improvement; absence cannot silently construct top-level provenance; no Any/cast/ignore/copy shortcut introduced. Shared domain TrackerComment remains unchanged.
- Hygiene: two coherent adapter files and existing boundary tests plus the unchanged independent probe file; isolated source/commit and public evidence.

Pending: independent acceptance, canonical cherry-picks of681 and03b99ac, affected integrated tests/full gate. This does not complete semantic reconciliation, graph hygiene, state effects, or any lane. Conflicting observations cause refusal, not a claim of backend atomic snapshots or permanent conflict.

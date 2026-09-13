# Independent M1 native read extraction review

Reviewed exact fec7f28292975f0b4635a06ba84e4e35fdc3c2e8 / tree8b76e5df013c41ebf8741d292a1cb1034f2d5f87 relative to a4575cd. Root is independent of the extraction author. Review began with actual diff, requirements and executable probes. Accept the bounded read/config/boot tranche; no whole M1/lane acceptance.

Actual source inspection covered all new production hunks, existing mapping reconciliation, strict native labels/parent fields, current-child pagination, configured constructor and prompt bindings. Independently checked all28 file hashes and811 claimed donor-identical AST symbols; many are inherited surrounding methods, not811 new functions. Proof script/output: m1-native-read-root-proof.py/.json. Exact source-only corrections remain separate from the donor extraction.

Execution: unchanged original criterion-family probe at6346911 reproduces3 failures in1.15s (blank two mappings and substituted parent). Log m1-native-read-root-before.log. At exactfec7, `uv run --locked pytest -q tests/tracker tests/adapters/test_tracker_self_writes.py tests/prompts` passed1320 in154.07s. Log m1-native-read-root-affected-fec7.log. Actual constructor, full-page/native detail reads, required fields, current relabel/moved parent, valid UUID/native alias, missing/mismatched/malformed alias, single subject read, no-I/O invalid mapping, real boot label creation/idempotent reentry, cancellation/programmer-error propagation, marker/lease and authored compatibility fixture coverage included. Author full repository gate was still running at review time; it is not counted as a completed result.

Eight lenses:

- SOLID: narrow TrackerCriteriaReader owns current family reads; classification config remains operation-owned and adapter performs native projection. No authoring, execution or state authority added.
- DRY: reuses existing pagination, native label ensure/boot reconciler and one classification-label guard. Native UUID normalization is one addressed wire method; upcoming canonical approval consumer must reuse it.
- Hexagonal: UUID/vendor spellings stay in adapter wire; domain receives canonical issue identities and semantic label keys. Actual build_tracker injects declared config.
- KISS: one native parent fetch plus paginated IDs/full child detail; no heuristic matching or parallel read framework. Uncertainty refuses rather than becoming an empty answer.
- Typed agent calls: not applicable to identity/membership/config enforcement itself; no semantic agent judgment is replaced. The result supplies typed native facts to later structured calls.
- Official framework practice: locked Pydantic models require reported child labels and nullable-but-required parent; UUID is validated at wire boundary. Existing ordinary description omission behavior is retained. No FastAPI/LangGraph lifecycle change in this tranche.
- Type safety: improvement. Narrow reader protocol, semantic classification field, purpose-specific typed read refusal and validated UUID; no new production Any/cast/ignore/model-copy workaround.
- Hygiene: changes in established adapter/types/boot/config/prompt modules; config and docs census included. No later lane modules introduced.

Remaining: final session/tool boundary migration; canonical approval/spec normalization paired correction; full M4 label/criterion/graph/split artifact consumers; full M1 acceptance/live conformance and complete milestone recomposition. No tracker-state or user approval policy ruling made.

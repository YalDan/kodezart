Native authored compatibility corrective freeze

Source: c47a7f112368db35a3d6e50315da9926e4282afc, parent 49843dc749aed049c5a3ded129874f1cdfc3917a. Clean isolated /private/tmp/kodezart-v03-recovery-native-compat. Independent root review pending; no canonical integration or push performed.

Findings and repair

1. CriteriaValidationOutput's captured digest was correct. Comparing the aa4d04f authored source schema against 49843dc shows exactly one nested change: Contradiction.criterionIds.items changed from authored ^AC-[1-9][0-9]*$ to shared nonblank native constraints. Contradiction's runtime consumers are the authored feasibility validator and its report. Restore only this authored field's per-element annotation; shared CriterionIdItem and native execution identities remain unchanged. The existing captured SHA256 stays 1e49292eb7a9c7b364fb7f30db016c2c2ab9505cf51bfaa6b9b1e0f532f327bc. No fingerprint refresh.
2. GatedQualityGate fixture lacked actual QualityGate.tracker_spec keyword and carried obsolete list[str] criteria. It now accepts ExecutionCriterion and TrackerSpec and forwards tracker_spec plus run_identity to FakeQualityGate. Gating, event release and all API progress assertions are unchanged.
3. Two prior native-migration test oracles incorrectly treated authored Contradiction as a shared native list. The shared runtime identity test now uses actual IterationGrade.missing_ids; the dispatched authored schema test asserts the authored pattern. Explicit native-key rejection cases were added for Contradiction. Shared runtime blank rejection remains covered.

Changed files
- src/kodezart/types/domain/criteria.py
- tests/api/v1/test_jobs.py
- tests/domain/test_criterion_identity.py
- tests/types/test_wire_schemas.py

Actual execution (all via uv run --locked)
- native-compat-before.log: original fingerprint + paused API case, 2 failed / 1 passed in 7.23s at exact parent.
- native-compat-schema-diff.log: executable schema diff from original authored source confirms sole nested constraint drift and both exact hashes.
- native-compat-focused.log: same original selection after fix, 3 passed in 0.65s.
- native-compat-acceptance.log: initial broad run, 4 failed / 213 passed in 18.05s. Three failures were my migrated fixture's invalid needs_work verdict literal, corrected to existing rejected; fourth exposed the contradictory wire assertion above. These failures are retained, not acceptance evidence.
- native-compat-identity.log: 17 passed in 1.55s.
- native-compat-acceptance-final.log: 217 passed in 17.82s over tests/domain/test_authored_feasibility_compatibility.py, test_criterion_identity.py, test_criteria_feasibility.py, tests/api/v1/test_jobs.py and tests/types/test_wire_schemas.py.
- native-compat-mypy.log: strict mypy succeeds for the single changed source module. No full source or test typing claim.
- native-compat-ruff-verified.log: Ruff check and format check pass across all four changed paths; git diff --check clean. Earlier formatting/line-length failures are retained in native-compat-ruff*.log.

Type impact and eight scoped lenses
- Domain authority: authored validation regains its original mint-format constraint; native key acceptance is unchanged.
- SOLID: no new collaborator or responsibility.
- Hexagonal boundaries: production ports and adapters unchanged; the fixture conforms to its actual port.
- DRY: reuse CRITERION_ID_PATTERN, preserving shared CriterionIdItem for actual shared fields.
- KISS: one annotation correction, no duplicate model or runtime parser.
- Typed agents: dispatched CriteriaValidationOutput again has its exact captured schema; runtime and wire constraints agree.
- Framework use: Pydantic model_json_schema and normal field annotations only; no sanitizer, casts or unchecked payload escape added.
- Hygiene/verification: isolated clean freeze, retained red logs, focused regression and source checks. No production graph behavior, pause timing or queue-state oracle was weakened.

Risks and integration
Cherry-pick only c47a7f112368db35a3d6e50315da9926e4282afc onto the integrated native stack. tests/types/test_wire_schemas.py has separately owned Organize/WriteBack census additions; retain them and resolve only the import/assertion hunk if required. Root explicitly approved these exact shared-test corrections. No new dependency. The broader repository's other seven CI failures are separately owned. This correction does not establish full L3, L5, L9, public restart, persistence or tracker writeback acceptance. Current KOD-763 native identity ruling and held KOD-814 persistence boundary remain intact.

Contract/evidence issue: https://linear.app/duckburg/issue/KOD-815

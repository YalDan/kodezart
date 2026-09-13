# Canonical ancestor identity correction

Frozen clean commit `8dedc14fb24a8302eec74d360785a3fd7067db87`, parent `8fc655d2ddca93357f9fc9475b41839d62652037`; tree `95e045045a6bb831a7b991487ee8ad4ecb2b70ff`. Isolated writer `/private/tmp/kodezart-v03-recovery-scope-ancestor`, branch `codex/v03-recovery-scope-ancestor`. Patch SHA256 `dfedc1746a23e973bde222b3daef96a47df5e1f27ccc6902933476a581b52879`. Root independently reviews/integrates; no push, canonical mutation, PR or issue-state change by this author.

Changed files:

- src/kodezart/adapters/linear_scope_reader.py: replace the discarded ancestor response with its actual wire and next-parent values. Refuse when wire.id differs from the already canonical ancestor.key. Raise existing ScopeReadError with the original requested ref. Four inserted source lines, one removed.
- tests/tracker/test_scope_ancestor_identity.py: retained independent parent-ID control and3 real adapter transport/cancellation controls.
- tests/tracker/test_scope_alias_identity.py: positive opaque issue UUID normalization to the canonical human-key subtree, preserving measured read shape.

The initial root-equality proposed defect was superseded after live UUID→human-key evidence, and that unjustified failing oracle is NOT included. Initial root aliases and global read_issue behavior are unchanged. This is a known native ancestor edge check only; no new port, public type, config flag, fallback, regex identity rule or duplicated reader.

The inherited defect reproduces in exact donord2c6fce and canonical5ef89e2 before this fix, as recorded in m1-scope-extraction-independent-review.md. On the actual new author base8fc655d, the justified parent control +3 positives +alias positive produced **1 failed / 4 passed in1.28s**, scope-ancestor-correction-before-8fc655d.log. After the source correction the same5 controls passed0.15s, scope-ancestor-correction-first-after.log.

Parent-negative function bytes are exactly preserved from the independent review: SHA256 `3a1f37e46f0cbca0e7cfb9932b8332daaf321c0ade0a0839561e06b09cb0cac8`. The remaining retained classes/test functions compare AST-identically to the original frozen probe after required Ruff formatting. The entire original five-case diagnostic, including the superseded oracle, remains externally at m1-scope-independent-frozen.py and was not altered. Final test module hashes: ancestor `efed2eea7083fab1c6a21753e854567206cfd07b707fb6b6330931faf2814c82`; alias `b310e54a1f8f1d1a53b3aac96393dc70142a411cbc2b13e730bd5fa7b008d87b`.

At frozen8dedc14, **292 passed in6.28s**, scope-ancestor-correction-affected-corrected-8dedc14.log:

```
uv run pytest -q tests/tracker/test_scope_ancestor_identity.py tests/tracker/test_scope_alias_identity.py tests/tracker/test_scope_reads.py tests/tracker/test_scope_approval.py tests/domain/test_scope_container.py tests/tracker/test_organize_graph_writes.py tests/tracker/test_linear_mcp_tracker.py tests/services/test_pass_gate.py
uv run mypy src/kodezart/adapters/linear_scope_reader.py
uv run ruff check src/kodezart/adapters/linear_scope_reader.py tests/tracker/test_scope_ancestor_identity.py tests/tracker/test_scope_alias_identity.py
uv run ruff format --check src/kodezart/adapters/linear_scope_reader.py tests/tracker/test_scope_ancestor_identity.py tests/tracker/test_scope_alias_identity.py
```

Strict mypy: one changed source file clean. Ruff and format: all three files clean. Logs scope-ancestor-correction-mypy-8dedc14.log and scope-ancestor-correction-ruff-8dedc14.log. First affected command mistakenly selected nonexistent tests/tracker/test_organize_graph.py; it collected no tests and exited4. That diagnostic remains scope-ancestor-correction-affected-8dedc14.log. Corrected selection uses the actual existing graph_writes module; no source or oracle change fixed that harness error. No full suite run by this author.

Eight lenses: SOLID—existing scope reader retains native traversal ownership; DRY—same _metadata and ScopeReadError used; Hexagonal architecture—native identity check stays at adapter, no vendor shape enters port; KISS—one direct equality after its actual awaited read; Typed agent calls instead of semantic heuristics—no agent calls or inferred identity; Official framework practices (version-matched)—ordinary typed Pydantic wire access, existing async control flow; Type safety—neutral signatures, improved runtime identity fidelity; Repository hygiene—small clean frozen delta, preserved repros, no source edits during frozen tests.

Limits: only observed ancestor response identity is verified. This does not make a multipage read atomic, establish unobserved concurrent membership stability, change approval rules, or complete L1 leases. Initial opaque aliases remain accepted under the existing resolver. Parent-ID mismatch refuses before returning metadata; it performs no tracker mutation.

Integration: after root's fresh independent review, apply only8dedc14 once to canonical. Carry the exact same source correction into the maintained M1 PR119 as an explicitly accepted post-watermark fix, not a claim of byte identity with originald2. The tests use existing canonical and extracted fixture APIs; root should rerun them in the actual M1 tree and preserve its original scope/port gates. The pure extraction review remains separately pinned at1811397 and its17 equivalence controls.

## Synthetic fixture follow-up

Root's fresh hygiene review identified that the alias fixture copied the connected real issue UUID. Tiny follow-up `dd6147c6c9ac40b6799e408bc8fd6b5554919646` atop8dedc14 replaces only that fixture constant with `00000000-0000-4000-8000-000000000073`. Source and assertions are unchanged; independent AST comparison confirms the UUID constant is the sole executable difference. Original live evidence and frozen8dedc14 logs remain preserved above. No behavior or type-contract change.

Exact frozen follow-up command: `uv run pytest -q tests/tracker/test_scope_ancestor_identity.py tests/tracker/test_scope_alias_identity.py` — **5 passed in0.73s**, scope-ancestor-synthetic-uuid-tests.log. Worktree clean. Integrate both8dedc14 anddd6147c after independent review; no push or canonical edits by this author.

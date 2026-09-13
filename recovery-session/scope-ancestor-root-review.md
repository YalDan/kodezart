# Root independent scope ancestor review

Accepted bounded correction at dd6147c6c9ac40b6799e408bc8fd6b5554919646 (source8dedc14fb24a8302eec74d360785a3fd7067db87, parent8fc655d2ddca93357f9fc9475b41839d62652037). Fresh isolated root review /private/tmp/kodezart-v03-recovery-scope-ancestor-root-review. Root inspected actual container_metadata/_metadata/_parent, both new test modules, original scope fixtures, and actual graph/native adapter tests. Native parent references already contain canonical IDs; comparing the returned parent's wire.id with that edge is required. Initial root lookup still normalizes its opaque alias before traversal; a blanket requested-root comparison is explicitly not prescribed.

The negative oracle injects a foreign native ancestor ID and requires existing ScopeReadError before metadata return. The alias positive uses synthetic UUID00000000-0000-4000-8000-000000000073 and checks full canonical subtree membership. Transport controls preserve exact typed cause and one unsafe mutation attempt; cancellation control requires no partial result and successful fresh subsequent read. Root rejected an initially copied connected identifier; fixture-only dd6147c replaces it without changing assertions or source. Historical evidence preserved separately.

Actually executed on immutable dd6147c:

uv run --locked pytest -q tests/tracker/test_scope_ancestor_identity.py tests/tracker/test_scope_alias_identity.py tests/tracker/test_scope_reads.py tests/tracker/test_scope_approval.py tests/domain/test_scope_container.py tests/tracker/test_organize_graph_writes.py tests/tracker/test_linear_mcp_tracker.py tests/services/test_pass_gate.py

292 passed in9.25s; scope-ancestor-root-independent-dd6147c.log. Strict mypy on changed reader: clean; scope-ancestor-root-mypy-dd6147c.log. Ruff check and format-check on all3 changed files: clean. No full canonical or extracted-tree gate executed by this review yet.

Eight lenses: SOLID existing traversal owner; DRY reuses _metadata and existing refusal; hexagonal vendor provenance stays inside adapter; KISS one direct canonical-edge comparison; typed-agent lens no semantic decision applies to native identity, no heuristic identity inference added; framework lens actual typed Pydantic wire access and ordinary cancellation propagation, no graph-state or lifecycle changes; type safety neutral signatures and strengthened observed runtime identity; hygiene one source hunk with focused tests and synthetic inputs. These are bounded results, not whole-lane acceptance. Unobserved concurrent changes are not fenced and multipage reads are not atomic.

Integration: apply8dedc14 then dd6147c once to canonical b803fe2 and maintained M1 a2ee4c6. M1 carries an explicitly accepted post-watermark repair, not donor-byte equality for its scope reader. Rerun scope and protected-surface tests after integration; trigger full current-head gates on both PRs. No release/approval/issue-state authorization follows from this review.

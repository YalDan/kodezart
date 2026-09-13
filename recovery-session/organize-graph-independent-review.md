# Organize graph independent review

Verdict: request changes on frozen f47a1c64d567cd345fedba572bb2d31b7f6e3625 (parent53b3ca89efec37b23570bf6cfd78780cc2acddef). Source donor and detached review tree remain unchanged. Review tree: /private/tmp/kodezart-v03-organize-graph-independent-review. Live KOD368 body and all three comments were read, plus current KOD74 body/rulings; graph hygiene is directly authorized, report-only restrictions do not prohibit these Organize writes. No full L2 completion claim.

## Findings

1. update_issue_graph checks leases, then awaits all graph facts; expiration during the last read still issues save_issue. Split create has the same deadline gap. This is before issuing the write, not the acknowledged inability to fence an in-flight backend commit.
2. create_split_if_absent checks a full graph snapshot, then separately reads current_source. That final actual observation can contain changed body, priority or parent; only key/team/project are checked, so stale prepared content is created. A real configured build_organize_owner path reproduces the unwanted create too.
3. A matching native source+deliverable identity appears while list_issue_statuses is awaited. The method does not refresh the identity set before save_issue and creates a duplicate. Existing-key replay controls pass when the identity predates entry.
4. Milestone placement is checked before awaited holder evidence. A milestone moved into another project during that wait is still assigned to the original project's issue. The unchanged milestone positive passes.

Production models and owner are not replaced. Test doubles mutate only external MCP/native storage/clock at specific actual awaited boundaries. The same canonical RunSurfaceLease and LinearMcpTracker execute. No CAS claim is made. A finite observation cannot prevent every later backend mutation; these controls concern observable stale evidence and avoidable awaited preparation windows.

## Evidence

- Baseline: uv run pytest -q tests/tracker/test_organize_graph_writes.py tests/chains/test_organize_graph_owner.py →44 passed4.88s, organize-graph-baseline-f47a1c6.log.
- Initial independent10 →7 failed3 passed0.71s, organize-graph-independent-f47a1c6.log.
- Added actual owner counterexample:11 →8 failed3 passed1.07s, organize-graph-independent-final-f47a1c6.log.
- Affected compatibility selection: tests/chains/{test_organize,test_organize_owner,test_organize_graph_owner,test_organize_delivery_independent,test_organize_native_independent}.py, tests/tracker/test_organize_graph_writes.py, tests/domain/{test_organize,test_organize_routing}.py, tests/types/test_organize_halt.py →309 passed10.78s, organize-graph-affected-f47a1c6.log. Includes actual configured graph/split/reentry, issued-create cancellation settling, fresh judge sessionNone, exact peer refutation citations and configured bound.
- Probe Ruff clean after formatting; the earlier line-length diagnostic is retained. Only line formatting and unused imports changed, not assertions. Frozen tracker file SHA2560d64e27361e6f21323d99efd2600aad5833cf2a54601f53d58b8bfff73dbefe4, owner file09049c43c3cb3306c2aabffe484602b3c246cc83deea91f1e324886f3d0f15c2; copies in session as organize-graph-{tracker,owner}-independent-frozen.py.
- No independent strict-source run claimed here; root handles the separate architecture/type review. Author's311/1634 counts were read but not used as an oracle.

## Eight lenses

SOLID: existing owner keeps application authorization, adapter native integrity, pure graph module arithmetic. DRY: one graph proposal and snapshot policy, one existing lease implementation; correction must reuse canonical grant arithmetic. Hexagonal architecture: actual production constructor and adapter were tested with external boundaries only. KISS: fix the final observations already needed, not a retry loop that keeps moving the last read. Typed agent calls instead of semantic heuristics: closed explicit field changes and stable child identities; no text inference. Official framework practices (version-matched): locked uv environment, actual async cancellation settling and Pydantic typed proposals exercised; no framework-version claim beyond installed behavior. Type safety: improvement in closed proposal vocabulary; temporal ownership failures remain despite valid types. Repository hygiene: source-pinned isolated review, exact immutable probes, prior diagnostics retained; subsequent author correction gets a separate branch/commit and independent root review.

Own evidence: https://linear.app/duckburg/issue/KOD-368#comment-1ef9453b-58e3-4c5e-a427-32f6639dd821

## Authorized correction provenance

Root authorized this reviewer to become the bounded corrective author only after recording the independent findings. New branch codex/v03-recovery-organize-graph-correction starts canonicald14254e1b64b581693fd1032adbdaac57ccc9ce5, then source prerequisite53b3→964b0f3, graphf47→fc34117, shared comment/lease factor9136→3ffc89e. The f47 cherry conflicts were only adjacent imports with canonical alarm imports; both complete imports were retained. No shared behavior was invented. Correction source ownership is only graph/split adapter methods plus tests. Root independently reviews the eventual corrective commit and original probes before integration; do not count this author's rerun as fresh independent acceptance.

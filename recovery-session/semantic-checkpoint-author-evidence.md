# Author-owned native checkpoint evidence — 2026-09-12

This is framework research and executable evidence by the native author, **not independent acceptance review**. Source is exact frozen `2727ee67175d8e2b078f02add50d15d3dffb00c4`, inspected/executed in detached `/private/tmp/kodezart-v03-recovery-semantic-checkpoint-evidence`. No production source edits. Both original author worktrees remain frozen. Independent native correctness review is still required.

## What actually survives

The actual production `build_workflow_engine` constructs the native fire and Ralph loop with the same `InMemorySaver`. Each probe initializes a legitimate pre-loop graph checkpoint through the existing `prepare` state and `aupdate_state(..., as_node="revalidate_criteria")`, then runs real `native_graph.ainvoke(None, config)`. Branch entry preceding that checkpoint is supplied by the fixture; all subsequent writer, semantic judge, archive/reset/apply, canonical verifier, worktree and Git code is actual. SDK and tracker service are external doubles; local Git/worktrees/persister and LangGraph serialization are real. Injected ordinary failures occur at actual completed effects, not synthetic checkpoint values.

| Cut | Parent actual saved next node | Ralph actual saved next node | Amendment child latest state | External effects retained |
|---|---|---|---|---|
| Archive saved, before first independent writeback finding | run_ralph_loop | execute | canonical_write_back pending; one judgment; no completed verdict | Exact prior archive exists |
| Native reset returned | run_ralph_loop | execute | canonical_write_back pending; one judgment; no completed verdict | Archive plus current criterion reset survive |
| Applied canonical verifier returned actual HOLDS | run_ralph_loop | execute | canonical_write_back pending; one judgment; no completed verdict | Archive, amended pending criterion, actual HOLDS occurred; no graph receipt yet |
| Commit-message session, before harness commit | run_ralph_loop | execute | Child graph complete; one judgment and one completed verdict | Verified archive and applied amendment; no harness publication |

In every cut the parent `total_iterations` remains zero and trajectory is absent; the Ralph amendment-report list has no receipt yet. The amendment child is under thread `actual-parent-job-ralph`, namespace `execute:<task-id>`. The parent task error is persisted. The native fire's `aget_state(..., subgraphs=True)` reports no discoverable nested graph state for its imperative collaborator; the concrete child is nevertheless present in `saver.list(None)`. This distinguishes actual persisted data from automatic graph-discovery visibility.

## What the actual parent resume does

A fresh production engine with the **same actual saver, same parent config, and `native_graph.ainvoke(None, config)`** resumes the outer `run_ralph_loop` task. However, `RalphLoop.run` constructs a new configurable map and always calls its graph with non-None initial state. Its isolated thread therefore receives another input checkpoint (step 0 to step 2 in these probes), reruns `execute`, and opens a fresh NativeWriterOutput session. The old amendment child's saved checkpoint ID remains unchanged. The probe deliberately stops at that second writer to identify the real resume boundary before any additional external effects.

This proves parent saved-state resume does **not** presently resume the earlier inner amendment operation. It does not prove automatic successful recovery, exactly-once tracker mutation, or reuse of the previous semantic judgment. The earlier `test_amendment_interrupted_writes.py` successful re-entry tests use a fresh service invocation at the same branch and current authority; they are distinct recovery evidence, not LangGraph saved-state resume.

## Framework control and matching source

Installed versions: LangGraph1.0.10, langgraph-checkpoint4.0.1, langchain-core1.2.17, langgraph-prebuilt1.0.8. `semantic-checkpoint-framework-source.json` records hashes for installed main.py/_loop.py/_algo.py/_config.py and the official 1.0.10 tag; **all four are byte-identical**.

The matching official [configuration source](https://github.com/langchain-ai/langgraph/blob/1.0.10/libs/langgraph/langgraph/_internal/_config.py) copies ambient child config when no explicit replacement is supplied. [Pregel main](https://github.com/langchain-ai/langgraph/blob/1.0.10/libs/langgraph/langgraph/pregel/main.py) selects the inherited checkpointer unless explicitly disabled. The matching [loop source](https://github.com/langchain-ai/langgraph/blob/1.0.10/libs/langgraph/langgraph/pregel/_loop.py) resumes for None outer input or the inherited resuming flag; non-resuming input discards unfinished tasks and creates input writes. [Task construction](https://github.com/langchain-ai/langgraph/blob/1.0.10/libs/langgraph/langgraph/pregel/_algo.py) carries the checkpointer and task namespace.

Two fast controls invoke the **actual NativeAmendmentGraph** inside a small parent graph with controlled actions. A true parent None resume reruns the parent function but resumes the child at canonical_write_back without rerunning judge (judge1/apply2). A fresh `{}` invocation reruns judge (judge2/apply2). Thus supplying initial data to the amendment graph alone does not establish restart: the ambient framework resume flag matters. The production Ralph configurable replacement is the distinguishing boundary.

## Exact execution

- `/Users/kodezart/.local/bin/uv run pytest -q -s tests/chains/test_native_checkpoint_evidence.py` → **4 passed103.19s**, `semantic-checkpoint-parent-first.log`. All four real cuts, full actual saver channel/pending-write snapshots printed. No production or probe source mutation during execution.
- `/Users/kodezart/.local/bin/uv run pytest -q -s tests/chains/test_amendment_framework_resume_evidence.py` → initial **2 failed0.70s**, `semantic-checkpoint-framework-control.log`: research oracle expected raw saver tuple, but actual msgpack roundtrip supplies an empty list. Corrected assertion checks the required zero verdict count; no runtime change.
- Same command after that oracle correction → **2 passed1.48s**, `semantic-checkpoint-framework-control-final.log`.
- Initial version-inventory command accidentally requested absent optional `langgraph-checkpoint-sqlite`, so it exited after printing the three installed versions. Corrected manifest excludes that unconfigured backend; no SQLite support is claimed.

## Scope and risks

This uses the production-resolved in-memory saver implementation and its actual serde, but does not demonstrate PostgreSQL, process restart, SIGKILL, or crash durability. Ordinary exceptions execute Python cleanup/finalizers. `make_checkpointer` supports None, :memory:, and optional PostgreSQL; no second persistence authority was added. LangGraph's default async durability is not an atomic transaction with tracker writes. Archive/reset/body application all reside inside one canonical_write_back node, so there is no distinct checkpoint after each external effect. The surrounding parent cannot automatically reconstruct a completed effect whose child node never returned. The canonical amendment record preserves actual prior bytes, but no stable transaction or operation identity is claimed. No backend CAS guarantee exists.

Type impact: neutral, evidence only. The installed serializer reconstructs nested typed Pydantic payloads; raw TypedDict tuple channels become lists under msgpack (current consumers accept their sequence behavior). Default deserialization emits unregistered-type future-version warnings; dependency upgrade/allowlist compatibility is not verified here. No new source suppressions/casts/Any or schema changes.

Eight lenses: (1) SOLID—actual responsibilities traced across fire/loop/guard/persister; (2) DRY—same graphs and saver, no substitute runtime; (3) hexagonal—actual adapters with external tracker/SDK doubles; (4) KISS—four real cutpoints and two framework controls; (5) typed orchestration—real completed verdict distinct from saved judgment; (6) framework—version/hash matched official source and actual serde; (7) type safety—neutral evidence, tuple/list serialization limit explicit; (8) work sequencing—separate detached tree, frozen source unchanged, immutable execution logs.

Integration recommendation: no source cherry-pick. Use these probes and observations to scope the missing inner resume/operation-recovery contract; do not turn them into a full native restart acceptance claim. KOD814 and KOD96 remain separate. Independent review remains required.

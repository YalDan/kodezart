# Independent M1 scope / label / bootstrap review

**ACCEPT the bounded extraction** at `ddb8cdef33f90f30aecc4343e36c2293bdf39a27`,
tree `5ed89888cb982ee2972f5a79e25da681b4323eeb`, exact parent
`2bc237577da7a3db3101e67c324eea0eaa6e6abd`. No blocking source or oracle finding.
Root owns integration, PR and initiative/Notion actions. This is not whole L1
completion or an approval-policy/lease-ownership ruling.

Reviewer `/root/native_final_review`, independent of source authorship. Requested
ultra review effort retained; effective runtime model metadata is unverified.
The writer tree was only read. All execution used clean detached worktree
`/private/tmp/kodezart-v03-m1-scope-bindings-independent`. No M2/M3 source edits.

## Requirements and review basis

Read current KOD-73 and its latest comments, the founder scope-label and
milestone rulings, current KOD-378/379/380/381, and KOD-381 cancellation comments.
Canonical engineering instructions remain
https://app.notion.com/p/3abf89e34d10812d9b3fd3551cc2208a.

The finite obligation is configured scope vocabulary, namespace definition
bootstrap, actual scope/approval reads, strict constructor configuration and
current consumers. Empty mapping is valid; populated mapping requires all three
semantic members. Bootstrap defines labels without applying approval. Issue
scope means subtree; containers mean membership. Milestones have no independent
approval label level. Actual approval is reread from issue ancestry and the
addressed issue's own container ancestry. Native alias evidence must identify
the requested subject without guessed canonicalization.

KOD-381 was explicitly canceled by the founder in comment
`162f7ade-3947-45d9-9fd4-f30572b3e8d9`, reaffirmed in `724c345b`: label presence
is the ruled observable fact; no fabricated scope-label actor/provenance carrier
should be introduced. Older KOD-73/KOD-380 prose mentioning actor proof is not a
new requirement overriding that cancellation. KOD-751/787/788 reserved approval
placement and composed ownership decisions remain separate from these primitives.

Review started from requirements, immutable diff, source and test oracles before
reading the author's acceptance narrative. Candidate has 37 changed files,
2,040 additions and 26 deletions. Read all production changes and changed
configuration/contract censuses, plus original namespace/approval/alias/boot/
milestone tests. No new scheduling or native execution path is included.

## Source and oracle evidence

The supplied extraction map names donor
`36083f83f42c03240ebb5861fe284da2c9f04180`; the assignment names
`da39c439898aec1233aa8b6157b35e961df1e453`. Independent immutable-object
comparison proves the five full files and nine adapter helpers declared in the
map are identical to **both** donors. The relevant shared implementation did not
change between them. Their broad protocols file differs in unrelated later
native checkpoint/workspace authority contracts, which were not imported.

Five exact files: `domain/scope_approval.py`, `services/scope_resolution.py`,
`services/tracker_boot.py`, `adapters/linear_scope_reader.py`, and
`adapters/linear_scope_types.py`. Nine exact adapter helpers:
`_scope_label_members`, `_read_scope_issue`, `read_scope_labels`,
`execution_approved`, `_read_execution_approval`, `project_milestones`,
`_scope_label_definitions`, `_ensure_scope_label`, and `_label_entries`.
`m1-scope-bindings-independent-source-proof.json` records these comparisons and
the required `scope_labels: Mapping[str, str]` constructor with no default.
Composition forwards the actual configured mapping; unrelated fixtures explicitly
supply `{}`. No source Any, cast, type-ignore, noqa, hasattr or getattr was added.

Boot enumerates one workspace reference per configured scope label, independent
of issue-team count. The adapter maintains separate issue/project/initiative
definition sets, checks complete paginated listings, refuses declared-team name
collisions before namespace writes, creates only missing definitions, and
re-lists the actual namespace after creation. It neither applies approval to an
entity nor replaces an unread namespace with an empty one. Partial successful
definition creation remains visible and a later bootstrap fills missing kinds.

Approval uses the existing strict addressed wire while preserving required
labels and parent fields. UUID normalization uses reported evidence and the
subject cache exists only within one call. The pure ancestry resolver remains
exact donor source; current revocation/reparenting is reread rather than cached
or materialized. Missing mapping, malformed ancestry, foreign identity and
cancellation remain typed refusal/propagation. `project_milestones` checks native
detail identity and stable full membership. The 15-line shared scope resolver
preserves the port's issue records and structural edges unchanged.

`m1-scope-bindings-independent-provenance.json` compares all 37 changed files
against parent and requested donor; it records 57 added test functions. Original
constructor fixtures differ only by explicit namespace input. Configuration
censuses retain exact equality while including the new declared field/native
consumer; the literal scan also includes configured scope labels. Boot's mapping
census fixture supplies all declared keys and retains its original assertions.

Two milestone test functions moved from donor `test_organize_graph_writes.py`
are exact AST matches, independently verified in
`m1-scope-bindings-independent-oracle-proof.json`. Approval alias tests retain the
actual approval assertions and explicit custom label configuration; only the
mixed native FIRE-spec arm is omitted because it belongs to M3. It must remain
in that donor/consumer lineage, not be described as exercised here.

The tool-roster and argument census changes deserve explicit interpretation:
they now check measured public declarations, combining the unchanged August
service capture with separately attributed September connected-app declarations.
The roster file is exact donor bytes. Exact closed-set coverage, required
arguments and unknown-tool refusal checks remain, but this does **not** establish
that the deployment's service credential supports `save_project_label`. The
old service capture is not rewritten; the different evidence scope is stated in
source and tests. No skip/xfail or broad suppression is introduced.

## Actual independent commands and outcomes

Created detached worktree with `git worktree add --detach` at ddb8cde, then ran
`/Users/kodezart/.local/bin/uv sync --frozen --all-groups` (exit 0;
`m1-scope-bindings-independent-sync.log`). All commands below ran there:

```text
.venv/bin/python -m pytest -q tests/domain/test_scope_label_config.py tests/domain/test_scope_labels.py tests/prompts/test_scope_label_bindings.py tests/services/test_scope_resolution.py tests/tracker/test_scope_label_mappings.py tests/tracker/test_scope_approval.py tests/tracker/test_native_approval_aliases.py tests/tracker/test_milestone_approval.py tests/tracker/test_project_milestones.py tests/tracker/test_scope_tool_arguments.py tests/tracker/test_tracker_boot.py
.venv/bin/python -m pytest -c pyproject.toml -q /private/tmp/kodezart-recovery-session/test_m1_scope_bindings_independent.py
.venv/bin/python -m pytest -q tests/tracker/test_linear_tool_arguments.py tests/tracker/test_linear_tool_roster.py tests/prompts/test_operation_config.py
```

* **175 passed in 2.93s**, exit 0, `m1-scope-bindings-independent-selected.log`.
  Actual factory, native adapter and shared fake conformance cover configured
  custom/empty/incomplete mappings, boot creation/adoption/no entity writes,
  complete namespaces and pagination, team conflicts, creator/readback failures,
  live approval ancestry, missing/foreign addresses, UUID aliases, revocation,
  cancellation, milestones and structural scope resolution.
* **5 passed in 3.17s**, exit 0, `m1-scope-bindings-independent-probes.log`.
  Independently authored external-MCP controls: matching/foreign display address
  with reported UUID, three live approval/revocation reads through the same UUID
  alias and adapter, and present/missing project-label pagination metadata.
  Foreign identity cannot borrow a UUID; omitted namespace metadata refuses
  before any definition or entity write; live controls remain functional.
* **61 passed in 1.65s**, exit 0, `m1-scope-bindings-independent-census.log`.
  Exercises the changed public-contract/argument/configuration censuses directly.

Reused reviewer script `m2_independent_provenance.py` with immutable arguments:
repository path, ddb8cde, parent 2bc, requested donor da39 and output
`m1-scope-bindings-independent-provenance.json`; log retained beside it.
Additional direct immutable Git AST/blob comparisons produced source and oracle
proof JSON files. `git diff --check` passed; review worktree remains clean.
All sync/provenance/test processes completed; no test runner remains active.

Separately attributed author evidence was inspected only after source/oracle
review: final affected selection **775 passed in 12.45s**;
`m1-scope-affected-final.log`. Strict mypy **181 source files clean**;
`m1-scope-mypy-final.log`. Author's full immutable gate actually finished:
**3,866 passed, 16 skipped in 432.31s**, `m1-scope-full-gate.log`, with formatting,
Ruff and type checks first. This is not an independently repeated full gate.
Earlier failures and baseline MCP reopen timeouts remain recorded; this slice
does not change their source, oracle or deadline, or claim to explain their cause.

## Eight lenses and type impact

| Lens | Independent assessment |
| --- | --- |
| SOLID | Configuration names vocabulary; pure resolver computes ancestry; composition supplies dependencies; native adapter owns namespace I/O. No execution policy added. |
| DRY | One shared resolver, one mapping and existing pagination/address matcher are reused. M2 provisional overlap should be reconciled through ancestry, not duplicated. |
| Hexagonal | TrackerPort and typed callables bound core reads. Native UUIDs, namespace tools and wire shapes stay in the adapter. |
| KISS | Existing donor mechanisms are extracted without new framework, fallback, scheduling or authority carrier. |
| Typed agent calls | No semantic agent call changes. Deterministic MCP calls retain validated wires and declared argument sets; no prose-based identity or approval inference. |
| Official framework practice | Existing actual Pydantic inheritance/required fields and standard asynchronous pagination are preserved; no dependency, lock or private SDK compatibility change. |
| Type safety | Required Mapping input, closed semantic enum and strict approval wire improve the prior absent capability. Optional map presence remains explicit; no new source suppression or Any. |
| Hygiene | Immutable reviewed source, preserved donor oracles, scoped census updates, clear service-vs-connected evidence, clean isolated worktree and no duplicate full gate. |

Type impact: **improvement over parent**, **neutral against donor contracts**.
No blocking extraction finding across these lenses.

## Limits and exact integration direction

Accept ddb8cde as the M1 generic owner of scope vocabulary, native approval reads,
namespace bootstrap and pure scope resolution. Preserve it as the descendant of
2bc on maintained PR119, then propagate actual M1 ancestry into M4 and reconcile
M2's provisional copies using the per-symbol ownership map. Do not overwrite
mixed files wholesale: M2 mandate/owner/identity/graph/split/criterion code is
distinct, while the six overlap read helpers, three port methods, resolver,
ScopeLabel and scope_labels field should exist once. Root performs integration
and the maintained gate/publication; no force/reset/delete was used or requested.

Remaining limits: connected-app public declarations do not prove service-credential
access to the newer project-label creator, populated project-label reply shape
was not live captured, and no new live entity writes were performed in review.
Approval is the ruled label-presence fact, not invented actor attribution. This
does not complete HTTP/request/queue/engine scope propagation, native M3 FIRE
entry, M2 Organize policy, or the reserved KOD-751/787/788 decisions. It provides
the shared prerequisite for those separately owned consumers.

Own KOD-73 acceptance:
https://linear.app/duckburg/issue/KOD-73#comment-42a78db8-5aeb-4bf3-b4ac-36b67ab44c40

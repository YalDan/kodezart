# Independent M2 scope-bootstrap merge review

**ACCEPT bounded merge semantics** at `37723cd8c660de5ad959de1f118b93287da48e64`,
tree `965ce3a5833741293a7405cfed50bc6d2ab960ac`, with one nonblocking fixture
hygiene observation below. This is acceptance of composition with the reviewed
M1/M4 prerequisite, not whole KOD-74/L2 completion or acceptance of a subsequent
description-authority implementation.

Exact parents:

- M2 `fadf6efe29c7addcc608b22e8345cd9008ce4bab`.
- Shared M4/M1 `268be4057dbcd4d4edb79b35d7e2b60a30f2f3c8`, carrying independently
  accepted M1 `ddb8cdef33f90f30aecc4343e36c2293bdf39a27`.

Reviewer `/root/native_final_review`, independent of this source and merge
authorship. Requested ultra effort retained; effective model runtime metadata
is unverified. No delegation. Source author/root-owned maintained trees were
not edited or tested. Execution used clean detached review worktree
`/private/tmp/kodezart-v03-m2-scope-bootstrap-independent`. Root alone owns
integration, publishing, initiative and Notion changes.

## Basis and finite obligations

Current KOD-74 comments were reread, including root census acceptance
`4850b6d3-9af3-4f2e-9c7b-e1e2e92dd0cc`, my prior correction acceptance
`d5557112-901c-486f-bfbe-b483d142d91a`, and the current extraction remainder.
Earlier KOD-73/74/750/751/787/788 and engineering instructions remain the
requirements basis. M1 label-presence approval ruling and its explicit KOD-381
cancellation are retained; no invented approval-actor proof is required.

The finite review is the exact two-parent composition: shared scope reads,
namespace bootstrap and strict configuration appear once, while actual M2
mandates, typed proposals, owner behavior, graph/split/criterion boundaries and
corrected criteria prompts remain intact. The preserved conflict diff and
resolution manifest were read as provenance, not used as proof of correctness.

Relative to its M2 parent the merge changes 24 files, 1,146 additions and 49
deletions. All production changes, conflict blocks, test-double changes and
changed test/census oracles were reviewed independently.

## Source and oracle evidence

Immutable Git-object audit `m2_scope_merge_independent_audit.py` proves:

- All 14 manifest-named shared methods have exactly the same AST as **both**
  parents. Each is defined once in the candidate. Their file position is not
  semantic evidence; the exact implementations and one-definition property are.
- Every production function changed from M2 is exact shared-parent AST: nine
  functions across the native adapter, scope resolver and tracker bootstrap.
  No M2 production function is lost and no production function is newly authored
  by the merge. The full source scan finds no duplicate definitions or direct
  constructor assignments in production.
- Mandates/config validation, prompt bindings, typed proposals, corrected
  criterion retry/receipt boundary and role prompt bytes remain M2-owned and
  unchanged. Namespace constants, strict label-page fields, instatable mapping
  kind and bootstrap table are the already accepted shared source.
- Both M1-owned approval test files are exact shared-parent bytes. The removed
  unreachable spec branch previously had only an approval parameter arm; this
  imports the accepted approval-only owner, not a loss of an executed M2 spec
  assertion. The actual milestone resolver control is restored.
- Changed test functions are exact shared-parent AST except the composed fake
  constructor, the additive scope-label type assertion in operation config,
  and a fixture keyword order move. The public tool-roster test rename and
  scope expansion are exact accepted M1 bytes. Original August service capture
  stays unchanged; the separately attributed September connected-app declarations
  do not claim service-credential availability.
- The M2 operation-field census retains its actual prompt consumers and the
  native `organize_scopes` composition consumer. The existing model-to-consumer
  closed-set assertions remain, and fresh execution passes.

Nonblocking hygiene observation: `tests/fakes.py` initializes
`FakeTrackerPort.scope_label_members` twice (lines 3092/3101) and `issue_writes`
twice (3094/3143). Each pair is byte-identical within construction. The existing
`update_issue` implementation remains exact M2 AST, appends once, and no
post-construction reset is introduced. No normal-fixture history loss or
production defect is demonstrated. Root acknowledged the exact later duplicate
removals for a separate successor, leaving the running maintained gate frozen.
This observation must not be described as a reproduced authorization defect.

## Fresh execution

The environment was prepared with `uv sync --frozen --all-groups`; the actual
interpreter is Python 3.12.13. The following command ran in the isolated tree:

```sh
/Users/kodezart/.local/bin/uv run --frozen pytest -q \
  tests/tracker/test_scope_label_mappings.py \
  tests/tracker/test_tracker_boot.py \
  tests/tracker/test_scope_approval.py \
  tests/tracker/test_native_approval_aliases.py \
  tests/tracker/test_milestone_approval.py \
  tests/tracker/test_project_milestones.py \
  tests/chains/test_organize_graph_owner.py \
  tests/chains/test_organize_owner.py \
  tests/tracker/test_organize_graph_retry_boundary.py \
  tests/tracker/test_organize_graph_receipt_boundary.py \
  tests/tracker/test_organize_graph_final_boundary.py \
  tests/tracker/test_criterion_creation.py \
  tests/tracker/test_criterion_retry_independent.py \
  tests/tracker/test_criterion_receipt_boundary.py \
  tests/prompts/test_operation_config.py \
  tests/prompts/test_scope_label_bindings.py \
  tests/prompts/test_organize_roles.py \
  tests/prompts/test_organize_mandate_bindings.py \
  tests/prompts/test_prompt_wiring.py
```

**343 passed in 28.41s**, exit 0. Full output:
`m2-scope-bootstrap-independent-selected.log`.

This exercises actual scope bootstrap and fresh/native-alias approval reads,
the Organize owner and graph owner, preserved retry/receipt/final write controls,
original criterion creation plus the independently derived expired/live lease
pair, completed-save readback non-resend, and prompt/config composition census.
No full gate was repeated. The maintained full gate remains root-owned; this
report does not claim its result or claim a fresh independent whole-source
static gate.

Audit command:

```sh
.venv/bin/python /private/tmp/kodezart-recovery-session/m2_scope_merge_independent_audit.py
git diff --check fadf6ef HEAD
git status --porcelain=v1
```

Audit and diff check exit 0; review tree is clean. The first audit launch used
system `python3` (3.9), which cannot parse existing `match` syntax and stopped
before producing a proof. The completed proof was produced by the locked-tree
Python 3.12.13 interpreter. That tooling launch failure is not a source failure
or test execution. All runners are settled.

## Eight lenses

| Lens | Finding |
| --- | --- |
| SOLID | Existing scope/boot and Organize responsibilities remain separate; the merge introduces no new owner or policy decision. |
| DRY | Fourteen shared methods compose to one exact implementation; only the two reported fake initializations remain as mechanical residue. |
| Hexagonal architecture | Domain/config types remain pure; the adapter owns native namespaces and the existing port owns consumers. Tests reach actual adapter behavior through the external MCP double. |
| KISS | Merge uses existing typed configuration, reader, bootstrap and retry machinery; no compatibility layer or framework. |
| Typed calls | Required scope mapping and validated proposals stay explicit; corrected criteria prompt capabilities and owner contract are preserved. |
| Framework use | No dependency or framework-version change; existing Pydantic validation, native tool boundaries and async owner behavior are exercised. |
| Type safety | Neutral as a merge of accepted source; stricter shared label-page validation improves the M2 parent. No new Any, suppression or optional-constructor fallback. |
| Engineering hygiene | Exact parents/tree, immutable source/oracle evidence and fresh isolated execution are recorded; the two fake duplicate initializations need the acknowledged mechanical cleanup. |

Correctness/concurrency: the accepted fresh approval reads, existing lease
checks, known-unsent retry boundaries and receipt non-resend remain intact.
This merge adds no atomic backend fencing claim. Unknown response and already
issued write limits remain as documented in the accepted parent reviews.

## Limits and integration

Root may retain this merge as the bounded shared-prerequisite composition and
apply the exact fixture cleanup once in a successor. Preserve both parents,
the two M1 oracle owners, original expired/live criterion probe and receipt
control. Review any subsequent production correction separately, then run the
maintained exact-head gate before the relevant final publication.

This acceptance does not close the named native GROOM/description-authority
remainder now owned by root, dated durable-product parity KOD-366, digest boot
capability KOD-358, all-write gate census KOD-369, or reserved KOD-751/787/788
approval/ownership decisions. Native WALK/FIRE remains M3. Connected-app label
contract evidence does not prove service credential access to the project-label
creator or an unmeasured populated project response.

Related own reviews:

- This merge: https://linear.app/duckburg/issue/KOD-74#comment-3f27e107-c43a-4db2-a9ab-97d70b54ed65
- M1: https://linear.app/duckburg/issue/KOD-73#comment-42a78db8-5aeb-4bf3-b4ac-36b67ab44c40
- M2 correction: https://linear.app/duckburg/issue/KOD-74#comment-d5557112-901c-486f-bfbe-b483d142d91a

Artifacts in `/private/tmp/kodezart-recovery-session`: this report;
`m2-scope-bootstrap-independent-provenance.json` and `.log`;
`m2-scope-bootstrap-independent-selected.log`; immutable audit script;
`m2-scope-bootstrap-independent-sync.log`; root's preserved
`m2-scope-bootstrap-resolution.json` and `m2-scope-bootstrap-conflicts.diff`.

## Mechanical successor

Independently ACCEPT `9e387b4a830adf87a3cebcbe705c17f1569101c7`: its only
change is removal of the exact two later duplicate constructor assignments
reported above. No production source, method, assertion or observer behavior
changes. The hygiene observation is closed for that successor. The 343-test
execution remains evidence for 37723cd; it was not rerun for these inert
duplicate deletions. Any later description-authority source is separate.

# Independent artifact-boundary review — final bounded approval

**Final verdict: approve root corrective6f431bbaa8ad88d21c0527f4bee95853c573925b for this bounded artifact/identity repair.** Independent reviewer HEAD0e30d784f981889379c2ef48083b700f6f2326bb has exact same treea1d5534d3603e5713b004fc8b5601b845e1eebf9. The initial da37 findings below are historical and corrected. No production source was authored or edited by this reviewer. The b84 shared native-address dependency was independently accepted by root/Surface; this review does not self-approve that dependency, which I previously authored.

Final correction validates read_issue_identity's actual LinearAddressedIssueWire against canonical id or attested UUID before decoding. The existing strict full-child read/equality check now follows identity acquisition. Thus the separate identity response cannot substitute a foreign child, and the final observed body/state/classification facts cannot silently differ from the listed snapshot. The number of reads is unchanged. This is an observed consistency guard, not backend transactional fencing.

Final actual locked test selection: **118passed156.95s**, log `artifact-boundary-consumers-independent-6f431.log`. It includes all five immutable split controls (SHA256f7f15e266a4d364db4f2ff2af84af6e6969bd89be38c202ec2ee4cddae85e470), all15 original field controls,33 independent all-arm cancellation/programming/declared-failure/unsupported controls,4 new direct identity-UUID controls, existing native identity/criterion/approval tests, the canonical verifier and fresh judge, actual configured graph/split reentry owners, native archive positive and actual Audit publication/summary production composition. Strict two source files passed; Ruff/format six source/test files passed. Source/test bytes stayed immutable while tests ran.


Initial review: root candidate `da37bda26f22c5681d8f62e766c19b077e53388f`, parent `25b73576a9d224197241323d1bd2c9769d843b9d`. Independent tree `/private/tmp/kodezart-v03-artifact-read-independent` carries tree-identical cherry-pick `5a0692836e561a005de1dcdd8d6e6e9b1afedff4`, tree `f2e49cffe9241d8d0a3fc50c04dbb15cd42ea2f7`. No production source authored or edited by this reviewer. Root retains exclusive corrective ownership.

Read live KOD76, KOD531 and KOD538 descriptions/current comments before assessing source and probes. KOD531 requires actual scope writers to use the canonical verifier; KOD538 requires sanitization at the actual gate. This review concerns completeness and consistency of the artifact given to that mechanism. It does not settle all writer adoption, event/state authority, sanitization coverage or full L4 acceptance.

## Historical da37 material findings, corrected by6f431

1. **Split identity and body can come from incompatible observations.** `read_tracker_artifact` first gets the listed child's body, then strictly rereads/equality-checks it, then separately calls `read_issue_identity`. If the actual backend child body changes at that last read, the accepted artifact contains the replacement stable identity but the old body still encodes the original identity. The artifact therefore asserts facts no single observed child held together. This is distinct from an unavoidable mutation after the last observation: the implementation directly receives the inconsistent evidence and still returns a complete artifact.
2. **A foreign native identity response is accepted for the addressed split child.** Existing `LinearMcpTracker.read_issue_identity` decodes the returned description while discarding the returned native issue id. A get_issue response naming `FOREIGN-CHILD` supplies the stable identity in the artifact for `SPLIT-CHILD`. The earlier strict child's identity check does not validate this later response.

Both are actual adapter/reader failures with only external MCP responses/state controlled. Immutable test `tests/tracker/test_artifact_split_identity_independent.py`, copied to the recovery session with SHA256 `f7f15e266a4d364db4f2ff2af84af6e6969bd89be38c202ec2ee4cddae85e470`. At baseline25b:2failed3passed2.24s (`artifact-split-identity-independent-before-25b.log`). At root candidate da37:2failed18passed1.59s, including all15 root controls (`artifact-boundary-independent-da37.log`). Baseline failures occur at the second child read; after the new strict read, the same probes expose both failures at the third child read. The unchanged no-drift and earlier-mutation controls pass.

Narrow corrective direction sent to root: validate the native address in the actual identity-reading adapter boundary; order the existing strict child/equality reread after identity acquisition. This does not require another verifier, semantic parser in the service, new journal, additional read or claimed backend compare-and-set. Root owns the implementation and must preserve legitimate attested UUID aliases where applicable.

## What the candidate correctly repairs

The source diff uses existing `read_planning_issue` for graph and structured label/criterion artifacts. Split children receive a strict full read and equality guard instead of serializing only the lenient identity census row. All15 original field-omission/positive controls pass independently. Root's corrected original baseline was5failed10passed0.51s; its earlier sixth failure was a positive test using `issueKey` instead of the actual `issue_key` serialization. The correction changes only that field spelling, retains the key equality, and is justified by the actual unchanged serialization; it is not a removed failure oracle.

Strict reads require native labels and relations. This avoids claiming missing labels/edges are empty. Description-only reads deliberately retain their existing compatibility and do not consume unreported classification. Whole structured reads request these native facts; refusing an incomplete response is appropriate. No unjustified new description-read strictness found.

## Eight supported arms / boundary assessment

| Arm | Assessment |
| --- | --- |
| ISSUE_DESCRIPTION | Existing ordinary full issue read, exact requested native identity, raw body; omission of unused labels remains compatible. |
| MARKER_COMMENT | Exact issue and marker lookup; missing/wrong-issue/duplicate marker semantics preserved. No new write or attribution path. |
| CONTAINER_DESCRIPTION | Existing addressed metadata read and exact scope ref equality; raw description retained. |
| ISSUE_LABEL_SET | Strict reported labels now required before serializing configured semantic classification/queue label facts; native id equality retained. |
| CRITERION_SUB_ISSUE | Strict read plus required criterion classification/non-null parent; state/body/native key serialization retained. |
| CRITERION_CHILD_SET | Existing native criterion reader validates child labels/current parentage/duplicate identities. Final6f431 includes the separately reviewed b84 native-parent response correction; the historical25b family reader contained that already-reported issue. No new M2 dependency is inferred. |
| ISSUE_GRAPH | Strict labels/relations now feed deterministic graph snapshot; exact native key and all existing graph facts retained. |
| ISSUE_SPLIT_SET | At da37 the later identity defects remained;6f431 validates the identity response and places the final strict child check after it. All unchanged controls now pass. |

CONTAINER_STATUS_UPDATE remains unsupported and must refuse before a write/read. No ninth arm is implied. Error/cancellation and real consumer gates are recorded below when completed.

## Eight lenses and type impact

* SOLID: the candidate keeps one canonical artifact reader and existing verification/repair owner. The adapter should own native response identity rather than making a service infer vendor provenance.
* DRY: existing strict planning read and native reader/pagination/identity mechanisms reused; no parallel verifier or retry engine. The remaining fix should reuse the shared native address validation.
* Hexagonal: root source retains neutral TrackerIssue/TrackerArtifact/port types; no vendor parsing enters the service. Independent tests intentionally use the real adapter and its carrier only to assert observed internal consistency.
* KISS: seven added/three removed source lines introduce a deterministic reread/equality check. Moving that check after all relevant awaited reads is simpler than an additional loop.
* Typed agent calls: unchanged. Actual FreshWriteBackJudge/WriteBackVerifier consumers are exercised; no admission schema is substituted for landed-claim judgment.
* Official framework/version: locked Python3.12/Pydantic2.12.5 semantics are unchanged; existing model validation/equality supplies the check. No FastAPI0.135.1 or LangGraph1.0.10 sequencing/retry changes. Earlier exact-tag Pydantic documentation verification remains applicable.
* Type safety: required native wire facts improve correctness; no Any/cast/ignore/model_copy added. The remaining native-id loss is a concrete boundary validation gap, not a need to weaken the neutral model.
* Hygiene: exact source/test pins, original red/green probes and hashes preserved; independent source is read-only. Root's initial positive-field harness error is distinguished from the five behavioral failures. Author green controls do not overrule two independently reproduced failures.

## Verification and publication

Commands use `/Users/kodezart/.local/bin/uv run --locked pytest -q` in the independent tree. Baseline command selected `tests/tracker/test_artifact_split_identity_independent.py`. Candidate boundary command selected that unchanged module and `tests/tracker/test_artifact_native_fields.py`.

The initial da37 additional command selected independent all-arm failure/cancellation controls plus actual verifier, fresh judge, Organize owner/graph owner, native amendment adoption/archive, and Audit runtime modules. Log `artifact-consumers-independent-da37.log`. Strict source check log `artifact-reader-independent-mypy-da37.log`. That initial run passed104tests579.09s; strict one source file also passed. No full suite is claimed.

Own KOD76 finding: https://linear.app/duckburg/issue/KOD-76#comment-5c6236a6-c7bf-4390-9640-4f1360b980c4 . No issue state, initiative, Notion, push or integration action. Requested High/Astra inherited, effective metadata unverified. Initial da37 verdict was request changes. Final6f431 verdict is bounded approval, after unchanged reruns and inclusion of the independently accepted canonical parent-read dependency.

## Exact final commands, files, and integration

```
uv run --locked pytest -q tests/tracker/test_artifact_split_identity_independent.py tests/tracker/test_artifact_native_fields.py tests/tracker/test_artifact_failure_boundary_independent.py tests/tracker/test_artifact_identity_alias_independent.py tests/tracker/test_issue_identity_boundary.py tests/tracker/test_criterion_family_identity.py tests/tracker/test_criterion_family_aliases.py tests/tracker/test_native_approval_aliases.py tests/chains/test_write_back_verifier.py tests/chains/test_fresh_write_back_judge.py tests/chains/test_organize_graph_owner.py::test_configured_owner_applies_graph_priority_then_reentry_writes_nothing tests/chains/test_organize_graph_owner.py::test_configured_split_prepares_children_without_execution_and_replays_cleanly 'tests/services/test_native_amendment_archive_independent.py::test_native_amendment_requires_preserved_current_history[unchanged-author]' tests/integration/test_audit_runtime_native.py::test_current_native_scope_publishes_verified_records_then_summary
uv run --locked mypy src/kodezart/services/tracker_artifacts.py src/kodezart/adapters/linear_mcp_tracker.py
uv run --locked ruff check src/kodezart/services/tracker_artifacts.py src/kodezart/adapters/linear_mcp_tracker.py tests/tracker/test_artifact_native_fields.py tests/tracker/test_artifact_split_identity_independent.py tests/tracker/test_artifact_failure_boundary_independent.py tests/tracker/test_artifact_identity_alias_independent.py
uv run --locked ruff format --check src/kodezart/services/tracker_artifacts.py src/kodezart/adapters/linear_mcp_tracker.py tests/tracker/test_artifact_native_fields.py tests/tracker/test_artifact_split_identity_independent.py tests/tracker/test_artifact_failure_boundary_independent.py tests/tracker/test_artifact_identity_alias_independent.py
```

All commands ran in the isolated reviewer tree with `/Users/kodezart/.local/bin/uv`; tests use locked Python3.12. Source SHA authored by reviewer: **none**. Reviewer-owned independent files are the split probe (root retained it unchanged in6f431) and two additional untracked test files for all-arm failures and native identity aliases. All are copied to the session for reuse; exact source/test hashes are in `artifact-read-independent-final-hashes.json`.

Root can integrate da37 + the accepted b84 dependency +6f431, serially reconciling any already-integrated dependency rather than duplicating it. Do not integrate the initial da37 alone as complete artifact-boundary acceptance. The M1 addressed native wire/error helper remains shared infrastructure; M4 label/criterion artifact reader and M2 graph/split artifact hunks keep their existing extraction owners. No new cyclic M2 dependency is introduced. Root alone maintains milestone PRs and canonical integration. Full KOD76/531/538 adoption, event/state authority, public-write sanitization census, transactional backend snapshots and in-flight fencing are not certified by this bounded review.

Final superseding own76 approval: https://linear.app/duckburg/issue/KOD-76#comment-100a8534-d246-4fdb-a992-491ec79cacd4 . The earlier request-changes comment is preserved as the exact da37 evidence and is superseded only for6f431 after the successful independent rerun.

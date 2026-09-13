# Local git state left by the dead Codex run — kodezart (inventory, 2026-09-13 ~05:20 CEST)

Strictly read-only inventory. No checkout/reset/stash/clean/commit/push/fetch/prune/worktree-remove was run.
(Only side effect: `git status` refreshed the per-worktree `index` files under `.git/worktrees/*`, which bumps their mtimes; refs and working trees untouched.)

## 0. Headline

| Item | Value |
|---|---|
| Tmp clone | `/private/tmp/kodezart-v03-implementation` — born 2026-09-07 16:48:31, `.git` last written 2026-09-13 02:36:32, `.git` = 114 MB |
| Clone integrity | **`.git/HEAD` is missing** (git refuses the top dir: "not a git repository"); everything else present. **343 objects missing** from the store (328 blobs, 15 trees; `git fsck --connectivity-only`). No `objects/info/alternates`. Sampled missing blobs are not in the primary repo either. |
| Run timeline | Kill-state capture 2026-09-12 13:20 → run **kept going** until ~2026-09-13 02:36–02:42 (last commit 980b179 @ 02:33:21; canonical eae9a94 @ 02:24:37; last file write in m5 worktree 02:34; `.git/worktrees` 02:36; `kodezart-recovery-session/` log 02:42). Eleven of the 14 kill-state worktree dirs were wiped by the run itself at 2026-09-13 00:00–00:07. |
| Worktrees | `git worktree list` = 172 (main + 171 linked), 0 prunable. But `.git/worktrees/` holds **507** entries: 336 are corpses (only `index` + `logs`, no `gitdir`/`HEAD`) that git silently ignores. |
| Local branches | 295 in the tmp clone. **60 PUSHED** (head reachable from an `origin/*` ref in the primary repo), **235 ORPHAN-OBJECT** (head SHA does not exist in the primary object store at all ⇒ not on origin), 0 plain UNPUSHED. 235 orphan names = **195 unique heads**. |
| Orphan heads by date | 2026-09-12/13: **65 heads, all object-complete** (the recovery/M1–M5 work). 2026-09-08: 130 heads — **38 complete, 92 have missing objects** in their own delta (cannot be fully checked out). |
| Dirty worktrees | **39** dirty (none trivial: all are new review/oracle test files or src edits). Biggest: `kodezart-v03-m5-delivery-termination` — 75 files (22 modified +916/−51, 53 untracked ≈ 8,827 lines). |
| Detached-only commits | **16 detached worktrees whose HEAD commit is on no branch** (anchored only by the worktree `HEAD` file). |
| Dropped stashes | 1 live stash + **4 dropped stash snapshots** on `codex/v03-recovery-audit-runtime` (Sep 12 20:05–20:54) reachable only via fsck; 52 fsck-unreachable commits total; 30 corpse-stub reflogs point at otherwise-dangling Sep-8 commits. |
| Not in git at all | `/private/tmp/kodezart-recovery-session/` — 2,039 files (1,445 .log, 199 .py, 172 .json, 149 .md incl. `m1..m5-pr-body.md`, `canonical-pr-body.md`, `m3-remaining-checklist.md`, review notes; **27 .patch files**, 4.5 MB largest), Sep 12 13:51 → Sep 13 02:42. The run's own notes/patches. |
| Primary repo | `/Users/kodezart/Projects/kodezart` main @ `92597c0f` (2026-07-27) is **11 behind `origin/main` @ `4661a24b`** (2026-09-07, v0.2.0), 0 ahead. 12-file uncommitted diff (mtime 2026-08-04 15:57) = stream-drain/error-egress observability work on the old v0.1.x lineage, unrelated to v0.3; overlaps 7 files with stash@{0}. `recovery-section-top/` is a Figma landing-page recovery spec, unrelated to kodezart. |
| Filesystem | `/private/tmp` lives on the APFS Data volume (`/dev/disk3s5`, 228 GiB, **95 % full, 11 GiB free**). Not a RAM disk (uptime 19 days; entries from Aug 24 still present). macOS `dirhelper` (`/System/Library/LaunchDaemons/com.apple.bsd.dirhelper.plist`) is installed, which removes `/private/tmp` items untouched for ~3 days — treat as volatile. |

## 1. `/private/tmp/kodezart-v03-implementation` (the Codex clone)

### 1.1 Repository shape
- `.git/` contents: `config`, `hooks/`, `index` (Sep 7 22:57), `info/`, `logs/`, `objects/` (4 packs + 256 loose dirs; last write Sep 13 02:33:21), `packed-refs` (341 lines, Sep 13 00:29:51), `refs/`, `worktrees/` (507 entries). **No `HEAD`, no `ORIG_HEAD`.** Consequence: run every git command from a linked worktree, e.g. `git -C /private/tmp/kodezart-v03-recovery-integration …` (that is what this inventory did).
- `git remote -v`: `origin https://github.com/yaldan/kodezart.git` (fetch+push). 58 `refs/remotes/origin/*`, SHA-for-SHA identical to the primary repo's freshly fetched `origin/*` (so the tmp clone's remote view is current; the earlier run did fetch).
- `git stash list` (live): `stash@{0}: On codex/v03-recovery-audit-runtime: audit-runtime-before-shared-writeback-dependency` — 2026-09-12 19:25:02, 4 files +102/−54.
- Upstream tracking: only the 34 branches with `[branch "…"]` stanzas in `.git/config` have upstreams (all `origin/<same name>`); every other local branch has none. Six tracked branches are *behind* origin (focused-readers-and-admission −2, permissions-and-fixed-admission −1, session-failures-and-agent-settings −1, shared-policies-and-sessions −1, typed-runtime-boundaries −2, workflow-phases-and-settings −1); none is ahead.
- `git worktree list`: 172 lines; `--porcelain` reports 0 `prunable`. `.git/worktrees/` has 507 dirs; the 336 not listed are corpses: `index` + `logs/` only (64 of them lack even `logs/HEAD`). 30 corpse reflogs end at a commit on no branch (all 2026-09-08; e.g. `dc0d86ab4 refactor(adapters): share bounded retry policy and backoff`, `89c31088c fix(tracker): refuse native claims without fenced renewal`, `45e3e9f7e Reject retired knowledge secret files…`) — full list in `scratchpad/stub_reflog.txt`.
- Object-store health: `git fsck --connectivity-only` → 343 missing (328 blob, 15 tree). All missing objects belong to 2026-09-08-era commits (the earlier `kod-*`/`v03-*` ticket phase); every 2026-09-12/13 commit checked is complete.

### 1.2 Branch classification (295 local heads → primary repo's `origin/*`)
Method: `git -C <primary> cat-file -e <sha>` (exists at all?) then `git -C <primary> branch -r --contains <sha>`. Commit counts are `git rev-list --count <merge-base(canonical, sha)>..<sha>` with canonical = `origin/codex/v03-recovery-integration` = `eae9a940e9682a09680b4adf49781f3743c12754`. Full 295-row table in Appendix A.

- **PUSHED (60)** — safe on origin. Includes all four milestone tips and canonical: `codex/v03-m1-scope-ports`=`ddb8cdef`, `codex/v03-m2-organize`=`cef0dff6`, `codex/v03-m3-plan-walk`=`dad63c4f`, `codex/v03-m4-tracker-lifecycle`=`c855fb06`, `codex/v03-recovery-integration`=`eae9a940`, plus `codex/v03-m5-delivery-termination`=`10c51a74` (= `origin/codex/v03-m3-plan-walk`), `codex/v03-m5-union-roster-correction`=`1a83163e` and `codex/v03-native-resume-integration-review`=`da39c439` (both on canonical), `codex/v03-m3-plan-walk-census`=`7675c0b4`, `codex/v03-m4-native-read-independent`=`8930fcb2`, `codex/v03-m2-integrated-root-review`=`9e387b4a`, `codex/v03-recovery`=`55a87a50` (= `origin/kodezart/v03-trunk`), and 47 older `codex/v03-*` branches whose heads sit on the pushed `codex/v03-*` lineage.
- **ORPHAN-OBJECT (235 names / 195 heads)** — exist only in `/private/tmp`. Three families:

**(a) 13 heads on the `main` lineage (merge-base with canonical = `4661a24b` = origin/main): the M1–M5 extraction family.** Delta measured against the nearest pushed milestone branch:

| head | branches | nearest pushed ref | ahead | delta | what it is |
|---|---|---|---|---|---|
| `40b250c5c` | v03-m4-verifier-extraction | origin/codex/v03-m1-scope-ports | 3 | 93 files +9851/−1043 | M4: canonical shared write-back verifier + fresh judge, on top of the M1 lease extraction (`d6e40fd`, `57dcc8c`) |
| `57dcc8c3a` | v03-m1-lease-root-review | origin/codex/v03-m1-scope-ports | 2 | 70 files +8121/−1036 | M1: configured surface leases + run-owned terminal writes, marker prompt/fixture closure |
| `90aea37cc` | v03-m1-lease-extraction | origin/codex/v03-m1-scope-ports | 1 | 64 files +8103/−1029 | M1 lease extraction (parent of the above) |
| `864bc0ab3` | v03-m1-delta-cancel, v03-m1-delta-cancel-root-review | origin/codex/v03-m1-scope-ports | 8 | 85 files +2685/−325 | M1 native-criterion-read boundary + session policy + "restore scheduled gate windows when observation is cancelled" |
| `ea1d91596` | v03-m1-session-policy-extraction | origin/codex/v03-m1-scope-ports | 7 | 81 files +2504/−315 | same chain minus the delta-cancel commit |
| `fec7f2829` | v03-m1-native-read-extraction | origin/codex/v03-m1-scope-ports | 4 | 28 files +991/−7 | M1 configured native criterion read boundary (`6346911`…`fec7f28`) |
| `797931418` | v03-m3-oracle-closure | origin/codex/v03-m3-plan-walk | 1 | 9 files +1314/−1 | M3: "test(workflow): restore native specification and CI observation controls" (the 8 oracle tests also sitting untracked in m3-plan-walk-census) |
| `980b17965` | v03-m2-groom-rubric-freshness | origin/codex/v03-m2-organize | 2 | 18 files +668/−38 | M2 Organize rubric: freshness + native binding failures (newest commit of the run, 02:33) |
| `0363cc9fa` | v03-m2-groom-rubric-correction | origin/codex/v03-m2-organize | 1 | 17 files +560/−38 | M2: supply native Organize rubrics through validated prompt metadata (parent of 980b179) |
| `5e4ec11e1` | v03-m1-logging-root-review, v03-mcp-reopen-diagnosis | origin/codex/v03-m1-scope-ports | 1 | 6 files +169/−26 | M1: safe traceback rendering + configured MCP reopen check |
| `ac541892f` | v03-m4-classification-shared-prerequisite | origin/codex/v03-m2-organize | 1 | 3 files +53 | M4 classification writer contracts (prerequisite) |
| `1020f0de6` | v03-m4-write-back-settings-extraction | origin/codex/v03-m2-organize | 1 | 3 files +50 | M4 explicit canonical write-verification settings |
| `eb57778ed` | v03-m1-dispatch-timeout-independent, v03-m1-dispatch-timeout-oracle | origin/codex/v03-m1-scope-ports | 1 | 1 file +13/−6 | M1 dispatch-cancellation oracle sync |

**(b) 52 heads dated 2026-09-12/13 on the canonical (`codex/v03-recovery*`) lineage — object-complete, fully recoverable.** Table (sorted by commits not on canonical):

| commits not on canonical | head | date | delta vs merge-base | branches | subjects (newest first, max 4) |
|---|---|---|---|---|---|
| 13 | `c579eb9da` | 2026-09-13 | 68 files, 6326(+), 300(-) | v03-native-identity-cancel-correction v03-native-identity-root-review  | c579eb9 fix: settle native identity subprocesses on cancellation; 3508980 fix: release native workspaces when preparation fails; 2618272 test: seed saved outcome from the reconciled prior grade; a198da3 fix: preserve cancellation across native phase boundaries |
| 12 | `f632aee96` | 2026-09-12 | 67 files, 3923(+), 779(-) | v03-recovery-scope-runtime  | f632aee Keep API event census aligned with scoped native streams; 331cb45 Validate scoped event payloads and recheck admission at graph launch; db76a86 Verify scoped native replay and serialize addressed stream events; ee13a79 Verify native PR head base and lifecycle around delivery watches |
| 12 | `350898010` | 2026-09-12 | 67 files, 6239(+), 300(-) | v03-recovery-native-resume-prepare  | 3508980 fix: release native workspaces when preparation fails; 2618272 test: seed saved outcome from the reconciled prior grade; a198da3 fix: preserve cancellation across native phase boundaries; 4dd7b3e fix: resume native writer phases through parent checkpoints |
| 12 | `2727ee671` | 2026-09-12 | 74 files, 4653(+), 180(-) | v03-recovery-semantic-classification-corrective  | 2727ee6 Require strict native classification for description authority; 5cafa69 Require known native classification before selecting write authority; 9456614 Honor criterion surface ownership for classification writes; db9d370 Implement verified native amendments before harness persistence |
| 11 | `e7ae430ca` | 2026-09-12 | 82 files, 4593(+), 990(-) | v03-recovery-lane-delivery  | e7ae430 Revalidate native delivery evidence at resumed terminal boundaries; 52cd934 Validate delivery PR identity before gated comments; 4e9d9b6 Verify native PR head base and lifecycle around delivery watches; fead928 Recheck delivery refs after awaited publication preparation |
| 11 | `26182720e` | 2026-09-12 | 65 files, 6091(+), 300(-) | v03-recovery-native-resume-cancel  | 2618272 test: seed saved outcome from the reconciled prior grade; a198da3 fix: preserve cancellation across native phase boundaries; 4dd7b3e fix: resume native writer phases through parent checkpoints; 62a86ad fix(amendment): retain verified archive authority through publication |
| 10 | `38e848b2f` | 2026-09-12 | 64 files, 4715(+), 203(-) | v03-recovery-audit-preflight  | 38e848b Require actual audit classification reads before queue startup; 0db8d05 Format independent classification regression probes; dd11ffd Compose configured native audit with verified publication and coverage receipts; 080fa6c Require known native classification before selecting write authority |
| 9 | `db9d3702d` | 2026-09-12 | 71 files, 4251(+), 157(-) | v03-recovery-semantic-applied-current  | db9d370 Implement verified native amendments before harness persistence; e94f825 Register native prompt inputs and align utility prompt metadata; 5347e9b fix(tracker): retain escalation label authority across retries; 2d463a2 WIP connect native amendment graph and preserve tested write ordering |
| 9 | `4dd7b3eb4` | 2026-09-12 | 65 files, 6077(+), 300(-) | v03-recovery-native-resume  | 4dd7b3e fix: resume native writer phases through parent checkpoints; 62a86ad fix(amendment): retain verified archive authority through publication; 4c6322f Require strict native classification for description authority; 8f80bb3 Format independent classification regression probes |
| 8 | `dd11ffd54` | 2026-09-12 | 63 files, 4661(+), 203(-) | v03-recovery-audit-runtime  | dd11ffd Compose configured native audit with verified publication and coverage receipts; 080fa6c Require known native classification before selecting write authority; 8f04db9 Honor criterion surface ownership for classification writes; ab75f03 Register native prompt inputs and align utility prompt metadata |
| 7 | `68eabb515` | 2026-09-12 | 71 files, 4264(+), 239(-) | v03-recovery-organize-composition  | 68eabb5 Keep Organize halt identity scoped to its actual container; 641797e fix(organize): bind writes to current authority and retain verifier evidence; d52f0c7 Compose configured Organize scheduling and preserve addressed halts; 161ca93 Compose bounded Organize preparation and fresh write-back judgment |
| 7 | `4c6322fb1` | 2026-09-12 | 51 files, 3471(+), 127(-) | v03-recovery-native-amended-root-review  | 4c6322f Require strict native classification for description authority; 8f80bb3 Format independent classification regression probes; 8cd8feb Require known native classification before selecting write authority; ddfd79f Implement verified native amendments before harness persistence |
| 7 | `3df7469e4` | 2026-09-12 | 55 files, 1381(+), 195(-) | v03-recovery-surface-owner  | 3df7469 fix(config): refuse the retired surface lease duration key; e179701 Validate terminal marker configuration when constructing the writer; 82c3d20 Acquire explicit fixture leases for protected marker writes; 0e503a5 Require declared marker ownership across the tracker port and fake |
| 7 | `19e5d870d` | 2026-09-12 | 66 files, 2447(+), 774(-) | v03-recovery-native-fire  | 19e5d87 fix(fire): bind resumed effects and fresh judgments to current checks; 10fa10d fix(fire): carry native specs through live execution and replay; d785dfd feat(fire): the criteria a native fire owes come from the tracker's own spec read; 9dd5d5e feat(fire): an execution-only fire composition that re-validates its subtree's Tod |
| 6 | `edbd4112a` | 2026-09-12 | 44 files, 3510(+), 133(-) | v03-recovery-amendment-comment  | edbd411 wip(native): assert existing comment before amendment; d80e7fe Reject ambiguous native comment provenance across all readers; 1c39736 fix(native): retain worktree across incomplete persistence receipt; 2e7fdd9 Preserve native cancellation, publication authority and genuine evaluation history |
| 6 | `6f431bbaa` | 2026-09-12 | 9 files, 456(+), 22(-) | v03-artifact-read-final  | 6f431bb Validate split identity response before final source consistency read; 0111df9 Retain actual native address and approval alias regression controls; ca439f3 Normalize attested native approval identity before ancestry and family reads; 0996962 Validate criterion parent response against its native address |
| 6 | `31bb28982` | 2026-09-12 | 48 files, 3907(+), 123(-) | v03-recovery-graph-root-review v03-recovery-organize-graph-correction  | 31bb289 Retry graph mutations only after fresh authorization and before receipt; e687c76 fix(tracker): refresh protected comment checks on unsent retries; 645d032 Validate graph and split observations before issuing native writes; 3ffc89e fix(tracker): bind comment amendments to final native snapshot |
| 6 | `0f6e88c0f` | 2026-09-12 | 50 files, 4148(+), 131(-) | v03-recovery-semantic-applied  | 0f6e88c WIP preserve applied native amendment draft before dependency update; d80e7fe Reject ambiguous native comment provenance across all readers; 1c39736 fix(native): retain worktree across incomplete persistence receipt; 2e7fdd9 Preserve native cancellation, publication authority and genuine evaluation history |
| 6 | `0e30d784f` | 2026-09-12 | 9 files, 456(+), 22(-) | v03-artifact-read-independent  | 0e30d78 Validate split identity response before final source consistency read; 15c3817 Retain actual native address and approval alias regression controls; 9cc9061 Normalize attested native approval identity before ancestry and family reads; bd5511b Validate criterion parent response against its native address |
| 5 | `ee1d44bd5` | 2026-09-12 | 65 files, 3881(+), 202(-) | v03-recovery-organize-owner  | ee1d44b fix(organize): bind writes to current authority and retain verifier evidence; 161ca93 Compose bounded Organize preparation and fresh write-back judgment; fd8686d Reconcile remaining lifecycle and lease fixtures with current contracts; 835604a Keep parent completion out of merged-fire lifecycle writes |
| 5 | `9b9008676` | 2026-09-12 | 40 files, 521(+), 243(-) | v03-recovery-port-contract  | 9b90086 Migrate tracker boundary regressions to neutral port failures; 5b4ae92 Keep pretty tracebacks from rendering live workflow locals; 219d132 Translate tracker and record failures at adapter boundaries; 2574358 Refuse protected record replacement under another writer attribution |
| 4 | `b84d43ba4` | 2026-09-12 | 6 files, 218(+), 16(-) | v03-native-read-address-corrective  | b84d43b Retain actual native address and approval alias regression controls; 7cd8e70 Normalize attested native approval identity before ancestry and family reads; a05c169 Validate criterion parent response against its native address; 0aad6e6 Refuse blank criterion membership mappings before tracker reads |
| 4 | `b714b9841` | 2026-09-12 | 42 files, 324(+), 544(-) | v03-recovery-criterion-class  | b714b98 test(criteria): prove uniform failure and refuse every retired class payload; abfb0c8 test(fakes): drop the fixture the two-partition gate left behind; 0fefea7 docs(changelog): record the criterion class's removal from the wire; caa1c3c refactor(criteria): delete the criterion class |
| 4 | `36b18f5e5` | 2026-09-12 | 46 files, 3531(+), 147(-) | v03-audit-workspace-corrective  | 36b18f5 Preserve programmer failures during workspace acquisition; 536d704 Preserve operational versus programmer failure boundaries in Audit; 5a3431f Require actual audit classification reads before queue startup; 68a58c7 Compose configured native audit with verified publication and coverage receipts |
| 4 | `1c397368e` | 2026-09-12 | 38 files, 3035(+), 82(-) | v03-recovery-semantic-writeback  | 1c39736 fix(native): retain worktree across incomplete persistence receipt; 2e7fdd9 Preserve native cancellation, publication authority and genuine evaluation history; d93f3e9 Add native semantic judgment and precommit guard review candidate; 515724a Read issue rulings across their actual recorded lane markers |
| 3 | `fc4953e1d` | 2026-09-12 | 6 files, 379(+), 79(-) | v03-recovery-audit-types  | fc4953e fix(audit): require strict indices and covered mandate sources; 37075cc refactor(audit): carry mandate verdicts through closed typed variants; 4daec66 test(audit): bind temporary PR base to the actual repository |
| 3 | `536d704aa` | 2026-09-12 | 44 files, 3474(+), 143(-) | v03-audit-root-integration-review  | 536d704 Preserve operational versus programmer failure boundaries in Audit; 5a3431f Require actual audit classification reads before queue startup; 68a58c7 Compose configured native audit with verified publication and coverage receipts |
| 3 | `3135131ab` | 2026-09-12 | 7 files, 336(+), 43(-) | v03-recovery-criterion-label-surface  | 3135131 Format independent classification regression probes; ce2751e Require known native classification before selecting write authority; 246da86 Honor criterion surface ownership for classification writes |
| 3 | `2e7fdd9b5` | 2026-09-12 | 36 files, 2901(+), 82(-) | v03-recovery-semantic-amended  | 2e7fdd9 Preserve native cancellation, publication authority and genuine evaluation history; d93f3e9 Add native semantic judgment and precommit guard review candidate; 515724a Read issue rulings across their actual recorded lane markers |
| 2 | `dd6147c6c` | 2026-09-12 | 3 files, 126(+), 1(-) | v03-recovery-scope-ancestor v03-recovery-scope-ancestor-root-review  | dd6147c Use a synthetic UUID in the scope alias fixture; 8dedc14 Refuse mismatched canonical scope ancestor identities |
| 2 | `d93f3e973` | 2026-09-12 | 33 files, 2199(+), 80(-) | v03-recovery-semantic-amendment  | d93f3e9 Add native semantic judgment and precommit guard review candidate; 515724a Read issue rulings across their actual recorded lane markers |
| 2 | `d59047eb0` | 2026-09-12 | 2 files, 26(+) | v03-recovery-shared-contracts  | d59047e Preserve delivery head mismatch evidence in a typed error; c37dfa2 Define an explicit surface for criterion child creation |
| 2 | `53b3ca89e` | 2026-09-12 | 7 files, 279(+), 36(-) | v03-recovery-native-state-resolver v03-recovery-state-resolver-review  | 53b3ca8 Resolve initial issue state by its validated native team identity; 68187b0 Read issue rulings across their actual recorded lane markers |
| 2 | `2c7bd233d` | 2026-09-12 | 8 files, 725(+), 24(-) | v03-recovery-amendment-comment-retry  | 2c7bd23 fix(tracker): refresh protected comment checks on unsent retries; 9136d9f fix(tracker): bind comment amendments to final native snapshot |
| 2 | `179ef5acf` | 2026-09-12 | 34 files, 1630(+), 527(-) | v03-recovery-alarm-owner  | 179ef5a fix(alarms): validate the exact record snapshot before upsert; 76125c3 feat(alarms): persist typed observations by complete native address |
| 2 | `03b99acb7` | 2026-09-12 | 7 files, 400(+), 58(-) | v03-recovery-comment-provenance  | 03b99ac Reject ambiguous native comment provenance across all readers; ba1050d Read issue rulings across their actual recorded lane markers |
| 1 | `fd93cf2a1` | 2026-09-12 | 7 files, 141(+), 26(-) | v03-recovery-audit-report-contract  | fd93cf2 Require the matching mandate payload in completed audit report types |
| 1 | `da37bda26` | 2026-09-12 | 2 files, 133(+), 3(-) | v03-artifact-read-corrective  | da37bda Require reported native fields in structured tracker artifacts |
| 1 | `c47a7f112` | 2026-09-12 | 4 files, 44(+), 9(-) | v03-recovery-native-compat  | c47a7f1 Restore authored contradiction wire contract and paused job fixture |
| 1 | `be9717f47` | 2026-09-12 | 1 file, 2(+) | v03-recovery-marker-preflight  | be9717f Validate ownership marker configuration before reading native records |
| 1 | `a51d7779f` | 2026-09-12 | 5 files, 218(+), 16(-) | v03-recovery-classification-authority v03-recovery-classification-root-review  | a51d777 fix(tracker): retain escalation label authority across retries |
| 1 | `9136d9f31` | 2026-09-12 | 6 files, 413(+), 20(-) | v03-recovery-amendment-comment-current v03-recovery-comment-independent  | 9136d9f fix(tracker): bind comment amendments to final native snapshot |
| 1 | `85fc8a3d2` | 2026-09-12 | 7 files, 200(+), 38(-) | v03-recovery-job-acceptance  | 85fc8a3 fix(api): validate job acceptance and reverse reconnect routes |
| 1 | `7d6208af8` | 2026-09-12 | 7 files, 69(+), 23(-) | v03-recovery-parent-rollup  | 7d6208a Keep parent completion out of merged-fire lifecycle writes |
| 1 | `730051660` | 2026-09-12 | 2 files, 31(+), 15(-) | v03-native-gate-corrective  | 7300516 Reuse native criterion reader and census nonretrying graphs |
| 1 | `64aa3bd1b` | 2026-09-12 | 13 files, 159(+), 6(-) | v03-recovery-write-back-settings  | 64aa3bd Give canonical write verification its own configured repair bound |
| 1 | `643c96ea6` | 2026-09-12 | 8 files, 51(+), 21(-) | v03-audit-gate-corrective  | 643c96e Keep audit outcome dependency in its runtime and align failure fixtures |
| 1 | `49b4bffa6` | 2026-09-12 | 5 files, 8(+), 2(-) | v03-recovery-native-prompt-census  | 49b4bff Register native prompt inputs and align utility prompt metadata |
| 1 | `417ceecab` | 2026-09-12 | 1 file, 10(+), 2(-) | v03-recovery-alarm-payload-fixture  | 417ceec Update alarm payload oracle for typed evidence values |
| 1 | `2e6baba4a` | 2026-09-12 | 9 files, 60(+), 62(-) | v03-recovery-port-failures v03-recovery-wire-boundary  | 2e6baba refactor(adapters): own vendor wire schemas at their boundaries |
| 1 | `1088e18fb` | 2026-09-12 | 3 files, 20(+), 8(-) | v03-recovery-gate-reconciliation  | 1088e18 Reconcile repository gate fixtures and protocol documentation |
| 1 | `01a62d6aa` | 2026-09-12 | 5 files, 12(+), 8(-) | v03-recovery-gate-followup  | 01a62d6 Reconcile remaining lifecycle and lease fixtures with current contracts |

**(c) 130 heads dated 2026-09-08 (earlier `kod-*` ticket phase; 1–14 commits each above canonical).** 38 are object-complete, 92 have missing blobs/trees inside their own delta (`CORRUPT-N` = number of missing objects in `merge-base..head`), so they cannot be checked out or diffed in full from this clone. Full per-head table in Appendix B. Object-complete ones (38): `kod-139-operation-sections` d7e7521, `kod-380-scope-approval`/`scope-approval-mutants` 4f48afe/b1d410c, `kod-470-public-docs` 19a03d9, `kod-510-recorded-assertion-*` 9ed8989, `dispatch-budget-fixture`/`pass-budget-mutants` 1f722c1, `v03-union-composition` e6e9c97 (12 commits, 11 files +1133), `v03-delivery-stalled*` 38a8638, `v03-workspace-url-*` 763d7be/1ded742, `v03-marker-*` c14123d, `v03-milestone-metadata*` 335f9bd, `v03-http-dependencies(-before)` ecd0d14/2298e0d, `v03-criterion-*` (record-fidelity 9bbf670, fidelity-mutations b139373, resolution f094b5e), `v03-pagination-*` 294b2e0/93f9e89, `v03-audit-evidence` 5595af1, `v03-audit-combined-review` 2adc8c8, `v03-audit-worktree-integrity` e2a49a8, `v03-cache-remote-name` 852623f, `v03-check-runner-cancellation` da1d474, `v03-delivery-no-adapter` dc49867, `v03-dispatch-log-isolation` cf6fc4e, `v03-event-credential-fixtures` bbed103, `v03-event-model-conformance` 4f96b46, `v03-fire-writer-inventory` a3a3089, `v03-http-mcp-lifecycle` 2bedc1a, `v03-judgment-schema-census` e5bb791, `v03-logging-test-isolation` 51e45d2, `v03-nested-settings-docs` 6745593, `v03-retirement-census-fix` 8f222ac, `v03-ruling-prompt(-mutations)` 2723b79, `v03-stalled-cumulative` c8382d9, `v03-union-native-inputs`/`union-replacement-mutants` 79a7a46, `v03-writeback-native-proof(-mutations)` d0f30f2.

## 2. Dirty and detached worktrees

### 2.1 Dirty worktrees (39) — none trivial
No worktree is dirty only in `uv.lock`, logs or caches. Every dirty item is either a new `tests/**/test_*_{independent,peer,original,review}.py` oracle/review file written by the run (25–526 lines each) or tracked source/test edits. Sorted by size:

| worktree | branch / HEAD | files | content | lines | newest mtime | nature |
|---|---|---|---|---|---|---|
| m5-delivery-termination | codex/v03-m5-delivery-termination@10c51a7 | 75 (22 M, 53 ??) | github_api.py github_types.py linear_markers.py linear_mcp_tracker.py … | untracked=8827, tracked=+916/-51 | 09-13 02:34 | substantive (tracked src/test edits + new files) |
| m3-plan-walk-census | codex/v03-m3-plan-walk-census@7675c0b | 9 (1 M, 8 ??) | test_native_approval_aliases.py test_ci_observation.py test_ci_watch_evidence.py test_ci_watch_result.py … | untracked=1314, tracked=+2/-2 | 09-13 02:15 | substantive (tracked src/test edits + new files) |
| m2-authority-review-before | DETACHED@9e387b4 | 3 (0 M, 3 ??) | test_authority_independent_review.py test_organize_description_authority.py test_organize_write_revalidation.py | untracked=526, tracked=+0/-0 | 09-13 02:01 | substantive (new review/oracle tests) |
| native-resume-independent | DETACHED@4dd7b3e | 5 (0 M, 5 ??) | test_native_completed_writer_gap_peer.py test_native_prepare_cancel_peer.py test_native_prepare_cleanup_peer.py test_native_resume_peer.py … | untracked=479, tracked=+0/-0 | 09-12 23:17 | substantive (new review/oracle tests) |
| native-resume-cancel-independent | DETACHED@3508980 | 4 (0 M, 4 ??) | test_native_prepare_cancel_original_peer.py test_native_prepare_cleanup_original_peer.py test_native_resume_peer.py test_native_receipt_synchronized_peer.py | untracked=354, tracked=+0/-0 | 09-12 23:23 | substantive (new review/oracle tests) |
| organize-graph-independent-review | DETACHED@f47a1c6 | 2 (0 M, 2 ??) | test_organize_graph_independent.py test_organize_graph_independent.py | untracked=323, tracked=+0/-0 | 09-12 19:15 | substantive (new review/oracle tests) |
| recovery-amendment-comment-before | DETACHED@d14254e | 1 (0 M, 1 ??) | test_comment_expected.py | untracked=246, tracked=+0/-0 | 09-12 19:10 | substantive (new review/oracle tests) |
| m2-groom-independent-parent | DETACHED@e99c9d1 | 1 (0 M, 1 ??) | test_organize_rubric.py | untracked=244, tracked=+0/-0 | 09-13 02:30 | substantive (new review/oracle tests) |
| m4-heartbeat-review | DETACHED@c855fb0 | 2 (0 M, 2 ??) | test_claim_heartbeat_clock.py test_heartbeat_clock_diagnostic.py | untracked=224, tracked=+0/-0 | 09-13 02:30 | substantive (new review/oracle tests) |
| native-amended-independent-current | DETACHED@4c6322f | 2 (0 M, 2 ??) | test_native_amendment_archive_independent.py test_native_amendment_payload_independent.py | untracked=203, tracked=+0/-0 | 09-12 21:08 | substantive (new review/oracle tests) |
| scope-review-delivery | DETACHED@9d379f4 | 1 (0 M, 1 ??) | test_scope_runtime_independent_review.py | untracked=190, tracked=+0/-0 | 09-12 16:11 | substantive (new review/oracle tests) |
| recovery-semantic-checkpoint-evidence | DETACHED@2727ee6 | 2 (0 M, 2 ??) | test_amendment_framework_resume_evidence.py test_native_checkpoint_evidence.py | untracked=189, tracked=+0/-0 | 09-12 21:02 | substantive (new review/oracle tests) |
| recovery-ruling-reader-before | DETACHED@44fd521 | 1 (0 M, 1 ??) | test_ruling_reader_independent.py | untracked=182, tracked=+0/-0 | 09-12 18:00 | substantive (new review/oracle tests) |
| m4-native-read-independent | codex/v03-m4-native-read-independent@8930fcb | 2 (0 M, 2 ??) | test_m4_local_sdk_independent.py test_m4_native_extension_independent.py | untracked=176, tracked=+0/-0 | 09-12 22:58 | substantive (new review/oracle tests) |
| recovery-classification-before | DETACHED@5ef89e2 | 1 (0 M, 1 ??) | test_classification_authority.py | untracked=157, tracked=+0/-0 | 09-12 19:49 | substantive (new review/oracle tests) |
| artifact-read-independent | codex/v03-artifact-read-independent@0e30d78 | 2 (0 M, 2 ??) | test_artifact_failure_boundary_independent.py test_artifact_identity_alias_independent.py | untracked=150, tracked=+0/-0 | 09-12 22:39 | substantive (new review/oracle tests) |
| m1-scope-independent-review | DETACHED@1811397 | 2 (0 M, 2 ??) | test_m1_scope_alias_calibration.py test_m1_scope_independent.py | untracked=121, tracked=+0/-0 | 09-12 20:01 | substantive (new review/oracle tests) |
| amendment-comment-independent-review | DETACHED@e5ef84b | 1 (0 M, 1 ??) | test_comment_expected_review_original.py | untracked=118, tracked=+0/-0 | 09-12 19:21 | substantive (new review/oracle tests) |
| organize-native-review | DETACHED@ee1d44b | 1 (0 M, 1 ??) | test_organize_native_review_original.py | untracked=117, tracked=+0/-0 | 09-12 17:03 | substantive (new review/oracle tests) |
| audit-corrective-independent | DETACHED@1041412 | 2 (0 M, 2 ??) | test_audit_historical_source_review.py test_audit_workspace_failure_review.py | untracked=116, tracked=+0/-0 | 09-12 21:43 | substantive (new review/oracle tests) |
| m4-heartbeat-parent-review | DETACHED@268be40 | 1 (0 M, 1 ??) | test_heartbeat_clock_diagnostic.py | untracked=100, tracked=+0/-0 | 09-13 02:26 | substantive (new review/oracle tests) |
| marker-preflight-independent-review | DETACHED@be9717f | 1 (0 M, 1 ??) | test_m1_scope_independent.py | untracked=92, tracked=+0/-0 | 09-12 20:01 | substantive (new review/oracle tests) |
| m1-scope-donor-review | DETACHED@5ef89e2 | 1 (0 M, 1 ??) | test_m1_scope_independent.py | untracked=92, tracked=+0/-0 | 09-12 19:59 | substantive (new review/oracle tests) |
| alarm-independent-review | DETACHED@cd558ab | 1 (0 M, 1 ??) | test_alarm_review_original.py | untracked=86, tracked=+0/-0 | 09-12 17:39 | substantive (new review/oracle tests) |
| m2-integrated-root-review | codex/v03-m2-integrated-root-review@9e387b4 | 1 (0 M, 1 ??) | test_organize_write_revalidation.py | untracked=81, tracked=+0/-0 | 09-13 01:46 | substantive (new review/oracle tests) |
| native-address-independent | DETACHED@b84d43b | 1 (0 M, 1 ??) | test_address_identity_peer.py | untracked=79, tracked=+0/-0 | 09-12 22:28 | substantive (new review/oracle tests) |
| audit-report-independent-review | DETACHED@fd93cf2 | 1 (0 M, 1 ??) | test_audit_report_independent.py | untracked=78, tracked=+0/-0 | 09-12 18:46 | substantive (new review/oracle tests) |
| m1-dispatch-timeout-independent | codex/v03-m1-dispatch-timeout-independent@eb57778 | 1 (0 M, 1 ??) | test_dispatch_timeout_review_independent.py | untracked=77, tracked=+0/-0 | 09-12 23:15 | substantive (new review/oracle tests) |
| native-identity-cancel-probe | DETACHED@3508980 | 1 (0 M, 1 ??) | test_native_identity_process_probe.py | untracked=69, tracked=+0/-0 | 09-12 23:26 | substantive (new review/oracle tests) |
| native-identity-cancel-correction | codex/v03-native-identity-cancel-correction@c579eb9 | 1 (0 M, 1 ??) | test_native_identity_process_original_peer.py | untracked=69, tracked=+0/-0 | 09-13 00:29 | substantive (new review/oracle tests) |
| audit-workspace-independent | DETACHED@36b18f5 | 1 (0 M, 1 ??) | test_git_workspace_types_independent.py | untracked=60, tracked=+0/-0 | 09-12 21:54 | substantive (new review/oracle tests) |
| audit-gate-independent | DETACHED@643c96e | 1 (0 M, 1 ??) | test_audit_gate_peer.py | untracked=49, tracked=+0/-0 | 09-12 22:46 | substantive (new review/oracle tests) |
| delivery-root-review | DETACHED@f0142dc | 1 (0 M, 1 ??) | test_lane_delivery_root_review.py | untracked=44, tracked=+0/-0 | 09-12 15:59 | substantive (new review/oracle tests) |
| organize-composition-review | DETACHED@a6cde3b | 1 (0 M, 1 ??) | test_organize_scheduler_independent.py | untracked=39, tracked=+0/-0 | 09-12 17:09 | substantive (new review/oracle tests) |
| m1-lease-corrective-independent-review | DETACHED@57dcc8c | 1 (0 M, 1 ??) | test_m1_marker_prefix_independent.py | untracked=37, tracked=+0/-0 | 09-12 21:09 | substantive (new review/oracle tests) |
| delivery-native-review | DETACHED@e7ae430 | 1 (0 M, 1 ??) | test_delivery_native_independent.py | untracked=37, tracked=+0/-0 | 09-12 16:08 | substantive (new review/oracle tests) |
| organize-delivery-review | DETACHED@5f31342 | 1 (0 M, 1 ??) | test_organize_halt_independent_identity.py | untracked=35, tracked=+0/-0 | 09-12 17:22 | substantive (new review/oracle tests) |
| recovery-audit-fixture-review | DETACHED@556552c | 1 (0 M, 1 ??) | test_audit_fixture_base_independent.py | untracked=25, tracked=+0/-0 | 09-12 17:36 | substantive (new review/oracle tests) |
| recovery-semantic-local-source-before | DETACHED@e94f825 | 2 (2 M, 0 ??) | test_applied_native_amendments.py test_native_amendments.py | untracked=0, tracked=+110/-4 | 09-12 20:12 | substantive (tracked src/test edits + new files) |

Notes:
- `kodezart-v03-m5-delivery-termination` (branch `codex/v03-m5-delivery-termination` @ `10c51a7` = `origin/codex/v03-m3-plan-walk`): the **M5 delivery/union termination implementation, entirely uncommitted**. Modified (22): `src/kodezart/adapters/{github_api,github_types,linear_markers,linear_mcp_tracker,no_forge_delivery,subprocess_git_service}.py`, `composition/engine.py`, `core/{config,errors,protocols}.py`, `domain/errors.py`, `handlers/agent_handler.py`, `types/domain/{agent,branch,delivery,operation,run_state}.py`, `types/requests/agent.py`, `tests/{chains/test_ralph_workflow,domain/test_amendment,fakes,test_forge_origin_selection}.py` (+916/−51; src/kodezart +634/−33). Untracked (53): `src/kodezart/adapters/subprocess_check_chain.py`, `chains/{delivery_coordinator,lane_delivery,native_delivery}.py`, `composition/{delivery,scope_runtime}.py`, `domain/check_chain.py`, `services/{lane_reports,scope_runtime,union_composition,union_identity,union_tick}.py`, `types/domain/{check_chain,native_delivery,pr_state,scope_runtime,scope_terminal,union,union_tick}.py`, and 34 test files (`tests/chains/test_{delivery_coordinator,lane_delivery,native_delivery,union_*}.py`, `tests/services/test_union_*.py`, `tests/tracker/test_*landing*.py`, `tests/integration/test_scope_runtime.py` (765 lines), …). File mtimes 2026-09-13 02:28–02:34 — the run was mid-write when it died. Worktree reflog shows only creation at `10c51a7` (no commits made). `kodezart-recovery-session/m5-hunk-ownership.md` and `m5-union-correction.patch` (48 KB, 01:49) relate to it.
- `kodezart-v03-m3-plan-walk-census` (`codex/v03-m3-plan-walk-census` @ `7675c0b`, PUSHED): 8 untracked oracle tests (1,314 lines) + 1 modified test. The same 8 files are committed as `7979314` on unpushed `codex/v03-m3-oracle-closure` (9 files +1314/−1), so this dirty state is duplicated by a branch.
- `kodezart-v03-recovery-semantic-local-source-before` (detached @ `e94f825`): the only dirty worktree with tracked-file edits and no new files (`tests/services/test_{applied_,}native_amendments.py` +110/−4).

### 2.2 Detached worktrees whose HEAD is on no branch (16) — lost with the directory
These commits are reachable only from the worktree's `HEAD` file (not from any local/remote ref); they are the "independent review" bases the run created by cherry-picking onto canonical:

| worktree | HEAD | date | commits not on canonical | delta vs merge-base | subject |
|---|---|---|---|---|---|
| kodezart-v03-alarm-independent-review | `cd558ab02` | 2026-09-12 | 2 | 34 files, 1630(+), 527(-) | fix(alarms): validate the exact record snapshot before upsert |
| kodezart-v03-amendment-comment-independent-review | `e5ef84bbb` | 2026-09-12 | 2 | 8 files, 725(+), 24(-) | fix(tracker): refresh protected comment checks on unsent retries |
| kodezart-v03-audit-corrective-independent | `104141269` | 2026-09-12 | 3 | 44 files, 3474(+), 143(-) | Preserve operational versus programmer failure boundaries in Audit |
| kodezart-v03-criterion-label-independent-review | `008ff1821` | 2026-09-12 | 2 | 7 files, 338(+), 43(-) | Require known native classification before selecting write authority |
| kodezart-v03-delivery-root-review | `f0142dc90` | 2026-09-12 | 11 | 82 files, 4593(+), 990(-) | Revalidate native delivery evidence at resumed terminal boundaries |
| kodezart-v03-m2-authority-review | `ad9ba4e61` | 2026-09-13 | 52 | 294 files, 34565(+), 1589(-) | Exercise Organize retry authority through independent native controls |
| kodezart-v03-m3-oracle-review | `cbbe5dd1d` | 2026-09-13 | 49 | 421 files, 55538(+), 4716(-) | test(fire): preserve original extracted workflow oracles |
| kodezart-v03-m4-heartbeat-clock-fix | `06214eb45` | 2026-09-13 | 43 | 227 files, 21498(+), 1587(-) | test(lifecycle): advance heartbeat time only on explicit intervals |
| kodezart-v03-organize-composition-review | `a6cde3bfd` | 2026-09-12 | 7 | 71 files, 4264(+), 239(-) | Keep Organize halt identity scoped to its actual container |
| kodezart-v03-organize-delivery-review | `5f3134224` | 2026-09-12 | 8 | 72 files, 4538(+), 239(-) | fix(organize): require evidence appropriate to each halt cause |
| kodezart-v03-organize-graph-independent-review | `f47a1c64d` | 2026-09-12 | 3 | 41 files, 2662(+), 110(-) | Prepare admitted native issue graphs and stable split children through Organize |
| kodezart-v03-recovery-audit-fixture-review | `556552cb9` | 2026-09-12 | 3 | 6 files, 379(+), 79(-) | fix(audit): require strict indices and covered mandate sources |
| kodezart-v03-recovery-organize-ci | `3aaad2d0f` | 2026-09-12 | 1 | 9 files, 212(+), 42(-) | Describe Organize output fields and align integrated contract checks |
| kodezart-v03-recovery-organize-graph | `f47a1c64d` | 2026-09-12 | 3 | 41 files, 2662(+), 110(-) | Prepare admitted native issue graphs and stable split children through Organize |
| kodezart-v03-recovery-organize-halt-types | `64d64b315` | 2026-09-12 | 8 | 72 files, 4538(+), 239(-) | fix(organize): require evidence appropriate to each halt cause |
| kodezart-v03-scope-review-delivery | `9d379f485` | 2026-09-12 | 13 | 70 files, 3967(+), 788(-) | Keep API event census aligned with scoped native streams |

All other detached worktrees (35) sit on a commit that some local branch or origin ref also contains (Appendix C lists each with its anchoring ref).

### 2.3 The 14 kill-state worktrees — now

| dir (`/private/tmp/kodezart-v03-…`) | kill-state 09-12 13:20 | now |
|---|---|---|
| audit-evidence-review | 11 dirty, detached | **WIPED** 09-13 00:04: no `.git` file, no tracked files left (only `.claude/ .github/ docs/(empty) src/ tests/` skeleton + `.pytest_cache`). Corpse stub `.git/worktrees/kodezart-v03-audit-evidence-review` (index Sep 8 08:21 == HEAD `ff6e7c528` tree; nothing staged). Dirty content unrecoverable from disk. |
| escalation-review | 1 dirty | **WIPED** 00:02, same pattern; last HEAD `aafb0d225` (= branch `codex/v03-escalation-review`, orphan, CORRUPT-2). |
| forge-settings-and-live-tests | 1 dirty | **WIPED** 00:00; 5 residual files (`README.md` 58 KB mtime Sep 8 15:51, `docs/migration-v0.1-to-v0.2.md`, `docs/configuration.md`, `tests/spec/test_model_agreement.py`, `tests/adapters/test_github_api.py`) whose HEAD blobs are unreadable (branch `codex/v03-forge-settings-and-live-tests` @ `67d6ab276` is CORRUPT-25) — `README.md` is plausibly the kill-time dirty file but cannot be verified. |
| http-dependencies-before | 2 dirty | **WIPED** 00:04; 128 residual tracked files, all byte-identical to HEAD `2298e0d26` (branch `codex/v03-http-dependencies-before`, orphan, intact). No dirty content survives. |
| knowledge-mutants | 1 dirty | **WIPED** 00:00; 0 files left; last HEAD `45e3e9f7e` is on no branch (dangling; corpse reflog only). |
| knowledge-settings | 1 dirty | **WIPED** 00:03; 0 files left; last HEAD `535351e12` = `codex/v03-knowledge-settings` (orphan, CORRUPT-7). |
| operation-sections | **114 dirty**, `codex/kod-139-operation-sections` | **WIPED** 00:04 (stub touched 00:07); 0 tracked files left. Branch `codex/kod-139-operation-sections` @ `d7e7521e970bf4d9a4137c2a687152e98ff41a98` (2026-09-08 14:46, "Replace private source citations with their behavioral meaning") survives: 1 commit above canonical, **39 files, all under `src/kodezart/`, +256/−272**, objects intact, unpushed. Worktree reflog: created at `83bea4d5` (= `codex/v03-permissions-and-fixed-admission`, pushed) → cherry-pick → `d7e7521`. Stub `index` (Sep 8 15:10) is identical to `d7e7521`'s tree ⇒ **none of the 114-file working-tree change was staged; it is gone.** |
| queue-settings-before | 1 dirty | **WIPED** 00:01; 285 residual files all identical to HEAD `debfbf65b` (= `codex/v03-check-runner-settings`, CORRUPT-6). |
| recovery-criterion-class | 1 dirty | **ALIVE, clean.** Branch `codex/v03-recovery-criterion-class` @ `b714b98` — committed 2026-09-12 13:35:13 "test(criteria): prove uniform failure and refuse every retired class payload" (after the capture). Orphan/unpushed, 4 commits above canonical, intact. |
| recovery-job-acceptance | 6 dirty | **ALIVE, clean.** `codex/v03-recovery-job-acceptance` @ `85fc8a3` — committed 13:45:07 "fix(api): validate job acceptance and reverse reconnect routes". Orphan/unpushed, 1 commit above canonical, intact. |
| recovery-native-fire | 1 dirty | **ALIVE, clean.** `codex/v03-recovery-native-fire` @ `19e5d87` — commits 14:28:53 `10fa10d` "fix(fire): carry native specs through live execution and replay" and 14:52:58 `19e5d87` "fix(fire): bind resumed effects and fresh judgments to current checks". Orphan/unpushed, 7 commits above canonical, intact. |
| scanner-sdk-before | 1 dirty | **WIPED** 00:00; 254 residual files identical to HEAD `daeb1cb76` (on no branch). |
| workflow-ownership-before | 1 dirty | **WIPED** 00:02; 182 residual files identical to HEAD `f1ae6ac78` (= `codex/v03-session-failures-and-agent-settings`, PUSHED). |
| workflow-readiness-review | 2 dirty | **WIPED** 00:00; 89 residual files identical to HEAD `f1ae6ac78` (PUSHED). |

The six `*-before`/review stubs whose `index` mtime is exactly 2026-09-12 13:20 are the ones the kill-state capture itself touched.

## 3. What each unpushed / dirty item was for (inferred from names, subjects, diffs)

Milestone family (main lineage): M1 = extract scope ports/leases/session policy/native reads into typed boundaries; M2 = Organize (grooming rubric, description authority); M3 = plan-walk + CI observation oracles; M4 = tracker lifecycle, write-back verifier, classification; M5 = delivery/union termination (the uncommitted 75-file worktree). Each `*-root-review`, `*-independent`, `*-oracle`, `*-donor` branch/worktree is a review sandbox seeded on a milestone tip plus one cherry-picked commit; the untracked `test_*_independent/peer/original.py` files are the independent oracle tests the run wrote to grade those cherry-picks.

`codex/v03-recovery-*` family (canonical lineage, Sep 12): incremental fixes the run made while building canonical — audit runtime/preflight/report-contract, native resume/cancel/prepare, semantic amendments + write-back, Organize owner/graph/composition, lane delivery + CI observations, scope runtime, criterion class/label surface, comment provenance, alarm owner, parent rollup, surface owner, port contract, shared contracts. Some subjects say `WIP`/`wip` (`0f6e88c` "WIP preserve applied native amendment draft", `edbd411` "wip(native): assert existing comment before amendment").

Sep-8 `kod-*`/`v03-*` family: earlier ticket-by-ticket work (KOD-95…KOD-746 in subjects) with `-mutants`/`-mutations` twins (mutation-testing runs of the same ticket). Mostly superseded by the milestone branches; 92 of 130 are object-incomplete anyway.

## 4. WOULD BE LOST IF `/private/tmp` WERE CLEANED

Ordered by size. Everything below exists **only** under `/private/tmp` (heads absent from the primary object store; working-tree files not in any commit).

| # | what | size | where |
|---|---|---|---|
| 1 | **M5 delivery/union termination — uncommitted working tree** | 75 files: 22 modified (+916/−51), 53 new (~8,827 lines) | `/private/tmp/kodezart-v03-m5-delivery-termination` on `codex/v03-m5-delivery-termination`@`10c51a7` |
| 2 | **`kodezart-recovery-session/`** run notes: 27 `.patch` (incl. `extraction-watermark-d2c6fce.patch` 4.5 MB, `m1-lease-complete-90aea37.patch` 459 KB, `semantic-applied-full.patch` 244 KB, `native-amended-independent-4c6322.patch` 174 KB, `m1-scope-bindings-extraction.patch` 112 KB, `native-impl-final.patch` 111 KB, `m1-lease-adapter-90aea37.patch` 92 KB, `m2-ruling-prerequisite.patch` 77 KB, `m3-protocol-donor.patch` 57 KB, `m1-remaining-scope-mixed-deltas.patch` 53 KB, `m5-union-correction.patch` 48 KB, …), 149 `.md` (PR bodies for m1–m5 + canonical, checklists, independent-review verdicts), 172 `.json`, 199 `.py`, 1,445 `.log` | 2,039 files | `/private/tmp/kodezart-recovery-session/` (not a git dir) |
| 3 | M4 verifier extraction chain `40b250c5c` (3 commits over m1-scope-ports; 93 files +9851/−1043) incl. M1 lease extraction `90aea37`/`57dcc8c` | 3 commits | branches `codex/v03-m4-verifier-extraction`, `codex/v03-m1-lease-root-review`, `codex/v03-m1-lease-extraction` |
| 4 | M1 native-read / session-policy / delta-cancel chain `864bc0ab3` (8 commits; 85 files +2685/−325) | 8 commits | `codex/v03-m1-delta-cancel(-root-review)`, `codex/v03-m1-session-policy-extraction`, `codex/v03-m1-native-read-extraction` |
| 5 | native identity/resume/cancel chain `c579eb9da` (13 commits on canonical lineage; 68 files +6326/−300) and its ancestors `3508980` (12), `2618272` (11), `4dd7b3e` (9) | 13 commits | `codex/v03-native-identity-cancel-correction`, `codex/v03-native-identity-root-review`, `codex/v03-recovery-native-resume{,-cancel,-prepare}` |
| 6 | recovery scope runtime `f632aee96` (12 commits; 67 files +3923/−779) | 12 | `codex/v03-recovery-scope-runtime` |
| 7 | recovery semantic classification corrective `2727ee671` (12; 74 files +4653/−180), semantic-applied-current `db9d370` (9), semantic-applied `0f6e88c` (6), amendment-comment `edbd411` (6), native-amended-root-review `4c6322f` (7) | 12 | `codex/v03-recovery-semantic-*`, `codex/v03-recovery-amendment-comment*`, `codex/v03-recovery-native-amended-root-review` |
| 8 | recovery lane delivery `e7ae430ca` (11; 82 files +4593/−990) | 11 | `codex/v03-recovery-lane-delivery` |
| 9 | recovery audit preflight `38e848b2f` (10; 64 files +4715/−203), audit runtime `dd11ffd54` (8), audit types `fc4953e` (3), audit report contract `fd93cf2` (1) | 10 | `codex/v03-recovery-audit-*` |
| 10 | recovery Organize composition `68eabb515` (7), organize owner `ee1d44b` (5), graph correction `31bb289` (6) | 7 | `codex/v03-recovery-organize-*`, `codex/v03-recovery-graph-root-review` |
| 11 | recovery native fire `19e5d870d` (7; 66 files +2447/−774), surface owner `3df7469` (7), port contract `9b90086` (5), criterion class `b714b98` (4), criterion label surface `3135131` (3) | 7 | `codex/v03-recovery-native-fire`, `…-surface-owner`, `…-port-contract`, `…-criterion-*` |
| 12 | M3 oracle closure `797931418` (1; 9 files +1314) — duplicated as untracked files in m3-plan-walk-census | 1 | `codex/v03-m3-oracle-closure` |
| 13 | M2 groom rubric `980b17965`/`0363cc9fa` (2; 18 files +668/−38) | 2 | `codex/v03-m2-groom-rubric-{freshness,correction}` |
| 14 | 29 more single/few-commit Sep-12 recovery heads (table 1.2b) — e.g. artifact-read `6f431bb`/`0e30d78` (6 each, 9 files +456), comment provenance `03b99ac` (2), alarm owner `179ef5a` (2), parent rollup `7d6208a` (1), write-back settings `64aa3bd` (1), job acceptance `85fc8a3` (1), marker preflight `be9717f` (1), classification authority `a51d777` (1) | 1–6 each | see table 1.2b |
| 15 | `kod-139-operation-sections` `d7e7521` (1 commit; 39 src files +256/−272) — the branch survives; its 114-file dirty tree does not | 1 | `codex/kod-139-operation-sections` |
| 16 | 16 detached-only review-base commits (table 2.2; each 1–2 commits, e.g. `cd558ab` alarms 34 files +1630/−527, `1041412` audit failure boundaries, `f47a1c6` Organize graph, `3aaad2d` Organize CI, `9d379f4` scope runtime review) | 1–2 each | worktree `HEAD` files only |
| 17 | 55 dirty files (review/oracle tests, plus one 2-file tracked edit) across 37 worktrees (table 2.1; 25–526 lines each, ~5300 lines total excluding m5/m3) | ~5374 lines | each `/private/tmp/kodezart-v03-*` worktree |
| 18 | 37 intact Sep-8 heads (1–12 commits each; table 1.2c / Appendix B) | ≤12 | `codex/kod-*`, `codex/v03-*` |
| 19 | 92 object-incomplete Sep-8 heads — commits exist, some blobs/trees do not; only partially recoverable even today | — | Appendix B (`CORRUPT-N`) |
| 20 | 1 live stash (4 files +102/−54) + 4 dropped stash snapshots `d415676`/`f93ed89`/`f6da180`/`08c0005` (7–24 files, up to +361/−123) on `codex/v03-recovery-audit-runtime`; 30 Sep-8 dangling commits held only by corpse reflogs; `99d59d4` "wip(native): assert existing comment before amendment" (6 files +315/−20, Sep 12 19:06); `41812b2` (pre-amend of 980b179) | — | tmp `.git` only |

**Safe on origin (would NOT be lost):** the 60 PUSHED heads — canonical `codex/v03-recovery-integration`@`eae9a940`, `codex/v03-m1-scope-ports`@`ddb8cdef`, `codex/v03-m2-organize`@`cef0dff6`, `codex/v03-m3-plan-walk`@`dad63c4f`, `codex/v03-m4-tracker-lifecycle`@`c855fb06`, the m5-delivery-termination *base* `10c51a74`, `codex/v03-m5-union-roster-correction`@`1a83163e`, `codex/v03-native-resume-integration-review`@`da39c439`, `codex/v03-m3-plan-walk-census`@`7675c0b4`, `codex/v03-m2-{description-authority,organize-boundaries,integrated-root-review}`, `codex/v03-m4-{classification-extraction,classification-root-review,native-read-extension,native-read-independent,ruling-protocol-correction,shared-semantic-extraction}`, `codex/v03-m1-{git-settings-extraction,git-settings-root-review,scope-bindings-extraction}`, `codex/v03-recovery`@`55a87a50`, and the 30-odd older `codex/v03-*` lineage branches (audit-evidence-consumers, fire-*-consumers, focused-readers-*, permissions-and-fixed-admission, runtime-*, session-*, shared-policies-*, typed-runtime-*, union-*, workflow-phases-and-settings, …). Also the 35 detached worktrees whose HEAD is on one of those refs, and every wiped `*-before` dir whose residual files equal a pushed HEAD (`workflow-ownership-before`, `workflow-readiness-review`).

## 5. Primary repo `/Users/kodezart/Projects/kodezart`

- `main` = `92597c0f` (2026-07-27, "chore: bump claude-agent-sdk pin to ~=0.2.128, project version to 0.1.5 (#41)"), upstream `origin/main` = `4661a24b` (2026-09-07, "test: retire the knowledge lane's additivity guard… (#71)"); **main is 11 behind, 0 ahead** (merge-base = main). The 11 include `a1993e31 release: v0.2.0`, `fbbc615e feat(survivability)…(KOD-55) (#50)`, `7773772f feat(criteria)…(KOD-53) (#47)`.
- **12-file uncommitted diff** (+283/−38; every file mtime 2026-08-04 15:57): hardens the agent stream drain for observability after the "DUC-127/DUC-143 postmortem" — `core/stream_drain.py` (+71) logs every `ErrorEvent` and emits a `stream_drained` summary (event counts, result subtype/num_turns/duration/stop_reason/result tail), `core/constants.py` adds `RESULT_TAIL_CHARS = 2048`, `core/errors.py` adds forensic fields (`result_event_observed`, `subtype`, `num_turns`, `duration_ms`, `result_tail`) to `NoStructuredOutputError`, `core/error_egress.py` forwards them with `redact_credentials`, `handlers/agent_handler.py` refactors the stream-failure path into `_egress_error`, `types/domain/agent.py`/`core/logging.py`/`chains/*`/`adapters/git_change_persister.py` are plumbing, `tests/core/test_error_egress.py` (+68). It is **v0.1.x-lineage error-egress work, not v0.3** — and against `origin/main` those same 12 files differ by +675/−2886, so it would not apply cleanly to current main.
- Stashes: `stash@{0}` 2026-06-02 17:05 "On kodezart/fix-pipeline-obs-git-remote-is-ancestor-1c5f230d-ralph-27fc4305: dispatch-stash-pre-main-deploy-2026-06-02" (17 files +27/−228: deletes `core/soft_failure.py`, renames `test_soft_failure.py → test_error_egress.py`) — **overlaps 7 of the 12 dirty files** (`git_change_persister.py`, `ralph_loop.py`, `ralph_workflow.py`, `ticket_generation.py`, `agent_handler.py`, `types/domain/agent.py`, `tests/core/test_error_egress.py`): an earlier step of the same soft_failure→error_egress migration. `stash@{1}` 2026-06-01 19:48 "On main: dispatch-stash-pre-pr17-deploy-2026-06-01" (5 files +731: adds `core/soft_failure.py`, `tests/core/test_soft_failure.py`, `types/domain/consolidation.py`, `.kodezart/{criteria,ticket}.json`) — no file overlap with the dirty diff, but same lineage (the module stash@{0} later removes).
- `recovery-section-top/` (untracked, 248 KB, files dated 2026-08-17 19:21–19:24): `REBUILD-SPEC.md` opens "`section #top` — recovery specification / Figma file **Scrooge Landing** (`sHf4KcgYWIIZPSVhX4BO6s`), page PROPOSED REDESIGN … Recovered from Claude Code session transcripts … Compiled 2026-08-17", plus node-tree dumps, deletion records and `screenshots/`. **Unrelated to kodezart** (a Figma landing-page recovery artifact parked in the repo root).
- Worktrees of the primary: `.claude/worktrees/keen-hamilton` @`10e1df44` [`kodezart/post-merge-review-pr-ci-monitoring-6ec5f630`, upstream gone; dirty `uv.lock`], `.claude/worktrees/scrooge-grooming-pass-4dea72` @`1d1bab5a` (detached, clean), and `<scratchpad>/wt-canonical` @`eae9a940` (detached, clean — created by this recovery session). 66 local branches (57 `claude/*` scratch, 4 `kodezart/*` with `[gone]` upstreams, `pr-15/17/22` twins, `main`).

## 6. Filesystem / volatility of `/private/tmp`

- `stat /private/tmp/kodezart-v03-implementation`: inode 210199400, `drwxr-xr-x kodezart:wheel`, birth 2026-09-07 16:48:31, mtime 2026-09-12 00:02:57, atime 2026-09-12 00:04:48. `.git/` mtime 2026-09-13 02:36:32; `.git/objects` 02:33:21; `.git/packed-refs` born/modified 2026-09-13 00:29:51.
- `/tmp -> private/tmp`; `/private/tmp` is `drwxrwxrwt root:wheel` on the APFS Data volume `/dev/disk3s5` mounted at `/System/Volumes/Data` (228 GiB, 177 GiB used, **11 GiB free, 95 %**). Not tmpfs. System up 19 days (boot 2026-08-24 16:58); entries from Aug 24 17:00 still exist, so no reboot-wipe has occurred in the window, but `/System/Library/LaunchDaemons/com.apple.bsd.dirhelper.plist` + `/usr/libexec/dirhelper` are present (macOS's periodic `/private/tmp` sweep of items not accessed in ~3 days). 519 `kodezart-*` directories + 13 files remain under `/private/tmp` (532 entries; the earlier count of 704 has already shrunk). `sudo` not used.

## 7. Reference files (scratchpad)
`classify.txt` (295-row classification), `tmp_heads.txt`, `orphan_subjects.txt` (subjects for all 195 orphan heads, ≤15 each), `orphan_sizes.txt`, `orphan_missing.txt`, `sep8_heads.txt`, `wt_status.txt` (status of every dirty/detached worktree), `stub_reflog.txt`, `unreachable_full.txt` (52 fsck-unreachable commits), `enum.txt` (prior enumeration), all under `/private/tmp/claude-503/-Users-kodezart-Projects-kodezart/f325c30b-eb65-4b5e-83ca-1ef5846ade2d/scratchpad/`.

---
## Appendix A — classification of all 295 local heads (`class | branch | sha | commits not on canonical | commits not on origin/main | date | origin refs containing`)
```
ORPHAN-OBJECT | codex/audit-replace-integrity | 8faf6f4b6 | notOnCanon=4 | notOnMain=276 | date=2026-09-08 | on=
ORPHAN-OBJECT | codex/audit-replace-mutants | 8faf6f4b6 | notOnCanon=4 | notOnMain=276 | date=2026-09-08 | on=
ORPHAN-OBJECT | codex/dispatch-budget-fixture | 1f722c153 | notOnCanon=1 | notOnMain=203 | date=2026-09-08 | on=
ORPHAN-OBJECT | codex/kod-139-operation-sections | d7e7521e9 | notOnCanon=1 | notOnMain=374 | date=2026-09-08 | on=
ORPHAN-OBJECT | codex/kod-338-admission-ownership | 0d46227cb | notOnCanon=1 | notOnMain=180 | date=2026-09-08 | on=
ORPHAN-OBJECT | codex/kod-338-admission-ownership-mutants | 0d46227cb | notOnCanon=1 | notOnMain=180 | date=2026-09-08 | on=
ORPHAN-OBJECT | codex/kod-380-scope-approval | 4f48afe6d | notOnCanon=3 | notOnMain=218 | date=2026-09-08 | on=
ORPHAN-OBJECT | codex/kod-424-fire-entry-mutants | 2d389e5e1 | notOnCanon=1 | notOnMain=224 | date=2026-09-08 | on=
ORPHAN-OBJECT | codex/kod-424-fire-spec-approval | 2d389e5e1 | notOnCanon=1 | notOnMain=224 | date=2026-09-08 | on=
ORPHAN-OBJECT | codex/kod-450-addressed-preloop | d5ae87082 | notOnCanon=3 | notOnMain=258 | date=2026-09-08 | on=
ORPHAN-OBJECT | codex/kod-450-addressed-preloop-mutants | 0bc666dc9 | notOnCanon=3 | notOnMain=258 | date=2026-09-08 | on=
ORPHAN-OBJECT | codex/kod-470-public-docs | 19a03d96c | notOnCanon=1 | notOnMain=367 | date=2026-09-08 | on=
ORPHAN-OBJECT | codex/kod-470-source-prose | 92af9aa12 | notOnCanon=1 | notOnMain=367 | date=2026-09-08 | on=
ORPHAN-OBJECT | codex/kod-485-aggregate-retirement | d46b3292c | notOnCanon=11 | notOnMain=343 | date=2026-09-08 | on=
ORPHAN-OBJECT | codex/kod-485-outbound-admission | 4bb47e5c4 | notOnCanon=13 | notOnMain=345 | date=2026-09-08 | on=
ORPHAN-OBJECT | codex/kod-485-outbound-admission-mutations | cf063e32f | notOnCanon=13 | notOnMain=345 | date=2026-09-08 | on=
ORPHAN-OBJECT | codex/kod-485-retirement-mutations | d46b3292c | notOnCanon=11 | notOnMain=343 | date=2026-09-08 | on=
ORPHAN-OBJECT | codex/kod-486-aggregate-mutations | 49aed9271 | notOnCanon=8 | notOnMain=340 | date=2026-09-08 | on=
ORPHAN-OBJECT | codex/kod-486-authored-aggregate-admission | fd25528a0 | notOnCanon=8 | notOnMain=340 | date=2026-09-08 | on=
ORPHAN-OBJECT | codex/kod-491-reference-mutations | 64d60ef1c | notOnCanon=2 | notOnMain=326 | date=2026-09-08 | on=
ORPHAN-OBJECT | codex/kod-491-typed-private-references | 8167a2dc3 | notOnCanon=2 | notOnMain=326 | date=2026-09-08 | on=
ORPHAN-OBJECT | codex/kod-503-scope-tally | dd6c7faa6 | notOnCanon=3 | notOnMain=248 | date=2026-09-08 | on=
ORPHAN-OBJECT | codex/kod-503-scope-tally-mutants | 51bc8f9d0 | notOnCanon=3 | notOnMain=248 | date=2026-09-08 | on=
ORPHAN-OBJECT | codex/kod-510-assertion-drift | 23a375710 | notOnCanon=2 | notOnMain=162 | date=2026-09-08 | on=
ORPHAN-OBJECT | codex/kod-510-assertion-drift-mutants | 23a375710 | notOnCanon=2 | notOnMain=162 | date=2026-09-08 | on=
ORPHAN-OBJECT | codex/kod-510-recorded-assertion-drift | 9ed898951 | notOnCanon=2 | notOnMain=279 | date=2026-09-08 | on=
ORPHAN-OBJECT | codex/kod-510-recorded-assertion-mutants | 9ed898951 | notOnCanon=2 | notOnMain=279 | date=2026-09-08 | on=
ORPHAN-OBJECT | codex/kod-511-record-superseded | fcf8c094f | notOnCanon=3 | notOnMain=101 | date=2026-09-08 | on=
ORPHAN-OBJECT | codex/kod-513-record-collector | 289ad5104 | notOnCanon=7 | notOnMain=139 | date=2026-09-08 | on=
ORPHAN-OBJECT | codex/kod-514-detector-removal | 1dce04381 | notOnCanon=13 | notOnMain=192 | date=2026-09-08 | on=
ORPHAN-OBJECT | codex/kod-519-mandate-graph | ed7c99422 | notOnCanon=6 | notOnMain=104 | date=2026-09-08 | on=
ORPHAN-OBJECT | codex/kod-633-ruling-proposal | c01dcd81c | notOnCanon=1 | notOnMain=293 | date=2026-09-08 | on=
ORPHAN-OBJECT | codex/kod-633-ruling-proposal-mutations | c01dcd81c | notOnCanon=1 | notOnMain=293 | date=2026-09-08 | on=
ORPHAN-OBJECT | codex/kod-637-addressed-ruling-entry | 1fd931783 | notOnCanon=2 | notOnMain=305 | date=2026-09-08 | on=
ORPHAN-OBJECT | codex/kod-637-entry-mutations | 4040e2960 | notOnCanon=2 | notOnMain=305 | date=2026-09-08 | on=
ORPHAN-OBJECT | codex/kod-746-delivery-mutations | a6b0383fa | notOnCanon=3 | notOnMain=312 | date=2026-09-08 | on=
ORPHAN-OBJECT | codex/kod-746-delivery-retirement | d504adda7 | notOnCanon=4 | notOnMain=313 | date=2026-09-08 | on=
ORPHAN-OBJECT | codex/kod-94-ruling-records | 687723f9c | notOnCanon=5 | notOnMain=158 | date=2026-09-08 | on=
ORPHAN-OBJECT | codex/kod-ruling-record-mutants | a2efabfde | notOnCanon=5 | notOnMain=158 | date=2026-09-08 | on=
PUSHED | codex/landing-review-base | 162c34f24 | notOnCanon=0 | notOnMain=215 | date=2026-09-08 | on=origin/codex/v03-audit-evidence-consumers,origin/codex/v03-ci-locked-uv,origin/codex/v03-fire-audit-consumers,origin/cod
ORPHAN-OBJECT | codex/pass-budget-mutants | 1f722c153 | notOnCanon=1 | notOnMain=203 | date=2026-09-08 | on=
ORPHAN-OBJECT | codex/scope-approval-mutants | b1d410c28 | notOnCanon=3 | notOnMain=218 | date=2026-09-08 | on=
ORPHAN-OBJECT | codex/v03-agent-settings | de632003b | notOnCanon=2 | notOnMain=368 | date=2026-09-08 | on=
ORPHAN-OBJECT | codex/v03-artifact-read-corrective | da37bda26 | notOnCanon=1 | notOnMain=529 | date=2026-09-12 | on=
ORPHAN-OBJECT | codex/v03-artifact-read-final | 6f431bbaa | notOnCanon=6 | notOnMain=534 | date=2026-09-12 | on=
ORPHAN-OBJECT | codex/v03-artifact-read-independent | 0e30d784f | notOnCanon=6 | notOnMain=534 | date=2026-09-12 | on=
ORPHAN-OBJECT | codex/v03-audit-claim-session | 4cf792972 | notOnCanon=3 | notOnMain=156 | date=2026-09-08 | on=
ORPHAN-OBJECT | codex/v03-audit-combined-review | 2adc8c8d6 | notOnCanon=2 | notOnMain=285 | date=2026-09-08 | on=
ORPHAN-OBJECT | codex/v03-audit-coverage | 42c58b10f | notOnCanon=2 | notOnMain=139 | date=2026-09-08 | on=
ORPHAN-OBJECT | codex/v03-audit-detector-sweep | 096a6ccab | notOnCanon=3 | notOnMain=259 | date=2026-09-08 | on=
ORPHAN-OBJECT | codex/v03-audit-evidence | 5595af10f | notOnCanon=2 | notOnMain=181 | date=2026-09-08 | on=
PUSHED | codex/v03-audit-evidence-consumers | 4dce27069 | notOnCanon=0 | notOnMain=286 | date=2026-09-08 | on=origin/codex/v03-audit-evidence-consumers,origin/codex/v03-ci-locked-uv,origin/codex/v03-focused-readers-and-admission,o
ORPHAN-OBJECT | codex/v03-audit-forge-evidence | df442a261 | notOnCanon=6 | notOnMain=185 | date=2026-09-08 | on=
ORPHAN-OBJECT | codex/v03-audit-forge-sweep | 204e5a030 | notOnCanon=4 | notOnMain=260 | date=2026-09-08 | on=
ORPHAN-OBJECT | codex/v03-audit-forge-sweep-mutants | 204e5a030 | notOnCanon=4 | notOnMain=260 | date=2026-09-08 | on=
ORPHAN-OBJECT | codex/v03-audit-gate-corrective | 643c96ea6 | notOnCanon=1 | notOnMain=529 | date=2026-09-12 | on=
ORPHAN-OBJECT | codex/v03-audit-mandate-hunt | 5cee6db41 | notOnCanon=6 | notOnMain=166 | date=2026-09-08 | on=
ORPHAN-OBJECT | codex/v03-audit-overclaims | 0a7ac0365 | notOnCanon=7 | notOnMain=197 | date=2026-09-08 | on=
PUSHED | codex/v03-audit-record-pr-replay | 5ad022b7d | notOnCanon=0 | notOnMain=153 | date=2026-09-08 | on=origin/codex/v03-audit-evidence-consumers,origin/codex/v03-audit-record-pr-replay,origin/codex/v03-ci-locked-uv,origin/c
ORPHAN-OBJECT | codex/v03-audit-removal-sweep | e2866ebba | notOnCanon=4 | notOnMain=260 | date=2026-09-08 | on=
ORPHAN-OBJECT | codex/v03-audit-root-integration-review | 536d704aa | notOnCanon=3 | notOnMain=521 | date=2026-09-12 | on=
ORPHAN-OBJECT | codex/v03-audit-settings | f85fa8e75 | notOnCanon=2 | notOnMain=326 | date=2026-09-08 | on=
ORPHAN-OBJECT | codex/v03-audit-state-history | 5fec92b88 | notOnCanon=4 | notOnMain=141 | date=2026-09-08 | on=
ORPHAN-OBJECT | codex/v03-audit-sweep-assessment | c046b6243 | notOnCanon=5 | notOnMain=231 | date=2026-09-08 | on=
ORPHAN-OBJECT | codex/v03-audit-sweep-native | 1cfa58f1a | notOnCanon=1 | notOnMain=257 | date=2026-09-08 | on=
ORPHAN-OBJECT | codex/v03-audit-terminal-consistency | 5ccb7b97c | notOnCanon=7 | notOnMain=186 | date=2026-09-08 | on=
ORPHAN-OBJECT | codex/v03-audit-terminal-mandate | e6be6a68b | notOnCanon=7 | notOnMain=263 | date=2026-09-08 | on=
ORPHAN-OBJECT | codex/v03-audit-workspace-corrective | 36b18f5e5 | notOnCanon=4 | notOnMain=522 | date=2026-09-12 | on=
ORPHAN-OBJECT | codex/v03-audit-worktree-integrity | e2a49a868 | notOnCanon=1 | notOnMain=161 | date=2026-09-08 | on=
ORPHAN-OBJECT | codex/v03-barren-record-collector | c046b6243 | notOnCanon=5 | notOnMain=231 | date=2026-09-08 | on=
ORPHAN-OBJECT | codex/v03-cache-remote-name | 852623fc9 | notOnCanon=1 | notOnMain=198 | date=2026-09-08 | on=
ORPHAN-OBJECT | codex/v03-check-runner-cancellation | da1d474dd | notOnCanon=2 | notOnMain=199 | date=2026-09-08 | on=
ORPHAN-OBJECT | codex/v03-check-runner-settings | debfbf65b | notOnCanon=4 | notOnMain=336 | date=2026-09-08 | on=
PUSHED | codex/v03-ci-locked-uv | a29f50b97 | notOnCanon=0 | notOnMain=304 | date=2026-09-08 | on=origin/codex/v03-ci-locked-uv,origin/codex/v03-focused-readers-and-admission,origin/codex/v03-permissions-and-fixed-admi
ORPHAN-OBJECT | codex/v03-config-reduction | 7b58c746b | notOnCanon=3 | notOnMain=335 | date=2026-09-08 | on=
ORPHAN-OBJECT | codex/v03-configured-fire-mutations | 1ecc99ed0 | notOnCanon=5 | notOnMain=260 | date=2026-09-08 | on=
ORPHAN-OBJECT | codex/v03-configured-fire-preparation | 87eaf3813 | notOnCanon=6 | notOnMain=261 | date=2026-09-08 | on=
ORPHAN-OBJECT | codex/v03-criterion-fidelity-mutations | b1393733a | notOnCanon=2 | notOnMain=375 | date=2026-09-08 | on=
ORPHAN-OBJECT | codex/v03-criterion-lifecycle-identity | 8ef12cd90 | notOnCanon=2 | notOnMain=199 | date=2026-09-08 | on=
ORPHAN-OBJECT | codex/v03-criterion-read-boot | 1c20be4f2 | notOnCanon=1 | notOnMain=132 | date=2026-09-08 | on=
ORPHAN-OBJECT | codex/v03-criterion-record-fidelity | 9bbf67087 | notOnCanon=2 | notOnMain=375 | date=2026-09-08 | on=
ORPHAN-OBJECT | codex/v03-criterion-resolution | f094b5eb6 | notOnCanon=1 | notOnMain=269 | date=2026-09-08 | on=
ORPHAN-OBJECT | codex/v03-current-union | 39990202b | notOnCanon=1 | notOnMain=202 | date=2026-09-08 | on=
ORPHAN-OBJECT | codex/v03-current-union-mutants | 39990202b | notOnCanon=1 | notOnMain=202 | date=2026-09-08 | on=
ORPHAN-OBJECT | codex/v03-delivery-flake | e6988cf4b | notOnCanon=4 | notOnMain=136 | date=2026-09-08 | on=
ORPHAN-OBJECT | codex/v03-delivery-flake-mutants | 79c333e60 | notOnCanon=3 | notOnMain=135 | date=2026-09-08 | on=
ORPHAN-OBJECT | codex/v03-delivery-no-adapter | dc49867b5 | notOnCanon=1 | notOnMain=177 | date=2026-09-08 | on=
ORPHAN-OBJECT | codex/v03-delivery-read-ownership | 6fb6ad11a | notOnCanon=1 | notOnMain=198 | date=2026-09-08 | on=
ORPHAN-OBJECT | codex/v03-delivery-replay | bd38c4a26 | notOnCanon=13 | notOnMain=100 | date=2026-09-08 | on=
ORPHAN-OBJECT | codex/v03-delivery-replay-mutants | bd38c4a26 | notOnCanon=13 | notOnMain=100 | date=2026-09-08 | on=
ORPHAN-OBJECT | codex/v03-delivery-stalled | 38a8638fb | notOnCanon=4 | notOnMain=164 | date=2026-09-08 | on=
ORPHAN-OBJECT | codex/v03-delivery-stalled-mutants | 38a8638fb | notOnCanon=4 | notOnMain=164 | date=2026-09-08 | on=
ORPHAN-OBJECT | codex/v03-description-replay-mutants | 058dca9a1 | notOnCanon=1 | notOnMain=312 | date=2026-09-08 | on=
ORPHAN-OBJECT | codex/v03-dispatch-log-isolation | cf6fc4ed1 | notOnCanon=1 | notOnMain=375 | date=2026-09-08 | on=
ORPHAN-OBJECT | codex/v03-domain-permissions | 372fb93a8 | notOnCanon=3 | notOnMain=354 | date=2026-09-08 | on=
ORPHAN-OBJECT | codex/v03-empty-criteria-entry | 387078509 | notOnCanon=6 | notOnMain=138 | date=2026-09-08 | on=
ORPHAN-OBJECT | codex/v03-empty-criteria-mutations | 387078509 | notOnCanon=6 | notOnMain=138 | date=2026-09-08 | on=
ORPHAN-OBJECT | codex/v03-escalation-record-collector | d8796325b | notOnCanon=4 | notOnMain=230 | date=2026-09-08 | on=
ORPHAN-OBJECT | codex/v03-escalation-review | aafb0d225 | notOnCanon=3 | notOnMain=229 | date=2026-09-08 | on=
ORPHAN-OBJECT | codex/v03-event-credential-fixtures | bbed103b1 | notOnCanon=1 | notOnMain=302 | date=2026-09-08 | on=
ORPHAN-OBJECT | codex/v03-event-model-conformance | 4f96b46aa | notOnCanon=1 | notOnMain=297 | date=2026-09-08 | on=
ORPHAN-OBJECT | codex/v03-evidence-ancestry-integrity | a6db13095 | notOnCanon=8 | notOnMain=264 | date=2026-09-08 | on=
ORPHAN-OBJECT | codex/v03-exact-description-replay | 058dca9a1 | notOnCanon=1 | notOnMain=312 | date=2026-09-08 | on=
PUSHED | codex/v03-fire-audit-consumers | dd13f6286 | notOnCanon=0 | notOnMain=221 | date=2026-09-08 | on=origin/codex/v03-audit-evidence-consumers,origin/codex/v03-ci-locked-uv,origin/codex/v03-fire-audit-consumers,origin/cod
PUSHED | codex/v03-fire-criterion-consumers | 2d4ff6383 | notOnCanon=0 | notOnMain=277 | date=2026-09-08 | on=origin/codex/v03-audit-evidence-consumers,origin/codex/v03-ci-locked-uv,origin/codex/v03-fire-criterion-consumers,origin
ORPHAN-OBJECT | codex/v03-fire-extraction | eeb91ef7a | notOnCanon=6 | notOnMain=182 | date=2026-09-08 | on=
ORPHAN-OBJECT | codex/v03-fire-spec-consumer-mutations | f50e35033 | notOnCanon=4 | notOnMain=136 | date=2026-09-08 | on=
ORPHAN-OBJECT | codex/v03-fire-spec-consumers | f50e35033 | notOnCanon=4 | notOnMain=136 | date=2026-09-08 | on=
ORPHAN-OBJECT | codex/v03-fire-writer-inventory | a3a3089f5 | notOnCanon=1 | notOnMain=216 | date=2026-09-08 | on=
PUSHED | codex/v03-focused-readers-and-admission | 50c46d618 | notOnCanon=0 | notOnMain=360 | date=2026-09-08 | on=origin/codex/v03-focused-readers-and-admission,origin/codex/v03-permissions-and-fixed-admission,origin/codex/v03-recover
PUSHED | codex/v03-focused-readers-correction | 6fe9dfe7b | notOnCanon=0 | notOnMain=362 | date=2026-09-08 | on=origin/codex/v03-focused-readers-and-admission,origin/codex/v03-permissions-and-fixed-admission,origin/codex/v03-recover
ORPHAN-OBJECT | codex/v03-forge-settings-and-live-tests | 67d6ab276 | notOnCanon=3 | notOnMain=388 | date=2026-09-08 | on=
ORPHAN-OBJECT | codex/v03-fresh-judgment-mechanics | b62f57a6f | notOnCanon=2 | notOnMain=334 | date=2026-09-08 | on=
ORPHAN-OBJECT | codex/v03-fresh-judgment-mutations | b62f57a6f | notOnCanon=2 | notOnMain=334 | date=2026-09-08 | on=
ORPHAN-OBJECT | codex/v03-git-settings | e6d467cd6 | notOnCanon=8 | notOnMain=359 | date=2026-09-08 | on=
ORPHAN-OBJECT | codex/v03-github-settings | 20ac47f58 | notOnCanon=2 | notOnMain=385 | date=2026-09-08 | on=
ORPHAN-OBJECT | codex/v03-http-dependencies | ecd0d144b | notOnCanon=2 | notOnMain=326 | date=2026-09-08 | on=
ORPHAN-OBJECT | codex/v03-http-dependencies-before | 2298e0d26 | notOnCanon=1 | notOnMain=325 | date=2026-09-08 | on=
ORPHAN-OBJECT | codex/v03-http-mcp-lifecycle | 2bedc1a3c | notOnCanon=1 | notOnMain=202 | date=2026-09-08 | on=
ORPHAN-OBJECT | codex/v03-http-settings | f3ca519c4 | notOnCanon=6 | notOnMain=338 | date=2026-09-08 | on=
PUSHED | codex/v03-immediate-scope-refusal | 013844933 | notOnCanon=0 | notOnMain=310 | date=2026-09-08 | on=origin/codex/v03-focused-readers-and-admission,origin/codex/v03-permissions-and-fixed-admission,origin/codex/v03-recover
PUSHED | codex/v03-job-acceptance-contract | b89db2ac0 | notOnCanon=0 | notOnMain=385 | date=2026-09-08 | on=origin/codex/v03-recovery-integration,origin/codex/v03-session-failures-and-agent-settings,origin/kodezart/v03-integrati
ORPHAN-OBJECT | codex/v03-job-queue-settings | 3def3ece1 | notOnCanon=5 | notOnMain=337 | date=2026-09-08 | on=
ORPHAN-OBJECT | codex/v03-judgment-schema-census | e5bb791f5 | notOnCanon=1 | notOnMain=343 | date=2026-09-08 | on=
ORPHAN-OBJECT | codex/v03-knowledge-settings | 535351e12 | notOnCanon=3 | notOnMain=314 | date=2026-09-08 | on=
ORPHAN-OBJECT | codex/v03-lane-record-mutations | 975532d89 | notOnCanon=5 | notOnMain=137 | date=2026-09-08 | on=
ORPHAN-OBJECT | codex/v03-lane-record-reader | c3ef39d0b | notOnCanon=6 | notOnMain=138 | date=2026-09-08 | on=
ORPHAN-OBJECT | codex/v03-live-harness | ce193c878 | notOnCanon=2 | notOnMain=385 | date=2026-09-08 | on=
ORPHAN-OBJECT | codex/v03-logging-settings | aedb2f20b | notOnCanon=7 | notOnMain=339 | date=2026-09-08 | on=
ORPHAN-OBJECT | codex/v03-logging-test-isolation | 51e45d2fd | notOnCanon=1 | notOnMain=361 | date=2026-09-08 | on=
ORPHAN-OBJECT | codex/v03-m1-delta-cancel | 864bc0ab3 | notOnCanon=15 | notOnMain=15 | date=2026-09-12 | on=
ORPHAN-OBJECT | codex/v03-m1-delta-cancel-root-review | 864bc0ab3 | notOnCanon=15 | notOnMain=15 | date=2026-09-12 | on=
ORPHAN-OBJECT | codex/v03-m1-dispatch-timeout-independent | eb57778ed | notOnCanon=16 | notOnMain=16 | date=2026-09-12 | on=
ORPHAN-OBJECT | codex/v03-m1-dispatch-timeout-oracle | eb57778ed | notOnCanon=16 | notOnMain=16 | date=2026-09-12 | on=
PUSHED | codex/v03-m1-git-settings-extraction | 2bc237577 | notOnCanon=19 | notOnMain=19 | date=2026-09-13 | on=origin/codex/v03-m1-scope-ports,origin/codex/v03-m2-organize,origin/codex/v03-m3-plan-walk,origin/codex/v03-m4-tracker-l
PUSHED | codex/v03-m1-git-settings-root-review | 2bc237577 | notOnCanon=19 | notOnMain=19 | date=2026-09-13 | on=origin/codex/v03-m1-scope-ports,origin/codex/v03-m2-organize,origin/codex/v03-m3-plan-walk,origin/codex/v03-m4-tracker-l
ORPHAN-OBJECT | codex/v03-m1-lease-extraction | 90aea37cc | notOnCanon=4 | notOnMain=4 | date=2026-09-12 | on=
ORPHAN-OBJECT | codex/v03-m1-lease-root-review | 57dcc8c3a | notOnCanon=7 | notOnMain=7 | date=2026-09-12 | on=
ORPHAN-OBJECT | codex/v03-m1-logging-root-review | 5e4ec11e1 | notOnCanon=16 | notOnMain=16 | date=2026-09-12 | on=
ORPHAN-OBJECT | codex/v03-m1-native-read-extraction | fec7f2829 | notOnCanon=11 | notOnMain=11 | date=2026-09-12 | on=
PUSHED | codex/v03-m1-scope-bindings-extraction | ddb8cdef3 | notOnCanon=20 | notOnMain=20 | date=2026-09-13 | on=origin/codex/v03-m1-scope-ports,origin/codex/v03-m2-organize,origin/codex/v03-m3-plan-walk,origin/codex/v03-m4-tracker-l
PUSHED | codex/v03-m1-scope-ports | ddb8cdef3 | notOnCanon=20 | notOnMain=20 | date=2026-09-13 | on=origin/codex/v03-m1-scope-ports,origin/codex/v03-m2-organize,origin/codex/v03-m3-plan-walk,origin/codex/v03-m4-tracker-l
ORPHAN-OBJECT | codex/v03-m1-session-policy-extraction | ea1d91596 | notOnCanon=14 | notOnMain=14 | date=2026-09-12 | on=
PUSHED | codex/v03-m2-description-authority | 38956bb04 | notOnCanon=51 | notOnMain=51 | date=2026-09-13 | on=origin/codex/v03-m2-organize,origin/codex/v03-m3-plan-walk
ORPHAN-OBJECT | codex/v03-m2-groom-rubric-correction | 0363cc9fa | notOnCanon=53 | notOnMain=53 | date=2026-09-13 | on=
ORPHAN-OBJECT | codex/v03-m2-groom-rubric-freshness | 980b17965 | notOnCanon=54 | notOnMain=54 | date=2026-09-13 | on=
PUSHED | codex/v03-m2-integrated-root-review | 9e387b4a8 | notOnCanon=50 | notOnMain=50 | date=2026-09-13 | on=origin/codex/v03-m2-organize,origin/codex/v03-m3-plan-walk
PUSHED | codex/v03-m2-organize | cef0dff60 | notOnCanon=54 | notOnMain=54 | date=2026-09-13 | on=origin/codex/v03-m2-organize,origin/codex/v03-m3-plan-walk
PUSHED | codex/v03-m2-organize-boundaries | fadf6efe2 | notOnCanon=46 | notOnMain=46 | date=2026-09-13 | on=origin/codex/v03-m2-organize,origin/codex/v03-m3-plan-walk
ORPHAN-OBJECT | codex/v03-m3-oracle-closure | 797931418 | notOnCanon=59 | notOnMain=59 | date=2026-09-13 | on=
PUSHED | codex/v03-m3-plan-walk | dad63c4f4 | notOnCanon=47 | notOnMain=47 | date=2026-09-13 | on=origin/codex/v03-m3-plan-walk
PUSHED | codex/v03-m3-plan-walk-census | 7675c0b46 | notOnCanon=48 | notOnMain=48 | date=2026-09-13 | on=origin/codex/v03-m3-plan-walk
PUSHED | codex/v03-m4-classification-extraction | 25c32f603 | notOnCanon=30 | notOnMain=30 | date=2026-09-13 | on=origin/codex/v03-m2-organize,origin/codex/v03-m3-plan-walk,origin/codex/v03-m4-tracker-lifecycle
PUSHED | codex/v03-m4-classification-root-review | 25c32f603 | notOnCanon=30 | notOnMain=30 | date=2026-09-13 | on=origin/codex/v03-m2-organize,origin/codex/v03-m3-plan-walk,origin/codex/v03-m4-tracker-lifecycle
ORPHAN-OBJECT | codex/v03-m4-classification-shared-prerequisite | ac541892f | notOnCanon=28 | notOnMain=28 | date=2026-09-12 | on=
PUSHED | codex/v03-m4-native-read-extension | 267262342 | notOnCanon=27 | notOnMain=27 | date=2026-09-12 | on=origin/codex/v03-m2-organize,origin/codex/v03-m3-plan-walk,origin/codex/v03-m4-tracker-lifecycle
PUSHED | codex/v03-m4-native-read-independent | 8930fcb23 | notOnCanon=18 | notOnMain=18 | date=2026-09-12 | on=origin/codex/v03-m2-organize,origin/codex/v03-m3-plan-walk,origin/codex/v03-m4-tracker-lifecycle
PUSHED | codex/v03-m4-ruling-protocol-correction | 47b72c773 | notOnCanon=26 | notOnMain=26 | date=2026-09-13 | on=origin/codex/v03-m2-organize,origin/codex/v03-m3-plan-walk,origin/codex/v03-m4-tracker-lifecycle
PUSHED | codex/v03-m4-shared-semantic-extraction | 783edbfa4 | notOnCanon=25 | notOnMain=25 | date=2026-09-13 | on=origin/codex/v03-m2-organize,origin/codex/v03-m3-plan-walk,origin/codex/v03-m4-tracker-lifecycle
PUSHED | codex/v03-m4-tracker-lifecycle | c855fb061 | notOnCanon=42 | notOnMain=42 | date=2026-09-13 | on=origin/codex/v03-m2-organize,origin/codex/v03-m3-plan-walk,origin/codex/v03-m4-tracker-lifecycle
ORPHAN-OBJECT | codex/v03-m4-verifier-extraction | 40b250c5c | notOnCanon=8 | notOnMain=8 | date=2026-09-12 | on=
ORPHAN-OBJECT | codex/v03-m4-write-back-settings-extraction | 1020f0de6 | notOnCanon=31 | notOnMain=31 | date=2026-09-13 | on=
PUSHED | codex/v03-m5-delivery-termination | 10c51a744 | notOnCanon=60 | notOnMain=60 | date=2026-09-13 | on=origin/codex/v03-m3-plan-walk
PUSHED | codex/v03-m5-union-roster-correction | 1a83163ec | notOnCanon=0 | notOnMain=543 | date=2026-09-13 | on=origin/codex/v03-recovery-integration
PUSHED | codex/v03-mandate-evidence-terminal | 00239d2fc | notOnCanon=0 | notOnMain=203 | date=2026-09-08 | on=origin/codex/v03-audit-evidence-consumers,origin/codex/v03-ci-locked-uv,origin/codex/v03-fire-audit-consumers,origin/cod
ORPHAN-OBJECT | codex/v03-marker-lookups | c14123dc2 | notOnCanon=3 | notOnMain=345 | date=2026-09-08 | on=
ORPHAN-OBJECT | codex/v03-marker-mutations | c14123dc2 | notOnCanon=3 | notOnMain=345 | date=2026-09-08 | on=
ORPHAN-OBJECT | codex/v03-mcp-reopen-diagnosis | 5e4ec11e1 | notOnCanon=16 | notOnMain=16 | date=2026-09-12 | on=
ORPHAN-OBJECT | codex/v03-milestone-metadata | 335f9bd28 | notOnCanon=2 | notOnMain=265 | date=2026-09-08 | on=
ORPHAN-OBJECT | codex/v03-milestone-metadata-mutants | 335f9bd28 | notOnCanon=2 | notOnMain=265 | date=2026-09-08 | on=
ORPHAN-OBJECT | codex/v03-model-agreement | 8d4714f47 | notOnCanon=4 | notOnMain=304 | date=2026-09-08 | on=
PUSHED | codex/v03-native-audit-fire-preparation | 5f04abf43 | notOnCanon=0 | notOnMain=268 | date=2026-09-08 | on=origin/codex/v03-audit-evidence-consumers,origin/codex/v03-ci-locked-uv,origin/codex/v03-fire-criterion-consumers,origin
ORPHAN-OBJECT | codex/v03-native-gate-corrective | 730051660 | notOnCanon=1 | notOnMain=524 | date=2026-09-12 | on=
ORPHAN-OBJECT | codex/v03-native-identity-cancel-correction | c579eb9da | notOnCanon=13 | notOnMain=528 | date=2026-09-13 | on=
ORPHAN-OBJECT | codex/v03-native-identity-root-review | c579eb9da | notOnCanon=13 | notOnMain=528 | date=2026-09-13 | on=
ORPHAN-OBJECT | codex/v03-native-initiative-mutations | 65e2ed7a2 | notOnCanon=2 | notOnMain=362 | date=2026-09-08 | on=
ORPHAN-OBJECT | codex/v03-native-read-address-corrective | b84d43ba4 | notOnCanon=4 | notOnMain=531 | date=2026-09-12 | on=
PUSHED | codex/v03-native-resume-integration-review | da39c4398 | notOnCanon=0 | notOnMain=541 | date=2026-09-13 | on=origin/codex/v03-recovery-integration
PUSHED | codex/v03-native-ruling-consumers | ce1bd9a3c | notOnCanon=0 | notOnMain=292 | date=2026-09-08 | on=origin/codex/v03-ci-locked-uv,origin/codex/v03-focused-readers-and-admission,origin/codex/v03-native-ruling-consumers,or
PUSHED | codex/v03-native-ruling-proposal | 98e780d22 | notOnCanon=0 | notOnMain=309 | date=2026-09-08 | on=origin/codex/v03-focused-readers-and-admission,origin/codex/v03-permissions-and-fixed-admission,origin/codex/v03-recover
PUSHED | codex/v03-native-session-writeback | 89f7059af | notOnCanon=0 | notOnMain=303 | date=2026-09-08 | on=origin/codex/v03-ci-locked-uv,origin/codex/v03-focused-readers-and-admission,origin/codex/v03-native-session-writeback,o
PUSHED | codex/v03-native-supervisor-collectors | d7dab61ac | notOnCanon=0 | notOnMain=258 | date=2026-09-08 | on=origin/codex/v03-audit-evidence-consumers,origin/codex/v03-ci-locked-uv,origin/codex/v03-fire-criterion-consumers,origin
ORPHAN-OBJECT | codex/v03-nested-settings-docs | 67455934b | notOnCanon=2 | notOnMain=334 | date=2026-09-08 | on=
ORPHAN-OBJECT | codex/v03-node-event-review | 196e8b992 | notOnCanon=1 | notOnMain=285 | date=2026-09-08 | on=
ORPHAN-OBJECT | codex/v03-node-session-events | b559dfb3d | notOnCanon=3 | notOnMain=287 | date=2026-09-08 | on=
ORPHAN-OBJECT | codex/v03-node-session-mutants | b559dfb3d | notOnCanon=3 | notOnMain=287 | date=2026-09-08 | on=
ORPHAN-OBJECT | codex/v03-operation-concerns | ae3a8b814 | notOnCanon=2 | notOnMain=362 | date=2026-09-08 | on=
PUSHED | codex/v03-organize-gap-lane-reports | 38ea118a1 | notOnCanon=0 | notOnMain=117 | date=2026-09-08 | on=origin/codex/v03-audit-evidence-consumers,origin/codex/v03-audit-record-pr-replay,origin/codex/v03-ci-locked-uv,origin/c
ORPHAN-OBJECT | codex/v03-owned-resource-mechanics | 47d39b3d9 | notOnCanon=1 | notOnMain=333 | date=2026-09-08 | on=
ORPHAN-OBJECT | codex/v03-owned-resource-mutations | 47d39b3d9 | notOnCanon=1 | notOnMain=333 | date=2026-09-08 | on=
PUSHED | codex/v03-pagination-and-markers | 7042032c2 | notOnCanon=0 | notOnMain=342 | date=2026-09-08 | on=origin/codex/v03-focused-readers-and-admission,origin/codex/v03-permissions-and-fixed-admission,origin/codex/v03-recover
ORPHAN-OBJECT | codex/v03-pagination-implementation | 294b2e0b7 | notOnCanon=2 | notOnMain=344 | date=2026-09-08 | on=
ORPHAN-OBJECT | codex/v03-pagination-mutations | 93f9e89af | notOnCanon=2 | notOnMain=344 | date=2026-09-08 | on=
PUSHED | codex/v03-permissions-and-fixed-admission | 83bea4d56 | notOnCanon=0 | notOnMain=373 | date=2026-09-08 | on=origin/codex/v03-permissions-and-fixed-admission,origin/codex/v03-recovery-integration,origin/codex/v03-session-failures
PUSHED | codex/v03-phase-ownership-guards | 7d856f24e | notOnCanon=0 | notOnMain=352 | date=2026-09-08 | on=origin/codex/v03-focused-readers-and-admission,origin/codex/v03-permissions-and-fixed-admission,origin/codex/v03-recover
ORPHAN-OBJECT | codex/v03-pr-content-mutants | 51d83d55b | notOnCanon=1 | notOnMain=133 | date=2026-09-08 | on=
ORPHAN-OBJECT | codex/v03-pr-content-replay | 51d83d55b | notOnCanon=1 | notOnMain=133 | date=2026-09-08 | on=
ORPHAN-OBJECT | codex/v03-pr-footer | bb80d0715 | notOnCanon=14 | notOnMain=101 | date=2026-09-08 | on=
ORPHAN-OBJECT | codex/v03-pr-footer-mutants | bb80d0715 | notOnCanon=14 | notOnMain=101 | date=2026-09-08 | on=
PUSHED | codex/v03-queue-origin-fixture | 4b3ed1c0e | notOnCanon=0 | notOnMain=361 | date=2026-09-08 | on=origin/codex/v03-focused-readers-and-admission,origin/codex/v03-permissions-and-fixed-admission,origin/codex/v03-recover
PUSHED | codex/v03-ready-spec-escalations | 6e961a78a | notOnCanon=0 | notOnMain=255 | date=2026-09-08 | on=origin/codex/v03-audit-evidence-consumers,origin/codex/v03-ci-locked-uv,origin/codex/v03-fire-criterion-consumers,origin
ORPHAN-OBJECT | codex/v03-recorded-landing | affc8ff7a | notOnCanon=13 | notOnMain=228 | date=2026-09-08 | on=
PUSHED | codex/v03-recovery | 55a87a500 | notOnCanon=0 | notOnMain=445 | date=2026-09-09 | on=origin/codex/v03-recovery-integration,origin/kodezart/v03-r2-01,origin/kodezart/v03-trunk
ORPHAN-OBJECT | codex/v03-recovery-alarm-owner | 179ef5acf | notOnCanon=2 | notOnMain=486 | date=2026-09-12 | on=
ORPHAN-OBJECT | codex/v03-recovery-alarm-payload-fixture | 417ceecab | notOnCanon=1 | notOnMain=497 | date=2026-09-12 | on=
ORPHAN-OBJECT | codex/v03-recovery-amendment-comment | edbd4112a | notOnCanon=6 | notOnMain=495 | date=2026-09-12 | on=
ORPHAN-OBJECT | codex/v03-recovery-amendment-comment-current | 9136d9f31 | notOnCanon=1 | notOnMain=504 | date=2026-09-12 | on=
ORPHAN-OBJECT | codex/v03-recovery-amendment-comment-retry | 2c7bd233d | notOnCanon=2 | notOnMain=505 | date=2026-09-12 | on=
ORPHAN-OBJECT | codex/v03-recovery-audit-preflight | 38e848b2f | notOnCanon=10 | notOnMain=513 | date=2026-09-12 | on=
ORPHAN-OBJECT | codex/v03-recovery-audit-report-contract | fd93cf2a1 | notOnCanon=1 | notOnMain=500 | date=2026-09-12 | on=
ORPHAN-OBJECT | codex/v03-recovery-audit-runtime | dd11ffd54 | notOnCanon=8 | notOnMain=511 | date=2026-09-12 | on=
ORPHAN-OBJECT | codex/v03-recovery-audit-types | fc4953e1d | notOnCanon=3 | notOnMain=492 | date=2026-09-12 | on=
ORPHAN-OBJECT | codex/v03-recovery-classification-authority | a51d7779f | notOnCanon=1 | notOnMain=512 | date=2026-09-12 | on=
ORPHAN-OBJECT | codex/v03-recovery-classification-root-review | a51d7779f | notOnCanon=1 | notOnMain=512 | date=2026-09-12 | on=
ORPHAN-OBJECT | codex/v03-recovery-comment-independent | 9136d9f31 | notOnCanon=1 | notOnMain=504 | date=2026-09-12 | on=
ORPHAN-OBJECT | codex/v03-recovery-comment-provenance | 03b99acb7 | notOnCanon=2 | notOnMain=498 | date=2026-09-12 | on=
ORPHAN-OBJECT | codex/v03-recovery-criterion-class | b714b9841 | notOnCanon=4 | notOnMain=449 | date=2026-09-12 | on=
ORPHAN-OBJECT | codex/v03-recovery-criterion-label-surface | 3135131ab | notOnCanon=3 | notOnMain=518 | date=2026-09-12 | on=
ORPHAN-OBJECT | codex/v03-recovery-gate-followup | 01a62d6aa | notOnCanon=1 | notOnMain=472 | date=2026-09-12 | on=
ORPHAN-OBJECT | codex/v03-recovery-gate-reconciliation | 1088e18fb | notOnCanon=1 | notOnMain=470 | date=2026-09-12 | on=
ORPHAN-OBJECT | codex/v03-recovery-graph-root-review | 31bb28982 | notOnCanon=6 | notOnMain=509 | date=2026-09-12 | on=
PUSHED | codex/v03-recovery-integration | eae9a940e | notOnCanon=0 | notOnMain=547 | date=2026-09-13 | on=origin/codex/v03-recovery-integration
ORPHAN-OBJECT | codex/v03-recovery-job-acceptance | 85fc8a3d2 | notOnCanon=1 | notOnMain=446 | date=2026-09-12 | on=
ORPHAN-OBJECT | codex/v03-recovery-lane-delivery | e7ae430ca | notOnCanon=11 | notOnMain=462 | date=2026-09-12 | on=
ORPHAN-OBJECT | codex/v03-recovery-marker-preflight | be9717f47 | notOnCanon=1 | notOnMain=512 | date=2026-09-12 | on=
ORPHAN-OBJECT | codex/v03-recovery-native-amended-root-review | 4c6322fb1 | notOnCanon=7 | notOnMain=522 | date=2026-09-12 | on=
ORPHAN-OBJECT | codex/v03-recovery-native-compat | c47a7f112 | notOnCanon=1 | notOnMain=469 | date=2026-09-12 | on=
ORPHAN-OBJECT | codex/v03-recovery-native-fire | 19e5d870d | notOnCanon=7 | notOnMain=452 | date=2026-09-12 | on=
ORPHAN-OBJECT | codex/v03-recovery-native-prompt-census | 49b4bffa6 | notOnCanon=1 | notOnMain=504 | date=2026-09-12 | on=
ORPHAN-OBJECT | codex/v03-recovery-native-resume | 4dd7b3eb4 | notOnCanon=9 | notOnMain=524 | date=2026-09-12 | on=
ORPHAN-OBJECT | codex/v03-recovery-native-resume-cancel | 26182720e | notOnCanon=11 | notOnMain=526 | date=2026-09-12 | on=
ORPHAN-OBJECT | codex/v03-recovery-native-resume-prepare | 350898010 | notOnCanon=12 | notOnMain=527 | date=2026-09-12 | on=
ORPHAN-OBJECT | codex/v03-recovery-native-state-resolver | 53b3ca89e | notOnCanon=2 | notOnMain=494 | date=2026-09-12 | on=
ORPHAN-OBJECT | codex/v03-recovery-organize-composition | 68eabb515 | notOnCanon=7 | notOnMain=474 | date=2026-09-12 | on=
ORPHAN-OBJECT | codex/v03-recovery-organize-graph-correction | 31bb28982 | notOnCanon=6 | notOnMain=509 | date=2026-09-12 | on=
ORPHAN-OBJECT | codex/v03-recovery-organize-owner | ee1d44bd5 | notOnCanon=5 | notOnMain=472 | date=2026-09-12 | on=
ORPHAN-OBJECT | codex/v03-recovery-parent-rollup | 7d6208af8 | notOnCanon=1 | notOnMain=468 | date=2026-09-12 | on=
ORPHAN-OBJECT | codex/v03-recovery-port-contract | 9b9008676 | notOnCanon=5 | notOnMain=452 | date=2026-09-12 | on=
ORPHAN-OBJECT | codex/v03-recovery-port-failures | 2e6baba4a | notOnCanon=1 | notOnMain=446 | date=2026-09-12 | on=
PUSHED | codex/v03-recovery-rulings-writeback | 375314bd5 | notOnCanon=0 | notOnMain=182 | date=2026-09-08 | on=origin/codex/v03-audit-evidence-consumers,origin/codex/v03-ci-locked-uv,origin/codex/v03-fire-audit-consumers,origin/cod
ORPHAN-OBJECT | codex/v03-recovery-scope-ancestor | dd6147c6c | notOnCanon=2 | notOnMain=514 | date=2026-09-12 | on=
ORPHAN-OBJECT | codex/v03-recovery-scope-ancestor-root-review | dd6147c6c | notOnCanon=2 | notOnMain=514 | date=2026-09-12 | on=
ORPHAN-OBJECT | codex/v03-recovery-scope-runtime | f632aee96 | notOnCanon=12 | notOnMain=472 | date=2026-09-12 | on=
ORPHAN-OBJECT | codex/v03-recovery-semantic-amended | 2e7fdd9b5 | notOnCanon=3 | notOnMain=492 | date=2026-09-12 | on=
ORPHAN-OBJECT | codex/v03-recovery-semantic-amendment | d93f3e973 | notOnCanon=2 | notOnMain=491 | date=2026-09-12 | on=
ORPHAN-OBJECT | codex/v03-recovery-semantic-applied | 0f6e88c0f | notOnCanon=6 | notOnMain=495 | date=2026-09-12 | on=
ORPHAN-OBJECT | codex/v03-recovery-semantic-applied-current | db9d3702d | notOnCanon=9 | notOnMain=512 | date=2026-09-12 | on=
ORPHAN-OBJECT | codex/v03-recovery-semantic-classification-corrective | 2727ee671 | notOnCanon=12 | notOnMain=515 | date=2026-09-12 | on=
ORPHAN-OBJECT | codex/v03-recovery-semantic-writeback | 1c397368e | notOnCanon=4 | notOnMain=493 | date=2026-09-12 | on=
ORPHAN-OBJECT | codex/v03-recovery-shared-contracts | d59047eb0 | notOnCanon=2 | notOnMain=469 | date=2026-09-12 | on=
ORPHAN-OBJECT | codex/v03-recovery-state-resolver-review | 53b3ca89e | notOnCanon=2 | notOnMain=494 | date=2026-09-12 | on=
ORPHAN-OBJECT | codex/v03-recovery-surface-owner | 3df7469e4 | notOnCanon=7 | notOnMain=454 | date=2026-09-12 | on=
ORPHAN-OBJECT | codex/v03-recovery-wire-boundary | 2e6baba4a | notOnCanon=1 | notOnMain=446 | date=2026-09-12 | on=
ORPHAN-OBJECT | codex/v03-recovery-write-back-settings | 64aa3bd1b | notOnCanon=1 | notOnMain=504 | date=2026-09-12 | on=
ORPHAN-OBJECT | codex/v03-repository-read-ownership | 2b546f712 | notOnCanon=3 | notOnMain=182 | date=2026-09-08 | on=
PUSHED | codex/v03-retire-unused-writeback | dc294ef95 | notOnCanon=0 | notOnMain=311 | date=2026-09-08 | on=origin/codex/v03-focused-readers-and-admission,origin/codex/v03-permissions-and-fixed-admission,origin/codex/v03-recover
ORPHAN-OBJECT | codex/v03-retirement-census-fix | 8f222ac2a | notOnCanon=2 | notOnMain=326 | date=2026-09-08 | on=
PUSHED | codex/v03-roster-fixture-correction | dee967495 | notOnCanon=0 | notOnMain=374 | date=2026-09-08 | on=origin/codex/v03-permissions-and-fixed-admission,origin/codex/v03-recovery-integration,origin/codex/v03-session-failures
ORPHAN-OBJECT | codex/v03-ruling-prompt | 2723b7919 | notOnCanon=1 | notOnMain=285 | date=2026-09-08 | on=
ORPHAN-OBJECT | codex/v03-ruling-prompt-mutations | 2723b7919 | notOnCanon=1 | notOnMain=285 | date=2026-09-08 | on=
PUSHED | codex/v03-runtime-claims-fire-spec | 014b2fe45 | notOnCanon=0 | notOnMain=161 | date=2026-09-08 | on=origin/codex/v03-audit-evidence-consumers,origin/codex/v03-ci-locked-uv,origin/codex/v03-fire-audit-consumers,origin/cod
PUSHED | codex/v03-runtime-simplification | 5c62eb147 | notOnCanon=0 | notOnMain=327 | date=2026-09-08 | on=origin/codex/v03-focused-readers-and-admission,origin/codex/v03-permissions-and-fixed-admission,origin/codex/v03-recover
ORPHAN-OBJECT | codex/v03-scanner-failure-contract | b3acf83ba | notOnCanon=1 | notOnMain=367 | date=2026-09-08 | on=
PUSHED | codex/v03-scope-native-consumers | 30d6bab0a | notOnCanon=0 | notOnMain=244 | date=2026-09-08 | on=origin/codex/v03-audit-evidence-consumers,origin/codex/v03-ci-locked-uv,origin/codex/v03-fire-criterion-consumers,origin
ORPHAN-OBJECT | codex/v03-scope-plan-barriers | c0638caf0 | notOnCanon=2 | notOnMain=217 | date=2026-09-08 | on=
ORPHAN-OBJECT | codex/v03-scope-plan-mutations | 616801e01 | notOnCanon=2 | notOnMain=217 | date=2026-09-08 | on=
ORPHAN-OBJECT | codex/v03-scope-ready | f356c5fa7 | notOnCanon=5 | notOnMain=231 | date=2026-09-08 | on=
ORPHAN-OBJECT | codex/v03-scope-ready-mutants | f356c5fa7 | notOnCanon=5 | notOnMain=231 | date=2026-09-08 | on=
ORPHAN-OBJECT | codex/v03-self-write-attribution | 4ae496cd3 | notOnCanon=4 | notOnMain=281 | date=2026-09-08 | on=
PUSHED | codex/v03-session-failures-and-agent-settings | f1ae6ac78 | notOnCanon=0 | notOnMain=384 | date=2026-09-08 | on=origin/codex/v03-recovery-integration,origin/codex/v03-session-failures-and-agent-settings,origin/kodezart/v03-integrati
PUSHED | codex/v03-session-guard-mutations | b89db2ac0 | notOnCanon=0 | notOnMain=385 | date=2026-09-08 | on=origin/codex/v03-recovery-integration,origin/codex/v03-session-failures-and-agent-settings,origin/kodezart/v03-integrati
PUSHED | codex/v03-session-settings-correction | b89db2ac0 | notOnCanon=0 | notOnMain=385 | date=2026-09-08 | on=origin/codex/v03-recovery-integration,origin/codex/v03-session-failures-and-agent-settings,origin/kodezart/v03-integrati
PUSHED | codex/v03-shared-policies-and-sessions | 7042032c2 | notOnCanon=0 | notOnMain=342 | date=2026-09-08 | on=origin/codex/v03-focused-readers-and-admission,origin/codex/v03-permissions-and-fixed-admission,origin/codex/v03-recover
PUSHED | codex/v03-shared-policies-correction | d350b0a44 | notOnCanon=0 | notOnMain=343 | date=2026-09-08 | on=origin/codex/v03-focused-readers-and-admission,origin/codex/v03-permissions-and-fixed-admission,origin/codex/v03-recover
ORPHAN-OBJECT | codex/v03-stalled-cumulative | c8382d953 | notOnCanon=2 | notOnMain=178 | date=2026-09-08 | on=
ORPHAN-OBJECT | codex/v03-terminal-mandate-mutants | e6be6a68b | notOnCanon=7 | notOnMain=263 | date=2026-09-08 | on=
ORPHAN-OBJECT | codex/v03-tool-presets | 3123e235e | notOnCanon=2 | notOnMain=375 | date=2026-09-08 | on=
ORPHAN-OBJECT | codex/v03-tracker-feasibility | dc25386a8 | notOnCanon=5 | notOnMain=165 | date=2026-09-08 | on=
ORPHAN-OBJECT | codex/v03-tracker-feasibility-mutations | dc25386a8 | notOnCanon=5 | notOnMain=165 | date=2026-09-08 | on=
ORPHAN-OBJECT | codex/v03-tracker-settings | a4e758bae | notOnCanon=3 | notOnMain=376 | date=2026-09-08 | on=
PUSHED | codex/v03-typed-runtime-boundaries | 63e892cc6 | notOnCanon=0 | notOnMain=332 | date=2026-09-08 | on=origin/codex/v03-focused-readers-and-admission,origin/codex/v03-permissions-and-fixed-admission,origin/codex/v03-recover
PUSHED | codex/v03-typed-runtime-correction | a2f2e99d2 | notOnCanon=0 | notOnMain=334 | date=2026-09-08 | on=origin/codex/v03-focused-readers-and-admission,origin/codex/v03-permissions-and-fixed-admission,origin/codex/v03-recover
ORPHAN-OBJECT | codex/v03-union-composition | e6e9c97e3 | notOnCanon=12 | notOnMain=114 | date=2026-09-08 | on=
ORPHAN-OBJECT | codex/v03-union-conflict-mutants | a87996e79 | notOnCanon=1 | notOnMain=256 | date=2026-09-08 | on=
ORPHAN-OBJECT | codex/v03-union-conflict-remediation | a87996e79 | notOnCanon=1 | notOnMain=256 | date=2026-09-08 | on=
ORPHAN-OBJECT | codex/v03-union-native-inputs | 79a7a4624 | notOnCanon=2 | notOnMain=261 | date=2026-09-08 | on=
PUSHED | codex/v03-union-record-delivery | 81e485c78 | notOnCanon=0 | notOnMain=137 | date=2026-09-08 | on=origin/codex/v03-audit-evidence-consumers,origin/codex/v03-audit-record-pr-replay,origin/codex/v03-ci-locked-uv,origin/c
ORPHAN-OBJECT | codex/v03-union-replacement-mutants | 79a7a4624 | notOnCanon=2 | notOnMain=261 | date=2026-09-08 | on=
PUSHED | codex/v03-union-workspace-protection | b3c8d1a73 | notOnCanon=0 | notOnMain=263 | date=2026-09-08 | on=origin/codex/v03-audit-evidence-consumers,origin/codex/v03-ci-locked-uv,origin/codex/v03-fire-criterion-consumers,origin
PUSHED | codex/v03-workflow-phases-and-settings | a10985532 | notOnCanon=0 | notOnMain=351 | date=2026-09-08 | on=origin/codex/v03-focused-readers-and-admission,origin/codex/v03-permissions-and-fixed-admission,origin/codex/v03-recover
ORPHAN-OBJECT | codex/v03-workspace-url-mutants | 1ded74256 | notOnCanon=3 | notOnMain=261 | date=2026-09-08 | on=
ORPHAN-OBJECT | codex/v03-workspace-url-patterns | 763d7be11 | notOnCanon=4 | notOnMain=262 | date=2026-09-08 | on=
ORPHAN-OBJECT | codex/v03-write-back-verifier | 3a288c514 | notOnCanon=4 | notOnMain=164 | date=2026-09-08 | on=
ORPHAN-OBJECT | codex/v03-writeback-native-proof | d0f30f29b | notOnCanon=1 | notOnMain=293 | date=2026-09-08 | on=
ORPHAN-OBJECT | codex/v03-writeback-native-proof-mutations | d0f30f29b | notOnCanon=1 | notOnMain=293 | date=2026-09-08 | on=
```

## Appendix B — 130 Sep-8 orphan heads: object completeness
| head | commits not on canonical | branches | objects | subject |
|---|---|---|---|---|
| `1fd931783` | 2 | kod-637-addressed-ruling-entry  | CORRUPT-1 | Account for prior workspace release in entry cancellation proof |
| `4040e2960` | 2 | kod-637-entry-mutations  | CORRUPT-1 | Account for prior workspace release in entry cancellation proof |
| `f85fa8e75` | 2 | v03-audit-settings  | CORRUPT-1 | Assert configured remote on each audit observation path |
| `b3acf83ba` | 1 | v03-scanner-failure-contract  | CORRUPT-1 | Classify native session failures at the SDK boundary |
| `65e2ed7a2` | 2 | v03-native-initiative-mutations  | CORRUPT-1 | Exercise native initiative guidance in composed grooming output |
| `ae3a8b814` | 2 | v03-operation-concerns  | CORRUPT-1 | Exercise native initiative guidance in composed grooming output |
| `1cfa58f1a` | 1 | v03-audit-sweep-native  | CORRUPT-1 | feat(audit): assemble native scope requests and run read-only sweep |
| `196e8b992` | 1 | v03-node-event-review  | CORRUPT-1 | feat(run): validate the complete run-event table before tracker boot |
| `1c20be4f2` | 1 | v03-criterion-read-boot  | CORRUPT-1 | feat(tracker): require criterion reads before startup (KOD-465) |
| `4cf792972` | 3 | v03-audit-claim-session  | CORRUPT-1 | fix(audit): settle owned workspaces through repeated cancellation |
| `6fb6ad11a` | 1 | v03-delivery-read-ownership  | CORRUPT-1 | fix(delivery): settle native repository reads before cancellation (KOD |
| `2b546f712` | 3 | v03-repository-read-ownership  | CORRUPT-1 | fix(fire): own feasibility cache acquisition through cancellation (KOD |
| `0d46227cb` | 1 | kod-338-admission-ownership kod-338-admission-ownership-mutants  | CORRUPT-1 | fix(organize): settle admission workspace ownership before cancellatio |
| `a87996e79` | 1 | v03-union-conflict-mutants v03-union-conflict-remediation  | CORRUPT-1 | fix(union): carry one remediation for native merge conflicts (KOD-593) |
| `8ef12cd90` | 2 | v03-criterion-lifecycle-identity  | CORRUPT-1 | test(identity): resolve qualified local ruling aliases (KOD-598) |
| `a4e758bae` | 3 | v03-tracker-settings  | CORRUPT-10 | Assert tracker token exclusion in serialized nested settings |
| `372fb93a8` | 3 | v03-domain-permissions  | CORRUPT-10 | Keep live probe inputs on explicit permission boundaries |
| `3def3ece1` | 5 | v03-job-queue-settings  | CORRUPT-10 | Pass focused queue settings to the composed job lifecycle |
| `49aed9271` | 8 | kod-486-aggregate-mutations  | CORRUPT-11 | Reject trailing-underscore configuration typos without prefix exemptio |
| `fd25528a0` | 8 | kod-486-authored-aggregate-admission  | CORRUPT-11 | Reject trailing-underscore configuration typos without prefix exemptio |
| `5cee6db41` | 6 | v03-audit-mandate-hunt  | CORRUPT-12 | Keep unreadable audit coverage grounded in native read failures |
| `e6be6a68b` | 7 | v03-audit-terminal-mandate v03-terminal-mandate-mutants  | CORRUPT-12 | feat(audit): complete native terminal mandates through the shared hunt |
| `f3ca519c4` | 6 | v03-http-settings  | CORRUPT-13 | Group HTTP settings and inject only the response prefix |
| `e6d467cd6` | 8 | v03-git-settings  | CORRUPT-14 | Migrate remaining Git environment fixtures to nested names |
| `a6db13095` | 8 | v03-evidence-ancestry-integrity  | CORRUPT-14 | fix(audit): reject replacement-only Evidence ancestry |
| `79c333e60` | 3 | v03-delivery-flake-mutants  | CORRUPT-14 | fix(delivery): retain one failing-check reader across recovery |
| `bd38c4a26` | 13 | v03-delivery-replay v03-delivery-replay-mutants  | CORRUPT-15 | fix(delivery): read the existing PR before creation (KOD-314) |
| `e6988cf4b` | 4 | v03-delivery-flake  | CORRUPT-15 | test(delivery): require the original failed-check read at zero reruns |
| `aedb2f20b` | 7 | v03-logging-settings  | CORRUPT-16 | Group logging choices and reject unsupported severity names |
| `bb80d0715` | 14 | v03-pr-footer v03-pr-footer-mutants  | CORRUPT-16 | fix(delivery): retain tracker identity after outbound gating (KOD-739) |
| `d46b3292c` | 11 | kod-485-aggregate-retirement kod-485-retirement-mutations  | CORRUPT-17 | Retire aggregate prose patterns after authored admission migration |
| `058dca9a1` | 1 | v03-description-replay-mutants v03-exact-description-replay  | CORRUPT-2 | Make guarded description edits converge at an exact target |
| `92af9aa12` | 1 | kod-470-source-prose  | CORRUPT-2 | Replace private source citations with their behavioral meaning |
| `39990202b` | 1 | v03-current-union v03-current-union-mutants  | CORRUPT-2 | feat(union): recheck current lane heads before reporting scope results |
| `c01dcd81c` | 1 | kod-633-ruling-proposal kod-633-ruling-proposal-mutations  | CORRUPT-2 | feat: propose rulings from fresh native fire sources |
| `aafb0d225` | 3 | v03-escalation-review  | CORRUPT-2 | fix(supervisor): verify the decision used for escalation age |
| `4ae496cd3` | 4 | v03-self-write-attribution  | CORRUPT-2 | fix: bound receipt retention and wake on expired history |
| `387078509` | 6 | v03-empty-criteria-entry v03-empty-criteria-mutations  | CORRUPT-2 | test(fire): prove empty criteria refuse before loop dispatch (KOD-710) |
| `975532d89` | 5 | v03-lane-record-mutations  | CORRUPT-2 | test(records): verify complete reads and document runtime limits (KOD- |
| `d8796325b` | 4 | v03-escalation-record-collector  | CORRUPT-2 | test(supervisor): include typed escalation observation import |
| `fcf8c094f` | 3 | kod-511-record-superseded  | CORRUPT-2 | test(supervisor): require lane subject for record supersession (KOD-51 |
| `51bc8f9d0` | 3 | kod-503-scope-tally-mutants  | CORRUPT-2 | test: isolate scope tally identity and duplicate roster guards |
| `dd6c7faa6` | 3 | kod-503-scope-tally  | CORRUPT-2 | test: isolate scope tally identity and duplicate roster guards |
| `1dce04381` | 13 | kod-514-detector-removal  | CORRUPT-21 | test(audit): verify exact detached acquisition arguments (KOD-514) |
| `20ac47f58` | 2 | v03-github-settings  | CORRUPT-22 | Preserve historical GitHub names in the versioned migration guide |
| `67d6ab276` | 3 | v03-forge-settings-and-live-tests  | CORRUPT-25 | Preserve hermetic collection and explicit native live test inputs |
| `4bb47e5c4` | 13 | kod-485-outbound-admission  | CORRUPT-28 | Exercise mandatory branch judgment for both provenance classes |
| `cf063e32f` | 13 | kod-485-outbound-admission-mutations  | CORRUPT-28 | Exercise mandatory branch judgment for both provenance classes |
| `5ccb7b97c` | 7 | v03-audit-terminal-consistency  | CORRUPT-3 | Assert terminal admission and unresolved native PR facts |
| `ce193c878` | 2 | v03-live-harness  | CORRUPT-3 | Preserve hermetic collection and explicit native live test inputs |
| `7b58c746b` | 3 | v03-config-reduction  | CORRUPT-3 | Remove unconsumed ORGANIZE round settings and refuse retired inputs |
| `8faf6f4b6` | 4 | audit-replace-integrity audit-replace-mutants  | CORRUPT-3 | docs(audit): state original-tree claim requirement |
| `c3ef39d0b` | 6 | v03-lane-record-reader  | CORRUPT-3 | docs(records): state the declared collection mutability (KOD-683) |
| `c046b6243` | 5 | v03-audit-sweep-assessment v03-barren-record-collector  | CORRUPT-3 | feat(supervisor): observe growth from native lane records |
| `23a375710` | 2 | kod-510-assertion-drift kod-510-assertion-drift-mutants  | CORRUPT-3 | test(audit): preserve literal Git magic in protected source paths (KOD |
| `096a6ccab` | 3 | v03-audit-detector-sweep  | CORRUPT-3 | test(audit): prove clean self rules and mixed-head refusal |
| `687723f9c` | 5 | kod-94-ruling-records  | CORRUPT-3 | test(rulings): recognize quoted and aliased string addresses (KOD-636) |
| `a2efabfde` | 5 | kod-ruling-record-mutants  | CORRUPT-3 | test(rulings): recognize quoted and aliased string addresses (KOD-636) |
| `2d389e5e1` | 1 | kod-424-fire-entry-mutants kod-424-fire-spec-approval  | CORRUPT-4 | Require current completion and approval at FireSpec read |
| `51d83d55b` | 1 | v03-pr-content-mutants v03-pr-content-replay  | CORRUPT-4 | feat(delivery): edit existing PR content on replay (KOD-95) |
| `b559dfb3d` | 3 | v03-node-session-events v03-node-session-mutants  | CORRUPT-4 | feat(run): emit native evaluator session openings from the harness |
| `289ad5104` | 7 | kod-513-record-collector  | CORRUPT-4 | feat(supervisor): collect commit consistency from the live lane record |
| `42c58b10f` | 2 | v03-audit-coverage  | CORRUPT-4 | fix(audit): cover before a nondivisible cadence deadline (KOD-537) |
| `f50e35033` | 4 | v03-fire-spec-consumer-mutations v03-fire-spec-consumers  | CORRUPT-4 | test(delivery): pin captured criterion multiplicity and order (KOD-410 |
| `eeb91ef7a` | 6 | v03-fire-extraction  | CORRUPT-4 | test(fire): assert delivery-free compiled graph and state (KOD-332) |
| `0bc666dc9` | 3 | kod-450-addressed-preloop-mutants  | CORRUPT-4 | test(scope): isolate undeclared repository refusal |
| `d5ae87082` | 3 | kod-450-addressed-preloop  | CORRUPT-4 | test(scope): isolate undeclared repository refusal |
| `affc8ff7a` | 13 | v03-recorded-landing  | CORRUPT-4 | test: preserve provenance checks for typed landing bindings |
| `a6b0383fa` | 3 | kod-746-delivery-mutations  | CORRUPT-5 | Pin the native authored retry proof to an actual cleanup call |
| `47d39b3d9` | 1 | v03-owned-resource-mechanics v03-owned-resource-mutations  | CORRUPT-5 | Share owned operation settlement and read-only workspace lifetimes |
| `e2866ebba` | 4 | v03-audit-removal-sweep  | CORRUPT-5 | feat(audit): compose source-backed detector losses into sweep reports |
| `ed7c99422` | 6 | kod-519-mandate-graph  | CORRUPT-5 | test(supervisor): keep superseded fires distinct from crossed fires (K |
| `8d4714f47` | 4 | v03-model-agreement  | CORRUPT-5 | test: bind model pointers to native target identities |
| `3123e235e` | 2 | v03-tool-presets  | CORRUPT-6 | Keep application tool bundles neutral at the SDK boundary |
| `debfbf65b` | 4 | v03-check-runner-settings  | CORRUPT-6 | Keep subprocess cleanup cadence at its owner and inject only the comma |
| `3a288c514` | 4 | v03-write-back-verifier  | CORRUPT-6 | Publish cancellation probe identity atomically before observing it |
| `b62f57a6f` | 2 | v03-fresh-judgment-mechanics v03-fresh-judgment-mutations  | CORRUPT-6 | Share fresh judging dispatch within caller-owned workspaces |
| `df442a261` | 6 | v03-audit-forge-evidence  | CORRUPT-6 | fix(audit): classify readable red before roster acceptance (KOD-508) |
| `d504adda7` | 4 | kod-746-delivery-retirement  | CORRUPT-6 | fix(composition): forward declared repositories to authored delivery |
| `dc25386a8` | 5 | v03-tracker-feasibility v03-tracker-feasibility-mutations  | CORRUPT-6 | fix(fire): settle feasibility reads and reject duplicate conflict keys |
| `64d60ef1c` | 2 | kod-491-reference-mutations  | CORRUPT-6 | fix(privacy): classify rendered Markdown reference authorities |
| `8167a2dc3` | 2 | kod-491-typed-private-references  | CORRUPT-6 | fix(privacy): classify rendered Markdown reference authorities |
| `f356c5fa7` | 5 | v03-scope-ready v03-scope-ready-mutants  | CORRUPT-6 | test(scope): isolate final tree and membership freshness windows (KOD- |
| `535351e12` | 3 | v03-knowledge-settings  | CORRUPT-7 | Reject retired knowledge secret files and prove credential exclusion |
| `5fec92b88` | 4 | v03-audit-state-history  | CORRUPT-7 | docs(tracker): keep native history contract source neutral |
| `204e5a030` | 4 | v03-audit-forge-sweep v03-audit-forge-sweep-mutants  | CORRUPT-7 | feat(audit): compose exact-SHA forge checks into native sweeps |
| `0a7ac0365` | 7 | v03-audit-overclaims  | CORRUPT-7 | fix(audit): refuse replaced Git objects in fresh sessions (KOD-514) |
| `1ecc99ed0` | 5 | v03-configured-fire-mutations  | CORRUPT-7 | fix(scope): retain complete team identity through preparation |
| `87eaf3813` | 6 | v03-configured-fire-preparation  | CORRUPT-8 | docs(scope): describe both first-entry repository routes |
| `de632003b` | 2 | v03-agent-settings  | CORRUPT-9 | Cover nested agent typos and composed skill-loadout refusal |
| `616801e01` | 2 | v03-scope-plan-mutations  | CORRUPT-9 | fix(scope): require reported planning labels and relations (KOD-450) |
| `c0638caf0` | 2 | v03-scope-plan-barriers  | CORRUPT-9 | fix(scope): require reported planning labels and relations (KOD-450) |
| `2298e0d26` | 1 | v03-http-dependencies-before  | INTACT | Correct the documented immediate scope refusal |
| `ecd0d144b` | 2 | v03-http-dependencies  | INTACT | Declare HTTP dependencies and validate existing response models |
| `4f96b46aa` | 1 | v03-event-model-conformance  | INTACT | Exercise model event agreement against the deployed table |
| `9bbf67087` | 2 | v03-criterion-record-fidelity  | INTACT | Keep the criterion fidelity matrix total over native state kinds |
| `b1393733a` | 2 | v03-criterion-fidelity-mutations  | INTACT | Keep the criterion fidelity matrix total over native state kinds |
| `8f222ac2a` | 2 | v03-retirement-census-fix  | INTACT | Keep wire rename exclusions aligned with active outcome producers |
| `294b2e0b7` | 2 | v03-pagination-implementation  | INTACT | Make cancellation regression reject swallowed cancellation promptly |
| `93f9e89af` | 2 | v03-pagination-mutations  | INTACT | Make cancellation regression reject swallowed cancellation promptly |
| `d0f30f29b` | 1 | v03-writeback-native-proof v03-writeback-native-proof-mutations  | INTACT | Prove missing-test repair against native Git bytes |
| `bbed103b1` | 1 | v03-event-credential-fixtures  | INTACT | Provide the event table in credential boot fixtures |
| `e2a49a868` | 1 | v03-audit-worktree-integrity  | INTACT | Reject dirty audit workspaces and register audit dispatch census |
| `67455934b` | 2 | v03-nested-settings-docs  | INTACT | Reject trailing-underscore configuration typos without prefix exemptio |
| `d7e7521e9` | 1 | kod-139-operation-sections  | INTACT | Replace private source citations with their behavioral meaning |
| `cf6fc4ed1` | 1 | v03-dispatch-log-isolation  | INTACT | Restore logging globals after tests that configure application startup |
| `51e45d2fd` | 1 | v03-logging-test-isolation  | INTACT | Restore logging state after the native traceback test |
| `9ed898951` | 2 | kod-510-recorded-assertion-drift kod-510-recorded-assertion-mutants  | INTACT | Retain typed ruling identities in protection designations |
| `c14123dc2` | 3 | v03-marker-lookups v03-marker-mutations  | INTACT | Share unique marker lookup with native lane prefix discovery |
| `19a03d96c` | 1 | kod-470-public-docs  | INTACT | State documentation behavior without private tracker citations |
| `e5bb791f5` | 1 | v03-judgment-schema-census  | INTACT | Trace schema coverage through the shared judgment dispatch |
| `1ded74256` | 3 | v03-workspace-url-mutants  | INTACT | docs(config): preserve workspace URL defaults in environment example |
| `763d7be11` | 4 | v03-workspace-url-patterns  | INTACT | docs(gate): align pattern default reference with workspace URL protect |
| `2adc8c8d6` | 2 | v03-audit-combined-review  | INTACT | feat(audit): complete native terminal mandates through the shared hunt |
| `38a8638fb` | 4 | v03-delivery-stalled v03-delivery-stalled-mutants  | INTACT | feat(delivery): publish recorded stalled work through the common route |
| `dc49867b5` | 1 | v03-delivery-no-adapter  | INTACT | feat(delivery): report healthy handoffs without a PR adapter (KOD-313) |
| `2723b7919` | 1 | v03-ruling-prompt v03-ruling-prompt-mutations  | INTACT | feat(prompts): register the evaluative fire-time ruling role |
| `f094b5eb6` | 1 | v03-criterion-resolution  | INTACT | feat(tracker): resolve native criterion keys at one read boundary |
| `c8382d953` | 2 | v03-stalled-cumulative  | INTACT | fix(delivery): accept cumulative stalled iteration totals (KOD-327) |
| `852623fc9` | 1 | v03-cache-remote-name  | INTACT | fix(git): preserve configured remote during cold bare clone |
| `1f722c153` | 1 | dispatch-budget-fixture pass-budget-mutants  | INTACT | fix(passes): rearm gate reads cancelled before dispatch (KOD-164) |
| `4f48afe6d` | 3 | kod-380-scope-approval  | INTACT | fix(tracker): refuse incomplete positive project membership (KOD-380) |
| `b1d410c28` | 3 | scope-approval-mutants  | INTACT | fix(tracker): refuse incomplete positive project membership (KOD-380) |
| `5595af10f` | 2 | v03-audit-evidence  | INTACT | test(audit): distinguish valid lane-record replacement from unreadabil |
| `da1d474dd` | 2 | v03-check-runner-cancellation  | INTACT | test(checks): publish controlled child identity atomically (KOD-597) |
| `a3a3089f5` | 1 | v03-fire-writer-inventory  | INTACT | test(fire): follow extracted PR writers in sanitization inventory |
| `2bedc1a3c` | 1 | v03-http-mcp-lifecycle  | INTACT | test(mcp): isolate lifecycle assertions from traceback rendering (KOD- |
| `335f9bd28` | 2 | v03-milestone-metadata v03-milestone-metadata-mutants  | INTACT | test(scope): retain tool argument checks for optional milestone URLs |
| `79a7a4624` | 2 | v03-union-native-inputs v03-union-replacement-mutants  | INTACT | test(union): isolate the pinned conflict identity guard |
| `e6e9c97e3` | 12 | v03-union-composition  | INTACT | test(union): reap running checks before discarding the scratch tree (K |

## Appendix C — every dirty or detached worktree (`git status --short`, ≤30 lines, diff --stat summary)
```
### /private/tmp/kodezart-v03-implementation  NO .git FILE
### /private/tmp/kodezart-organize-before-proof | DETACHED | fc87741 | dirty=0 | last=2026-09-12 fix(config): refuse the retired surface lease duration key
### /private/tmp/kodezart-v03-alarm-independent-review | DETACHED | cd558ab | dirty=1 | last=2026-09-12 fix(alarms): validate the exact record snapshot before upsert
?? tests/tracker/test_alarm_review_original.py
### /private/tmp/kodezart-v03-amendment-comment-independent-review | DETACHED | e5ef84b | dirty=1 | last=2026-09-12 fix(tracker): refresh protected comment checks on unsent retries
?? tests/tracker/test_comment_expected_review_original.py
### /private/tmp/kodezart-v03-artifact-read-independent | codex/v03-artifact-read-independent | 0e30d78 | dirty=2 | last=2026-09-12 Validate split identity response before final source consistency read
?? tests/tracker/test_artifact_failure_boundary_independent.py
?? tests/tracker/test_artifact_identity_alias_independent.py
### /private/tmp/kodezart-v03-audit-boundary-parent-independent | DETACHED | 5a3431f | dirty=0 | last=2026-09-12 Require actual audit classification reads before queue startup
### /private/tmp/kodezart-v03-audit-corrective-independent | DETACHED | 1041412 | dirty=2 | last=2026-09-12 Preserve operational versus programmer failure boundaries in Audit
?? tests/integration/test_audit_historical_source_review.py
?? tests/integration/test_audit_workspace_failure_review.py
### /private/tmp/kodezart-v03-audit-gate-independent | DETACHED | 643c96e | dirty=1 | last=2026-09-12 Keep audit outcome dependency in its runtime and align failure fixtures
?? tests/tracker/test_audit_gate_peer.py
### /private/tmp/kodezart-v03-audit-independent-current | DETACHED | 38e848b | dirty=0 | last=2026-09-12 Require actual audit classification reads before queue startup
### /private/tmp/kodezart-v03-audit-report-independent-review | DETACHED | fd93cf2 | dirty=1 | last=2026-09-12 Require the matching mandate payload in completed audit report types
?? tests/types/test_audit_report_independent.py
### /private/tmp/kodezart-v03-audit-workspace-independent | DETACHED | 36b18f5 | dirty=1 | last=2026-09-12 Preserve programmer failures during workspace acquisition
?? tests/integration/test_git_workspace_types_independent.py
### /private/tmp/kodezart-v03-ci-review-native | DETACHED | 2eddd4d | dirty=0 | last=2026-09-12 Return coherent typed CI observations from each watch
### /private/tmp/kodezart-v03-classification-independent-review | DETACHED | a51d777 | dirty=0 | last=2026-09-12 fix(tracker): retain escalation label authority across retries
### /private/tmp/kodezart-v03-criterion-label-independent-review | DETACHED | 008ff18 | dirty=0 | last=2026-09-12 Require known native classification before selecting write authority
### /private/tmp/kodezart-v03-delivery-native-review | DETACHED | e7ae430 | dirty=1 | last=2026-09-12 Revalidate native delivery evidence at resumed terminal boundaries
?? tests/chains/test_delivery_native_independent.py
### /private/tmp/kodezart-v03-delivery-root-review | DETACHED | f0142dc | dirty=1 | last=2026-09-12 Revalidate native delivery evidence at resumed terminal boundaries
?? tests/chains/test_lane_delivery_root_review.py
### /private/tmp/kodezart-v03-m1-dispatch-timeout-independent | codex/v03-m1-dispatch-timeout-independent | eb57778 | dirty=1 | last=2026-09-12 Synchronize the dispatch cancellation oracle at its required entry boundary
?? tests/services/test_dispatch_timeout_review_independent.py
### /private/tmp/kodezart-v03-m1-git-settings-baseline-check | DETACHED | 241e85c | dirty=0 | last=2026-09-12 Synchronize the dispatch cancellation oracle at its required entry boundary
### /private/tmp/kodezart-v03-m1-lease-corrective-independent-review | DETACHED | 57dcc8c | dirty=1 | last=2026-09-12 Complete M1 configured marker prompt and fixture closure
?? tests/prompts/test_m1_marker_prefix_independent.py
### /private/tmp/kodezart-v03-m1-native-read-canonical-before | DETACHED | 7892ca1 | dirty=0 | last=2026-09-12 Format independent classification regression probes
### /private/tmp/kodezart-v03-m1-native-read-root-before | DETACHED | 6346911 | dirty=0 | last=2026-09-12 Extract configured native criterion read boundary for M1
### /private/tmp/kodezart-v03-m1-native-read-root-review | DETACHED | fec7f28 | dirty=0 | last=2026-09-12 Preserve actual UUID alias and foreign parent regression controls
### /private/tmp/kodezart-v03-m1-scope-bindings-independent | DETACHED | ddb8cde | dirty=0 | last=2026-09-13 Extract shared scope labels, native approval reads and bootstrap
### /private/tmp/kodezart-v03-m1-scope-donor-review | DETACHED | 5ef89e2 | dirty=1 | last=2026-09-12 Register native prompt inputs and align utility prompt metadata
?? tests/tracker/test_m1_scope_independent.py
### /private/tmp/kodezart-v03-m1-scope-independent-review | DETACHED | 1811397 | dirty=2 | last=2026-09-12 Extract current scope read ports and native adapter boundaries for M1
?? tests/tracker/test_m1_scope_alias_calibration.py
?? tests/tracker/test_m1_scope_independent.py
### /private/tmp/kodezart-v03-m1-session-policy-root-review | DETACHED | ea1d915 | dirty=0 | last=2026-09-12 Document domain session policy and preserved HTTP vocabulary
### /private/tmp/kodezart-v03-m2-authority-donor-integration | DETACHED | eae9a94 | dirty=0 | last=2026-09-13 Normalize the restored adapter method spacing
### /private/tmp/kodezart-v03-m2-authority-review | DETACHED | ad9ba4e | dirty=0 | last=2026-09-13 Exercise Organize retry authority through independent native controls
### /private/tmp/kodezart-v03-m2-authority-review-before | DETACHED | 9e387b4 | dirty=3 | last=2026-09-13 Remove duplicate merged scope fixture initialization
?? tests/chains/test_authority_independent_review.py
?? tests/chains/test_organize_description_authority.py
?? tests/chains/test_organize_write_revalidation.py
### /private/tmp/kodezart-v03-m2-groom-independent-parent | DETACHED | e99c9d1 | dirty=1 | last=2026-09-13 Exercise Organize retry authority through independent native controls
?? tests/chains/test_organize_rubric.py
### /private/tmp/kodezart-v03-m2-groom-independent-review | DETACHED | 980b179 | dirty=0 | last=2026-09-13 Expose dynamic Organize rubric freshness and native binding failures
### /private/tmp/kodezart-v03-m2-groom-rubric-baseline-e99 | DETACHED | e99c9d1 | dirty=0 | last=2026-09-13 Exercise Organize retry authority through independent native controls
### /private/tmp/kodezart-v03-m2-groom-rubric-independent | DETACHED | 37723cd | dirty=0 | last=2026-09-13 Reconcile Organize with its scope bootstrap prerequisite
### /private/tmp/kodezart-v03-m2-integrated-root-review | codex/v03-m2-integrated-root-review | 9e387b4 | dirty=1 | last=2026-09-13 Remove duplicate merged scope fixture initialization
?? tests/chains/test_organize_write_revalidation.py
### /private/tmp/kodezart-v03-m2-organize-independent-a15 | DETACHED | a15b334 | dirty=0 | last=2026-09-13 fix(organize): revalidate criterion retries and align author instructions
### /private/tmp/kodezart-v03-m2-organize-independent-daae | DETACHED | daae8df | dirty=0 | last=2026-09-13 feat(organize): extract bounded native owner and tracker writes
### /private/tmp/kodezart-v03-m2-scope-bootstrap-independent | DETACHED | 37723cd | dirty=0 | last=2026-09-13 Reconcile Organize with its scope bootstrap prerequisite
### /private/tmp/kodezart-v03-m3-gate-closure | DETACHED | 10c51a7 | dirty=0 | last=2026-09-13 test(workflow): restore native specification and CI observation controls
### /private/tmp/kodezart-v03-m3-oracle-review | DETACHED | cbbe5dd | dirty=0 | last=2026-09-13 test(fire): preserve original extracted workflow oracles
### /private/tmp/kodezart-v03-m3-plan-walk-census | codex/v03-m3-plan-walk-census | 7675c0b | dirty=9 | last=2026-09-13 fix(workflow): preserve authored HTTP and checkpoint compatibility
 M tests/tracker/test_native_approval_aliases.py
?? tests/adapters/test_ci_observation.py
?? tests/adapters/test_ci_watch_evidence.py
?? tests/adapters/test_ci_watch_result.py
?? tests/chains/test_node_session_events.py
?? tests/domain/test_fire_spec_purity.py
?? tests/tracker/test_fire_spec_approval.py
?? tests/tracker/test_fire_spec_approval_ancestry.py
?? tests/tracker/test_fire_spec_reader.py
 1 file changed, 2 insertions(+), 2 deletions(-)
### /private/tmp/kodezart-v03-m3-root-integration | DETACHED | 10c51a7 | dirty=0 | last=2026-09-13 test(workflow): restore native specification and CI observation controls
### /private/tmp/kodezart-v03-m3-type-review | DETACHED | dad63c4 | dirty=0 | last=2026-09-13 feat(workflow): extract native and authored fire graphs with scope planning
### /private/tmp/kodezart-v03-m4-heartbeat-clock-fix | DETACHED | 06214eb | dirty=0 | last=2026-09-13 test(lifecycle): advance heartbeat time only on explicit intervals
### /private/tmp/kodezart-v03-m4-heartbeat-parent-review | DETACHED | 268be40 | dirty=1 | last=2026-09-13 Integrate reviewed scope label and bootstrap prerequisite
?? tests/services/test_heartbeat_clock_diagnostic.py
### /private/tmp/kodezart-v03-m4-heartbeat-review | DETACHED | c855fb0 | dirty=2 | last=2026-09-13 Carry the shared run event vocabulary for native observers
?? tests/services/test_claim_heartbeat_clock.py
?? tests/services/test_heartbeat_clock_diagnostic.py
### /private/tmp/kodezart-v03-m4-native-read-independent | codex/v03-m4-native-read-independent | 8930fcb | dirty=2 | last=2026-09-12 Consume shared typed evaluation policy in the fresh write-back judge
?? tests/chains/test_m4_local_sdk_independent.py
?? tests/chains/test_m4_native_extension_independent.py
### /private/tmp/kodezart-v03-m4-ruling-source-corrected-independent | DETACHED | 47b72c7 | dirty=0 | last=2026-09-13 Preserve runtime reader protocols and native source oracle
### /private/tmp/kodezart-v03-m4-ruling-source-independent | DETACHED | 783edbf | dirty=0 | last=2026-09-13 Extract shared ruling and immutable source contracts for M4
### /private/tmp/kodezart-v03-m4-write-back-settings-independent | DETACHED | 1020f0d | dirty=0 | last=2026-09-13 Extract explicit canonical write verification settings
### /private/tmp/kodezart-v03-m5-delivery-termination | codex/v03-m5-delivery-termination | 10c51a7 | dirty=75 | last=2026-09-13 test(workflow): restore native specification and CI observation controls
 M src/kodezart/adapters/github_api.py
 M src/kodezart/adapters/github_types.py
 M src/kodezart/adapters/linear_markers.py
 M src/kodezart/adapters/linear_mcp_tracker.py
 M src/kodezart/adapters/no_forge_delivery.py
 M src/kodezart/adapters/subprocess_git_service.py
 M src/kodezart/composition/engine.py
 M src/kodezart/core/config.py
 M src/kodezart/core/errors.py
 M src/kodezart/core/protocols.py
 M src/kodezart/domain/errors.py
 M src/kodezart/handlers/agent_handler.py
 M src/kodezart/types/domain/agent.py
 M src/kodezart/types/domain/branch.py
 M src/kodezart/types/domain/delivery.py
 M src/kodezart/types/domain/operation.py
 M src/kodezart/types/domain/run_state.py
 M src/kodezart/types/requests/agent.py
 M tests/chains/test_ralph_workflow.py
 M tests/domain/test_amendment.py
 M tests/fakes.py
 M tests/test_forge_origin_selection.py
?? src/kodezart/adapters/subprocess_check_chain.py
?? src/kodezart/chains/delivery_coordinator.py
?? src/kodezart/chains/lane_delivery.py
?? src/kodezart/chains/native_delivery.py
?? src/kodezart/composition/delivery.py
?? src/kodezart/composition/scope_runtime.py
?? src/kodezart/domain/check_chain.py
?? src/kodezart/services/lane_reports.py
 22 files changed, 916 insertions(+), 51 deletions(-)
### /private/tmp/kodezart-v03-m5-union-roster-independent-1a8 | DETACHED | 1a83163 | dirty=0 | last=2026-09-13 Revalidate retained union facts inside the head freshness boundary
### /private/tmp/kodezart-v03-m5-union-roster-independent-aef | DETACHED | aef9e78 | dirty=0 | last=2026-09-13 Compose complete retained scope lanes independently of dispatch readiness
### /private/tmp/kodezart-v03-marker-preflight-independent-review | DETACHED | be9717f | dirty=1 | last=2026-09-12 Validate ownership marker configuration before reading native records
?? tests/tracker/test_m1_scope_independent.py
### /private/tmp/kodezart-v03-native-address-independent | DETACHED | b84d43b | dirty=1 | last=2026-09-12 Retain actual native address and approval alias regression controls
?? tests/tracker/test_address_identity_peer.py
### /private/tmp/kodezart-v03-native-amended-corrective-independent | DETACHED | 62a86ad | dirty=0 | last=2026-09-12 fix(amendment): retain verified archive authority through publication
### /private/tmp/kodezart-v03-native-amended-independent-current | DETACHED | 4c6322f | dirty=2 | last=2026-09-12 Require strict native classification for description authority
?? tests/services/test_native_amendment_archive_independent.py
?? tests/services/test_native_amendment_payload_independent.py
### /private/tmp/kodezart-v03-native-gate-independent | DETACHED | 7300516 | dirty=0 | last=2026-09-12 Reuse native criterion reader and census nonretrying graphs
### /private/tmp/kodezart-v03-native-gate-parent-independent | DETACHED | 1f4296c | dirty=0 | last=2026-09-12 fix(amendment): retain verified archive authority through publication
### /private/tmp/kodezart-v03-native-identity-cancel-correction | codex/v03-native-identity-cancel-correction | c579eb9 | dirty=1 | last=2026-09-13 fix: settle native identity subprocesses on cancellation
?? tests/adapters/test_native_identity_process_original_peer.py
### /private/tmp/kodezart-v03-native-identity-cancel-probe | DETACHED | 3508980 | dirty=1 | last=2026-09-12 fix: release native workspaces when preparation fails
?? tests/adapters/test_native_identity_process_probe.py
### /private/tmp/kodezart-v03-native-prompt-independent-review | DETACHED | 49b4bff | dirty=0 | last=2026-09-12 Register native prompt inputs and align utility prompt metadata
### /private/tmp/kodezart-v03-native-read-address-root-review | DETACHED | b84d43b | dirty=0 | last=2026-09-12 Retain actual native address and approval alias regression controls
### /private/tmp/kodezart-v03-native-resume-cancel-independent | DETACHED | 3508980 | dirty=4 | last=2026-09-12 fix: release native workspaces when preparation fails
?? tests/chains/test_native_prepare_cancel_original_peer.py
?? tests/chains/test_native_prepare_cleanup_original_peer.py
?? tests/chains/test_native_resume_peer.py
?? tests/services/test_native_receipt_synchronized_peer.py
### /private/tmp/kodezart-v03-native-resume-independent | DETACHED | 4dd7b3e | dirty=5 | last=2026-09-12 fix: resume native writer phases through parent checkpoints
?? tests/chains/test_native_completed_writer_gap_peer.py
?? tests/chains/test_native_prepare_cancel_peer.py
?? tests/chains/test_native_prepare_cleanup_peer.py
?? tests/chains/test_native_resume_peer.py
?? tests/chains/test_native_unchanged_replay_peer.py
### /private/tmp/kodezart-v03-organize-composition-review | DETACHED | a6cde3b | dirty=1 | last=2026-09-12 Keep Organize halt identity scoped to its actual container
?? tests/integration/test_organize_scheduler_independent.py
### /private/tmp/kodezart-v03-organize-delivery-review | DETACHED | 5f31342 | dirty=1 | last=2026-09-12 fix(organize): require evidence appropriate to each halt cause
?? tests/integration/test_organize_halt_independent_identity.py
### /private/tmp/kodezart-v03-organize-graph-independent-review | DETACHED | f47a1c6 | dirty=2 | last=2026-09-12 Prepare admitted native issue graphs and stable split children through Organize
?? tests/chains/test_organize_graph_independent.py
?? tests/tracker/test_organize_graph_independent.py
### /private/tmp/kodezart-v03-organize-native-review | DETACHED | ee1d44b | dirty=1 | last=2026-09-12 fix(organize): bind writes to current authority and retain verifier evidence
?? tests/chains/test_organize_native_review_original.py
### /private/tmp/kodezart-v03-recovery-amendment-archive-guard | DETACHED | 62a86ad | dirty=0 | last=2026-09-12 fix(amendment): retain verified archive authority through publication
### /private/tmp/kodezart-v03-recovery-amendment-comment-before | DETACHED | d14254e | dirty=1 | last=2026-09-12 Require the matching mandate payload in completed audit report types
?? tests/tracker/test_comment_expected.py
### /private/tmp/kodezart-v03-recovery-audit-fixture-review | DETACHED | 556552c | dirty=1 | last=2026-09-12 fix(audit): require strict indices and covered mandate sources
?? tests/adapters/test_audit_fixture_base_independent.py
### /private/tmp/kodezart-v03-recovery-classification-before | DETACHED | 5ef89e2 | dirty=1 | last=2026-09-12 Register native prompt inputs and align utility prompt metadata
?? tests/tracker/test_classification_authority.py
### /private/tmp/kodezart-v03-recovery-organize-ci | DETACHED | 3aaad2d | dirty=0 | last=2026-09-12 Describe Organize output fields and align integrated contract checks
### /private/tmp/kodezart-v03-recovery-organize-graph | DETACHED | f47a1c6 | dirty=0 | last=2026-09-12 Prepare admitted native issue graphs and stable split children through Organize
### /private/tmp/kodezart-v03-recovery-organize-halt-types | DETACHED | 64d64b3 | dirty=0 | last=2026-09-12 fix(organize): require evidence appropriate to each halt cause
### /private/tmp/kodezart-v03-recovery-ruling-reader-before | DETACHED | 44fd521 | dirty=1 | last=2026-09-12 test(audit): bind temporary PR base to the actual repository
?? tests/tracker/test_ruling_reader_independent.py
### /private/tmp/kodezart-v03-recovery-ruling-reader-review | DETACHED | 03b99ac | dirty=0 | last=2026-09-12 Reject ambiguous native comment provenance across all readers
### /private/tmp/kodezart-v03-recovery-semantic-checkpoint-evidence | DETACHED | 2727ee6 | dirty=2 | last=2026-09-12 Require strict native classification for description authority
?? tests/chains/test_amendment_framework_resume_evidence.py
?? tests/chains/test_native_checkpoint_evidence.py
### /private/tmp/kodezart-v03-recovery-semantic-independent-review | DETACHED | 1c39736 | dirty=0 | last=2026-09-12 fix(native): retain worktree across incomplete persistence receipt
### /private/tmp/kodezart-v03-recovery-semantic-local-source-before | DETACHED | e94f825 | dirty=2 | last=2026-09-12 Register native prompt inputs and align utility prompt metadata
 M tests/services/test_applied_native_amendments.py
 M tests/services/test_native_amendments.py
 2 files changed, 110 insertions(+), 4 deletions(-)
### /private/tmp/kodezart-v03-rollup-review-native | DETACHED | 7d6208a | dirty=0 | last=2026-09-12 Keep parent completion out of merged-fire lifecycle writes
### /private/tmp/kodezart-v03-scope-review-delivery | DETACHED | 9d379f4 | dirty=1 | last=2026-09-12 Keep API event census aligned with scoped native streams
?? tests/integration/test_scope_runtime_independent_review.py
### /private/tmp/kodezart-v03-write-back-settings-independent-review | DETACHED | 64aa3bd | dirty=0 | last=2026-09-12 Give canonical write verification its own configured repair bound
```

## Appendix D — commit subjects for all 195 orphan heads (`merge-base(canonical)..head`, ≤15 each)
```
### 01a62d6aa  n=1  branches: codex/v03-recovery-gate-followup 
  01a62d6 2026-09-12 Reconcile remaining lifecycle and lease fixtures with current contracts
### 0363cc9fa  n=53  branches: codex/v03-m2-groom-rubric-correction 
  0363cc9 2026-09-13 Supply native Organize rubrics through validated prompt metadata
  e99c9d1 2026-09-13 Exercise Organize retry authority through independent native controls
  38956bb 2026-09-13 Retain Organize source authorization across native write retries
  9e387b4 2026-09-13 Remove duplicate merged scope fixture initialization
  37723cd 2026-09-13 Reconcile Organize with its scope bootstrap prerequisite
  268be40 2026-09-13 Integrate reviewed scope label and bootstrap prerequisite
  ddb8cde 2026-09-13 Extract shared scope labels, native approval reads and bootstrap
  fadf6ef 2026-09-13 test(organize): align native schema and surface inventories
  25377b4 2026-09-13 test(organize): migrate prompt and operation contract censuses
  4ecd7c7 2026-09-13 test(organize): supply explicit composition collaborators
  a15b334 2026-09-13 fix(organize): revalidate criterion retries and align author instructions
  73cc5de 2026-09-13 fix(config): deduplicate inherited write verification field
  8c3ccf5 2026-09-13 merge: align Organize extraction with maintained M4 prerequisites
  daae8df 2026-09-13 feat(organize): extract bounded native owner and tracker writes
  dddbcbb 2026-09-13 Compose reviewed M1 Git settings with M4 contracts
### 03b99acb7  n=2  branches: codex/v03-recovery-comment-provenance 
  03b99ac 2026-09-12 Reject ambiguous native comment provenance across all readers
  ba1050d 2026-09-12 Read issue rulings across their actual recorded lane markers
### 058dca9a1  n=1  branches: codex/v03-description-replay-mutants codex/v03-exact-description-replay 
  058dca9 2026-09-08 Make guarded description edits converge at an exact target
### 096a6ccab  n=3  branches: codex/v03-audit-detector-sweep 
  096a6cc 2026-09-08 test(audit): prove clean self rules and mixed-head refusal
  89e19c7 2026-09-08 feat(audit): run standing overclaim checks through native sweep
  1cfa58f 2026-09-08 feat(audit): assemble native scope requests and run read-only sweep
### 0a7ac0365  n=7  branches: codex/v03-audit-overclaims 
  0a7ac03 2026-09-08 fix(audit): refuse replaced Git objects in fresh sessions (KOD-514)
  d9418b4 2026-09-08 test(audit): falsify byte comparison and post-read source movement
  feb2b11 2026-09-08 test(audit): bind adoption evidence and document over-claim observations
  cc067db 2026-09-08 fix(git): preserve configured remote during cold bare clone
  006abe6 2026-09-08 test(audit): guard shared schema forwarding and source ownership (KOD-514)
  74f9710 2026-09-08 Add source-based over-claim judgments and native adoption byte checks
  4feb766 2026-09-08 feat(audit): share pinned source reads and owned fresh sessions (KOD-514)
### 0bc666dc9  n=3  branches: codex/kod-450-addressed-preloop-mutants 
  0bc666d 2026-09-08 test(scope): isolate undeclared repository refusal
  6288114 2026-09-08 fix(scope): require recorded-route operation authority
  841d37f 2026-09-08 feat(scope): prepare addressed tracker fires at their recorded head
### 0d46227cb  n=1  branches: codex/kod-338-admission-ownership codex/kod-338-admission-ownership-mutants 
  0d46227 2026-09-08 fix(organize): settle admission workspace ownership before cancellation (KOD-338)
### 0e30d784f  n=6  branches: codex/v03-artifact-read-independent 
  0e30d78 2026-09-12 Validate split identity response before final source consistency read
  15c3817 2026-09-12 Retain actual native address and approval alias regression controls
  9cc9061 2026-09-12 Normalize attested native approval identity before ancestry and family reads
  bd5511b 2026-09-12 Validate criterion parent response against its native address
  afe3095 2026-09-12 Refuse blank criterion membership mappings before tracker reads
  5a06928 2026-09-12 Require reported native fields in structured tracker artifacts
### 0f6e88c0f  n=6  branches: codex/v03-recovery-semantic-applied 
  0f6e88c 2026-09-12 WIP preserve applied native amendment draft before dependency update
  d80e7fe 2026-09-12 Reject ambiguous native comment provenance across all readers
  1c39736 2026-09-12 fix(native): retain worktree across incomplete persistence receipt
  2e7fdd9 2026-09-12 Preserve native cancellation, publication authority and genuine evaluation history
  d93f3e9 2026-09-12 Add native semantic judgment and precommit guard review candidate
  515724a 2026-09-12 Read issue rulings across their actual recorded lane markers
### 1020f0de6  n=31  branches: codex/v03-m4-write-back-settings-extraction 
  1020f0d 2026-09-13 Extract explicit canonical write verification settings
  25c32f6 2026-09-13 Format classification fake prerequisite
  a7b1893 2026-09-12 Extract configured classification and durable escalation prerequisites
  6ccd95d 2026-09-12 Supply classification writer contracts for the scoped escalation extraction
  2672623 2026-09-12 Compose reviewed logging and cancellation-oracle prerequisites
  241e85c 2026-09-12 Synchronize the dispatch cancellation oracle at its required entry boundary
  ef2012c 2026-09-12 Extract safe traceback rendering and verify configured MCP reopen
  139391d 2026-09-12 Preserve independent native read and local SDK verifier probes
  8a4fec9 2026-09-12 Compose reviewed current M1 session and gate prerequisites
  873855e 2026-09-12 Restore scheduled gate windows when observation is cancelled
  27681a4 2026-09-12 Document domain session policy and preserved HTTP vocabulary
  b91b1f8 2026-09-12 Validate the fallback base before typed workflow submission
  3b489fb 2026-09-12 Extract typed session permissions and SDK tool policy into M1
  8930fcb 2026-09-12 Consume shared typed evaluation policy in the fresh write-back judge
  798bdd3 2026-09-12 Document domain session policy and preserved HTTP vocabulary
### 1088e18fb  n=1  branches: codex/v03-recovery-gate-reconciliation 
  1088e18 2026-09-12 Reconcile repository gate fixtures and protocol documentation
### 179ef5acf  n=2  branches: codex/v03-recovery-alarm-owner 
  179ef5a 2026-09-12 fix(alarms): validate the exact record snapshot before upsert
  76125c3 2026-09-12 feat(alarms): persist typed observations by complete native address
### 196e8b992  n=1  branches: codex/v03-node-event-review 
  196e8b9 2026-09-08 feat(run): validate the complete run-event table before tracker boot
### 19a03d96c  n=1  branches: codex/kod-470-public-docs 
  19a03d9 2026-09-08 State documentation behavior without private tracker citations
### 19e5d870d  n=7  branches: codex/v03-recovery-native-fire 
  19e5d87 2026-09-12 fix(fire): bind resumed effects and fresh judgments to current checks
  10fa10d 2026-09-12 fix(fire): carry native specs through live execution and replay
  d785dfd 2026-09-12 feat(fire): the criteria a native fire owes come from the tracker's own spec read
  9dd5d5e 2026-09-12 feat(fire): an execution-only fire composition that re-validates its subtree's Todo criteria before the loop
  abfb0c8 2026-09-12 test(fakes): drop the fixture the two-partition gate left behind
  0fefea7 2026-09-12 docs(changelog): record the criterion class's removal from the wire
  caa1c3c 2026-09-12 refactor(criteria): delete the criterion class
### 1c20be4f2  n=1  branches: codex/v03-criterion-read-boot 
  1c20be4 2026-09-08 feat(tracker): require criterion reads before startup (KOD-465)
### 1c397368e  n=4  branches: codex/v03-recovery-semantic-writeback 
  1c39736 2026-09-12 fix(native): retain worktree across incomplete persistence receipt
  2e7fdd9 2026-09-12 Preserve native cancellation, publication authority and genuine evaluation history
  d93f3e9 2026-09-12 Add native semantic judgment and precommit guard review candidate
  515724a 2026-09-12 Read issue rulings across their actual recorded lane markers
### 1cfa58f1a  n=1  branches: codex/v03-audit-sweep-native 
  1cfa58f 2026-09-08 feat(audit): assemble native scope requests and run read-only sweep
### 1dce04381  n=13  branches: codex/kod-514-detector-removal 
  1dce043 2026-09-08 test(audit): verify exact detached acquisition arguments (KOD-514)
  4b56102 2026-09-08 feat(audit): observe removed mechanisms that lose detection (KOD-514)
  005e7c6 2026-09-08 fix(audit): refuse replaced Git objects in fresh sessions (KOD-514)
  1dd6d8b 2026-09-08 Keep authored criterion pattern guard specific to authored identity
  f4f5ba3 2026-09-08 Preserve native criterion identity types and retained outcome guard
  5a12407 2026-09-08 test(audit): guard shared schema forwarding and source ownership (KOD-514)
  bab98b1 2026-09-08 feat(audit): share pinned source reads and owned fresh sessions (KOD-514)
  cc11f4e 2026-09-08 Settle terminal cache acquisition before cancelled return
  f4f5cb7 2026-09-08 fix(fire): own feasibility cache acquisition through cancellation (KOD-398)
  77e37bc 2026-09-08 fix(audit): settle native repository reads before cancellation (KOD-500)
  4562d97 2026-09-08 test(audit): distinguish valid lane-record replacement from unreadability (KOD-520)
  d8b9195 2026-09-08 feat(audit): read recorded Evidence against the live branch head (KOD-520)
  abeca25 2026-09-08 Observe actual review-terminal branch and native PR consistency
### 1ded74256  n=3  branches: codex/v03-workspace-url-mutants 
  1ded742 2026-09-08 docs(config): preserve workspace URL defaults in environment example
  a0d06d0 2026-09-08 test(gate): reject lookalike provider hosts in URL defaults
  7b0fa11 2026-09-08 fix(gate): redact native workspace URLs with existing tracker patterns
### 1ecc99ed0  n=5  branches: codex/v03-configured-fire-mutations 
  1ecc99e 2026-09-08 fix(scope): retain complete team identity through preparation
  0af27e9 2026-09-08 test(scope): isolate undeclared repository refusal
  3c418d7 2026-09-08 feat(scope): prepare configured first-entry routes from native facts
  55f8436 2026-09-08 fix(scope): require recorded-route operation authority
  841d37f 2026-09-08 feat(scope): prepare addressed tracker fires at their recorded head
### 1f722c153  n=1  branches: codex/dispatch-budget-fixture codex/pass-budget-mutants 
  1f722c1 2026-09-08 fix(passes): rearm gate reads cancelled before dispatch (KOD-164)
### 1fd931783  n=2  branches: codex/kod-637-addressed-ruling-entry 
  1fd9317 2026-09-08 Account for prior workspace release in entry cancellation proof
  4d3cc67 2026-09-08 Compose addressed FIRE preparation with fresh ruling proposals
### 204e5a030  n=4  branches: codex/v03-audit-forge-sweep codex/v03-audit-forge-sweep-mutants 
  204e5a0 2026-09-08 feat(audit): compose exact-SHA forge checks into native sweeps
  75c4856 2026-09-08 test(audit): prove clean self rules and mixed-head refusal
  89e19c7 2026-09-08 feat(audit): run standing overclaim checks through native sweep
  1cfa58f 2026-09-08 feat(audit): assemble native scope requests and run read-only sweep
### 20ac47f58  n=2  branches: codex/v03-github-settings 
  20ac47f 2026-09-08 Preserve historical GitHub names in the versioned migration guide
  fee39c6 2026-09-08 Group GitHub deployment settings at HTTP and Git consumers
### 2298e0d26  n=1  branches: codex/v03-http-dependencies-before 
  2298e0d 2026-09-08 Correct the documented immediate scope refusal
### 23a375710  n=2  branches: codex/kod-510-assertion-drift codex/kod-510-assertion-drift-mutants 
  23a3757 2026-09-08 test(audit): preserve literal Git magic in protected source paths (KOD-510)
  3520d6a 2026-09-08 feat(audit): detect protected assertion changes from pinned Git objects (KOD-510)
### 26182720e  n=11  branches: codex/v03-recovery-native-resume-cancel 
  2618272 2026-09-12 test: seed saved outcome from the reconciled prior grade
  a198da3 2026-09-12 fix: preserve cancellation across native phase boundaries
  4dd7b3e 2026-09-12 fix: resume native writer phases through parent checkpoints
  62a86ad 2026-09-12 fix(amendment): retain verified archive authority through publication
  4c6322f 2026-09-12 Require strict native classification for description authority
  8f80bb3 2026-09-12 Format independent classification regression probes
  8cd8feb 2026-09-12 Require known native classification before selecting write authority
  ddfd79f 2026-09-12 Implement verified native amendments before harness persistence
  c3e821e 2026-09-12 WIP connect native amendment graph and preserve tested write ordering
  182e698 2026-09-12 WIP preserve applied native amendment draft before dependency update
  fe6aa2f 2026-09-12 Honor criterion surface ownership for classification writes
### 2723b7919  n=1  branches: codex/v03-ruling-prompt codex/v03-ruling-prompt-mutations 
  2723b79 2026-09-08 feat(prompts): register the evaluative fire-time ruling role
### 2727ee671  n=12  branches: codex/v03-recovery-semantic-classification-corrective 
  2727ee6 2026-09-12 Require strict native classification for description authority
  5cafa69 2026-09-12 Require known native classification before selecting write authority
  9456614 2026-09-12 Honor criterion surface ownership for classification writes
  db9d370 2026-09-12 Implement verified native amendments before harness persistence
  e94f825 2026-09-12 Register native prompt inputs and align utility prompt metadata
  5347e9b 2026-09-12 fix(tracker): retain escalation label authority across retries
  2d463a2 2026-09-12 WIP connect native amendment graph and preserve tested write ordering
  f7c8b1f 2026-09-12 fix(tracker): refresh protected comment checks on unsent retries
  510cb36 2026-09-12 Give canonical write verification its own configured repair bound
  4c89eae 2026-09-12 fix(tracker): bind comment amendments to final native snapshot
  d553170 2026-09-12 Resolve initial issue state by its validated native team identity
  b92708a 2026-09-12 WIP preserve applied native amendment draft before dependency update
### 289ad5104  n=7  branches: codex/kod-513-record-collector 
  289ad51 2026-09-08 feat(supervisor): collect commit consistency from the live lane record (KOD-513)
  c3ef39d 2026-09-08 docs(records): state the declared collection mutability (KOD-683)
  975532d 2026-09-08 test(records): verify complete reads and document runtime limits (KOD-683)
  2682a70 2026-09-08 fix(records): preserve explicit absence for optional refs (KOD-679)
  298c573 2026-09-08 feat(records): reconstruct lanes through addressed tracker reads (KOD-683)
  26c1263 2026-09-08 feat(records): append explicit role-aware re-entry guidance (KOD-686)
  b0971ca 2026-09-08 feat(records): model and render lane branch facts (KOD-679)
### 294b2e0b7  n=2  branches: codex/v03-pagination-implementation 
  294b2e0 2026-09-08 Make cancellation regression reject swallowed cancellation promptly
  f5f87fc 2026-09-08 Share cursor progression across native tracker and record readers
### 2adc8c8d6  n=2  branches: codex/v03-audit-combined-review 
  2adc8c8 2026-09-08 feat(audit): complete native terminal mandates through the shared hunt
  db91511 2026-09-08 test(audit): exercise combined native detector lifetimes
### 2b546f712  n=3  branches: codex/v03-repository-read-ownership 
  2b546f7 2026-09-08 fix(fire): own feasibility cache acquisition through cancellation (KOD-398)
  badaa2b 2026-09-08 fix(audit): settle native repository reads before cancellation (KOD-500)
  96c9fd9 2026-09-08 feat(audit): read recorded Evidence against the live branch head (KOD-520)
### 2bedc1a3c  n=1  branches: codex/v03-http-mcp-lifecycle 
  2bedc1a 2026-09-08 test(mcp): isolate lifecycle assertions from traceback rendering (KOD-300)
### 2c7bd233d  n=2  branches: codex/v03-recovery-amendment-comment-retry 
  2c7bd23 2026-09-12 fix(tracker): refresh protected comment checks on unsent retries
  9136d9f 2026-09-12 fix(tracker): bind comment amendments to final native snapshot
### 2d389e5e1  n=1  branches: codex/kod-424-fire-entry-mutants codex/kod-424-fire-spec-approval 
  2d389e5 2026-09-08 Require current completion and approval at FireSpec read
### 2e6baba4a  n=1  branches: codex/v03-recovery-port-failures codex/v03-recovery-wire-boundary 
  2e6baba 2026-09-12 refactor(adapters): own vendor wire schemas at their boundaries
### 2e7fdd9b5  n=3  branches: codex/v03-recovery-semantic-amended 
  2e7fdd9 2026-09-12 Preserve native cancellation, publication authority and genuine evaluation history
  d93f3e9 2026-09-12 Add native semantic judgment and precommit guard review candidate
  515724a 2026-09-12 Read issue rulings across their actual recorded lane markers
### 3123e235e  n=2  branches: codex/v03-tool-presets 
  3123e23 2026-09-08 Keep application tool bundles neutral at the SDK boundary
  fb75e2e 2026-09-08 Update native boot and phase fixtures for retired configuration inputs
### 3135131ab  n=3  branches: codex/v03-recovery-criterion-label-surface 
  3135131 2026-09-12 Format independent classification regression probes
  ce2751e 2026-09-12 Require known native classification before selecting write authority
  246da86 2026-09-12 Honor criterion surface ownership for classification writes
### 31bb28982  n=6  branches: codex/v03-recovery-graph-root-review codex/v03-recovery-organize-graph-correction 
  31bb289 2026-09-12 Retry graph mutations only after fresh authorization and before receipt
  e687c76 2026-09-12 fix(tracker): refresh protected comment checks on unsent retries
  645d032 2026-09-12 Validate graph and split observations before issuing native writes
  3ffc89e 2026-09-12 fix(tracker): bind comment amendments to final native snapshot
  fc34117 2026-09-12 Prepare admitted native issue graphs and stable split children through Organize
  964b0f3 2026-09-12 Resolve initial issue state by its validated native team identity
### 335f9bd28  n=2  branches: codex/v03-milestone-metadata codex/v03-milestone-metadata-mutants 
  335f9bd 2026-09-08 test(scope): retain tool argument checks for optional milestone URLs
  d0b8eb6 2026-09-08 fix(scope): honor the optional milestone URL contract
### 350898010  n=12  branches: codex/v03-recovery-native-resume-prepare 
  3508980 2026-09-12 fix: release native workspaces when preparation fails
  2618272 2026-09-12 test: seed saved outcome from the reconciled prior grade
  a198da3 2026-09-12 fix: preserve cancellation across native phase boundaries
  4dd7b3e 2026-09-12 fix: resume native writer phases through parent checkpoints
  62a86ad 2026-09-12 fix(amendment): retain verified archive authority through publication
  4c6322f 2026-09-12 Require strict native classification for description authority
  8f80bb3 2026-09-12 Format independent classification regression probes
  8cd8feb 2026-09-12 Require known native classification before selecting write authority
  ddfd79f 2026-09-12 Implement verified native amendments before harness persistence
  c3e821e 2026-09-12 WIP connect native amendment graph and preserve tested write ordering
  182e698 2026-09-12 WIP preserve applied native amendment draft before dependency update
  fe6aa2f 2026-09-12 Honor criterion surface ownership for classification writes
### 36b18f5e5  n=4  branches: codex/v03-audit-workspace-corrective 
  36b18f5 2026-09-12 Preserve programmer failures during workspace acquisition
  536d704 2026-09-12 Preserve operational versus programmer failure boundaries in Audit
  5a3431f 2026-09-12 Require actual audit classification reads before queue startup
  68a58c7 2026-09-12 Compose configured native audit with verified publication and coverage receipts
### 372fb93a8  n=3  branches: codex/v03-domain-permissions 
  372fb93 2026-09-08 Keep live probe inputs on explicit permission boundaries
  481c9ba 2026-09-08 Check retry and provenance guards at active phase owners
  3373bba 2026-09-08 Translate domain permission modes at HTTP and SDK boundaries
### 387078509  n=6  branches: codex/v03-empty-criteria-entry codex/v03-empty-criteria-mutations 
  3870785 2026-09-08 test(fire): prove empty criteria refuse before loop dispatch (KOD-710)
  975532d 2026-09-08 test(records): verify complete reads and document runtime limits (KOD-683)
  2682a70 2026-09-08 fix(records): preserve explicit absence for optional refs (KOD-679)
  298c573 2026-09-08 feat(records): reconstruct lanes through addressed tracker reads (KOD-683)
  26c1263 2026-09-08 feat(records): append explicit role-aware re-entry guidance (KOD-686)
  b0971ca 2026-09-08 feat(records): model and render lane branch facts (KOD-679)
### 38a8638fb  n=4  branches: codex/v03-delivery-stalled codex/v03-delivery-stalled-mutants 
  38a8638 2026-09-08 feat(delivery): publish recorded stalled work through the common route
  148c4d7 2026-09-08 test(delivery): require the original failed-check read at zero reruns
  42a0eda 2026-09-08 fix(delivery): retain one failing-check reader across recovery
  ac5e5d7 2026-09-08 feat(delivery): recover runner flakes at the observed commit
### 38e848b2f  n=10  branches: codex/v03-recovery-audit-preflight 
  38e848b 2026-09-12 Require actual audit classification reads before queue startup
  0db8d05 2026-09-12 Format independent classification regression probes
  dd11ffd 2026-09-12 Compose configured native audit with verified publication and coverage receipts
  080fa6c 2026-09-12 Require known native classification before selecting write authority
  8f04db9 2026-09-12 Honor criterion surface ownership for classification writes
  ab75f03 2026-09-12 Register native prompt inputs and align utility prompt metadata
  b4b8601 2026-09-12 fix(tracker): retain escalation label authority across retries
  3009435 2026-09-12 fix(tracker): refresh protected comment checks on unsent retries
  60fab18 2026-09-12 fix(tracker): bind comment amendments to final native snapshot
  6c9c34c 2026-09-12 Give canonical write verification its own configured repair bound
### 39990202b  n=1  branches: codex/v03-current-union codex/v03-current-union-mutants 
  3999020 2026-09-08 feat(union): recheck current lane heads before reporting scope results
### 3a288c514  n=4  branches: codex/v03-write-back-verifier 
  3a288c5 2026-09-08 Publish cancellation probe identity atomically before observing it
  240e7e3 2026-09-08 Keep audit and write-back workspaces until native Git reads settle
  2e5cf42 2026-09-08 Settle owned Git workspace reads before propagating cancellation
  b7c43f9 2026-09-08 Add bounded native write-back verification with fresh read-only sessions
### 3def3ece1  n=5  branches: codex/v03-job-queue-settings 
  3def3ec 2026-09-08 Pass focused queue settings to the composed job lifecycle
  debfbf6 2026-09-08 Keep subprocess cleanup cadence at its owner and inject only the command timeout
  7b58c74 2026-09-08 Remove unconsumed ORGANIZE round settings and refuse retired inputs
  6745593 2026-09-08 Reject trailing-underscore configuration typos without prefix exemptions
  c196867 2026-09-08 Validate documentation against nested settings and exact migration names
### 3df7469e4  n=7  branches: codex/v03-recovery-surface-owner 
  3df7469 2026-09-12 fix(config): refuse the retired surface lease duration key
  e179701 2026-09-12 Validate terminal marker configuration when constructing the writer
  82c3d20 2026-09-12 Acquire explicit fixture leases for protected marker writes
  0e503a5 2026-09-12 Require declared marker ownership across the tracker port and fake
  46e02d0 2026-09-12 Own terminal outcome comment surfaces under their queued job ids
  1449e27 2026-09-12 Refuse protected record replacement under another writer attribution
  6e7a272 2026-09-12 Type loss of a run surface lease without inventing holder identity
### 4040e2960  n=2  branches: codex/kod-637-entry-mutations 
  4040e29 2026-09-08 Account for prior workspace release in entry cancellation proof
  4d3cc67 2026-09-08 Compose addressed FIRE preparation with fresh ruling proposals
### 40b250c5c  n=8  branches: codex/v03-m4-verifier-extraction 
  40b250c 2026-09-12 Extract canonical shared write-back verifier and fresh judge
  57dcc8c 2026-09-12 Complete M1 configured marker prompt and fixture closure
  d6e40fd 2026-09-12 Extract configured surface leases and run-owned terminal writes for M1
  b0def20 2026-09-12 Use a synthetic UUID in the scope alias fixture
  067b773 2026-09-12 Refuse mismatched canonical scope ancestor identities
  a2ee4c6 2026-09-12 Carry the existing priority wire census with its adapter relocation
  1811397 2026-09-12 Extract current scope read ports and native adapter boundaries for M1
  3c477a3 2026-09-12 Extract M1 scope addresses and surface lease value contracts
### 417ceecab  n=1  branches: codex/v03-recovery-alarm-payload-fixture 
  417ceec 2026-09-12 Update alarm payload oracle for typed evidence values
### 42c58b10f  n=2  branches: codex/v03-audit-coverage 
  42c58b1 2026-09-08 fix(audit): cover before a nondivisible cadence deadline (KOD-537)
  a543254 2026-09-08 feat(audit): cover deltas and periodic full snapshots (KOD-537)
### 47d39b3d9  n=1  branches: codex/v03-owned-resource-mechanics codex/v03-owned-resource-mutations 
  47d39b3 2026-09-08 Share owned operation settlement and read-only workspace lifetimes
### 49aed9271  n=8  branches: codex/kod-486-aggregate-mutations 
  49aed92 2026-09-08 Reject trailing-underscore configuration typos without prefix exemptions
  63f9a12 2026-09-08 Validate documentation against nested settings and exact migration names
  1738494 2026-09-08 Preserve audit payload boundary and prove authored policy separation
  551dcc1 2026-09-08 Require authored tracker aggregate judgment before durable writes
  238a319 2026-09-08 test(privacy): retain typed facts in composed retry fixture
  881017e 2026-09-08 refactor(adapters): share bounded retry policy and backoff
  03cafd8 2026-09-08 fix(privacy): classify rendered Markdown reference authorities
  b0b3bd4 2026-09-08 feat(privacy): classify native references from deployment facts
### 49b4bffa6  n=1  branches: codex/v03-recovery-native-prompt-census 
  49b4bff 2026-09-12 Register native prompt inputs and align utility prompt metadata
### 4ae496cd3  n=4  branches: codex/v03-self-write-attribution 
  4ae496c 2026-09-08 fix: bound receipt retention and wake on expired history
  3bb4ea2 2026-09-08 test: distinguish own label additions from principal labels
  67bc094 2026-09-08 test: prove native movement snapshot bounds directly
  6cc3195 2026-09-08 fix: replay explicit self-write receipts in scheduled gates
### 4bb47e5c4  n=13  branches: codex/kod-485-outbound-admission 
  4bb47e5 2026-09-08 Exercise mandatory branch judgment for both provenance classes
  3e5f6ef 2026-09-08 Replace configurable scanners with fixed outbound admission
  d46b329 2026-09-08 Retire aggregate prose patterns after authored admission migration
  cb6dba0 2026-09-08 Keep subprocess cleanup cadence at its owner and inject only the command timeout
  3517402 2026-09-08 Remove unconsumed ORGANIZE round settings and refuse retired inputs
  fd25528 2026-09-08 Reject trailing-underscore configuration typos without prefix exemptions
  f075ab6 2026-09-08 Validate documentation against nested settings and exact migration names
  ec8ac51 2026-09-08 Preserve audit payload boundary and prove authored policy separation
  551dcc1 2026-09-08 Require authored tracker aggregate judgment before durable writes
  238a319 2026-09-08 test(privacy): retain typed facts in composed retry fixture
  881017e 2026-09-08 refactor(adapters): share bounded retry policy and backoff
  03cafd8 2026-09-08 fix(privacy): classify rendered Markdown reference authorities
  b0b3bd4 2026-09-08 feat(privacy): classify native references from deployment facts
### 4c6322fb1  n=7  branches: codex/v03-recovery-native-amended-root-review 
  4c6322f 2026-09-12 Require strict native classification for description authority
  8f80bb3 2026-09-12 Format independent classification regression probes
  8cd8feb 2026-09-12 Require known native classification before selecting write authority
  ddfd79f 2026-09-12 Implement verified native amendments before harness persistence
  c3e821e 2026-09-12 WIP connect native amendment graph and preserve tested write ordering
  182e698 2026-09-12 WIP preserve applied native amendment draft before dependency update
  fe6aa2f 2026-09-12 Honor criterion surface ownership for classification writes
### 4cf792972  n=3  branches: codex/v03-audit-claim-session 
  4cf7929 2026-09-08 fix(audit): settle owned workspaces through repeated cancellation
  749d1c2 2026-09-08 test(audit): verify executor forwarding and independent error refusal
  573e192 2026-09-08 feat(audit): verify current criterion claims in fresh head sessions (KOD-523)
### 4dd7b3eb4  n=9  branches: codex/v03-recovery-native-resume 
  4dd7b3e 2026-09-12 fix: resume native writer phases through parent checkpoints
  62a86ad 2026-09-12 fix(amendment): retain verified archive authority through publication
  4c6322f 2026-09-12 Require strict native classification for description authority
  8f80bb3 2026-09-12 Format independent classification regression probes
  8cd8feb 2026-09-12 Require known native classification before selecting write authority
  ddfd79f 2026-09-12 Implement verified native amendments before harness persistence
  c3e821e 2026-09-12 WIP connect native amendment graph and preserve tested write ordering
  182e698 2026-09-12 WIP preserve applied native amendment draft before dependency update
  fe6aa2f 2026-09-12 Honor criterion surface ownership for classification writes
### 4f48afe6d  n=3  branches: codex/kod-380-scope-approval 
  4f48afe 2026-09-08 fix(tracker): refuse incomplete positive project membership (KOD-380)
  bfd696d 2026-09-08 test(tracker): prove milestone scopes inherit project approval (KOD-382)
  fa456f5 2026-09-08 feat(tracker): resolve live scope approval ancestry (KOD-380)
### 4f96b46aa  n=1  branches: codex/v03-event-model-conformance 
  4f96b46 2026-09-08 Exercise model event agreement against the deployed table
### 51bc8f9d0  n=3  branches: codex/kod-503-scope-tally-mutants 
  51bc8f9 2026-09-08 test: isolate scope tally identity and duplicate roster guards
  c5dff46 2026-09-08 feat: observe scope phase marker barriers from native rosters
  3fbd5da 2026-09-08 feat(tracker): require complete issue classification for scope readiness (KOD-425)
### 51d83d55b  n=1  branches: codex/v03-pr-content-mutants codex/v03-pr-content-replay 
  51d83d5 2026-09-08 feat(delivery): edit existing PR content on replay (KOD-95)
### 51e45d2fd  n=1  branches: codex/v03-logging-test-isolation 
  51e45d2 2026-09-08 Restore logging state after the native traceback test
### 535351e12  n=3  branches: codex/v03-knowledge-settings 
  535351e 2026-09-08 Reject retired knowledge secret files and prove credential exclusion
  e464ccc 2026-09-08 Carry typed knowledge settings to sessions and recorder
  c5817a2 2026-09-08 ci: verify the lock with a pinned uv release
### 536d704aa  n=3  branches: codex/v03-audit-root-integration-review 
  536d704 2026-09-12 Preserve operational versus programmer failure boundaries in Audit
  5a3431f 2026-09-12 Require actual audit classification reads before queue startup
  68a58c7 2026-09-12 Compose configured native audit with verified publication and coverage receipts
### 53b3ca89e  n=2  branches: codex/v03-recovery-native-state-resolver codex/v03-recovery-state-resolver-review 
  53b3ca8 2026-09-12 Resolve initial issue state by its validated native team identity
  68187b0 2026-09-12 Read issue rulings across their actual recorded lane markers
### 5595af10f  n=2  branches: codex/v03-audit-evidence 
  5595af1 2026-09-08 test(audit): distinguish valid lane-record replacement from unreadability (KOD-520)
  96c9fd9 2026-09-08 feat(audit): read recorded Evidence against the live branch head (KOD-520)
### 57dcc8c3a  n=7  branches: codex/v03-m1-lease-root-review 
  57dcc8c 2026-09-12 Complete M1 configured marker prompt and fixture closure
  d6e40fd 2026-09-12 Extract configured surface leases and run-owned terminal writes for M1
  b0def20 2026-09-12 Use a synthetic UUID in the scope alias fixture
  067b773 2026-09-12 Refuse mismatched canonical scope ancestor identities
  a2ee4c6 2026-09-12 Carry the existing priority wire census with its adapter relocation
  1811397 2026-09-12 Extract current scope read ports and native adapter boundaries for M1
  3c477a3 2026-09-12 Extract M1 scope addresses and surface lease value contracts
### 5ccb7b97c  n=7  branches: codex/v03-audit-terminal-consistency 
  5ccb7b9 2026-09-08 Assert terminal admission and unresolved native PR facts
  cc11f4e 2026-09-08 Settle terminal cache acquisition before cancelled return
  f4f5cb7 2026-09-08 fix(fire): own feasibility cache acquisition through cancellation (KOD-398)
  77e37bc 2026-09-08 fix(audit): settle native repository reads before cancellation (KOD-500)
  4562d97 2026-09-08 test(audit): distinguish valid lane-record replacement from unreadability (KOD-520)
  d8b9195 2026-09-08 feat(audit): read recorded Evidence against the live branch head (KOD-520)
  abeca25 2026-09-08 Observe actual review-terminal branch and native PR consistency
### 5cee6db41  n=6  branches: codex/v03-audit-mandate-hunt 
  5cee6db 2026-09-08 Keep unreadable audit coverage grounded in native read failures
  9a0e11a 2026-09-08 Publish cancellation probe identity atomically before observing it
  4df53a9 2026-09-08 Complete audit refutations with native source mandate judgments
  ae7bb12 2026-09-08 Keep audit and write-back workspaces until native Git reads settle
  2bed514 2026-09-08 Settle owned Git workspace reads before propagating cancellation
  b7c43f9 2026-09-08 Add bounded native write-back verification with fresh read-only sessions
### 5e4ec11e1  n=16  branches: codex/v03-m1-logging-root-review codex/v03-mcp-reopen-diagnosis 
  5e4ec11 2026-09-12 Extract safe traceback rendering and verify configured MCP reopen
  873855e 2026-09-12 Restore scheduled gate windows when observation is cancelled
  27681a4 2026-09-12 Document domain session policy and preserved HTTP vocabulary
  b91b1f8 2026-09-12 Validate the fallback base before typed workflow submission
  3b489fb 2026-09-12 Extract typed session permissions and SDK tool policy into M1
  926067d 2026-09-12 Preserve actual UUID alias and foreign parent regression controls
  9f184fc 2026-09-12 Validate criterion parent response against its native address
  0ff4e10 2026-09-12 Refuse blank criterion membership mappings before tracker reads
  ee9b44e 2026-09-12 Extract configured native criterion read boundary for M1
  a4575cd 2026-09-12 Complete M1 configured marker prompt and fixture closure
  c795bb6 2026-09-12 Extract configured surface leases and run-owned terminal writes for M1
  b0def20 2026-09-12 Use a synthetic UUID in the scope alias fixture
  067b773 2026-09-12 Refuse mismatched canonical scope ancestor identities
  a2ee4c6 2026-09-12 Carry the existing priority wire census with its adapter relocation
  1811397 2026-09-12 Extract current scope read ports and native adapter boundaries for M1
### 5fec92b88  n=4  branches: codex/v03-audit-state-history 
  5fec92b 2026-09-08 docs(tracker): keep native history contract source neutral
  a573c5a 2026-09-08 feat(audit): collect native state transitions with membership checks (KOD-537)
  42c58b1 2026-09-08 fix(audit): cover before a nondivisible cadence deadline (KOD-537)
  a543254 2026-09-08 feat(audit): cover deltas and periodic full snapshots (KOD-537)
### 616801e01  n=2  branches: codex/v03-scope-plan-mutations 
  616801e 2026-09-08 fix(scope): require reported planning labels and relations (KOD-450)
  aad38f3 2026-09-08 feat(scope): refuse live plan barriers before dispatch (KOD-450)
### 643c96ea6  n=1  branches: codex/v03-audit-gate-corrective 
  643c96e 2026-09-12 Keep audit outcome dependency in its runtime and align failure fixtures
### 64aa3bd1b  n=1  branches: codex/v03-recovery-write-back-settings 
  64aa3bd 2026-09-12 Give canonical write verification its own configured repair bound
### 64d60ef1c  n=2  branches: codex/kod-491-reference-mutations 
  64d60ef 2026-09-08 fix(privacy): classify rendered Markdown reference authorities
  a14bd5c 2026-09-08 feat(privacy): classify native references from deployment facts
### 65e2ed7a2  n=2  branches: codex/v03-native-initiative-mutations 
  65e2ed7 2026-09-08 Exercise native initiative guidance in composed grooming output
  a540e50 2026-09-08 Read initiative membership and dates from the tracker instead of config
### 67455934b  n=2  branches: codex/v03-nested-settings-docs 
  6745593 2026-09-08 Reject trailing-underscore configuration typos without prefix exemptions
  c196867 2026-09-08 Validate documentation against nested settings and exact migration names
### 67d6ab276  n=3  branches: codex/v03-forge-settings-and-live-tests 
  67d6ab2 2026-09-08 Preserve hermetic collection and explicit native live test inputs
  822439f 2026-09-08 Preserve historical GitHub names in the versioned migration guide
  431be85 2026-09-08 Group GitHub deployment settings at HTTP and Git consumers
### 687723f9c  n=5  branches: codex/kod-94-ruling-records 
  687723f 2026-09-08 test(rulings): recognize quoted and aliased string addresses (KOD-636)
  6f9350a 2026-09-08 feat(supervisor): collect native ruling authorship for growth observations (KOD-519)
  7c30856 2026-09-08 feat(rulings): read addressed occurrences through tracker comments (KOD-634)
  86354ad 2026-09-08 feat(rulings): render explicit authored ruling records (KOD-630)
  4d63c86 2026-09-08 feat(rulings): centralize deterministic ruling identity construction (KOD-636)
### 68eabb515  n=7  branches: codex/v03-recovery-organize-composition 
  68eabb5 2026-09-12 Keep Organize halt identity scoped to its actual container
  641797e 2026-09-12 fix(organize): bind writes to current authority and retain verifier evidence
  d52f0c7 2026-09-12 Compose configured Organize scheduling and preserve addressed halts
  161ca93 2026-09-12 Compose bounded Organize preparation and fresh write-back judgment
  fd8686d 2026-09-12 Reconcile remaining lifecycle and lease fixtures with current contracts
  835604a 2026-09-12 Keep parent completion out of merged-fire lifecycle writes
  d6127a2 2026-09-12 Define an explicit surface for criterion child creation
### 6f431bbaa  n=6  branches: codex/v03-artifact-read-final 
  6f431bb 2026-09-12 Validate split identity response before final source consistency read
  0111df9 2026-09-12 Retain actual native address and approval alias regression controls
  ca439f3 2026-09-12 Normalize attested native approval identity before ancestry and family reads
  0996962 2026-09-12 Validate criterion parent response against its native address
  4edb827 2026-09-12 Refuse blank criterion membership mappings before tracker reads
  da37bda 2026-09-12 Require reported native fields in structured tracker artifacts
### 6fb6ad11a  n=1  branches: codex/v03-delivery-read-ownership 
  6fb6ad1 2026-09-08 fix(delivery): settle native repository reads before cancellation (KOD-314)
### 730051660  n=1  branches: codex/v03-native-gate-corrective 
  7300516 2026-09-12 Reuse native criterion reader and census nonretrying graphs
### 763d7be11  n=4  branches: codex/v03-workspace-url-patterns 
  763d7be 2026-09-08 docs(gate): align pattern default reference with workspace URL protection
  1ded742 2026-09-08 docs(config): preserve workspace URL defaults in environment example
  a0d06d0 2026-09-08 test(gate): reject lookalike provider hosts in URL defaults
  7b0fa11 2026-09-08 fix(gate): redact native workspace URLs with existing tracker patterns
### 797931418  n=59  branches: codex/v03-m3-oracle-closure 
  7979314 2026-09-13 test(workflow): restore native specification and CI observation controls
  8fbde41 2026-09-13 Share coherent scope facts while preserving planning admission
  f9069e9 2026-09-13 Integrate current scope and Organize authority prerequisites into M3
  7675c0b 2026-09-13 fix(workflow): preserve authored HTTP and checkpoint compatibility
  cef0dff 2026-09-13 Inherit the shared run event vocabulary from M4
  e99c9d1 2026-09-13 Exercise Organize retry authority through independent native controls
  dad63c4 2026-09-13 feat(workflow): extract native and authored fire graphs with scope planning
  c855fb0 2026-09-13 Carry the shared run event vocabulary for native observers
  38956bb 2026-09-13 Retain Organize source authorization across native write retries
  9e387b4 2026-09-13 Remove duplicate merged scope fixture initialization
  37723cd 2026-09-13 Reconcile Organize with its scope bootstrap prerequisite
  268be40 2026-09-13 Integrate reviewed scope label and bootstrap prerequisite
  ddb8cde 2026-09-13 Extract shared scope labels, native approval reads and bootstrap
  fadf6ef 2026-09-13 test(organize): align native schema and surface inventories
  25377b4 2026-09-13 test(organize): migrate prompt and operation contract censuses
### 79a7a4624  n=2  branches: codex/v03-union-native-inputs codex/v03-union-replacement-mutants 
  79a7a46 2026-09-08 test(union): isolate the pinned conflict identity guard
  a229571 2026-09-08 fix(union): refuse substituted Git object identities (KOD-601)
### 79c333e60  n=3  branches: codex/v03-delivery-flake-mutants 
  79c333e 2026-09-08 fix(delivery): retain one failing-check reader across recovery
  e73b4d7 2026-09-08 feat(delivery): recover runner flakes at the observed commit
  51d83d5 2026-09-08 feat(delivery): edit existing PR content on replay (KOD-95)
### 7b58c746b  n=3  branches: codex/v03-config-reduction 
  7b58c74 2026-09-08 Remove unconsumed ORGANIZE round settings and refuse retired inputs
  6745593 2026-09-08 Reject trailing-underscore configuration typos without prefix exemptions
  c196867 2026-09-08 Validate documentation against nested settings and exact migration names
### 7d6208af8  n=1  branches: codex/v03-recovery-parent-rollup 
  7d6208a 2026-09-12 Keep parent completion out of merged-fire lifecycle writes
### 8167a2dc3  n=2  branches: codex/kod-491-typed-private-references 
  8167a2d 2026-09-08 fix(privacy): classify rendered Markdown reference authorities
  a14bd5c 2026-09-08 feat(privacy): classify native references from deployment facts
### 852623fc9  n=1  branches: codex/v03-cache-remote-name 
  852623f 2026-09-08 fix(git): preserve configured remote during cold bare clone
### 85fc8a3d2  n=1  branches: codex/v03-recovery-job-acceptance 
  85fc8a3 2026-09-12 fix(api): validate job acceptance and reverse reconnect routes
### 864bc0ab3  n=15  branches: codex/v03-m1-delta-cancel codex/v03-m1-delta-cancel-root-review 
  864bc0a 2026-09-12 Restore scheduled gate windows when observation is cancelled
  ea1d915 2026-09-12 Document domain session policy and preserved HTTP vocabulary
  1553604 2026-09-12 Validate the fallback base before typed workflow submission
  990652c 2026-09-12 Extract typed session permissions and SDK tool policy into M1
  fec7f28 2026-09-12 Preserve actual UUID alias and foreign parent regression controls
  fd19b9a 2026-09-12 Validate criterion parent response against its native address
  f3c3dd3 2026-09-12 Refuse blank criterion membership mappings before tracker reads
  6346911 2026-09-12 Extract configured native criterion read boundary for M1
  a4575cd 2026-09-12 Complete M1 configured marker prompt and fixture closure
  c795bb6 2026-09-12 Extract configured surface leases and run-owned terminal writes for M1
  b0def20 2026-09-12 Use a synthetic UUID in the scope alias fixture
  067b773 2026-09-12 Refuse mismatched canonical scope ancestor identities
  a2ee4c6 2026-09-12 Carry the existing priority wire census with its adapter relocation
  1811397 2026-09-12 Extract current scope read ports and native adapter boundaries for M1
  3c477a3 2026-09-12 Extract M1 scope addresses and surface lease value contracts
### 87eaf3813  n=6  branches: codex/v03-configured-fire-preparation 
  87eaf38 2026-09-08 docs(scope): describe both first-entry repository routes
  0451762 2026-09-08 fix(scope): retain complete team identity through preparation
  0af27e9 2026-09-08 test(scope): isolate undeclared repository refusal
  3c418d7 2026-09-08 feat(scope): prepare configured first-entry routes from native facts
  55f8436 2026-09-08 fix(scope): require recorded-route operation authority
  841d37f 2026-09-08 feat(scope): prepare addressed tracker fires at their recorded head
### 8d4714f47  n=4  branches: codex/v03-model-agreement 
  8d4714f 2026-09-08 test: bind model pointers to native target identities
  97b8fff 2026-09-08 test: resolve model pointers from queried tracker sources
  a80befc 2026-09-08 fix: hydrate URLs only for native document assets
  5f03605 2026-09-08 feat: read complete configured model membership
### 8ef12cd90  n=2  branches: codex/v03-criterion-lifecycle-identity 
  8ef12cd 2026-09-08 test(identity): resolve qualified local ruling aliases (KOD-598)
  8196442 2026-09-08 test(lifecycle): enforce shared criterion and ruling identities (KOD-598)
### 8f222ac2a  n=2  branches: codex/v03-retirement-census-fix 
  8f222ac 2026-09-08 Keep wire rename exclusions aligned with active outcome producers
  d3a1a51 2026-09-08 Correct the documented immediate scope refusal
### 8faf6f4b6  n=4  branches: codex/audit-replace-integrity codex/audit-replace-mutants 
  8faf6f4 2026-09-08 docs(audit): state original-tree claim requirement
  5fe43d9 2026-09-08 fix(audit): refuse substituted inline verification trees
  d5aa422 2026-09-08 fix(audit): reject substituted mandate workspaces
  696e426 2026-09-08 fix(audit): reject substituted objects in claim sessions
### 90aea37cc  n=4  branches: codex/v03-m1-lease-extraction 
  90aea37 2026-09-12 Extract configured surface leases and run-owned terminal writes for M1
  a2ee4c6 2026-09-12 Carry the existing priority wire census with its adapter relocation
  1811397 2026-09-12 Extract current scope read ports and native adapter boundaries for M1
  3c477a3 2026-09-12 Extract M1 scope addresses and surface lease value contracts
### 9136d9f31  n=1  branches: codex/v03-recovery-amendment-comment-current codex/v03-recovery-comment-independent 
  9136d9f 2026-09-12 fix(tracker): bind comment amendments to final native snapshot
### 92af9aa12  n=1  branches: codex/kod-470-source-prose 
  92af9aa 2026-09-08 Replace private source citations with their behavioral meaning
### 93f9e89af  n=2  branches: codex/v03-pagination-mutations 
  93f9e89 2026-09-08 Make cancellation regression reject swallowed cancellation promptly
  f5f87fc 2026-09-08 Share cursor progression across native tracker and record readers
### 975532d89  n=5  branches: codex/v03-lane-record-mutations 
  975532d 2026-09-08 test(records): verify complete reads and document runtime limits (KOD-683)
  2682a70 2026-09-08 fix(records): preserve explicit absence for optional refs (KOD-679)
  298c573 2026-09-08 feat(records): reconstruct lanes through addressed tracker reads (KOD-683)
  26c1263 2026-09-08 feat(records): append explicit role-aware re-entry guidance (KOD-686)
  b0971ca 2026-09-08 feat(records): model and render lane branch facts (KOD-679)
### 980b17965  n=54  branches: codex/v03-m2-groom-rubric-freshness 
  980b179 2026-09-13 Expose dynamic Organize rubric freshness and native binding failures
  0363cc9 2026-09-13 Supply native Organize rubrics through validated prompt metadata
  e99c9d1 2026-09-13 Exercise Organize retry authority through independent native controls
  38956bb 2026-09-13 Retain Organize source authorization across native write retries
  9e387b4 2026-09-13 Remove duplicate merged scope fixture initialization
  37723cd 2026-09-13 Reconcile Organize with its scope bootstrap prerequisite
  268be40 2026-09-13 Integrate reviewed scope label and bootstrap prerequisite
  ddb8cde 2026-09-13 Extract shared scope labels, native approval reads and bootstrap
  fadf6ef 2026-09-13 test(organize): align native schema and surface inventories
  25377b4 2026-09-13 test(organize): migrate prompt and operation contract censuses
  4ecd7c7 2026-09-13 test(organize): supply explicit composition collaborators
  a15b334 2026-09-13 fix(organize): revalidate criterion retries and align author instructions
  73cc5de 2026-09-13 fix(config): deduplicate inherited write verification field
  8c3ccf5 2026-09-13 merge: align Organize extraction with maintained M4 prerequisites
  daae8df 2026-09-13 feat(organize): extract bounded native owner and tracker writes
### 9b9008676  n=5  branches: codex/v03-recovery-port-contract 
  9b90086 2026-09-12 Migrate tracker boundary regressions to neutral port failures
  5b4ae92 2026-09-12 Keep pretty tracebacks from rendering live workflow locals
  219d132 2026-09-12 Translate tracker and record failures at adapter boundaries
  2574358 2026-09-12 Refuse protected record replacement under another writer attribution
  ff7fc9e 2026-09-12 Type loss of a run surface lease without inventing holder identity
### 9bbf67087  n=2  branches: codex/v03-criterion-record-fidelity 
  9bbf670 2026-09-08 Keep the criterion fidelity matrix total over native state kinds
  b082993 2026-09-08 Verify criterion record fidelity and return the stored fake update
### 9ed898951  n=2  branches: codex/kod-510-recorded-assertion-drift codex/kod-510-recorded-assertion-mutants 
  9ed8989 2026-09-08 Retain typed ruling identities in protection designations
  cd3ff06 2026-09-08 Consume ruling-owned test protection in assertion audits
### a2efabfde  n=5  branches: codex/kod-ruling-record-mutants 
  a2efabf 2026-09-08 test(rulings): recognize quoted and aliased string addresses (KOD-636)
  6f9350a 2026-09-08 feat(supervisor): collect native ruling authorship for growth observations (KOD-519)
  7c30856 2026-09-08 feat(rulings): read addressed occurrences through tracker comments (KOD-634)
  86354ad 2026-09-08 feat(rulings): render explicit authored ruling records (KOD-630)
  4d63c86 2026-09-08 feat(rulings): centralize deterministic ruling identity construction (KOD-636)
### a3a3089f5  n=1  branches: codex/v03-fire-writer-inventory 
  a3a3089 2026-09-08 test(fire): follow extracted PR writers in sanitization inventory
### a4e758bae  n=3  branches: codex/v03-tracker-settings 
  a4e758b 2026-09-08 Assert tracker token exclusion in serialized nested settings
  4d13c95 2026-09-08 Group native tracker settings at actual boot consumers
  2368eb7 2026-09-08 Update native boot and phase fixtures for retired configuration inputs
### a51d7779f  n=1  branches: codex/v03-recovery-classification-authority codex/v03-recovery-classification-root-review 
  a51d777 2026-09-12 fix(tracker): retain escalation label authority across retries
### a6b0383fa  n=3  branches: codex/kod-746-delivery-mutations 
  a6b0383 2026-09-08 Pin the native authored retry proof to an actual cleanup call
  a2391a4 2026-09-08 Move delivery check policy into the composed authored path and retire unused slices
  ed2423b 2026-09-08 Refuse unsupported scopes before preparation and retire unused stack
### a6db13095  n=8  branches: codex/v03-evidence-ancestry-integrity 
  a6db130 2026-09-08 fix(audit): reject replacement-only Evidence ancestry
  e6be6a6 2026-09-08 feat(audit): complete native terminal mandates through the shared hunt
  01086e2 2026-09-08 fix(audit): reject substituted mandate workspaces
  99555ca 2026-09-08 fix(audit): reject substituted objects in claim sessions
  e2866eb 2026-09-08 feat(audit): compose source-backed detector losses into sweep reports
  4b2327f 2026-09-08 test(audit): prove clean self rules and mixed-head refusal
  89e19c7 2026-09-08 feat(audit): run standing overclaim checks through native sweep
  1cfa58f 2026-09-08 feat(audit): assemble native scope requests and run read-only sweep
### a87996e79  n=1  branches: codex/v03-union-conflict-mutants codex/v03-union-conflict-remediation 
  a87996e 2026-09-08 fix(union): carry one remediation for native merge conflicts (KOD-593)
### aafb0d225  n=3  branches: codex/v03-escalation-review 
  aafb0d2 2026-09-08 fix(supervisor): verify the decision used for escalation age
  1decbab 2026-09-08 test(supervisor): check escalation identity at its native reader
  ef45d14 2026-09-08 feat(supervisor): collect escalation age from native lane records
### ac541892f  n=28  branches: codex/v03-m4-classification-shared-prerequisite 
  ac54189 2026-09-12 Supply classification writer contracts for the scoped escalation extraction
  2672623 2026-09-12 Compose reviewed logging and cancellation-oracle prerequisites
  241e85c 2026-09-12 Synchronize the dispatch cancellation oracle at its required entry boundary
  ef2012c 2026-09-12 Extract safe traceback rendering and verify configured MCP reopen
  139391d 2026-09-12 Preserve independent native read and local SDK verifier probes
  8a4fec9 2026-09-12 Compose reviewed current M1 session and gate prerequisites
  873855e 2026-09-12 Restore scheduled gate windows when observation is cancelled
  27681a4 2026-09-12 Document domain session policy and preserved HTTP vocabulary
  b91b1f8 2026-09-12 Validate the fallback base before typed workflow submission
  3b489fb 2026-09-12 Extract typed session permissions and SDK tool policy into M1
  8930fcb 2026-09-12 Consume shared typed evaluation policy in the fresh write-back judge
  798bdd3 2026-09-12 Document domain session policy and preserved HTTP vocabulary
  68b5738 2026-09-12 Validate the fallback base before typed workflow submission
  77a70ce 2026-09-12 Extract typed session permissions and SDK tool policy into M1
  2dbbaa1 2026-09-12 Extend shared verification to current native criteria and local sources
### ae3a8b814  n=2  branches: codex/v03-operation-concerns 
  ae3a8b8 2026-09-08 Exercise native initiative guidance in composed grooming output
  a540e50 2026-09-08 Read initiative membership and dates from the tracker instead of config
### aedb2f20b  n=7  branches: codex/v03-logging-settings 
  aedb2f2 2026-09-08 Group logging choices and reject unsupported severity names
  f3ca519 2026-09-08 Group HTTP settings and inject only the response prefix
  3def3ec 2026-09-08 Pass focused queue settings to the composed job lifecycle
  debfbf6 2026-09-08 Keep subprocess cleanup cadence at its owner and inject only the command timeout
  7b58c74 2026-09-08 Remove unconsumed ORGANIZE round settings and refuse retired inputs
  6745593 2026-09-08 Reject trailing-underscore configuration typos without prefix exemptions
  c196867 2026-09-08 Validate documentation against nested settings and exact migration names
### affc8ff7a  n=13  branches: codex/v03-recorded-landing 
  affc8ff 2026-09-08 test: preserve provenance checks for typed landing bindings
  5c71493 2026-09-08 docs: explain recorded landing base selection
  aca0888 2026-09-08 test: guard mapping-based landing values and native provenance
  a069e59 2026-09-08 test: reject dictionary landing reads in three-state guard
  c36c4cc 2026-09-08 fix(base): refuse ambiguous recorded landing inputs (KOD-715)
  081b3bd 2026-09-08 test(base): reject landing derivation paths (KOD-721)
  ffc2c08 2026-09-08 test(base): keep every landing read three-state (KOD-720)
  e70f598 2026-09-08 test(base): refuse unknown missing refs before dispatch (KOD-719)
  9bba8a2 2026-09-08 test(base): retain unknown landing dispatch records (KOD-718)
  5040f09 2026-09-08 test(base): select only unlanded mixed inputs (KOD-717)
  4215c44 2026-09-08 test(base): compare real squash and merge landings (KOD-716)
  c8bb493 2026-09-08 fix(base): exclude recorded landed blocker refs (KOD-715)
  2fcc437 2026-09-08 feat(tracker): carry recorded landing on work refs (KOD-714)
### b1393733a  n=2  branches: codex/v03-criterion-fidelity-mutations 
  b139373 2026-09-08 Keep the criterion fidelity matrix total over native state kinds
  b082993 2026-09-08 Verify criterion record fidelity and return the stored fake update
### b1d410c28  n=3  branches: codex/scope-approval-mutants 
  b1d410c 2026-09-08 fix(tracker): refuse incomplete positive project membership (KOD-380)
  bfd696d 2026-09-08 test(tracker): prove milestone scopes inherit project approval (KOD-382)
  fa456f5 2026-09-08 feat(tracker): resolve live scope approval ancestry (KOD-380)
### b3acf83ba  n=1  branches: codex/v03-scanner-failure-contract 
  b3acf83 2026-09-08 Classify native session failures at the SDK boundary
### b559dfb3d  n=3  branches: codex/v03-node-session-events codex/v03-node-session-mutants 
  b559dfb 2026-09-08 feat(run): emit native evaluator session openings from the harness
  38256d7 2026-09-08 fix(config): preserve the two-scalar minimal operation floor
  196e8b9 2026-09-08 feat(run): validate the complete run-event table before tracker boot
### b62f57a6f  n=2  branches: codex/v03-fresh-judgment-mechanics codex/v03-fresh-judgment-mutations 
  b62f57a 2026-09-08 Share fresh judging dispatch within caller-owned workspaces
  47d39b3 2026-09-08 Share owned operation settlement and read-only workspace lifetimes
### b714b9841  n=4  branches: codex/v03-recovery-criterion-class 
  b714b98 2026-09-12 test(criteria): prove uniform failure and refuse every retired class payload
  abfb0c8 2026-09-12 test(fakes): drop the fixture the two-partition gate left behind
  0fefea7 2026-09-12 docs(changelog): record the criterion class's removal from the wire
  caa1c3c 2026-09-12 refactor(criteria): delete the criterion class
### b84d43ba4  n=4  branches: codex/v03-native-read-address-corrective 
  b84d43b 2026-09-12 Retain actual native address and approval alias regression controls
  7cd8e70 2026-09-12 Normalize attested native approval identity before ancestry and family reads
  a05c169 2026-09-12 Validate criterion parent response against its native address
  0aad6e6 2026-09-12 Refuse blank criterion membership mappings before tracker reads
### bb80d0715  n=14  branches: codex/v03-pr-footer codex/v03-pr-footer-mutants 
  bb80d07 2026-09-08 fix(delivery): retain tracker identity after outbound gating (KOD-739)
  bd38c4a 2026-09-08 fix(delivery): read the existing PR before creation (KOD-314)
  533d49a 2026-09-07 fix(delivery): preserve the stalled published head at terminal (KOD-327)
  2523f3e 2026-09-07 fix(delivery): bind the session to its dispatched fire (KOD-313)
  399eae8 2026-09-07 docs(scope): keep configured workflow labels out of gap prose
  7244fb8 2026-09-07 test(delivery): register the description session and outcome producer
  175a6be 2026-09-07 docs(delivery): state coordinator handoff and remaining consumers
  f6a15db 2026-09-07 test(delivery): keep merge capability absent (KOD-325)
  06724a9 2026-09-07 feat(delivery): bound concurrent lane watches (KOD-329)
  206553e 2026-09-07 test(delivery): verify native green PR path (KOD-314)
  10179d1 2026-09-07 feat(delivery): distinguish undeclared checks (KOD-315)
  8464ea5 2026-09-07 feat(delivery): verify recorded remote branches (KOD-328)
  f541915 2026-09-07 feat(delivery): add typed coordinator handoff (KOD-313)
  2f8f579 2026-09-07 feat(delivery): configure concurrent check watch bound (KOD-333)
### bbed103b1  n=1  branches: codex/v03-event-credential-fixtures 
  bbed103 2026-09-08 Provide the event table in credential boot fixtures
### bd38c4a26  n=13  branches: codex/v03-delivery-replay codex/v03-delivery-replay-mutants 
  bd38c4a 2026-09-08 fix(delivery): read the existing PR before creation (KOD-314)
  533d49a 2026-09-07 fix(delivery): preserve the stalled published head at terminal (KOD-327)
  2523f3e 2026-09-07 fix(delivery): bind the session to its dispatched fire (KOD-313)
  399eae8 2026-09-07 docs(scope): keep configured workflow labels out of gap prose
  7244fb8 2026-09-07 test(delivery): register the description session and outcome producer
  175a6be 2026-09-07 docs(delivery): state coordinator handoff and remaining consumers
  f6a15db 2026-09-07 test(delivery): keep merge capability absent (KOD-325)
  06724a9 2026-09-07 feat(delivery): bound concurrent lane watches (KOD-329)
  206553e 2026-09-07 test(delivery): verify native green PR path (KOD-314)
  10179d1 2026-09-07 feat(delivery): distinguish undeclared checks (KOD-315)
  8464ea5 2026-09-07 feat(delivery): verify recorded remote branches (KOD-328)
  f541915 2026-09-07 feat(delivery): add typed coordinator handoff (KOD-313)
  2f8f579 2026-09-07 feat(delivery): configure concurrent check watch bound (KOD-333)
### be9717f47  n=1  branches: codex/v03-recovery-marker-preflight 
  be9717f 2026-09-12 Validate ownership marker configuration before reading native records
### c01dcd81c  n=1  branches: codex/kod-633-ruling-proposal codex/kod-633-ruling-proposal-mutations 
  c01dcd8 2026-09-08 feat: propose rulings from fresh native fire sources
### c046b6243  n=5  branches: codex/v03-audit-sweep-assessment codex/v03-barren-record-collector 
  c046b62 2026-09-08 feat(supervisor): observe growth from native lane records
  25b6d1d 2026-09-08 test(supervisor): include typed escalation observation import
  7f5bf1d 2026-09-08 fix(supervisor): verify the decision used for escalation age
  1decbab 2026-09-08 test(supervisor): check escalation identity at its native reader
  ef45d14 2026-09-08 feat(supervisor): collect escalation age from native lane records
### c0638caf0  n=2  branches: codex/v03-scope-plan-barriers 
  c0638ca 2026-09-08 fix(scope): require reported planning labels and relations (KOD-450)
  aad38f3 2026-09-08 feat(scope): refuse live plan barriers before dispatch (KOD-450)
### c14123dc2  n=3  branches: codex/v03-marker-lookups codex/v03-marker-mutations 
  c14123d 2026-09-08 Share unique marker lookup with native lane prefix discovery
  294b2e0 2026-09-08 Make cancellation regression reject swallowed cancellation promptly
  f5f87fc 2026-09-08 Share cursor progression across native tracker and record readers
### c3ef39d0b  n=6  branches: codex/v03-lane-record-reader 
  c3ef39d 2026-09-08 docs(records): state the declared collection mutability (KOD-683)
  975532d 2026-09-08 test(records): verify complete reads and document runtime limits (KOD-683)
  2682a70 2026-09-08 fix(records): preserve explicit absence for optional refs (KOD-679)
  298c573 2026-09-08 feat(records): reconstruct lanes through addressed tracker reads (KOD-683)
  26c1263 2026-09-08 feat(records): append explicit role-aware re-entry guidance (KOD-686)
  b0971ca 2026-09-08 feat(records): model and render lane branch facts (KOD-679)
### c47a7f112  n=1  branches: codex/v03-recovery-native-compat 
  c47a7f1 2026-09-12 Restore authored contradiction wire contract and paused job fixture
### c579eb9da  n=13  branches: codex/v03-native-identity-cancel-correction codex/v03-native-identity-root-review 
  c579eb9 2026-09-13 fix: settle native identity subprocesses on cancellation
  3508980 2026-09-12 fix: release native workspaces when preparation fails
  2618272 2026-09-12 test: seed saved outcome from the reconciled prior grade
  a198da3 2026-09-12 fix: preserve cancellation across native phase boundaries
  4dd7b3e 2026-09-12 fix: resume native writer phases through parent checkpoints
  62a86ad 2026-09-12 fix(amendment): retain verified archive authority through publication
  4c6322f 2026-09-12 Require strict native classification for description authority
  8f80bb3 2026-09-12 Format independent classification regression probes
  8cd8feb 2026-09-12 Require known native classification before selecting write authority
  ddfd79f 2026-09-12 Implement verified native amendments before harness persistence
  c3e821e 2026-09-12 WIP connect native amendment graph and preserve tested write ordering
  182e698 2026-09-12 WIP preserve applied native amendment draft before dependency update
  fe6aa2f 2026-09-12 Honor criterion surface ownership for classification writes
### c8382d953  n=2  branches: codex/v03-stalled-cumulative 
  c8382d9 2026-09-08 fix(delivery): accept cumulative stalled iteration totals (KOD-327)
  dc49867 2026-09-08 feat(delivery): report healthy handoffs without a PR adapter (KOD-313)
### ce193c878  n=2  branches: codex/v03-live-harness 
  ce193c8 2026-09-08 Preserve hermetic collection and explicit native live test inputs
  14958fc 2026-09-08 Restore logging globals after tests that configure application startup
### cf063e32f  n=13  branches: codex/kod-485-outbound-admission-mutations 
  cf063e3 2026-09-08 Exercise mandatory branch judgment for both provenance classes
  3e5f6ef 2026-09-08 Replace configurable scanners with fixed outbound admission
  d46b329 2026-09-08 Retire aggregate prose patterns after authored admission migration
  cb6dba0 2026-09-08 Keep subprocess cleanup cadence at its owner and inject only the command timeout
  3517402 2026-09-08 Remove unconsumed ORGANIZE round settings and refuse retired inputs
  fd25528 2026-09-08 Reject trailing-underscore configuration typos without prefix exemptions
  f075ab6 2026-09-08 Validate documentation against nested settings and exact migration names
  ec8ac51 2026-09-08 Preserve audit payload boundary and prove authored policy separation
  551dcc1 2026-09-08 Require authored tracker aggregate judgment before durable writes
  238a319 2026-09-08 test(privacy): retain typed facts in composed retry fixture
  881017e 2026-09-08 refactor(adapters): share bounded retry policy and backoff
  03cafd8 2026-09-08 fix(privacy): classify rendered Markdown reference authorities
  b0b3bd4 2026-09-08 feat(privacy): classify native references from deployment facts
### cf6fc4ed1  n=1  branches: codex/v03-dispatch-log-isolation 
  cf6fc4e 2026-09-08 Restore logging globals after tests that configure application startup
### d0f30f29b  n=1  branches: codex/v03-writeback-native-proof codex/v03-writeback-native-proof-mutations 
  d0f30f2 2026-09-08 Prove missing-test repair against native Git bytes
### d46b3292c  n=11  branches: codex/kod-485-aggregate-retirement codex/kod-485-retirement-mutations 
  d46b329 2026-09-08 Retire aggregate prose patterns after authored admission migration
  cb6dba0 2026-09-08 Keep subprocess cleanup cadence at its owner and inject only the command timeout
  3517402 2026-09-08 Remove unconsumed ORGANIZE round settings and refuse retired inputs
  fd25528 2026-09-08 Reject trailing-underscore configuration typos without prefix exemptions
  f075ab6 2026-09-08 Validate documentation against nested settings and exact migration names
  ec8ac51 2026-09-08 Preserve audit payload boundary and prove authored policy separation
  551dcc1 2026-09-08 Require authored tracker aggregate judgment before durable writes
  238a319 2026-09-08 test(privacy): retain typed facts in composed retry fixture
  881017e 2026-09-08 refactor(adapters): share bounded retry policy and backoff
  03cafd8 2026-09-08 fix(privacy): classify rendered Markdown reference authorities
  b0b3bd4 2026-09-08 feat(privacy): classify native references from deployment facts
### d504adda7  n=4  branches: codex/kod-746-delivery-retirement 
  d504add 2026-09-08 fix(composition): forward declared repositories to authored delivery
  4950029 2026-09-08 Pin the native authored retry proof to an actual cleanup call
  a2391a4 2026-09-08 Move delivery check policy into the composed authored path and retire unused slices
  ed2423b 2026-09-08 Refuse unsupported scopes before preparation and retire unused stack
### d59047eb0  n=2  branches: codex/v03-recovery-shared-contracts 
  d59047e 2026-09-12 Preserve delivery head mismatch evidence in a typed error
  c37dfa2 2026-09-12 Define an explicit surface for criterion child creation
### d5ae87082  n=3  branches: codex/kod-450-addressed-preloop 
  d5ae870 2026-09-08 test(scope): isolate undeclared repository refusal
  55f8436 2026-09-08 fix(scope): require recorded-route operation authority
  841d37f 2026-09-08 feat(scope): prepare addressed tracker fires at their recorded head
### d7e7521e9  n=1  branches: codex/kod-139-operation-sections 
  d7e7521 2026-09-08 Replace private source citations with their behavioral meaning
### d8796325b  n=4  branches: codex/v03-escalation-record-collector 
  d879632 2026-09-08 test(supervisor): include typed escalation observation import
  7f5bf1d 2026-09-08 fix(supervisor): verify the decision used for escalation age
  1decbab 2026-09-08 test(supervisor): check escalation identity at its native reader
  ef45d14 2026-09-08 feat(supervisor): collect escalation age from native lane records
### d93f3e973  n=2  branches: codex/v03-recovery-semantic-amendment 
  d93f3e9 2026-09-12 Add native semantic judgment and precommit guard review candidate
  515724a 2026-09-12 Read issue rulings across their actual recorded lane markers
### da1d474dd  n=2  branches: codex/v03-check-runner-cancellation 
  da1d474 2026-09-08 test(checks): publish controlled child identity atomically (KOD-597)
  da84ac0 2026-09-08 fix(checks): retain process group ownership through pipe drain (KOD-597)
### da37bda26  n=1  branches: codex/v03-artifact-read-corrective 
  da37bda 2026-09-12 Require reported native fields in structured tracker artifacts
### db9d3702d  n=9  branches: codex/v03-recovery-semantic-applied-current 
  db9d370 2026-09-12 Implement verified native amendments before harness persistence
  e94f825 2026-09-12 Register native prompt inputs and align utility prompt metadata
  5347e9b 2026-09-12 fix(tracker): retain escalation label authority across retries
  2d463a2 2026-09-12 WIP connect native amendment graph and preserve tested write ordering
  f7c8b1f 2026-09-12 fix(tracker): refresh protected comment checks on unsent retries
  510cb36 2026-09-12 Give canonical write verification its own configured repair bound
  4c89eae 2026-09-12 fix(tracker): bind comment amendments to final native snapshot
  d553170 2026-09-12 Resolve initial issue state by its validated native team identity
  b92708a 2026-09-12 WIP preserve applied native amendment draft before dependency update
### dc25386a8  n=5  branches: codex/v03-tracker-feasibility codex/v03-tracker-feasibility-mutations 
  dc25386 2026-09-08 fix(fire): settle feasibility reads and reject duplicate conflict keys (KOD-398)
  97c919e 2026-09-08 Settle owned Git workspace reads before propagating cancellation
  069052c 2026-09-08 test(fire): refuse foreign captures before feasibility dispatch (KOD-398)
  d5956be 2026-09-08 feat(fire): validate tracker criteria through a fresh read-only session (KOD-398)
  4bc122f 2026-09-08 Reject dirty audit workspaces and register audit dispatch census
### dc49867b5  n=1  branches: codex/v03-delivery-no-adapter 
  dc49867 2026-09-08 feat(delivery): report healthy handoffs without a PR adapter (KOD-313)
### dd11ffd54  n=8  branches: codex/v03-recovery-audit-runtime 
  dd11ffd 2026-09-12 Compose configured native audit with verified publication and coverage receipts
  080fa6c 2026-09-12 Require known native classification before selecting write authority
  8f04db9 2026-09-12 Honor criterion surface ownership for classification writes
  ab75f03 2026-09-12 Register native prompt inputs and align utility prompt metadata
  b4b8601 2026-09-12 fix(tracker): retain escalation label authority across retries
  3009435 2026-09-12 fix(tracker): refresh protected comment checks on unsent retries
  60fab18 2026-09-12 fix(tracker): bind comment amendments to final native snapshot
  6c9c34c 2026-09-12 Give canonical write verification its own configured repair bound
### dd6147c6c  n=2  branches: codex/v03-recovery-scope-ancestor codex/v03-recovery-scope-ancestor-root-review 
  dd6147c 2026-09-12 Use a synthetic UUID in the scope alias fixture
  8dedc14 2026-09-12 Refuse mismatched canonical scope ancestor identities
### dd6c7faa6  n=3  branches: codex/kod-503-scope-tally 
  dd6c7fa 2026-09-08 test: isolate scope tally identity and duplicate roster guards
  c5dff46 2026-09-08 feat: observe scope phase marker barriers from native rosters
  3fbd5da 2026-09-08 feat(tracker): require complete issue classification for scope readiness (KOD-425)
### de632003b  n=2  branches: codex/v03-agent-settings 
  de63200 2026-09-08 Cover nested agent typos and composed skill-loadout refusal
  8b6714c 2026-09-08 Group agent deployment settings at native session consumers
### debfbf65b  n=4  branches: codex/v03-check-runner-settings 
  debfbf6 2026-09-08 Keep subprocess cleanup cadence at its owner and inject only the command timeout
  7b58c74 2026-09-08 Remove unconsumed ORGANIZE round settings and refuse retired inputs
  6745593 2026-09-08 Reject trailing-underscore configuration typos without prefix exemptions
  c196867 2026-09-08 Validate documentation against nested settings and exact migration names
### df442a261  n=6  branches: codex/v03-audit-forge-evidence 
  df442a2 2026-09-08 fix(audit): classify readable red before roster acceptance (KOD-508)
  ae19987 2026-09-08 feat(audit): verify forge claims at the exact Evidence commit (KOD-508)
  4db6269 2026-09-08 test(audit): distinguish valid lane-record replacement from unreadability (KOD-520)
  2b546f7 2026-09-08 fix(fire): own feasibility cache acquisition through cancellation (KOD-398)
  badaa2b 2026-09-08 fix(audit): settle native repository reads before cancellation (KOD-500)
  96c9fd9 2026-09-08 feat(audit): read recorded Evidence against the live branch head (KOD-520)
### e2866ebba  n=4  branches: codex/v03-audit-removal-sweep 
  e2866eb 2026-09-08 feat(audit): compose source-backed detector losses into sweep reports
  4b2327f 2026-09-08 test(audit): prove clean self rules and mixed-head refusal
  89e19c7 2026-09-08 feat(audit): run standing overclaim checks through native sweep
  1cfa58f 2026-09-08 feat(audit): assemble native scope requests and run read-only sweep
### e2a49a868  n=1  branches: codex/v03-audit-worktree-integrity 
  e2a49a8 2026-09-08 Reject dirty audit workspaces and register audit dispatch census
### e5bb791f5  n=1  branches: codex/v03-judgment-schema-census 
  e5bb791 2026-09-08 Trace schema coverage through the shared judgment dispatch
### e6988cf4b  n=4  branches: codex/v03-delivery-flake 
  e6988cf 2026-09-08 test(delivery): require the original failed-check read at zero reruns
  79c333e 2026-09-08 fix(delivery): retain one failing-check reader across recovery
  e73b4d7 2026-09-08 feat(delivery): recover runner flakes at the observed commit
  51d83d5 2026-09-08 feat(delivery): edit existing PR content on replay (KOD-95)
### e6be6a68b  n=7  branches: codex/v03-audit-terminal-mandate codex/v03-terminal-mandate-mutants 
  e6be6a6 2026-09-08 feat(audit): complete native terminal mandates through the shared hunt
  01086e2 2026-09-08 fix(audit): reject substituted mandate workspaces
  99555ca 2026-09-08 fix(audit): reject substituted objects in claim sessions
  e2866eb 2026-09-08 feat(audit): compose source-backed detector losses into sweep reports
  4b2327f 2026-09-08 test(audit): prove clean self rules and mixed-head refusal
  89e19c7 2026-09-08 feat(audit): run standing overclaim checks through native sweep
  1cfa58f 2026-09-08 feat(audit): assemble native scope requests and run read-only sweep
### e6d467cd6  n=8  branches: codex/v03-git-settings 
  e6d467c 2026-09-08 Migrate remaining Git environment fixtures to nested names
  f68aaad 2026-09-08 Group Git settings at actual repository and commit consumers
  359d7ea 2026-09-08 Group HTTP settings and inject only the response prefix
  1d6b67e 2026-09-08 Retire aggregate prose patterns after authored admission migration
  6fa0ef5 2026-09-08 Pass focused queue settings to the composed job lifecycle
  bc0631d 2026-09-08 Narrow tracker read consumers and remove empty boot probes
  24cebb5 2026-09-08 Make cancellation regression reject swallowed cancellation promptly
  7acf221 2026-09-08 Share cursor progression across native tracker and record readers
### e6e9c97e3  n=12  branches: codex/v03-union-composition 
  e6e9c97 2026-09-08 test(union): reap running checks before discarding the scratch tree (KOD-588)
  06f24b8 2026-09-08 test(union): preserve measured heads across moving references (KOD-601)
  a45a68e 2026-09-07 docs(union): describe shared scratch result and consumer limits (KOD-603)
  582e367 2026-09-07 fix(union): settle scratch observations before cancellation cleanup (KOD-588)
  0a5db7b 2026-09-07 test(union): keep scratch provenance fixtures lint-clean (KOD-601)
  75b79b7 2026-09-07 feat(union): consume the restored historical check classifier (KOD-595)
  1589a10 2026-09-07 feat(union): report scope composability independently of lanes (KOD-591)
  e6de54a 2026-09-07 test(union): verify scratch attempts preserve branches and open PRs (KOD-589)
  17de728 2026-09-07 fix(union): refuse composed trees without a declared chain (KOD-599)
  3929091 2026-09-07 feat(union): compose pinned heads in disposable Git worktrees (KOD-588)
  5ab6a80 2026-09-07 feat(union): expose shared scope check observation (KOD-603)
  61abcc7 2026-09-07 feat(union): carry ordered pinned scratch provenance (KOD-601)
### e7ae430ca  n=11  branches: codex/v03-recovery-lane-delivery 
  e7ae430 2026-09-12 Revalidate native delivery evidence at resumed terminal boundaries
  52cd934 2026-09-12 Validate delivery PR identity before gated comments
  4e9d9b6 2026-09-12 Verify native PR head base and lifecycle around delivery watches
  fead928 2026-09-12 Recheck delivery refs after awaited publication preparation
  48878b1 2026-09-12 Compose native lane delivery with typed checks and bounded remediation
  e722ada 2026-09-12 Preserve delivery head mismatch evidence in a typed error
  db28312 2026-09-12 fix(fire): bind resumed effects and fresh judgments to current checks
  097e285 2026-09-12 fix(fire): carry native specs through live execution and replay
  24fcfac 2026-09-12 feat(fire): the criteria a native fire owes come from the tracker's own spec read
  8c1b693 2026-09-12 feat(fire): an execution-only fire composition that re-validates its subtree's Todo criteria before the loop
  2eddd4d 2026-09-12 Return coherent typed CI observations from each watch
### ea1d91596  n=14  branches: codex/v03-m1-session-policy-extraction 
  ea1d915 2026-09-12 Document domain session policy and preserved HTTP vocabulary
  1553604 2026-09-12 Validate the fallback base before typed workflow submission
  990652c 2026-09-12 Extract typed session permissions and SDK tool policy into M1
  fec7f28 2026-09-12 Preserve actual UUID alias and foreign parent regression controls
  fd19b9a 2026-09-12 Validate criterion parent response against its native address
  f3c3dd3 2026-09-12 Refuse blank criterion membership mappings before tracker reads
  6346911 2026-09-12 Extract configured native criterion read boundary for M1
  a4575cd 2026-09-12 Complete M1 configured marker prompt and fixture closure
  c795bb6 2026-09-12 Extract configured surface leases and run-owned terminal writes for M1
  b0def20 2026-09-12 Use a synthetic UUID in the scope alias fixture
  067b773 2026-09-12 Refuse mismatched canonical scope ancestor identities
  a2ee4c6 2026-09-12 Carry the existing priority wire census with its adapter relocation
  1811397 2026-09-12 Extract current scope read ports and native adapter boundaries for M1
  3c477a3 2026-09-12 Extract M1 scope addresses and surface lease value contracts
### eb57778ed  n=16  branches: codex/v03-m1-dispatch-timeout-independent codex/v03-m1-dispatch-timeout-oracle 
  eb57778 2026-09-12 Synchronize the dispatch cancellation oracle at its required entry boundary
  873855e 2026-09-12 Restore scheduled gate windows when observation is cancelled
  27681a4 2026-09-12 Document domain session policy and preserved HTTP vocabulary
  b91b1f8 2026-09-12 Validate the fallback base before typed workflow submission
  3b489fb 2026-09-12 Extract typed session permissions and SDK tool policy into M1
  926067d 2026-09-12 Preserve actual UUID alias and foreign parent regression controls
  9f184fc 2026-09-12 Validate criterion parent response against its native address
  0ff4e10 2026-09-12 Refuse blank criterion membership mappings before tracker reads
  ee9b44e 2026-09-12 Extract configured native criterion read boundary for M1
  a4575cd 2026-09-12 Complete M1 configured marker prompt and fixture closure
  c795bb6 2026-09-12 Extract configured surface leases and run-owned terminal writes for M1
  b0def20 2026-09-12 Use a synthetic UUID in the scope alias fixture
  067b773 2026-09-12 Refuse mismatched canonical scope ancestor identities
  a2ee4c6 2026-09-12 Carry the existing priority wire census with its adapter relocation
  1811397 2026-09-12 Extract current scope read ports and native adapter boundaries for M1
### ecd0d144b  n=2  branches: codex/v03-http-dependencies 
  ecd0d14 2026-09-08 Declare HTTP dependencies and validate existing response models
  2298e0d 2026-09-08 Correct the documented immediate scope refusal
### ed7c99422  n=6  branches: codex/kod-519-mandate-graph 
  ed7c994 2026-09-08 test(supervisor): keep superseded fires distinct from crossed fires (KOD-519)
  0d5d7cf 2026-09-08 test(supervisor): separate required authorship and crossing boundaries (KOD-519)
  396150a 2026-09-08 feat(supervisor): observe mandate growth and structural regressions (KOD-519)
  fcf8c09 2026-09-08 test(supervisor): require lane subject for record supersession (KOD-511)
  c0826dd 2026-09-08 feat(supervisor): compare assertions in recorded commit order (KOD-511)
  ad8fe4d 2026-09-07 feat(supervisor): compare owed writes and recorded commit rows (KOD-513)
### edbd4112a  n=6  branches: codex/v03-recovery-amendment-comment 
  edbd411 2026-09-12 wip(native): assert existing comment before amendment
  d80e7fe 2026-09-12 Reject ambiguous native comment provenance across all readers
  1c39736 2026-09-12 fix(native): retain worktree across incomplete persistence receipt
  2e7fdd9 2026-09-12 Preserve native cancellation, publication authority and genuine evaluation history
  d93f3e9 2026-09-12 Add native semantic judgment and precommit guard review candidate
  515724a 2026-09-12 Read issue rulings across their actual recorded lane markers
### ee1d44bd5  n=5  branches: codex/v03-recovery-organize-owner 
  ee1d44b 2026-09-12 fix(organize): bind writes to current authority and retain verifier evidence
  161ca93 2026-09-12 Compose bounded Organize preparation and fresh write-back judgment
  fd8686d 2026-09-12 Reconcile remaining lifecycle and lease fixtures with current contracts
  835604a 2026-09-12 Keep parent completion out of merged-fire lifecycle writes
  d6127a2 2026-09-12 Define an explicit surface for criterion child creation
### eeb91ef7a  n=6  branches: codex/v03-fire-extraction 
  eeb91ef 2026-09-08 test(fire): assert delivery-free compiled graph and state (KOD-332)
  aff4ab7 2026-09-08 feat(fire): extract delivery while preserving authored execution (KOD-331)
  84ee446 2026-09-08 Keep authored criterion pattern guard specific to authored identity
  08b2d4e 2026-09-08 Preserve native criterion identity types and retained outcome guard
  c8382d9 2026-09-08 fix(delivery): accept cumulative stalled iteration totals (KOD-327)
  dc49867 2026-09-08 feat(delivery): report healthy handoffs without a PR adapter (KOD-313)
### f094b5eb6  n=1  branches: codex/v03-criterion-resolution 
  f094b5e 2026-09-08 feat(tracker): resolve native criterion keys at one read boundary
### f356c5fa7  n=5  branches: codex/v03-scope-ready codex/v03-scope-ready-mutants 
  f356c5f 2026-09-08 test(scope): isolate final tree and membership freshness windows (KOD-425)
  0ddfabf 2026-09-08 test(scope): prove exact gap selection without parent state reads (KOD-456)
  e91837a 2026-09-08 feat(scope): read approval and full blocker subtree readiness (KOD-425)
  715627a 2026-09-08 feat(tracker): require complete issue classification for scope readiness (KOD-425)
  ad1cf28 2026-09-08 test(scope): supply explicit approval mapping in planner fixture
### f3ca519c4  n=6  branches: codex/v03-http-settings 
  f3ca519 2026-09-08 Group HTTP settings and inject only the response prefix
  3def3ec 2026-09-08 Pass focused queue settings to the composed job lifecycle
  debfbf6 2026-09-08 Keep subprocess cleanup cadence at its owner and inject only the command timeout
  7b58c74 2026-09-08 Remove unconsumed ORGANIZE round settings and refuse retired inputs
  6745593 2026-09-08 Reject trailing-underscore configuration typos without prefix exemptions
  c196867 2026-09-08 Validate documentation against nested settings and exact migration names
### f50e35033  n=4  branches: codex/v03-fire-spec-consumer-mutations codex/v03-fire-spec-consumers 
  f50e350 2026-09-08 test(delivery): pin captured criterion multiplicity and order (KOD-410)
  91a2efb 2026-09-08 refactor(fire): preserve authored prompt bytes through FireSpec (KOD-416)
  07d87a3 2026-09-08 feat(delivery): consume captured FireSpec in description sessions (KOD-410)
  51d83d5 2026-09-08 feat(delivery): edit existing PR content on replay (KOD-95)
### f632aee96  n=12  branches: codex/v03-recovery-scope-runtime 
  f632aee 2026-09-12 Keep API event census aligned with scoped native streams
  331cb45 2026-09-12 Validate scoped event payloads and recheck admission at graph launch
  db76a86 2026-09-12 Verify scoped native replay and serialize addressed stream events
  ee13a79 2026-09-12 Verify native PR head base and lifecycle around delivery watches
  29a4d0b 2026-09-12 Recheck delivery refs after awaited publication preparation
  8ef48ec 2026-09-12 WIP verify scope replay and typed stream boundaries
  6d2ce10 2026-09-12 Compose native lane delivery with typed checks and bounded remediation
  d70b98e 2026-09-12 Preserve delivery head mismatch evidence in a typed error
  8f3ea9e 2026-09-12 WIP validate scope checkpoint state and preserve lane run identity
  6968402 2026-09-12 fix(native): normalize tracker port failures at current reads
  7c90b39 2026-09-12 Return coherent typed CI observations from each watch
  feb9898 2026-09-12 WIP scoped request runtime awaiting native delivery dependency
### f85fa8e75  n=2  branches: codex/v03-audit-settings 
  f85fa8e 2026-09-08 Assert configured remote on each audit observation path
  e57b39b 2026-09-08 Pass only the remote setting to audit consumers
### fc4953e1d  n=3  branches: codex/v03-recovery-audit-types 
  fc4953e 2026-09-12 fix(audit): require strict indices and covered mandate sources
  37075cc 2026-09-12 refactor(audit): carry mandate verdicts through closed typed variants
  4daec66 2026-09-12 test(audit): bind temporary PR base to the actual repository
### fcf8c094f  n=3  branches: codex/kod-511-record-superseded 
  fcf8c09 2026-09-08 test(supervisor): require lane subject for record supersession (KOD-511)
  c0826dd 2026-09-08 feat(supervisor): compare assertions in recorded commit order (KOD-511)
  ad8fe4d 2026-09-07 feat(supervisor): compare owed writes and recorded commit rows (KOD-513)
### fd25528a0  n=8  branches: codex/kod-486-authored-aggregate-admission 
  fd25528 2026-09-08 Reject trailing-underscore configuration typos without prefix exemptions
  f075ab6 2026-09-08 Validate documentation against nested settings and exact migration names
  ec8ac51 2026-09-08 Preserve audit payload boundary and prove authored policy separation
  551dcc1 2026-09-08 Require authored tracker aggregate judgment before durable writes
  238a319 2026-09-08 test(privacy): retain typed facts in composed retry fixture
  881017e 2026-09-08 refactor(adapters): share bounded retry policy and backoff
  03cafd8 2026-09-08 fix(privacy): classify rendered Markdown reference authorities
  b0b3bd4 2026-09-08 feat(privacy): classify native references from deployment facts
### fd93cf2a1  n=1  branches: codex/v03-recovery-audit-report-contract 
  fd93cf2 2026-09-12 Require the matching mandate payload in completed audit report types
### fec7f2829  n=11  branches: codex/v03-m1-native-read-extraction 
  fec7f28 2026-09-12 Preserve actual UUID alias and foreign parent regression controls
  fd19b9a 2026-09-12 Validate criterion parent response against its native address
  f3c3dd3 2026-09-12 Refuse blank criterion membership mappings before tracker reads
  6346911 2026-09-12 Extract configured native criterion read boundary for M1
  a4575cd 2026-09-12 Complete M1 configured marker prompt and fixture closure
  c795bb6 2026-09-12 Extract configured surface leases and run-owned terminal writes for M1
  b0def20 2026-09-12 Use a synthetic UUID in the scope alias fixture
  067b773 2026-09-12 Refuse mismatched canonical scope ancestor identities
  a2ee4c6 2026-09-12 Carry the existing priority wire census with its adapter relocation
  1811397 2026-09-12 Extract current scope read ports and native adapter boundaries for M1
  3c477a3 2026-09-12 Extract M1 scope addresses and surface lease value contracts
```

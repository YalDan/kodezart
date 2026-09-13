# M1 typed session and SDK policy extraction — frozen candidate

Candidate ea1d91596cc862dbfaa48abb149d2480702bf1bb, tree bd61aef524dcc9d8f7014859d7d37c79331521fd, isolated branch codex/v03-m1-session-policy-extraction. Baseline fec7f28292975f0b4635a06ba84e4e35fdc3c2e8/tree 8b76e5df013c41ebf8741d292a1cb1034f2d5f87. Maintained PR119's 926067d is root-reported tree-equivalent to this baseline; root alone integrates. No canonical/maintained-tree edits, pushes, issue states or new PRs.

## Findings and result

The existing domain PermissionMode and ToolPreset/AllowedTools vocabulary now reaches both real Claude SDK adapters through every already-present application caller in this extracted tree. HTTP keeps its existing plan/bypassPermissions literals, defaults, list-of-string tools and schemas. Actual HTTP handlers translate into the existing donor WorkflowSubmission domain command; the queue receives explicit normalized BaseSpec rather than HTTP DTOs. M3's absent native issue/scope/phase fields are deliberately left with their real consumers. No additional lane modules or write capability were imported.

The SDK boundary expands existing presets into the same ordered approval-selector lists; options.tools remains unset, so this is not a tool-exposure restriction or a shell sandbox. Claude-agent-sdk 0.2.151's official tagged subprocess transport is byte-identical to installed source (SHA256 748227e42cb9802d7316fabc836428d69bd219b63ef1b2f9f8f7a86a72a03e45). Official evidence: https://raw.githubusercontent.com/anthropics/claude-agent-sdk-python/v0.2.151/src/claude_agent_sdk/_internal/transport/subprocess_cli.py and https://code.claude.com/docs/en/agent-sdk/permissions . The actual options/command controls check --allowedTools independently from --tools. No paid/live CLI result is claimed.

A self-review found omitted donor HTTP fallback validation: empty baseBranch without baseSpec failed inside handler conversion, giving actual HTTP500 rather than422. Original six external HTTP controls gave 2 red/4 green in5.90s at990; exact donor _check_trunk_base is now extracted in1553604, preserving explicit valid baseSpec plus empty fallback and missing/default cases. Six original assertion ASTs remain unchanged after formatting/import sorting. The earlier external invocation without repository pytest config produced six async-configuration diagnostics; none are counted as executed counterexamples.

The first immutable full gate at990652c returned1 failed/3659 passed/16 skipped in697.33s. The failure was the inherited 10ms dispatch deadline test, canceled before dispatcher entry while PassGate.delta awaited logging. Unchanged module rerun29 passed0.98s. A deterministic probe then proved a real inherited consumed-mark defect at both990 (1red3.06s) and fec (1red3.05s); PromptPass has the analogous gap. That defect is being corrected separately in the existing rearm owners; no timeout was inflated and no gate fix is hidden in the mechanical policy migration. Final immutable ea1d915 full make check PASSED:3666 passed,16 skipped in622.67s, plus strict178 source,Ruff/format367 and literal gate. Source/test/docs stayed unchanged for the whole run. This does not negate the independently reproduced inherited cancellation defect, whose separate correction still needs review.

## Exact commits and provenance

1. 990652c072035615ff4ecb651d9758e184831de9 — existing typed session boundary and current caller/fixture extraction (54files).
2. 1553604 — exact donor fallback-base request validator and six real HTTP controls (source/test correction separately reviewable).
3. ea1d91596cc862dbfaa48abb149d2480702bf1bb — exact donor Permission Modes paragraph only.

Donor: 1f4296c8c36a7498625d2478f8ec7ae6ec187923. Original policy sources7cbe6bc4af03fd4e9f554807eda43ffbf6464667 and6994552011b8b83db0d3050baa98f8c378ab6ba2. No newer Audit/native donor tree was copied.

session-policy-final-hunk-map.json indexes EVERY changed path/hunk exactly once:56files,286hunks,22sourcefiles, hashes, enclosing symbols, exact donor AST comparisons and normalized-baseline method references. session-policy-normalized-source-proof.json proves26 changed methods remain AST-identical to baseline after reversing only policy annotations/values/mappers. Seven existing submission methods are recorded separately as the explicitly approved domain-command closure. New domain fields and handler method are mapped to exact donor nodes with current-shape adaptations.

Entire SDK mapping modules equal donor. Other source transplants are narrow: session enum/type nodes; SDK signatures/options; current evaluator/reviewer/remediation/ticket-generation/commit-message/scanner presets; AgentExecutor/AgentRunner/QualityGate/WorkflowEngine signatures; existing context fields and handler/queue/dispatcher command conversion. Tracker, workspace, Git, native-phase and lifecycle authority hunks were not imported. Existing tracker reader/milestone cuts remain untouched.

New permission_boundary/tool_selection tests retain donor assertions, adapting only its later dependency-injection fixtures to the present app.state API and omitting absent M3 fields. tool_request_schemas.json is the EXACT baseline HTTP schema, not the donor's later scope schema. Existing queue fixtures now provide all explicit domain command fields. tests/chains/test_session_policy_composition.py drives the actual existing graph+AgentService+real Git persister+real SDK option construction with only SDK transport doubled, both SDK backends. Existing underlying graph/Git assertions remain in force.

## Executed evidence

All logs in /private/tmp/kodezart-recovery-session. Commands use locked uv and immutable source during each run.

- session-policy-boundary-initial.log: both SDK executor modules plus permission_boundary/tool_selection,240 passed8.50s.
- session-policy-consumers-initial.log: current API/chains/service/composition consumers,2 failed631 passed99.31s. Both failures were original FireDispatcher tests accessing retired HTTP base_branch; three assertion paths migrated to base_spec.base_branch with expected values unchanged.
- session-policy-composition-initial.log: both actual SDK/real Git graph controls plus complete existing base-resolution class,7 passed13.76s.
- session-policy-http-final.log: full existing API + permission_boundary/tool_selection,252 passed21.01s after exact validator extraction.
- session-policy-full-frozen.log: complete990 make check1 failed3659 passed16 skipped697.33s; strict178source/Ruff+format366 and literal gate passed. Real inherited gate defect remains separately recorded above.
- session-policy-full-final-ea1d915.log: FINAL immutable make check PASSED,3666 passed16 skipped622.67s; strict178/Ruff+format367 and literal guard passed.
- session-policy-base-validator-before-executed-990652c.log:2red4green5.90s; supersedes diagnostic-only session-policy-base-validator-before-990652c.log.
- session-policy-dispatch-rerun-990652c.log: unchanged29passed0.98s.
- session-policy-delta-cancel-990652c.log / -fec7f28.log: deterministic inherited consumed-mark finding, separate correction owner.
- session-policy-sdk-source-proof.json and official source copy: version-matched options semantics.

## Eight bounded lenses

| Lens | Evidence/verdict |
|---|---|
| SOLID | Domain owns policy names; both SDK adapters own native expansion; HTTP handler owns conversion; queue owns the command without DTO leakage. |
| DRY | One permission map and one preset map; existing caller loops/graph behavior preserved, no repeated expansion or new executor. |
| Hexagonal architecture | Application contracts contain domain types; SDK/HTTP spellings stay at adapters/transport; real constructor and graph tests use external boundary doubles. |
| KISS | Existing four bundles and four permission choices; no parser for selectors, no compatibility flag, no new subsystem. |
| Typed agent calls instead of semantic heuristics | Closed enum/predefined bundle plus explicit list escape hatch; literal strings are not guessed as presets. Structured-output/session/cancellation contracts preserved. |
| Official framework practices (version-matched) | SDK0.2.151 tagged-source byte proof; actual native option/CLI argument checks; no claim of shell sandboxing. Current checkpoint enum/list roundtrip tested; unsupported cross-version workflow resume not invented. |
| Type safety | Improvement: SDK-specific literals/list unions no longer leak through application signatures and queue no longer accepts HTTP DTO. HTTP schema neutral. Later grouped settings and M3 fields explicitly deferred. |
| Repository hygiene | Clean isolated commits;56path/286hunk map; no dependency/lock change; diagnostics and original test assertions retained; separate validator/docs/gate corrections. |

## Dependencies and integration

Root may cherry-pick990652c then1553604 thenea1d915 onto the tree-equivalent maintained M1 base, preserve separate commits for review, and run fresh integrated gates. Review this exact final tree, not990's intermediate HTTP gap. The separately frozen gate cancellation correction follows only after independent review and is NOT a permission change. M4's actual judge consumer can then use these shared domain presets. M3 native scope/phase/submission fields, later session/knowledge settings, HTTP dependency/response migration, cross-version resume and full M1/milestone release remain outside this candidate. No state authority or tool-access expansion is claimed.

Requested Astra ultra was inherited; effective runtime settings remain unverified. No delegation was used.

Owning evidence: https://linear.app/duckburg/issue/KOD-73#comment-9051e0ef-6b7a-405c-bace-716620695e98 (frozen candidate checkpoint; exact final full-gate followup is published separately). Final HTTP/doc assertion/provenance proof:session-policy-final-closure-proof.json.

Final full-gate evidence:https://linear.app/duckburg/issue/KOD-73#comment-b4a57e96-249e-4667-8542-f4a8071a3885 . Separate cancellation correction frozen864bc0ab314183fed0deba95037f4b247e224552; full envelope delta-cancel-review.md.

Post-review provenance correction: root detected and I independently confirmed that1f4296c and canonical3528 already contain both delta/rearm protections.864bc0a repairs their omission from the M1 extracted tree; it is not a new canonical production repair. Earlier baseline/current red evidence remains accurate. Root will integrate source only into M1 and assess original regression tests for canonical compatibility.

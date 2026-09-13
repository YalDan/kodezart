# M1 GitSettings extraction — frozen author handoff

Candidate: `2bc237577da7a3db3101e67c324eea0eaa6e6abd`, branch `codex/v03-m1-git-settings-extraction`, isolated tree `/private/tmp/kodezart-v03-m1-git-settings-extraction`. Commits `7d95045` (source/consumer/tests/docs extraction), `2bc2375` (existing origin-literal guard owner follows the extraction). Base exactly `241e85cca03c963ec1ddd17e29f10700d499ce14` / maintained M1 PR119. Donor exactly `36083f83f42c03240ebb5861fe284da2c9f04180`. No existing tree or branch matched before creation. No maintained branch, donor, main worktree, PR or other subsystem configuration changed by this worker.

## Final scope

The donor `core/git_settings.py` is byte-identical. `AppConfig.git` replaces six fields, retaining every default: `git_remote → git.remote`, `git_base_url → git.base_url`, `clone_cache_dir → git.clone_cache_dir`, `integration_workspace_dir → git.integration_workspace_dir`, `git_committer_name → git.committer_name`, `git_committer_email → git.committer_email`.

All current M1 AppConfig consumers migrate: workspace stack, engine builder, dispatch/base resolver builder, main AgentService/stack constructors and the current smoke harness. Existing direct domain/adapter kwargs such as `git_remote=` are unchanged; they are not deprecated config aliases. The workspace builder now accepts donor `GitSettings` plus separate GitHub credential, while preserving the current M1 GitWorktreeProvider committer parameters; their removal in donor is out of scope. Future M3/M5 native callers were not copied.

Six exact retired names enter the existing source exposure/refusal mechanism. AST comparison proves every other config field and method unchanged, including source order and retired-secret no-read behavior. Prefix/case rules, validation redaction and standard initializer > environment > dotenv > file-secret precedence remain the existing mechanism. No compatibility aliases, fallback settings, Any, casts, typing ignores or invented default paths were added.

One necessary donor hunk was discovered by exercising the new native test: current M1 `SubprocessGitService.clone_bare` ignored the configured remote. The four init/env/dotenv/secret cases all failed with actual Git remote `origin` instead of `configured`. The exact donor addition `--origin self._remote` repaired this. No other Git adapter source changed. The failing evidence remains in `m1-git-settings-affected-initial.log` and `m1-git-settings-first-failure.log`.

The existing default-path policy moves with extraction: root explicitly authorized donor `git_settings.py` S108 per-file allowance as packaging of already-declared paths. No typing or other lint guard was weakened. The first full gate identified the Makefile origin-literal exception still pointing at config.py; its exact donor owner-path replacement is the entire Makefile diff. `m1-git-settings-origin-guard-before.log` preserves that finding.

## Verification

- `uv run --locked pytest -q tests/core/test_git_settings.py tests/core/test_tracker_settings.py tests/core/test_config_error_redaction.py tests/core/test_config.py tests/test_composition_root.py tests/integration/test_workflow_e2e.py`: first integrated selection **181 passed, 4 failed**; all four failures exclusively demonstrated the missing clone remote hunk, not configuration-source failure. Log `m1-git-settings-affected-initial.log`.
- Corrected GitSettings selection: **44 passed in 16.11s**, `m1-git-settings-focused-corrected.log`. Donor tests exercise real clone/cache/persist/push/author+committer identity over every source, default and custom dispatch remote/integration paths, exact retired fields over four sources, nesting errors and precedence. Ten additional controls prove no retired secret contents are read and unprefixed/archive names remain unrelated.
- Corrected GitSettings + existing Git adapter selection: **83 passed in 37.07s**, `m1-git-settings-consumers-final.log`.
- `uv run --locked mypy src/`: **179 source files clean**, `m1-git-settings-mypy-final.log`.
- `uv run --locked ruff check src/ tests/`: clean, `m1-git-settings-ruff-final.log`; formatter reports **373 files already formatted**, `m1-git-settings-format-check.log`.
- `make check` on frozen candidate: **2 failed, 3,723 passed, 16 skipped in 657.49s**, `m1-git-settings-full-gate.log`. Formatting/lint/mypy passed. Both failures are unchanged MCP reopen logging five-second timeouts (`default-True`, `pretty-False`), not Git settings tests. Unchanged targeted rerun: **1 failed (default-True), 7 passed in 11.99s**, `m1-git-settings-mcp-timeout-recheck.log`. Exact pre-extraction baseline `241e85cca03c963ec1ddd17e29f10700d499ce14` in a separate untouched checkout reproduces **1 failed (default-True), 7 passed in 14.29s**, `m1-git-settings-mcp-baseline-241.log`. All five relevant MCP/logging source/test files are byte-identical between baseline and candidate. This proves the default-mode failure predates this extraction; it does not establish the cause or clear the full gate. Do not claim a green full gate or dismiss the failures as flaky.
- `verify_m1_git_settings.py` executed under candidate Python 3.12 produces `m1-git-settings-equivalence.log` and `m1-git-settings-extraction-map.json`: exact donor GitSettings equality, only six old fields replaced by git, all other fields/methods unchanged AST, settings source unchanged except six retired constants, exact one-line Git adapter change, exact Makefile owner move, and zero stale current configuration attribute reads.
- Complete 15-file binary patch `m1-git-settings-extraction.patch`, SHA256 `b9d84a86478924cf021b5173dbcb37ac13bd2047be227868e20c8704ecb78a33`. Map carries each changed path, candidate blob/hash and exact source hunk, plus selective donor-boundary notes. No whole-donor tree-equivalence claim.

Locked framework inspected: `uv.lock` resolves Pydantic Settings **2.13.1**. Installed official `SecretsSettingsSource.__call__` initializes `secrets_paths` and delegates only configured fields; the existing wrapper subsequently rejects retired filenames without opening their values. No source priority/framework API change was made; standard model nesting is used.

## Eight bounded lenses and limits

- SOLID: Git stack construction depends on GitSettings plus its existing credential, not whole AppConfig. No new responsibility crosses the port boundary.
- DRY: one six-field authority; retired aliases are refused rather than maintained in parallel.
- Hexagonal: existing Git/queue/tracker ports and adapters remain; only current composition inputs change.
- KISS: a frozen nested settings model, no compatibility shim or invented transformation.
- Typed agent calls: no SDK, prompt, tool or structured output contract changes; real Git fixture uses existing typed executor events.
- Official framework practices: donor Pydantic BaseModel nesting and existing Settings sources retained; installed 2.13.1 source examined and precedence/source failures exercised.
- Type safety: improvement in the workspace constructor's dependency surface; all existing source strict typing stays green. No escape hatch added.
- Hygiene: isolated exact-base branch, frozen two-commit change, exact donor/retained-contract map, no unrelated config/API/native/runtime transfer.

Risk/remaining dependency: the six flat names intentionally stop loading and must be renamed as documented; grouping does not remove operator choices. Other M1 source/test behavior is unchanged except repairing configured remote cloning. M2 must use `config.git.remote` only after root integrates/reviews this prerequisite, preserving actual ancestry. The full milestone and old PR72 remain incomplete; no status or review acceptance is inferred from this slice.

Next action: fresh root review of exact frozen candidate, then root-only serialized integration into PR119 and propagation of that actual prerequisite into M4/M2. No new PR, push, merge, reset, branch deletion or force push performed here.

## Published destination and independent review

Fresh GitHub read confirms PR119 is OPEN at exact `2bc237577da7a3db3101e67c324eea0eaa6e6abd`, base main `4661a24b599d75503a997f3ce122f3ad2da77048`; root performed publication. Root independent bounded review reports 90 passed in 38.03s and approval at https://linear.app/duckburg/issue/KOD-73#comment-667cd349-e778-4818-abf7-94e735635e23 . That review does not erase the subsequently observed full-gate result. The remaining read-only scope/label/bootstrap map is `m1-remaining-scope-extraction-2bc.md` / `.json`; no further source started.

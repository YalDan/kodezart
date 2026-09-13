# Native persistence receipt retention correction

Frozen source: `1c397368ed14f901e072c0840da2afc58bdbd02a`, parent `2e7fdd9b5412d05ebabc8fd612503038f8737a1e`.
Worktree: `/private/tmp/kodezart-v03-recovery-semantic-writeback`, clean after commit.
Full diff SHA256: `50640095aaa6221a823d5fa9573b932ca39919ebb303ea21f2dbc825ddcbaf89`.

Actual `git commit` can succeed before its subsequent SHA readout fails or is cancelled. The old consumer released that committed worktree because `before_publish` had not executed. The correction marks native evidence for retention before the persistence await and clears retention only after successful return. It adds no judgment or Git observer and preserves the original exception/cancellation. No remote push occurred in either failed case. The commit remains reachable on the local branch; this is workspace retention, not object recovery.

Changed files: `src/kodezart/services/agent_service.py` (three production lines), `tests/services/test_native_commit_receipt_independent.py` (unchanged reviewer controls), `tests/services/test_agent_service.py` (authored failed-persistence cleanup control).

Executed from the worktree with `/Users/kodezart/.local/bin/uv run`:

* Before: `pytest -q tests/services/test_native_commit_receipt_independent.py` — 2 failed / 1 passed, 14.76s, `semantic-receipt-retention-before.log`.
* After: `pytest -q tests/services/test_native_commit_receipt_independent.py tests/services/test_agent_service.py tests/adapters/test_git_change_persister.py` — 21 passed, 21.66s, `semantic-receipt-retention-after.log`.
* Added explicit authored failure cleanup control: `pytest -q tests/services/test_agent_service.py` — 4 passed, 0.10s, `semantic-receipt-retention-authored.log`.
* `ruff check` and `ruff format --check` on all three changed Python files; `mypy src/kodezart/services/agent_service.py`; `git diff --check` — passed, `semantic-receipt-retention-static.log`.

All logs are in `/private/tmp/kodezart-recovery-session`. The exact reviewer test digest is `506698438a72e8c5a1f0a67f415a7100481a728a5755939fd68e51674b0a134e`.

Eight scoped lenses: requirement ordering, original-exception correctness, real cancellation, native/authored isolation, persistence protocol compatibility, actual Git side effects, evidence retention, and independent test-oracle preservation were checked. Type impact is neutral: no public protocol, state, receipt or schema change. Failed persistence now conservatively retains native evidence even if the failure preceded a commit; no exactly-once assertion is made. Authored failure cleanup remains unchanged.

Integration: root alone may cherry-pick this commit after the prior `d93f3e` / `2e7fdd9` native stack. Independent reviewer should rerun their unchanged controls. This correction does not complete full KOD97: applied AMENDED, canonical write-back/reset, durable accepted-not-actioned and escalation, and full explicit node/adoption work continue on a separate branch. The ruling reader dependency `68187b03` plus root correction `03b99ac` is accepted independently by root.

Own evidence: https://linear.app/duckburg/issue/KOD-97#comment-5f5aeee9-6852-41ac-bfdc-1d7ff3eeef62

# Audit runtime oracle and failure record

Author tree: /private/tmp/kodezart-v03-recovery-audit-runtime. This is an execution index, not independent acceptance.

## Native current versus historical records

The original assertion in `test_native_source_or_identity_failure_cannot_publish_clean_coverage`, shared by source / wrong_identity / cancel / head, was:

```python
assert not [row for row in server.comments if row.body.startswith("[native-audit:")]
```

`audit-runtime-native-final.log` preserves the full two failures: **2 failed, 7 passed, 374.32s**. For wrong_identity, actual current-check output was refused but independently valid other detector reports were published. For head (remote branch deletion), only forge evidence at the already recorded immutable Evidence SHA remained publishable. Existing audit_sweep deliberately excludes historical forge observations from current-head equality. This is a substantive policy/oracle interpretation requiring independent review, not a claim that the failures were merely harness errors.

The replacement keeps zero entire-scope publication for source and cancellation. Wrong identity / missing current head require an incomplete typed report, no summary destination comment, no current_check record, exactly one explicitly `kind=forge` record with graded_sha and report.claim.head_sha equal to the fixture's actual historical SHA, and no invented criterion key anywhere in native publication. The new closed AuditForgePublication arm makes the distinction visible to consumers. No successful coverage or current-head summary may result from historical forge evidence alone.

The original failure log can show newer source snippets around old executed lines because the test file was edited while that earlier run was live. The summary, failing node names, expected old assertion above, and exception output are preserved. Subsequent final runs must use unchanged source files for their whole lifetime.

## Operational failure taxonomy

`audit-runtime-programming-before.log`: actual executor RuntimeError was converted into unavailable; 1 real red. `audit-runtime-programming-after.log`: the identical exception now escapes, 1 pass. `audit-runtime-taxonomy-initial.log`: 22 failed / 152 passed after narrowing catches; fixture operational SDK/Git/tracker failures had generic RuntimeError types. Corresponding fixtures migrated only to AgentSDKError / GitSourceReadError / TrackerUnavailableError with existing unreadability, no-write, source and cancellation assertions retained. `audit-runtime-taxonomy-after.log`: 174 passed.

`audit-runtime-selected-final.log`: intentionally interrupted diagnostic, **4 failed / 773 passed / 553.31s**. Two `test_audit_forge` reader-error cases use raw OSError at a neutral CI port. Two `test_audit_mandate` fake-port cases leak dictionary KeyError for missing native records; its `unsupported` value was ISSUE_LABEL_SET, a now-supported artifact surface. Preserve these failures; migrate the explicit unavailable boundary to neutral errors and unsupported surface to actual CONTAINER_STATUS_UPDATE, with all covered/unreadable/no-session assertions retained. Do not add raw OSError/KeyError to operational catches.

## Report-time record loss

`audit-report-records-before.log` is an external pytest config-discovery harness error (async fixtures outside repository root without `-c`); it is not production evidence. Corrected invocation uses `pytest -c pyproject.toml` and is recorded in `audit-report-records-before-executed.log`. The unchanged external probe is `test_audit_report_records_probe.py`: real factory, Git, adapter, collection and verifier; external executor mutates native board state after first report verification but before final summary. The contract is KOD518: every summary claim resolves to a native record at report time.

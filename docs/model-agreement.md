# Model agreement checks

The spec conformance module reads model membership through
`TrackerPort.read_labeled_issues`, using the `criterion_lifecycle` semantic key
in the existing `OperationConfig.issue_labels` mapping. Configure its value as
the workspace's model marker. The query includes archived issues and every
workflow state. It follows all pages, hydrates full issues, and refuses duplicate
identities, unreadable state, missing labels, changed membership or incomplete
pagination. It does not use the bounded queue scan.

The suite reads each member's current criterion children through `read_criteria`.
Its pointer invariant then reads referenced target bodies through the same port.
It accepts explicit D-number headings and numbered lists inside a deliverables
section, and verifies native criterion keys against their actual parent family.
Condensed Fix bullets, comments, quoted examples and missing or duplicate targets
fail the corresponding numbered reference. Ordinary issue citations do not
become invented deliverable definitions.

Reference forms cover native issue mentions, Markdown links displaying the
issue key, and a backtick-quoted issue key followed by an explicit D number.
Parenthesized D-number lists are checked member by member. Unexpanded ranges
refuse; this checker does not guess the meaning of arbitrary prose or infer a
definition from implementation code. Source text, model membership, criterion
families and external target bodies are checked again before the result returns.

Run the deterministic suite with:

```sh
uv run pytest tests/spec/test_model_agreement.py tests/tracker/test_labeled_issues.py
```

The committed synthetic workspace in `tests/spec/fixtures/model_members.json` is
a regression fixture only. It is not the current board's specification. Adding a
marked member without editing any existing body sends that member through the
actual pointer checker. The full model's other invariants, including semantic
definition uniqueness, still require their own exercised consumers.

The separate live test uses the existing operation configuration, tracker
credential and MCP transport. It performs no boot reconciliation or tracker
writes. Supply `KODEZART_MODEL_SNAPSHOT` as the path to a previously captured JSON
projection returned by `model_agreement`; the snapshot contains the queried
member keys, body/parent/label facts and criterion family keys. Capture and review
the baseline separately. The comparison never refreshes its expected snapshot.

```sh
KODEZART_MODEL_SNAPSHOT=/path/to/reviewed-model-snapshot.json \
  uv run pytest -m live tests/spec/test_model_agreement.py
```

An absent snapshot, operation config or credential fails this explicitly selected
live test. The default CI run skips it. CI success is therefore neither a current
board agreement verdict nor proof that the live runner has been supplied. A live
comparison fails on snapshot drift and on unresolved current pointers.

Native document references may contain only their identifier and title. Full
issue reads retain that measured shape. When the asset reader needs a missing
document URL, it reads the exact native document and checks identity and title
before returning the URL. Attachment URLs remain required; no URL is synthesized
and no document row is silently dropped.

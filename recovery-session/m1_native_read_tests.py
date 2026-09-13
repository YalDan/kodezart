from pathlib import Path
import subprocess,ast
R=Path('/private/tmp/kodezart-v03-m1-native-read-extraction')
def donor(p):return subprocess.check_output(['git','show','7892ca1:'+p],cwd=R,text=True)
(R/'tests/tracker/native_read_fixtures.py').write_text('''"""Configured native reader with only the external MCP server doubled."""
from kodezart.adapters.linear_mcp_tracker import LinearMcpTracker
from kodezart.core.backoff import RetryPolicy
from kodezart.types.domain.dispatch import SelfWriteLedger
from tests.tracker.conftest import QUEUE_STATE_LABELS, WORKFLOW_STATE_NAMES, TEAM_IDENTIFIERS
from tests.tracker.marker_config import MARKER_PREFIXES

LABELS = {"criterion": "acceptance-condition", "decision": "recorded-question", "tracker": "execution-history"}

def tracker_over(server, *, issue_labels=None):
    return LinearMcpTracker(caller=server, issue_labels=LABELS if issue_labels is None else issue_labels,
        marker_prefixes=MARKER_PREFIXES, queue_state_labels=QUEUE_STATE_LABELS,
        workflow_state_names=WORKFLOW_STATE_NAMES, team_identifiers=TEAM_IDENTIFIERS,
        retry=RetryPolicy(attempts=1,initial_delay=0), ledger=SelfWriteLedger())

def native_tracker(server, labels):
    return tracker_over(server, issue_labels=labels)
''')
p='tests/tracker/test_criterion_reader_boundary.py';s=donor(p).replace('from tests.tracker.test_linear_mcp_tracker import tracker_over','from tests.tracker.native_read_fixtures import tracker_over');(R/p).write_text(s)
p='tests/tracker/test_issue_classification_reads.py';s=donor(p).replace('from tests.tracker.test_scope_planning import LABELS, native_tracker','from tests.tracker.native_read_fixtures import LABELS, native_tracker');(R/p).write_text(s)
p='tests/tracker/test_criterion_reader.py';s=donor(p)
s=s.replace('from tests.fakes import FakeMcpIssue, FakeTrackerPort','from tests.fakes import FakeMcpIssue\nfrom tests.tracker.native_read_fixtures import tracker_over')
# Preserve native oracles; fake round-trip boundary gets its own focused test.
a=s.index('    if isinstance(tracker, FakeTrackerPort):');b=s.index('    assert [',a)
replacement='''    server.issues[FIRST].labels = []
    server.issues[SECOND].parent_id = FIRST
    server.issues["ordinary/1"].labels = [LABEL]
'''
s=s[:a]+replacement+s[b:]
s+='''

@pytest.fixture
def tracker(server):
    return tracker_over(server)
'''
(R/p).write_text(s)
(R/'tests/tracker/test_native_read_constructor.py').write_text(Path('/private/tmp/kodezart-recovery-session/test_m1_native_read_before.py').read_text())
# Native binding census is necessary now: labels are consumed by actual tracker constructor, not decorative prompt prose.
p=R/'tests/prompts/test_operation_config.py';s=p.read_text().replace('    assert set(mapped.values()) == set(OperationConfig.model_fields)','''    native = dict(markdown_rows("## Native OperationConfig consumers"))
    assert native == {"issue_labels": "composition/tracker.py::build_tracker"}
    assert set(mapped).isdisjoint(native)
    assert set(mapped.values()) | set(native) == set(OperationConfig.model_fields)''',1)
s=s.replace('    unreachable = set(OperationConfig.model_fields) - reachable','''    native = dict(markdown_rows("## Native OperationConfig consumers"))
    unreachable = set(OperationConfig.model_fields) - reachable - set(native)''',1)
p.write_text(s)
p=R/'docs/cutover_mapping.md';p.write_text(p.read_text()+'''\n\n## Native OperationConfig consumers

| OperationConfig field | Actual native consumer |
| --- | --- |
| `issue_labels` | `composition/tracker.py::build_tracker` |
''')
p=R/'docs/operation.example.toml';p.write_text(p.read_text()+'''\n\n# Semantic issue classification keys map to labels this operation owns.
# Native criterion membership uses current direct children with this label.
[issue_labels]
criterion = "Verification requirement"
decision = "Unresolved decision"
tracker = "Run record"
''')
p=R/'docs/architecture.md';s=p.read_text().replace('| TrackerPort       |','| TrackerCriteriaReader | LinearMcpTracker | Complete current native criterion families, no authoring or execution |\n| TrackerPort       |',1);s+='''\n\n## Native criterion reads

The operation's `issue_labels` map names semantic classifications in the tracker.
Boot instates missing owned definitions through the existing mapping reconciler
and adopts existing definitions unchanged. `TrackerCriteriaReader.read_criteria`
reads complete current direct-child membership, including archived and completed
criteria, with full body/state/parentage. Failed or incomplete reads raise
`CriterionReadError`; a successful empty family is a distinct result.

The ordinary issue reader retains its existing compatibility contract.
`read_planning_issue` requires reported native labels and relations, and explicit
classification capability methods reject absent or blank required mappings before
I/O. These shared readers grant no authority to create, execute, grade or change
criteria and add no scope-label or state-transition policy.
''';p.write_text(s)

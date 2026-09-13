import ast
import hashlib
import json
import re
import subprocess
from pathlib import Path

root = Path('/private/tmp/kodezart-v03-m1-native-read-extraction')
base = 'a4575cd11133c5e631fff454de8cbac682f5a30e'
donor = '7892ca1a47adbcfd91fbcfb1e580dfc86f066a99'

def git(*args):
    return subprocess.check_output(['git', *args], cwd=root, text=True)

def nodes(source):
    out = {}
    def walk(node, prefix=''):
        for child in getattr(node, 'body', []):
            if isinstance(child, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                name = prefix + child.name
                out[name] = child
                walk(child, name + '.')
    walk(ast.parse(source))
    return out

notes = {
 'src/kodezart/adapters/linear_mcp_tracker.py': 'Canonical native-read methods and explicit constructor classification mapping; existing label ensure/resolution arm. Post-donor f3c3dd3 blank-map refusal and fd19b9a one-read parent-address validation. No write/graph/native-runtime methods extracted.',
 'src/kodezart/adapters/linear_mcp_types.py': 'Canonical LinearCriterionIssueWire only; additional measured LinearAddressedIssueWire/UUID helper in fd19b9a. Existing planning wire unchanged.',
 'src/kodezart/core/protocols.py': 'Canonical TrackerCriteriaReader and three strict/capability TrackerPort method declarations; no author/fire/event protocols.',
 'src/kodezart/types/domain/tracker.py': 'Canonical semantic issue_labels field, ISSUE_LABEL kind and instatable membership only.',
 'src/kodezart/types/domain/operation.py': 'Canonical issue_labels field, nonblank/unique mapping validation, OWNED registry row only.',
 'src/kodezart/composition/tracker.py': 'Exact canonical production constructor argument issue_labels=operation.issue_labels.',
 'src/kodezart/core/prompt_namespaces.py': 'Canonical issue_labels binding; absent-aware map is consumed by native tracker boot, no decorative prompt text.',
 'src/kodezart/domain/errors.py': 'Exact canonical CriterionReadError class.',
 'src/kodezart/services/tracker_boot.py': 'Canonical declared ISSUE_LABEL refs and ownership builder; existing generic boot reconciliation retained.',
 'tests/fakes.py': 'Canonical narrow reader/capability methods and external MCP parentId filtering; no semantic writer fake.',
 'tests/tracker/test_criterion_reader.py': 'Canonical read-only tests and owned-label ensure test. Later parent_rewrite writer test requires absent edit_description and remains pending M4, not weakened or claimed passed.',
 'tests/tracker/test_criterion_reader_boundary.py': 'Canonical test oracles unchanged; import uses explicit native-read fixture instead of later constructor helper.',
 'tests/tracker/test_issue_classification_reads.py': 'Canonical capability/read tests; absent later fake.issue_writes assertion replaced by exact existing fake issue/comment/queue/workflow no-write and no-read collections.',
 'tests/tracker/test_criterion_family_identity.py': 'New original immutable three regressions; identical bytes reproduced red on canonical donor, corrections separate.',
 'tests/tracker/test_criterion_family_aliases.py': 'New immutable native UUID positive and three foreign/malformed alias refusals; actual adapter and external MCP only.',
 'tests/tracker/test_native_read_configuration.py': 'New actual build_tracker/boot/capability/error controls and narrow fake contract positive.',
 'tests/tracker/test_native_read_constructor.py': 'New actual constructor and empty-membership controls; same initial absence probe after only formatting.',
 'tests/tracker/native_read_fixtures.py': 'Explicit actual LinearMcpTracker constructor with configured labels; no production fallback.',
 'tests/prompts/test_operation_config.py': 'Exact field and native+template consumer census with truthful issue_labels native owner; no decorative template reference.',
 'tests/tracker/test_tracker_boot.py': 'Existing fixture constructors explicit empty mapping; complete category census adds an actual configured issue label and retains equality.',
 'docs/architecture.md': 'Bounded native read protocol and config boot behavior documentation, no full L1/M4 claim.',
 'docs/cutover_mapping.md': 'Truthful native issue_labels consumer row preserving template census.',
 'docs/operation.example.toml': 'Existing canonical semantic label config shape with example-specific backend spellings.',
}
donor_paths = set(git('ls-tree', '-r', '--name-only', donor).splitlines())
rows = []
for path in git('diff', '--name-only', base, 'HEAD').splitlines():
    current = (root / path).read_text()
    original = git('show', donor + ':' + path) if path in donor_paths else ''
    diff = git('diff', '--unified=3', base, 'HEAD', '--', path)
    hunks = re.split(r'(?=^@@ )', diff, flags=re.M)[1:]
    row = {'path': path, 'sha256': hashlib.sha256(current.encode()).hexdigest(),
           'owner': 'M1 minimal native read/config boot',
           'donor': donor, 'mapping': notes.get(path, 'Mechanical existing test constructor declares issue_labels={} explicitly; original oracle unchanged.'),
           'hunks': [{'header': h.splitlines()[0], 'sha256': hashlib.sha256(h.encode()).hexdigest(), 'patch': h} for h in hunks]}
    if path.endswith('.py') and original:
        before, after = nodes(original), nodes(current)
        touched = []
        for name, node in after.items():
            if name in before:
                old = before[name]
                identical = ast.dump(node, include_attributes=False) == ast.dump(old, include_attributes=False)
                if identical:
                    touched.append({'symbol':name,'donor_line':old.lineno,'current_line':node.lineno,'ast_identical':True})
        row['unchanged_donor_symbols'] = touched
    rows.append(row)
report = {'base':base,'head':git('rev-parse','HEAD').strip(),'tree':git('rev-parse','HEAD^{tree}').strip(),
          'donor':donor,'initial_hunk_index':'m1-native-read-initial-hunks.json',
          'source_only_corrections':['f3c3dd3dbd8aa06ae4a97811d85db43c89f2b3c0',git('rev-parse','fd19b9a').strip()],
          'pending':['M4 label/criterion artifact reader consumes these native facts after independent M1 acceptance.', 'M4 later parent-rewrite preservation writer regression remains with its actual edit_description writer.', 'Canonical M3 approval consumer must normalize verified UUID subject before supplied-family call; separate correction under coordination.', 'SDK PermissionMode/ToolPreset migration is a separate M1 tranche.'],
          'files':rows}
Path('/private/tmp/kodezart-recovery-session/m1-native-read-provenance.json').write_text(json.dumps(report,indent=2)+'\n')
print(report['head'], len(rows), 'files mapped')

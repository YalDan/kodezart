import ast
import hashlib
import json
import subprocess
from pathlib import Path

ROOT = Path('/private/tmp/kodezart-v03-m4-shared-semantic-extraction')
DONOR = '36083f83f42c03240ebb5861fe284da2c9f04180'
proof = []

def source(path):
    return subprocess.check_output(['git', 'show', f'{DONOR}:{path}'], cwd=ROOT).decode()

def node(path, name):
    text = source(path)
    for item in ast.parse(text).body:
        if getattr(item, 'name', None) == name:
            value = '\n'.join(text.splitlines()[item.lineno - 1:item.end_lineno])
            proof.append({'path': path, 'symbol': name, 'donor': DONOR, 'sha256': hashlib.sha256(value.encode()).hexdigest()})
            return value
    raise LookupError((path, name))

def append(path, names):
    target = ROOT / path
    text = target.read_text()
    for name in names:
        assert not any(getattr(n, 'name', None) == name for n in ast.parse(text).body), (path, name)
        text += '\n\n' + node(path, name) + '\n'
    target.write_text(text)

def imports(path, before, after):
    target = ROOT / path
    text = target.read_text()
    assert text.count(before) == 1, (path, before)
    target.write_text(text.replace(before, after))

def whole(path):
    target = ROOT / path
    raw = source(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(raw)
    proof.append({'path': path, 'whole_file': True, 'donor': DONOR, 'sha256': hashlib.sha256(raw.encode()).hexdigest()})

for path in ['src/kodezart/adapters/subprocess_git_source_reader.py', 'src/kodezart/domain/rulings.py', 'src/kodezart/services/ruling_records.py', 'src/kodezart/types/domain/ruling_id.py']:
    assert not (ROOT / path).exists(), path
    whole(path)

path = 'src/kodezart/types/domain/assertion_drift.py'
assert not (ROOT / path).exists()
(ROOT / path).write_text('''"""Source-owned protected-test addresses and exact Git source objects."""

from pathlib import PurePosixPath
from typing import Self

from pydantic import ConfigDict, Field, model_validator

from kodezart.types.base import CamelCaseModel
''')
append(path, ['ProtectedTestRef', 'GitSourceBlob'])

path = 'src/kodezart/types/domain/agent.py'
imports(path, 'from kodezart.types.base import CamelCaseModel', 'from kodezart.types.base import CamelCaseModel\nfrom kodezart.types.domain.assertion_drift import ProtectedTestRef\nfrom kodezart.types.domain.ruling_id import RulingId as RulingId')
append(path, ['RulingAuthor', 'RulingClass', 'RulingProtectedTestRef', 'Ruling'])

path = 'src/kodezart/domain/agent.py'
before = (ROOT / path).read_text()
old_ast = ast.parse(before)
donor_ast = ast.parse(source(path))
for old in old_ast.body:
    if isinstance(old, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        match = next(n for n in donor_ast.body if getattr(n, 'name', None) == old.name)
        assert ast.dump(old) == ast.dump(match), (path, old.name)
whole(path)

append('src/kodezart/domain/errors.py', ['GitSourceReadError', 'RulingRecordReadError'])
path = 'src/kodezart/core/protocols.py'
imports(path, 'from kodezart.types.domain.agent import (', 'from kodezart.types.domain.assertion_drift import GitSourceBlob\nfrom kodezart.types.domain.agent import (')
append(path, ['GitSourceReader', 'TrackerCommentReader'])

for name in ['tests/domain/test_ruling_identity.py', 'tests/domain/test_ruling_protected_tests.py', 'tests/domain/test_rulings.py', 'tests/services/test_git_source_lookup.py', 'tests/tracker/test_issue_ruling_records.py', 'tests/tracker/test_recorded_ruling_growth.py', 'tests/tracker/test_ruling_reader_independent.py', 'tests/tracker/test_ruling_records.py']:
    assert not (ROOT / name).exists(), name
    whole(name)

Path('/private/tmp/kodezart-recovery-session/m4-ruling-source-initial-map.json').write_text(json.dumps(proof, indent=2) + '\n')
print('Mapped', len(proof), 'exact donor file/symbol units')

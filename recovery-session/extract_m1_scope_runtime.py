from pathlib import Path
import ast
import subprocess
import json
import hashlib

root = Path('/private/tmp/kodezart-v03-m1-scope-ports')
donor = 'd2c6fceab762191d4e40b23c8cd349ef476e4b12'
assert not subprocess.check_output(['git','status','--porcelain'], cwd=root).strip()
ledger = []
def source(path):
    return subprocess.check_output(['git','show',f'{donor}:{path}'],cwd=root).decode()
def write(path, content, selector='whole file'):
    p = root/path
    p.parent.mkdir(parents=True,exist_ok=True)
    p.write_text(content)
    ledger.append({'path':path,'selector':selector,'source_sha':donor,'destination_content_sha256':hashlib.sha256(content.encode()).hexdigest()})
def item(path, name, parent=None):
    text=source(path);tree=ast.parse(text)
    nodes=tree.body if parent is None else next(n for n in tree.body if isinstance(n,ast.ClassDef) and n.name==parent).body
    node=next(n for n in nodes if getattr(n,'name',None)==name)
    return ''.join(text.splitlines(keepends=True)[node.lineno-1:node.end_lineno])+'\n'
def append_methods(path,parent,names):
    p=root/path;text=p.read_text();tree=ast.parse(text)
    node=next(n for n in tree.body if isinstance(n,ast.ClassDef) and n.name==parent)
    lines=text.splitlines(keepends=True)
    methods='\n'+ '\n'.join(item(path,n,parent) for n in names)
    lines.insert(node.end_lineno,methods)
    write(path,''.join(lines),parent+': '+', '.join(names))
def insert_import(path,content):
    p=root/path;text=p.read_text();tree=ast.parse(text)
    first=next(n for n in tree.body if isinstance(n,(ast.Import,ast.ImportFrom)))
    lines=text.splitlines(keepends=True);lines.insert(first.lineno-1,content+'\n');p.write_text(''.join(lines))

for path in ['src/kodezart/adapters/linear_mcp_types.py','src/kodezart/adapters/linear_scope_types.py','src/kodezart/adapters/linear_scope_reader.py','src/kodezart/adapters/pagination.py','tests/tracker/test_scope_reads.py','tests/domain/test_scope_container.py']:
    write(path,source(path))
old=root/'src/kodezart/types/domain/linear_mcp.py'
assert old.exists();old.unlink()
for path in ['src/kodezart/adapters/linear_mcp_tracker.py','tests/tracker/test_linear_wire_shapes.py']:
    p=root/path;s=p.read_text();assert 'kodezart.types.domain.linear_mcp' in s
    write(path,s.replace('kodezart.types.domain.linear_mcp','kodezart.adapters.linear_mcp_types'),'vendor wire relocation import')
for path,parent in [('src/kodezart/core/protocols.py','TrackerPort'),('src/kodezart/adapters/linear_mcp_tracker.py','LinearMcpTracker'),('tests/fakes.py','FakeTrackerPort')]:
    append_methods(path,parent,['scope_issues','container_metadata'])
    insert_import(path,'from kodezart.types.domain.scope import ScopeContainer, ScopeKind, ScopeRef' if path=='tests/fakes.py' else 'from kodezart.types.domain.scope import ScopeContainer, ScopeRef')
path='src/kodezart/adapters/linear_mcp_tracker.py'
insert_import(path,'from kodezart.adapters.linear_scope_reader import SCOPE_READ_TOOLS, LinearScopeReader')
p=root/path;s=p.read_text();s=s.replace('_READ_TOOLS: Final[frozenset[str]] = frozenset(\n    {','_READ_TOOLS: Final[frozenset[str]] = frozenset(\n    {\n        *SCOPE_READ_TOOLS,',1);p.write_text(s)
path='src/kodezart/domain/errors.py';p=root/path;write(path,p.read_text()+'\n\n'+item(path,'ScopeReadError'),'ScopeReadError');insert_import(path,'from kodezart.types.domain.scope import ScopeRef')
path='tests/fakes.py';insert_import(path,'from kodezart.domain.errors import ScopeReadError');p=root/path;s=p.read_text();a=s.index('class FakeTrackerPort:');first=s[:a];tail=s[a:];tail=tail.replace('        issues: Sequence[TrackerIssue] = (),\n','        issues: Sequence[TrackerIssue] = (),\n        scope_containers: Sequence[ScopeContainer] = (),\n        scope_memberships: Mapping[ScopeRef, Sequence[str]] | None = None,\n',1);tail=tail.replace('        self.recorded_work_refs:', '        self.scope_containers: dict[ScopeRef, ScopeContainer] = {\n            container.ref: container for container in scope_containers\n        }\n        self.scope_memberships: dict[ScopeRef, tuple[str, ...]] = {\n            ref: tuple(keys) for ref, keys in (scope_memberships or {}).items()\n        }\n        self.recorded_work_refs:',1);p.write_text(first+tail)
# The final port boundary's two exception types and translations; retry settings
# are a separate M1 tranche and keep their current main-compatible constructor.
path='src/kodezart/core/errors.py';p=root/path;write(path,p.read_text()+'\n\n'+item(path,'TrackerUnavailableError')+'\n\n'+item(path,'TrackerAccessDeniedError'),'tracker port failure types')
path='src/kodezart/adapters/linear_mcp_tracker.py';p=root/path;s=p.read_text();a=s.index('    async def _call(');b=s.index('    def _validate[',a);part=s[a:b];part=part.replace('                raise\n','                raise TrackerAccessDeniedError(str(exc)) from exc\n',1).replace('                    raise\n','                    raise TrackerUnavailableError(str(exc)) from exc\n',1);p.write_text(s[:a]+part+s[b:]);insert_import(path,'from kodezart.core.errors import TrackerAccessDeniedError, TrackerUnavailableError')
Path('/private/tmp/kodezart-recovery-session/extraction-m1-scope-runtime-source-map.json').write_text(json.dumps({'donor':donor,'base':'3c477a3489bd0bf555ae7b3a08569c2c5bf049be','extracted':ledger},indent=2)+'\n')
print('Extracted',len(ledger),'file/member entries; validation pending')

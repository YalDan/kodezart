from pathlib import Path
import ast, subprocess
D='7892ca1'
def read(p):return subprocess.check_output(['git','show',f'{D}:{p}'],text=True)
def node(p,name):
 s=read(p);n=next(n for n in ast.parse(s).body if getattr(n,'name',None)==name);start=min(n.lineno,*[d.lineno for d in n.decorator_list]) if getattr(n,'decorator_list',[]) else n.lineno;return ''.join(s.splitlines(keepends=True)[start-1:n.end_lineno])
p=Path('src/kodezart/prompts/sets/claude-opus/set.toml');s=p.read_text().replace('content_audit = []','content_audit = []\nwrite_back_verify = ["code-review"]');p.write_text(s)
p=Path('src/kodezart/prompts/sets/anthropic_v5/set.toml');s=p.read_text().replace('    "criteria_validation",','    "criteria_validation",\n    "write_back_verify",');p.write_text(s)
p=Path('src/kodezart/core/prompt_namespaces.py');s=p.read_text().replace('        "base_ref",','        "base_ref",\n        "written_artifact",');p.write_text(s)
p=Path('tests/chains/test_fresh_write_back_judge.py');s=read(str(p));s=s.replace('from kodezart.types.domain.session import SessionType, ToolPreset','from kodezart.types.domain.session import SessionType\nfrom kodezart.core.constants import EVAL_TOOLS');s=s.replace('from tests.chains.test_organize import RecordingExecutor, RecordingWorkspace, result','from tests.chains.write_back_fixtures import RecordingWorkspace, result\nfrom tests.fakes import FakeAgentExecutor');s=s.replace('RecordingExecutor(', 'FakeAgentExecutor(').replace('SessionType.ORGANIZE_PASS','SessionType.SCHEDULED_PASS').replace('call["allowed_tools"] is ToolPreset.EVALUATION','call["allowed_tools"] == EVAL_TOOLS');p.write_text(s)
Path('tests/chains/write_back_fixtures.py').write_text('''"""External workspace and structured result fixtures for the shared judge."""
from kodezart.types.domain.agent import ResultEvent
from tests.fakes import FakeWorkspaceProvider


def result(**changes: object) -> ResultEvent:
    return ResultEvent.model_validate({
        "result": "Author transcript is not fresh evidence.",
        "session_id": "previous-agent-session", "subtype": "result",
        "duration_ms": 1, "duration_api_ms": 1, "is_error": False,
        "num_turns": 1, **changes,
    })


class RecordingWorkspace(FakeWorkspaceProvider):
    def __init__(self) -> None:
        super().__init__()
        self.arguments: list[dict[str, object]] = []

    async def acquire(self, **kwargs):
        self.arguments.append(kwargs)
        return await super().acquire(**kwargs)
''')
p=Path('tests/types/test_wire_schemas.py');s=p.read_text().replace('import re\n','import ast\nimport re\nimport sys\nfrom collections import Counter\n');s=s.replace('from kodezart.types.domain.criteria import CRITERION_ID_PATTERN','from kodezart.types.domain.criteria import CRITERION_ID_PATTERN\nfrom kodezart.types.domain.write_back import WriteBackFinding');s=s.replace('SCHEMA_ARGUMENT = re.compile(r\'"schema"\\s*:\\s*([A-Za-z_][A-Za-z0-9_.()\\[\\]]*)\')', '''SCHEMA_ARGUMENT = re.compile(
    r'(?:"schema"\\s*:\\s*|output_schema\\s*=\\s*)([A-Za-z_][A-Za-z0-9_.()\\[\\]]*)'
)''');s=s.replace('    "DRAFT_CRITIQUE_SCHEMA": DraftCritiqueOutput,','    "DRAFT_CRITIQUE_SCHEMA": DraftCritiqueOutput,\n    "WRITE_BACK_SCHEMA": WriteBackFinding,');n=ast.parse(s);fn=next(n for n in n.body if getattr(n,'name',None)=='test_every_dispatch_site_sends_the_models_own_schema');lines=s.splitlines(keepends=True);lines[fn.lineno-1:fn.end_lineno]=[node(str(p),'test_every_dispatch_site_sends_the_models_own_schema')+'\n'];s=''.join(lines)
helpers='\n\n'.join(node(str(p),name) for name in ['source_files','scoped_calls','audit_forwarding_lines','audit_schema_bindings'])
# The M6 FreshAuditSession bridge is a pending owner-specific addition.
a=helpers.index('            if (\n                scope == ("FreshAuditSession", "judge")');b=helpers.index('            if (\n                scope != ("judge_in_workspace",)',a);helpers=helpers[:a]+helpers[b:]
s+='\n\n'+helpers+'''\n\nAUDIT_SCHEMA_BINDINGS = [
    ("chains/write_back_verifier.py", ("FreshWriteBackJudge", "judge"),
     "judge_in_workspace", "WRITE_BACK_SCHEMA"),
]
'''
for name in ['test_only_the_actual_owned_audit_forwarding_site_is_registered','test_shared_judgment_callers_keep_their_exact_schema_and_forwarding_chain']:
 t=node(str(p),name).replace('assert len(forwarded) == 2','assert len(forwarded) == 1');s+='\n\n'+t
# Preserve the original damaged-forwarder oracle for the one extracted forwarding site.
t=node(str(p),'test_audit_schema_forwarding_registration_is_scoped_and_unfiltered').replace('@pytest.mark.parametrize("bridge", [False, True])','@pytest.mark.parametrize("bridge", [False])');s+='\n\n'+t
s+='''

@pytest.mark.parametrize("damage", ["caller", "filter", "unrelated", "missing"])
def test_actual_writeback_dispatch_census_rejects_changed_source(monkeypatch, damage):
    sources = source_files()
    if damage == "caller":
        sources["chains/write_back_verifier.py"] = sources["chains/write_back_verifier.py"].replace(
            "output_schema=WRITE_BACK_SCHEMA", "output_schema=COMMIT_MESSAGE_SCHEMA"
        )
        guard = test_shared_judgment_callers_keep_their_exact_schema_and_forwarding_chain
    elif damage == "missing":
        sources.pop("chains/write_back_verifier.py")
        guard = test_shared_judgment_callers_keep_their_exact_schema_and_forwarding_chain
    else:
        path = "services/audit_sessions.py" if damage == "filter" else "services/other.py"
        sources[path] = sources["services/audit_sessions.py"].replace(
            '"schema": output_schema',
            '"schema": sanitize_schema(output_schema)' if damage == "filter" else '"schema": output_schema',
        )
        guard = test_every_dispatch_site_sends_the_models_own_schema
    monkeypatch.setattr(sys.modules[__name__], "source_files", lambda: sources)
    with pytest.raises(AssertionError):
        guard()
'''
p.write_text(s)

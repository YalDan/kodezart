from pathlib import Path
import ast, hashlib, json, subprocess
DONOR='7892ca1a47adbcfd91fbcfb1e580dfc86f066a99'
HISTORICAL='7042032'
rows=[]
def read(path, ref=DONOR):
 return subprocess.check_output(['git','show',f'{ref}:{path}'],text=True)
def part(path, name, ref=DONOR):
 source=read(path,ref); tree=ast.parse(source)
 node=next(n for n in tree.body if getattr(n,'name',None)==name)
 start=min([node.lineno,*[d.lineno for d in getattr(node,'decorator_list',[])]])
 text=''.join(source.splitlines(keepends=True)[start-1:node.end_lineno])
 rows.append(dict(destination=path,symbol=name,donor=ref,sha256=hashlib.sha256(text.encode()).hexdigest()))
 return text

def save(path,text):
 p=Path(path);p.parent.mkdir(parents=True,exist_ok=True);p.write_text(text)
def copy(path,ref=DONOR):
 text=read(path,ref);save(path,text);rows.append(dict(destination=path,donor=ref,whole_file_sha256=hashlib.sha256(text.encode()).hexdigest()))
for path in ['src/kodezart/chains/write_back_verifier.py','src/kodezart/types/domain/write_back.py','src/kodezart/services/owned_workspace.py','src/kodezart/prompts/sets/claude-opus/write_back_verify.md','src/kodezart/prompts/sets/anthropic_v5/write_back_verify.md','tests/chains/test_write_back_verifier.py']:
 copy(path)
copy('src/kodezart/services/tracker_artifacts.py',HISTORICAL)
save('src/kodezart/types/domain/audit.py','''"""Shared verdict and addressed artifact consumed by canonical write verification."""

from enum import StrEnum
from pydantic import ConfigDict, Field
from kodezart.types.base import CamelCaseModel
from kodezart.types.domain.surface import WritableSurface


'''+part('src/kodezart/types/domain/audit.py','AuditVerdict')+'\n\n'+part('src/kodezart/types/domain/audit.py','TrackerArtifact'))
save('src/kodezart/services/audit_sessions.py','''"""Fresh structured judgment in an already owned workspace."""

from kodezart.core.constants import EVAL_PERMISSION_MODE, EVAL_TOOLS
from kodezart.core.errors import soft_failure
from kodezart.core.protocols import AgentRunner, PromptSetProvider
from kodezart.core.stream_drain import drain
from kodezart.types.domain.agent import RaiseSite
from kodezart.types.domain.prompts import PromptKey
from kodezart.types.domain.session import SessionType
from kodezart.types.domain.skills import SkillsSelection
from kodezart.types.domain.subagents import NO_SUBAGENTS


'''+part('src/kodezart/services/audit_sessions.py','judge_in_workspace',HISTORICAL))
save('src/kodezart/services/git_observations.py','''"""Settle current workspace facts before cancellation releases their owner."""

from kodezart.core.owned_tasks import settle
from kodezart.core.protocols import GitService


'''+part('src/kodezart/services/git_observations.py','read_workspace_head'))
p=Path('src/kodezart/domain/errors.py');p.write_text(p.read_text()+'\n\n'+part(str(p),'WriteBackReadError'))
p=Path('src/kodezart/core/protocols.py');s=p.read_text();needle='    async def has_changes(self, cwd: str) -> bool: ...\n';assert needle in s;s=s.replace(needle,needle+'''
    async def has_replace_refs(self, cwd: str) -> bool:
        """Whether native object replacement is configured for this repository."""
        ...
''');p.write_text(s)
p=Path('src/kodezart/adapters/subprocess_git_service.py');s=p.read_text();start=s.index('    async def is_path_ignored');s=s[:start]+'''    async def has_replace_refs(self, cwd: str) -> bool:
        """Read replacement refs from Git's active replacement namespace."""
        output = await self._run_output(["git", "replace", "--list"], cwd=cwd)
        return bool(output)

'''+s[start:];p.write_text(s)
p=Path('tests/fakes.py');s=p.read_text();s=s.replace('        is_path_ignored_result: bool = False,','        is_path_ignored_result: bool = False,\n        has_replace_refs_result: bool = False,',1);s=s.replace('        self.has_changes_result: bool = has_changes_result','        self.has_changes_result: bool = has_changes_result\n        self.has_replace_refs_result = has_replace_refs_result',1);start=s.index('    async def is_path_ignored');s=s[:start]+'''    async def has_replace_refs(self, cwd: str) -> bool:
        self.calls.append(("has_replace_refs", cwd))
        return self.has_replace_refs_result

'''+s[start:];p.write_text(s)
p=Path('src/kodezart/types/domain/agent.py');s=p.read_text();s=s.replace('from kodezart.types.domain.trajectory import LoopTrajectory','from kodezart.types.domain.trajectory import LoopTrajectory\nfrom kodezart.types.domain.write_back import WriteBackFinding');s=s.replace('    "content_audit",\n','    "content_audit",\n    "write_back_verify",\n',1);s=s.replace('#: Every wire schema this system dispatches','WRITE_BACK_SCHEMA: dict[str, object] = WriteBackFinding.model_json_schema()\n\n#: Every wire schema this system dispatches');s=s.replace('    "DRAFT_CRITIQUE_SCHEMA": DRAFT_CRITIQUE_SCHEMA,','    "DRAFT_CRITIQUE_SCHEMA": DRAFT_CRITIQUE_SCHEMA,\n    "WRITE_BACK_SCHEMA": WRITE_BACK_SCHEMA,');p.write_text(s)
p=Path('src/kodezart/types/domain/prompts.py');s=p.read_text().replace('    CONTENT_AUDIT = "content_audit"','    CONTENT_AUDIT = "content_audit"\n    WRITE_BACK_VERIFY = "write_back_verify"');p.write_text(s)
Path('/private/tmp/kodezart-recovery-session/m4-verifier-initial-provenance.json').write_text(json.dumps(rows,indent=2)+'\n')

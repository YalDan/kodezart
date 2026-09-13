import ast
import pathlib
import subprocess
root=pathlib.Path('/private/tmp/kodezart-v03-m4-verifier-extraction')
def donor(path):
 return subprocess.check_output(['git','show',f'7892ca1:{path}'],cwd=root,text=True)
s=donor('tests/services/test_audit_session_ownership.py')
s=s.replace('Canceled audit sessions release the actual detached worktree they acquired.', 'The actual fresh write-back judge owns its pinned native worktree.')
s=s.replace('AuditClaimReadError','WriteBackReadError')
s=s.replace('from tests.services.test_audit_sessions import invoke\nfrom tests.services.test_audit_sessions import session as session', '''from kodezart.services.agent_service import AgentService
from tests.chains.test_fresh_write_back_judge import ARTIFACT, build
from tests.fakes import FakeRepoCache


@pytest.fixture
def session(provider, git_repo, monkeypatch):
    judge, executor, _ = build({"verdict": "holds", "evidence": "Checked native source."})
    provider._cache = FakeRepoCache(repo_path=str(git_repo))
    judge._workspace = provider
    judge._git = provider._git
    judge._runner = AgentService(executor=executor, workspace=provider, git_base_url="https://example.invalid")
    original = executor.stream
    executor.during = None

    async def stream(**kwargs):
        if executor.during is not None:
            await executor.during()
        async for event in original(**kwargs):
            yield event

    monkeypatch.setattr(executor, "stream", stream)
    judge.executor = executor
    return judge


def invoke(session, *, repository, head_sha):
    session._repo_url = repository
    return session.judge(artifact=ARTIFACT, ref=head_sha)''')
s=s.replace('session._runner.during','session.executor.during').replace('session._runner.calls','session.executor.calls')
s=s.replace('"repo_path": str(git_repo),\n                "repo_url": None,','"repo_path": None,\n                "repo_url": str(git_repo),')
# During-cancel happens before executor bookkeeping; count actual callback entry for replacement.
s=s.replace('await executor.during()','executor.entered = True\n            await executor.during()')
s=s.replace('assert bool(session.executor.calls) is (phase == "during")','assert bool(session.executor.calls) is (phase == "during")')
(root/'tests/chains/test_write_back_workspace_ownership.py').write_text(s)
s=donor('tests/git_read_cancellation.py')
tree=ast.parse(s)
node=next(n for n in tree.body if isinstance(n,ast.AsyncFunctionDef) and n.name=='assert_git_read_settles_before_release')
lines=s.splitlines(keepends=True)
(root/'tests/chains/write_back_read_cancellation.py').write_text('''"""Actual subprocess cancellation control for the shared verifier."""
import asyncio
import os
import sys
from kodezart.adapters.subprocess_git_service import SubprocessGitService


'''+''.join(lines[node.lineno-1:node.end_lineno])+'\n')
p=root/'tests/chains/test_fresh_write_back_judge.py'
s=p.read_text().replace('import pytest','from functools import partial\n\nimport pytest',1).replace('from tests.chains.write_back_fixtures', 'from tests.chains.write_back_read_cancellation import assert_git_read_settles_before_release\nfrom tests.chains.write_back_fixtures')
s+='''

@pytest.mark.parametrize("phase", ["current_sha", "has_changes", "has_replace_refs"])
@pytest.mark.parametrize("read_number", [1, 2])
async def test_git_read_settles_before_workspace_release(monkeypatch, tmp_path, phase, read_number):
    judge, _, workspace = build({"verdict": "holds", "evidence": "Source checked."})
    await assert_git_read_settles_before_release(
        invoke=partial(judge.judge, artifact=ARTIFACT, ref=HEAD),
        git=judge._git, workspace=workspace, monkeypatch=monkeypatch,
        tmp_path=tmp_path, phase=phase, read_number=read_number,
    )
'''
p.write_text(s)
p=root/'tests/types/test_wire_schemas.py'
s=p.read_text().replace('Only the actual shared dispatch and its owned-workspace bridge forward.', 'Only the actual shared dispatch forwards its caller-provided schema.')
s=s.replace('@pytest.mark.parametrize("bridge", [False])\n','').replace('def test_audit_schema_forwarding_registration_is_scoped_and_unfiltered(bridge, damage):','def test_audit_schema_forwarding_registration_is_scoped_and_unfiltered(damage):')
s=s.replace('''        "class FreshAuditSession:\\n"
        "    async def judge(self):\\n"
        "        return judge_in_workspace(output_schema=output_schema)\\n"
        if bridge
        else "async def judge_in_workspace():\\n"''','''        "async def judge_in_workspace():\\n"''')
s=s.replace('indentation = "        " if bridge else "    "','indentation = "    "')
p.write_text(s)

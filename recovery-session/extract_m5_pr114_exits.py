import ast
from pathlib import Path
D=Path('/private/tmp/kodezart-recovery-session/m5-pr114-coordinator.py').read_text();F=Path('/private/tmp/kodezart-recovery-session/m5-pr114-forge-isolation.py').read_text()
def unit(s,name):
 n=next(n for n in ast.parse(s).body if getattr(n,'name',None)==name or isinstance(n,(ast.Assign,ast.AnnAssign)) and any(isinstance(t,ast.Name) and t.id==name for t in (n.targets if isinstance(n,ast.Assign) else [n.target])))
 a=min([n.lineno]+[d.lineno for d in getattr(n,'decorator_list',[])]);return ''.join(s.splitlines(True)[a-1:n.end_lineno]).rstrip()
s='''"""PR114 real-Git exit and ref-invariance scenarios, without source scanners.

The named scenarios exercise cleanup and publication invariance directly;
no AST inventory or object-holdings heuristic is treated as runtime proof.
"""

import asyncio
from pathlib import Path

import pytest

from kodezart.adapters.subprocess_check_chain import SubprocessCheckChainRunner
from kodezart.chains.delivery_coordinator import ScopeUnionCoordinator
from kodezart.core.config import AppConfig
from kodezart.domain.errors import CheckChainExecutionError, MergeConflictError
from kodezart.types.domain.operation import CheckStep
from kodezart.types.domain.union import UnionLaneHead, UnionOutcome
from kodezart.types.domain.union_tick import UnionTickContext
from tests.chains.test_delivery_coordinator import OPENED_ORDER, PROJECT, RaisingRunner, Scope
from tests.services import test_union_composition as pinned

INDEPENDENT_EDITS = {lane: (f"{lane}.txt", lane) for lane in OPENED_ORDER}
CONFLICTING_EDITS = {
    "z": ("api.py", "def build(timeout):\\n    return timeout\\n"),
    "a": ("api.py", "def build(credentials):\\n    return credentials\\n"),
}
COMPOSED_CHECK = pinned.entry().checks[0].command

'''
s+='\n\n'.join(unit(D,n).replace('DeliveryCoordinator','ScopeUnionCoordinator') for n in ['Fixture','make_repository','build_delivery'])+'\n\n'
s+='\n\n'.join(unit(F,n) for n in ['ForbiddenPublisher','PathlessConflict','BlockedCreate','drive_green','drive_merge_conflict','drive_pathless_conflict','drive_unclassifiable_chain','drive_undeclared_chain','drive_unobservable_chain','drive_cancellation','EXIT_SCENARIOS'])+'\n\n'
s+='''@pytest.mark.parametrize("name, edits, publisher, drive", EXIT_SCENARIOS, ids=[row[0] for row in EXIT_SCENARIOS])
async def test_named_union_exit_preserves_real_refs_and_removes_scratch(
    tmp_path, name, edits, publisher, drive,
):
    fixture = await build_delivery(tmp_path / "world", edits=edits, git=publisher())
    before = await fixture.refs()

    await drive(fixture)

    assert await fixture.refs() == before, name
    assert fixture.git.created == fixture.git.removed, name
    assert all(not Path(path).exists() for path in fixture.git.created), name
    assert (await pinned.git(fixture.observer, "worktree", "list", "--porcelain")).count("worktree ") == 1
'''
Path('tests/chains/test_union_exit_invariance.py').write_text(s)
p=Path('tests/chains/test_delivery_coordinator.py');s=p.read_text();s+='''

@pytest.mark.parametrize("changed", ["membership", "reference"])
async def test_roster_change_during_measurement_refuses_before_return(delivery, changed):
    class MovingRosterRunner(SubprocessCheckChainRunner):
        async def run_chain(self, *, cwd, steps):
            result = await super().run_chain(cwd=cwd, steps=steps)
            if changed == "membership":
                delivery.tracker.scope_memberships[PROJECT] = ("z",)
            else:
                delivery.tracker.recorded_work_refs["z"] = [
                    work_ref("z", "work/a", delivery.sha("a"))
                ]
            return result

    runner = MovingRosterRunner(timeout=AppConfig().union_check_step_timeout_seconds)
    with pytest.raises(UnionHeadReadError, match="roster changed"):
        await delivery.coordinator(runner).verify()
    assert delivery.git.created == delivery.git.removed
    assert all(not Path(path).exists() for path in delivery.git.created)
''';p.write_text(s)

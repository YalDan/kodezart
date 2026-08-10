"""The verification pass's pre-query, over the in-process git fakes.

Same claim as ``PassGate`` and checked the same way: the gate costs no
tokens, and that is asserted over the object and the module's imports
rather than over its prose, because a claim about cost stated only in a
docstring survives someone adding an executor to the constructor.

The reason this is a second gate rather than a second ``PassGate`` is
KOD-60 R11: a build goes red when the code moves, never when a label
moves, so a query over the board predicts nothing about a check chain.
"""

import ast
import re
from pathlib import Path

from kodezart.services.trunk_gate import TrunkGate
from tests.fakes import FakeGitService, FakeRepoCache

GATE_SOURCE = (
    Path(__file__).resolve().parents[2]
    / "src"
    / "kodezart"
    / "services"
    / "trunk_gate.py"
)

REPO_URL = "https://github.com/example/widget"
TRUNK = "main"
REMOTE = "origin"
TIP = "a" * 40
MOVED = "b" * 40


def gate(git: FakeGitService) -> TrunkGate:
    return TrunkGate(
        git=git,
        cache=FakeRepoCache(),
        repo_url=REPO_URL,
        trunk=TRUNK,
        remote=REMOTE,
    )


async def test_a_tip_nobody_verified_is_reported() -> None:
    """A first tick has verified nothing, so every tip is new."""
    assert (
        await gate(FakeGitService(remote_branch_shas={TRUNK: TIP})).unverified_tip()
        == TIP
    )


async def test_the_verified_tip_is_reported_no_further() -> None:
    """The recorded tip is the whole cost gate."""
    subject = gate(FakeGitService(remote_branch_shas={TRUNK: TIP}))
    subject.record(TIP)

    assert await subject.unverified_tip() is None
    assert subject.verified == TIP


async def test_a_tip_that_moved_past_the_verified_one_is_reported_again() -> None:
    """Guards the case above: a gate stuck shut would satisfy it."""
    subject = gate(FakeGitService(remote_branch_sha_sequences={TRUNK: [TIP, MOVED]}))
    subject.record(await subject.unverified_tip() or "")

    assert await subject.unverified_tip() == MOVED


async def test_a_branch_the_remote_does_not_carry_is_not_an_unchanged_one() -> None:
    """An absent declared trunk is a configuration fault, reported as absent."""
    subject = gate(FakeGitService(remote_branch_shas={TRUNK: None}))

    assert await subject.unverified_tip() is None
    assert subject.verified is None


async def test_the_gate_asks_the_remote_rather_than_a_checkout() -> None:
    """``ls-remote`` against the mirror: no worktree, no fetch of a tree."""
    git = FakeGitService(remote_branch_shas={TRUNK: TIP})
    await gate(git).unverified_tip()

    assert ("remote_branch_sha", "/tmp/fake-cache", REMOTE, TRUNK) in git.calls


#: The method each cost-bearing port is recognised by.  Identical to the
#: predicate ``test_pass_gate`` uses, and deliberately so: one rule about
#: what a pre-query may hold, applied to both pre-queries.
_COST_BEARING_METHODS: tuple[str, ...] = (
    "stream",
    "template_for",
    "resolution_table",
    "scan",
)


def _could_reach_a_model(value: object) -> bool:
    return any(hasattr(value, name) for name in _COST_BEARING_METHODS)


class _Executorish:
    """Stands in for the collaborator the gate must never acquire."""

    def stream(self) -> None: ...


def test_the_cost_predicate_recognises_an_executor_shaped_collaborator() -> None:
    """Guards the test below: a predicate that never fires proves nothing."""
    assert _could_reach_a_model(_Executorish())


def test_the_gate_holds_no_collaborator_that_could_reach_a_model() -> None:
    """AC-19: zero model involvement, asserted over the object, not the prose."""
    subject = gate(FakeGitService(remote_branch_shas={TRUNK: TIP}))

    assert [
        value for value in vars(subject).values() if _could_reach_a_model(value)
    ] == []


def test_the_gate_module_imports_nothing_that_could_reach_a_model() -> None:
    """A prompt, an executor or a skills selection here would be a cost."""
    tree = ast.parse(GATE_SOURCE.read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            imported.add(node.module)
    assert not [
        name
        for name in imported
        if re.search(r"executor|prompt|agent|claude|skills", name)
    ]

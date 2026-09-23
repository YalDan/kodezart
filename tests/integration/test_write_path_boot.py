"""A deployment refuses to boot over a tracker write path nothing accounts for.

KOD-533's second clause, driven through the real application lifespan of a
scope deployment configured from the shipped files: a write path that has
neither a write-back verifier nor a derived-write declaration beside its
writer raises a typed startup error naming it, before the tracker is dialled
and before anything is written.  The two halves of "neither" are each shown
to boot, so the refusal is about the missing account and not about a
planted module as such.

A write path is a call site in the installed code a deployment boots, so a
case configures one by planting a module into the tree the gate reads, beside
the real tree and never instead of it.
"""

import json
from collections.abc import Mapping

import pytest

from kodezart.composition import write_adoption
from kodezart.composition.write_adoption import (
    NO_WRITE_FOUND,
    installed_sources,
    verify_write_adoption,
)
from kodezart.domain.errors import UnverifiedWritePathError
from kodezart.main import create_app, lifespan
from tests.chains.test_write_back_adoption import DIRECT, DRIVEN, PLANTED, census
from tests.integration.test_scope_deployment import (
    guide_environment,
    scratch_project,
    shipped,
)
from tests.tools.scratch_board import ScratchBoardServer

#: The write path the refusal must name: module, function and write.
DIRECT_PATH = "planted/direct.py::Writer.publish::post_comment"


def logged(captured: str, name: str) -> list[dict[str, object]]:
    return [
        event
        for line in captured.splitlines()
        if line.strip().startswith("{")
        for event in [json.loads(line.strip())]
        if event.get("event") == name
    ]


def deployment(monkeypatch: pytest.MonkeyPatch) -> ScratchBoardServer:
    """The shipped scope deployment over a scratch board, and nothing else."""
    loaded = shipped()
    server = ScratchBoardServer(operation=loaded, project=scratch_project(loaded))
    for name, value in guide_environment().items():
        monkeypatch.setenv(name, value)
    monkeypatch.setattr(
        "kodezart.composition.tracker.make_mcp_tool_caller",
        lambda **_: server,
    )
    return server


def installed_with(monkeypatch: pytest.MonkeyPatch, planted: Mapping[str, str]) -> None:
    """Boot over the installed tree with *planted* modules beside it."""
    tree = {**installed_sources(), **planted}
    monkeypatch.setattr(write_adoption, "installed_sources", lambda: tree)


async def boots(capsys: pytest.CaptureFixture[str]) -> None:
    """One whole boot and shutdown, reconciling the mappings exactly once."""
    capsys.readouterr()
    async with lifespan(create_app()):
        reconciled = logged(capsys.readouterr().out, "tracker_mappings_reconciled")
        assert len(reconciled) == 1


async def test_a_deployment_with_an_unadopted_write_path_refuses_to_boot_naming_it(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The planted direct writer is named, and nothing was dialled or written.

    The same deployment first boots clean over the installed tree, so a gate
    that answered from what it saw first rather than from the tree in front
    of it would boot the planted one too.  The refused boot answered no tool
    call, opened no tracker session and reconciled nothing, which is what
    places the refusal before every dial and every write.
    """
    server = deployment(monkeypatch)
    await boots(capsys)

    installed_with(monkeypatch, {"planted/direct.py": DIRECT})
    calls, lifecycle = len(server.calls), list(server.lifecycle)
    with pytest.raises(UnverifiedWritePathError) as refused:
        async with lifespan(create_app()):
            pass

    assert refused.value.paths == (DIRECT_PATH,)
    assert DIRECT_PATH in str(refused.value)
    assert server.calls[calls:] == []
    assert server.lifecycle == lifecycle
    assert logged(capsys.readouterr().out, "tracker_mappings_reconciled") == []


async def test_a_boot_refusal_names_every_unadopted_write_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Two unadopted writers are both named, in order, in the error and its paths.

    A refusal that named only the first would leave the second to be found
    on the next boot, so the whole set is the thing asserted.
    """
    deployment(monkeypatch)
    other = "planted/other.py::Writer.publish::post_comment"
    installed_with(
        monkeypatch, {"planted/direct.py": DIRECT, "planted/other.py": DIRECT}
    )
    with pytest.raises(UnverifiedWritePathError) as refused:
        async with lifespan(create_app()):
            pass

    assert refused.value.paths == (DIRECT_PATH, other)
    for path in (DIRECT_PATH, other):
        assert path in str(refused.value)


async def test_a_write_path_declared_derived_boots(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The same undriven write, declared beside its writer, is held out."""
    deployment(monkeypatch)
    module = "planted/declared.py"
    installed_with(monkeypatch, {module: PLANTED["declared"]})

    await boots(capsys)
    assert f"{module}::Writer.publish::post_comment" in {
        str(site) for site in census((module, PLANTED["declared"])).held_out
    }


async def test_a_driven_write_path_passes_the_boot_check(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The same write, applied by a step the verifier drives, is driven."""
    deployment(monkeypatch)
    module = "planted/driven.py"
    installed_with(monkeypatch, {module: DRIVEN})

    await boots(capsys)
    assert f"{module}::Writer.publish.put::post_comment" in {
        str(site) for site in census((module, DRIVEN)).driven
    }


def test_the_shipped_tree_passes_the_boot_check() -> None:
    """The installed tree boots, and says something in each direction."""
    passed = verify_write_adoption()
    assert passed.paths == ()
    assert passed.driven and passed.held_out
    assert verify_write_adoption() is passed


async def test_an_empty_source_tree_refuses_to_boot(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A tree the gate finds no write in is refused rather than passed.

    A packaging change that left the census nothing to read would otherwise
    boot every deployment with no write path checked at all.
    """
    server = deployment(monkeypatch)
    monkeypatch.setattr(write_adoption, "installed_sources", dict)
    capsys.readouterr()
    with pytest.raises(UnverifiedWritePathError) as refused:
        async with lifespan(create_app()):
            pass

    assert refused.value.paths == (NO_WRITE_FOUND,)
    assert server.calls == []
    assert server.lifecycle == []
    assert logged(capsys.readouterr().out, "tracker_mappings_reconciled") == []

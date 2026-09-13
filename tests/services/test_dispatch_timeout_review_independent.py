"""Synchronization preserves the exact cancellation oracle under a delayed gate."""

import ast
import asyncio
import subprocess

import pytest

from kodezart.services.pass_gate import PassGate
from tests.services import test_dispatch_pass as original


def delayed_gate(monkeypatch):
    construct = original.failing_tick

    def delayed(*args, **kwargs):
        tick, gate, dispatcher = construct(*args, **kwargs)
        emit = gate._log.ainfo

        async def delayed_emit(*args, **kwargs):
            await asyncio.sleep(0.05)
            await emit(*args, **kwargs)

        monkeypatch.setattr(gate._log, "ainfo", delayed_emit)
        return tick, gate, dispatcher

    monkeypatch.setattr(original, "failing_tick", delayed)


def legacy_oracle():
    source = subprocess.check_output(
        [
            "git",
            "show",
            "873855ecbf70aeb4ff7fc8a3fe69adcfb8468290:tests/services/test_dispatch_pass.py",
        ],
        text=True,
    )
    tree = ast.parse(source)
    owner = next(
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef)
        and node.name == "TestAFailedPassGivesTheWakeUpBack"
    )
    method = next(
        node
        for node in owner.body
        if getattr(node, "name", None)
        == "test_a_tick_cancelled_on_its_budget_keeps_its_window_too"
    )
    namespace = dict(vars(original))
    exec(
        compile(ast.Module(body=[method], type_ignores=[]), "frozen-873855", "exec"),
        namespace,
    )
    return namespace[method.name]


async def test_original_oracle_before_entry_is_red_under_declared_gate_latency(
    monkeypatch,
):
    delayed_gate(monkeypatch)
    await legacy_oracle()(original.TestAFailedPassGivesTheWakeUpBack())


async def test_candidate_still_cancels_entered_dispatch_under_gate_latency(monkeypatch):
    delayed_gate(monkeypatch)
    await original.TestAFailedPassGivesTheWakeUpBack().test_a_tick_cancelled_on_its_budget_keeps_its_window_too()


async def test_candidate_refuses_a_broken_rearm_under_same_gate_latency(monkeypatch):
    delayed_gate(monkeypatch)
    monkeypatch.setattr(PassGate, "rearm", lambda self: None)
    with pytest.raises(AssertionError) as caught:
        await original.TestAFailedPassGivesTheWakeUpBack().test_a_tick_cancelled_on_its_budget_keeps_its_window_too()
    assert "guard.mark" in str(caught.traceback[-1].statement)

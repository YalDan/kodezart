"""The declared check chain, loaded from TOML and validated at load.

A step may name the step it depends on, and the loader rejects a chain that
cannot be read: a dependency on an undeclared step, a cycle, a duplicate
name.  These are load-time failures rather than report-time ones, so a
malformed chain fails loudly at boot.
"""

from pathlib import Path

import pytest

from kodezart.adapters.toml_operation_config import load_operation_config
from kodezart.core.errors import OperationConfigError
from kodezart.types.domain.operation import OperationConfig

_MINIMAL_TAIL = """
[documents.checkpoint]
system = "tracker"
name = "checkpoint"
id = "doc-1"

[records.run_log]
system = "knowledge"
id = "record-1"
append_only = true

[knowledge]

[endpoints]

[[initiatives]]
id = "init-1"
"""

_HEAD = """
operation_name = "fixture"
workspace = "fixture-workspace"
agent_identities = []

[[principals]]
tracker_user = "user-a"
roles = ["approver", "principal", "assignee"]
handle = "@user-a"

[teams]
primary = "team-1"

[queue_states]
triage = "queue:triage"
proposed = "queue:proposed"
approved = "queue:approved"
done = "queue:done"
decision = "queue:decision"

[workflow_states]
in_progress = "In Progress"
in_review = "In Review"
done = "Done"
"""


def _config(chain_toml: str, tmp_path: Path) -> OperationConfig:
    path = tmp_path / "operation.toml"
    path.write_text(
        _HEAD
        + '\n[[repos]]\nurl = "https://example.invalid/repo"\ntrunk = "main"\n'
        + chain_toml
        + _MINIMAL_TAIL,
        encoding="utf-8",
    )
    return load_operation_config(path)


_GATE_PLUS_DEPENDENTS = """
[[repos.checks]]
name = "lint"
command = "make lint"

[[repos.checks]]
name = "type-check"
command = "make type-check"
depends_on = "lint"

[[repos.checks]]
name = "test"
command = "make test"
depends_on = "type-check"
"""


def test_a_gate_plus_dependents_round_trips_through_the_model(
    tmp_path: Path,
) -> None:
    """The structure survives TOML -> model, naming what depends on what."""
    steps = _config(_GATE_PLUS_DEPENDENTS, tmp_path).repos[0].checks
    assert [step.name for step in steps] == ["lint", "type-check", "test"]
    assert [step.depends_on for step in steps] == [None, "lint", "type-check"]


def test_a_chain_depending_on_an_unknown_step_is_rejected_at_load(
    tmp_path: Path,
) -> None:
    """An unclassifiable chain fails loudly at load, not silently at report."""
    chain = """
[[repos.checks]]
name = "test"
command = "make test"
depends_on = "lint"
"""
    with pytest.raises(OperationConfigError) as excinfo:
        _config(chain, tmp_path)
    assert any("unknown step" in failure for failure in excinfo.value.failures)


def test_a_cyclic_chain_is_rejected_at_load(tmp_path: Path) -> None:
    chain = """
[[repos.checks]]
name = "a"
command = "make a"
depends_on = "b"

[[repos.checks]]
name = "b"
command = "make b"
depends_on = "a"
"""
    with pytest.raises(OperationConfigError) as excinfo:
        _config(chain, tmp_path)
    assert any("cycle" in failure for failure in excinfo.value.failures)


def test_a_duplicate_step_name_is_rejected_at_load(tmp_path: Path) -> None:
    chain = """
[[repos.checks]]
name = "lint"
command = "make lint"

[[repos.checks]]
name = "lint"
command = "make lint --again"
"""
    with pytest.raises(OperationConfigError) as excinfo:
        _config(chain, tmp_path)
    assert any("duplicate step name" in failure for failure in excinfo.value.failures)

"""KOD-112 defect 5 — the check chain carries gate-versus-cascade structure.

The passes carry an honesty rule: report one root failure plus its cascades,
never a list of independent-looking reds.  A flat ``list[str]`` cannot say
which command the rule is about, so the rule was unfollowable for any repo
whose chain has more than one step.  These tests exercise the structure end
to end — TOML on disk, through ``OperationConfig``, into the classifier a
consumer calls — because the criterion is about a ROUND TRIP, not about a
model field existing.
"""

from pathlib import Path

import pytest

from kodezart.adapters.toml_operation_config import load_operation_config
from kodezart.core.errors import OperationConfigError
from kodezart.domain.check_chain import classify_check_failures
from kodezart.types.domain.operation import CheckStep, OperationConfig

_MINIMAL_TAIL = """
[documents.checkpoint]
id = "doc-1"

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
role = "approver"

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
        + '\n[[repos]]\nurl = "https://example.invalid/repo"\n'
        + chain_toml
        + _MINIMAL_TAIL,
        encoding="utf-8",
    )
    return load_operation_config(path)


_GATE_PLUS_DEPENDENTS = """
[[repos.check_commands]]
name = "lint"
command = "make lint"

[[repos.check_commands]]
name = "type-check"
command = "make type-check"
depends_on = "lint"

[[repos.check_commands]]
name = "test"
command = "make test"
depends_on = "type-check"
"""


def test_a_gate_plus_dependents_round_trips_through_the_model(
    tmp_path: Path,
) -> None:
    """The structure survives TOML -> model, naming what depends on what."""
    steps = _config(_GATE_PLUS_DEPENDENTS, tmp_path).repos[0].check_commands
    assert [step.name for step in steps] == ["lint", "type-check", "test"]
    assert [step.depends_on for step in steps] == [None, "lint", "type-check"]


def test_a_consumer_can_tell_the_root_failure_from_its_cascades(
    tmp_path: Path,
) -> None:
    """The whole point: three reds, one problem."""
    steps = _config(_GATE_PLUS_DEPENDENTS, tmp_path).repos[0].check_commands
    classification = classify_check_failures(
        steps,
        ["lint", "type-check", "test"],
    )
    assert classification.roots == ("lint",)
    assert classification.cascades == ("type-check", "test")


def test_two_independent_gates_failing_are_two_roots(tmp_path: Path) -> None:
    """The paired negative: not everything collapses to one root."""
    chain = """
[[repos.check_commands]]
name = "lint"
command = "make lint"

[[repos.check_commands]]
name = "docs"
command = "make docs"
"""
    steps = _config(chain, tmp_path).repos[0].check_commands
    classification = classify_check_failures(steps, ["lint", "docs"])
    assert classification.roots == ("lint", "docs")
    assert classification.cascades == ()


def test_a_dependent_failing_alone_is_its_own_root(tmp_path: Path) -> None:
    """A cascade is a cascade only when its ancestor actually failed."""
    steps = _config(_GATE_PLUS_DEPENDENTS, tmp_path).repos[0].check_commands
    classification = classify_check_failures(steps, ["test"])
    assert classification.roots == ("test",)
    assert classification.cascades == ()


def test_a_cascade_two_levels_below_a_failed_gate_is_still_a_cascade(
    tmp_path: Path,
) -> None:
    """Reachability, not adjacency: ``test`` cascades from ``lint``."""
    steps = _config(_GATE_PLUS_DEPENDENTS, tmp_path).repos[0].check_commands
    classification = classify_check_failures(steps, ["lint", "test"])
    assert classification.roots == ("lint",)
    assert classification.cascades == ("test",)


def test_classification_is_ordered_by_the_declared_chain(tmp_path: Path) -> None:
    """Two runs over one chain and one failure set produce one report."""
    steps = _config(_GATE_PLUS_DEPENDENTS, tmp_path).repos[0].check_commands
    first = classify_check_failures(steps, ["test", "lint", "type-check"])
    second = classify_check_failures(steps, ["lint", "type-check", "test"])
    assert first == second


def test_a_failure_naming_no_declared_step_is_reported_as_a_root() -> None:
    """Never silently dropped: an undeclared failure has no ancestor to blame."""
    steps = [CheckStep(name="lint", command="make lint")]
    classification = classify_check_failures(steps, ["lint", "mystery"])
    assert classification.roots == ("lint", "mystery")
    assert classification.cascades == ()


def test_a_chain_depending_on_an_unknown_step_is_rejected_at_load(
    tmp_path: Path,
) -> None:
    """An unclassifiable chain fails loudly at load, not silently at report."""
    chain = """
[[repos.check_commands]]
name = "test"
command = "make test"
depends_on = "lint"
"""
    with pytest.raises(OperationConfigError) as excinfo:
        _config(chain, tmp_path)
    assert any("unknown step" in failure for failure in excinfo.value.failures)


def test_a_cyclic_chain_is_rejected_at_load(tmp_path: Path) -> None:
    chain = """
[[repos.check_commands]]
name = "a"
command = "make a"
depends_on = "b"

[[repos.check_commands]]
name = "b"
command = "make b"
depends_on = "a"
"""
    with pytest.raises(OperationConfigError) as excinfo:
        _config(chain, tmp_path)
    assert any("cycle" in failure for failure in excinfo.value.failures)


def test_a_duplicate_step_name_is_rejected_at_load(tmp_path: Path) -> None:
    chain = """
[[repos.check_commands]]
name = "lint"
command = "make lint"

[[repos.check_commands]]
name = "lint"
command = "make lint --again"
"""
    with pytest.raises(OperationConfigError) as excinfo:
        _config(chain, tmp_path)
    assert any("duplicate step name" in failure for failure in excinfo.value.failures)

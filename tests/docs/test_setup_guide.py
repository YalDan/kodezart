"""The setup guide is checked against the surface it instructs against.

A guide is executable only if every name in it is real.  Each assertion
here derives one side from the code and reads the other out of the README,
so an event renamed in `src/` or an error class that stops existing makes
this module red rather than leaving an operator following instructions for
a service that no longer behaves that way.
"""

import re
from pathlib import Path

from kodezart.core import errors
from kodezart.types.domain.dispatch import DispatchOutcome
from kodezart.types.domain.operation import QueueState

REPO_ROOT = Path(__file__).resolve().parents[2]
README = REPO_ROOT / "README.md"
SRC = REPO_ROOT / "src" / "kodezart"

GUIDE_HEADING = "### Setting up the self-running service"

#: Every startup or pass event the guide tells an operator to look for.
#: Each must be emitted somewhere under ``src/``.
CITED_EVENTS: frozenset[str] = frozenset(
    {
        "tracker_not_configured",
        "tracker_mappings_reconciled",
        "scheduled_passes_not_wired",
        "pass_scheduler_started",
        "pass_gate_delta",
        "dispatch_pass_completed",
    },
)

#: Every failure class the guide's boot-verify table names.
CITED_ERRORS: frozenset[str] = frozenset(
    {
        "OperationConfigError",
        "TrackerBootValidationError",
        "TrackerEnsureConflictError",
    },
)

#: Every environment variable the guide instructs the operator to set.
CITED_VARIABLES: frozenset[str] = frozenset(
    {
        "KODEZART_TRACKER_TOKEN",
        "KODEZART_OPERATION_CONFIG",
        "KODEZART_GITHUB_TOKEN",
    },
)


def _guide() -> str:
    """The setup section only, so a match elsewhere in the README is not one."""
    body = README.read_text(encoding="utf-8")
    start = body.index(GUIDE_HEADING)
    rest = body[start + len(GUIDE_HEADING) :]
    end = rest.find("\n## ")
    return rest if end == -1 else rest[:end]


def _emitted_events() -> set[str]:
    """Every structlog event name emitted anywhere under ``src/``."""
    events: set[str] = set()
    for source in sorted(SRC.rglob("*.py")):
        events.update(
            re.findall(
                r"a(?:info|warning|error|debug)\(\s*\n?\s*\"([a-z0-9_]+)\"",
                source.read_text(encoding="utf-8"),
            ),
        )
    return events


def test_the_setup_guide_exists_and_has_content() -> None:
    """Guards the assertions below: an empty section would satisfy them all."""
    guide = _guide()

    assert len(guide) > 1000
    assert "Smoke test" in guide
    # And the event scan actually scans: an empty set makes the subset
    # assertion below fail loudly rather than pass, but a near-empty one
    # would not, so the floor is stated here.
    assert len(_emitted_events()) > len(CITED_EVENTS) * 2


def test_every_event_the_guide_tells_an_operator_to_watch_for_is_emitted() -> None:
    """AC-23: an instruction to look for an event nothing logs is a dead end."""
    emitted = _emitted_events()

    assert CITED_EVENTS <= emitted, CITED_EVENTS - emitted
    for event in CITED_EVENTS:
        assert event in _guide(), event


def test_every_failure_class_the_guide_names_exists() -> None:
    """An operator matching a traceback against a class that is gone is stuck."""
    guide = _guide()

    for name in CITED_ERRORS:
        assert hasattr(errors, name), name
        assert name in guide, name


def test_every_variable_the_guide_sets_is_a_shipped_config_field() -> None:
    """The guide instructs against the env surface the code actually reads."""
    from kodezart.core.config import AppConfig

    shipped = {f"KODEZART_{name.upper()}" for name in AppConfig.model_fields}
    guide = _guide()

    for variable in CITED_VARIABLES:
        assert variable in shipped, variable
        assert variable in guide, variable


def test_the_guide_names_every_queue_state_the_code_addresses_by_name() -> None:
    """A member the operator never creates is a boot the guide cannot deliver."""
    guide = _guide()

    for member in QueueState:
        assert member.value in guide, member


def test_the_smoke_test_names_real_dispatch_outcomes() -> None:
    """The two outcomes the guide asks the operator to distinguish are real."""
    guide = _guide()

    assert DispatchOutcome.fire_enqueued.value in guide
    assert DispatchOutcome.empty_eligible_set.value in guide

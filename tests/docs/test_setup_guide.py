"""The setup guide is checked against the surface it instructs against.

A guide is executable only if every name in it is real.  Each assertion
here derives one side from the code and reads the other out of the README,
so an event renamed in `src/` or an error class that stops existing makes
this module red rather than leaving an operator following instructions for
a service that no longer behaves that way.
"""

import re
import tomllib
from fnmatch import fnmatch
from pathlib import Path

from kodezart.core import errors
from kodezart.types.domain.dispatch import DispatchOutcome
from kodezart.types.domain.operation import (
    DocumentEntry,
    OperationModel,
    Principal,
    PrincipalRole,
    QueueState,
    RecordDestination,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
README = REPO_ROOT / "README.md"
GITIGNORE = REPO_ROOT / ".gitignore"
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
        "TrackerCredentialExpiryError",
    },
)

#: Every environment variable the guide MUST instruct the operator to set.
#: A floor, not an inventory: the guide may name more, and every name it
#: does carry is checked against the shipped model by the test below.
CITED_VARIABLES: frozenset[str] = frozenset(
    {
        "KODEZART_TRACKER_TOKEN",
        "KODEZART_OPERATION_CONFIG",
        "KODEZART_GITHUB_TOKEN",
    },
)

#: The model that owns each config section the guide prints a block for.
#: A block declaring a section absent here fails rather than going
#: unvalidated, so a new section cannot be shown to an operator without
#: naming the model it has to satisfy.
SECTION_MODELS: dict[str, type[OperationModel]] = {
    "documents": DocumentEntry,
    "records": RecordDestination,
}


def _guide() -> str:
    """The setup section only, so a match elsewhere in the README is not one."""
    body = README.read_text(encoding="utf-8")
    start = body.index(GUIDE_HEADING)
    rest = body[start + len(GUIDE_HEADING) :]
    end = rest.find("\n## ")
    return rest if end == -1 else rest[:end]


def _guide_toml_blocks() -> list[str]:
    """Every fenced TOML block the guide prints for an operator to copy."""
    return re.findall(r"```toml\n(.*?)```", _guide(), flags=re.DOTALL)


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


def test_every_toml_block_the_guide_prints_satisfies_the_shipped_model() -> None:
    """A block an operator copies must load, not merely look like config.

    Step 4's block declared ``system`` and ``id`` and no ``name``, which
    ``DocumentEntry`` requires — so an operator following only the guide
    wrote a checkpoint entry that fails validation at load, while
    ``docs/operation.example.toml`` two steps later was right. Parsed here
    through the model itself rather than checked against a transcribed
    field list, so the guide cannot drift from the shape it instructs
    against.
    """
    blocks = _guide_toml_blocks()

    assert blocks, "the guide prints no TOML at all"
    for block in blocks:
        parsed = tomllib.loads(block)
        assert parsed, block
        for section, entries in parsed.items():
            model = SECTION_MODELS[section]
            for entry in entries.values():
                model.model_validate(entry)


def test_every_failure_class_the_guide_names_exists() -> None:
    """An operator matching a traceback against a class that is gone is stuck."""
    guide = _guide()

    for name in CITED_ERRORS:
        assert hasattr(errors, name), name
        assert name in guide, name


def test_the_guide_states_the_long_lived_credential_requirement() -> None:
    """KOD-186: the requirement boot enforces is the one the guide states.

    An operator who pastes an OAuth access token gets a service that works
    for the length of that token and then refuses every tracker call — the
    2026-09-01 failure.  Boot refuses that credential, so the guide has to
    say so before step 1 sends anybody to mint one.
    """
    guide = _guide()

    assert "long-lived" in guide
    assert "TrackerCredentialExpiryError" in guide
    assert hasattr(errors, "TrackerCredentialExpiryError")


def _shipped_variables() -> set[str]:
    """Every environment name ``AppConfig`` actually reads."""
    from kodezart.core.config import AppConfig

    return {f"KODEZART_{name.upper()}" for name in AppConfig.model_fields}


def test_every_variable_the_guide_sets_is_a_shipped_config_field() -> None:
    """The guide instructs against the env surface the code actually reads."""
    shipped = _shipped_variables()
    guide = _guide()

    for variable in CITED_VARIABLES:
        assert variable in shipped, variable
        assert variable in guide, variable


def test_the_guide_names_no_variable_the_config_does_not_ship() -> None:
    """The other direction, derived: an invented or misspelled name reds here.

    The floor above says which variables the guide owes an operator; this
    reads every one it actually carries out of the text, so a variable
    added to the guide is checked by the act of adding it rather than by
    somebody remembering to extend a list.
    """
    shipped = _shipped_variables()
    named = set(re.findall(r"\bKODEZART_[A-Z0-9_]+\b", _guide()))

    assert named, "the guide names no configuration variable at all"
    assert named <= shipped, named - shipped


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


# ---------------------------------------------------------------------------
# The principal vocabulary, derived rather than transcribed
# ---------------------------------------------------------------------------


#: Every identifier a principal is addressed by, read off the shipped model.
#: Derived so a fourth identifier — or a renamed one — makes this red rather
#: than leaving an operator collecting the wrong ids.
def _principal_identifier_fields() -> set[str]:
    return set(Principal.model_fields) - {"roles"}


def _structural_invariants() -> str:
    """The source of ``OperationConfig``'s own structural validator.

    The invariants are read out of the validator rather than restated,
    because a guide that restates them is a second copy of a rule the code
    already owns — which is the class this whole check exists to close.
    """
    source = (SRC / "types" / "domain" / "operation.py").read_text(encoding="utf-8")
    start = source.index("def _check_structure")
    return source[start : source.index("\n    def ", start + 1)]


def test_the_guide_names_every_principal_role_the_code_defines() -> None:
    """A role the guide omits is a config an operator cannot write correctly.

    `f7ce6cc`'s step 3 named an `escalation` target the model has never had
    and never mentioned `assignee`, which the model requires exactly one of
    — so an operator following only the guide wrote a config that fails to
    load, while `docs/operation.example.toml` two steps later was right.
    """
    guide = _guide()

    for member in PrincipalRole:
        assert member.value in guide, member


def test_the_guide_names_no_role_the_code_does_not_define() -> None:
    """The other direction: a role in the guide that the enum lacks is the drift."""
    guide = _guide().lower()
    shipped = {member.value for member in PrincipalRole}

    for invented in ("escalation", "reviewer", "owner", "watcher"):
        assert invented in shipped or f"`{invented}`" not in guide, invented


def test_the_guide_names_every_identifier_a_principal_carries() -> None:
    """Three fields on the model, three collected by the operator."""
    guide = _guide()

    for field in _principal_identifier_fields():
        assert field in guide, field


def test_the_guide_states_the_count_invariants_the_loader_enforces() -> None:
    """A count the loader rejects on must be a count the guide asked for.

    Both count forms are read out of the validator, so a count rule added
    to or reshaped in the model fails here until the guide carries it.
    The at-most-one arm exists because assignee absence is legal at load
    and refused at the point of need — a guide stating `exactly one` for
    it would send an operator inventing a principal the operation does
    not have.
    """
    invariants = _structural_invariants()
    exactly_one = re.findall(
        r"exactly one (\w+) principal is required",
        invariants,
    )
    at_most_one = re.findall(
        r"at most one (\w+) principal may be declared",
        invariants,
    )
    guide = _guide().lower()

    assert exactly_one == ["APPROVER"], exactly_one
    assert at_most_one == ["ASSIGNEE"], at_most_one
    for role in exactly_one + at_most_one:
        assert f"`{role.lower()}`" in guide, role
    assert "exactly one" in guide
    assert "at most one" in guide


def _guide_config_destinations() -> set[str]:
    """Every operation-config path the guide tells an operator to WRITE.

    The annotated examples under ``docs/`` are excluded: those are the
    tracked side, and the whole point of the anchoring below is that they
    stay tracked.  So are globs, which the guide quotes when it states the
    ignore rules themselves — a rule matches itself and would assert
    nothing.
    """
    cited = re.findall(r"`(/?[\w./*-]*operation[\w.*-]*\.toml)`", _guide())
    return {
        path.lstrip("/")
        for path in cited
        if not path.lstrip("/").startswith("docs/") and "*" not in path
    }


def _root_anchored_toml_ignores() -> set[str]:
    """The root-anchored ``.toml`` rules ``.gitignore`` carries."""
    lines = (GITIGNORE.read_text(encoding="utf-8")).splitlines()
    return {
        line.strip().lstrip("/")
        for line in lines
        if line.strip().startswith("/") and line.strip().endswith(".toml")
    }


def test_every_config_path_the_guide_names_is_ignored() -> None:
    """A guide telling you where to write real identifiers must not walk
    you into committing them.

    Both sides are derived: the destinations come out of the README and
    the rules out of ``.gitignore``.  Renaming the file in the guide
    without extending the ignore rules makes this red, which is the drift
    that would otherwise be found by an operator's handles appearing in a
    public repository.
    """
    destinations = _guide_config_destinations()
    patterns = _root_anchored_toml_ignores()

    assert destinations, "the guide must name where the filled-in config goes"
    for path in sorted(destinations):
        assert any(fnmatch(path, rule) for rule in patterns), (
            f"the guide names {path!r} and no root-anchored rule ignores it: "
            f"{sorted(patterns)}"
        )


def test_the_ignore_rules_are_anchored_so_the_examples_stay_tracked() -> None:
    """The examples the guide tells you to COPY must remain in the tree.

    An unanchored ``operation*.toml`` rule would ignore them too, and the
    guide's first instruction would point at a file a fresh clone does not
    have.
    """
    patterns = _root_anchored_toml_ignores()
    examples = sorted(
        path.relative_to(REPO_ROOT).as_posix()
        for path in (REPO_ROOT / "docs").glob("operation*.toml")
    )

    assert examples, "the guide's step 5 copies an example that must exist"
    for example in examples:
        assert Path(REPO_ROOT / example).is_file()
        assert not any(fnmatch(example, rule) for rule in patterns), example

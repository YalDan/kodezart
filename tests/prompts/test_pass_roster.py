"""The pass templates render the declared roster, and carry their mechanisms.

Two static rules over the template FILES of both shipped sets, and the
renders that show what the rules buy:

* no template names a team or a repository by a literal key or position, so
  a third team or a third repository reaches the prompt without a template
  edit and a later template cannot reintroduce a fixed slot (KOD-150,
  KOD-157);
* every shipped PASS template addresses, through configuration bindings, the
  marker its scan window starts from and the destination it records to
  (KOD-155).

Both rules are pattern-matched against BINDING NAMES rather than prose: the
prose is the set's to author, and a rule that reads it would be a rule about
authoring style.
"""

import re
import tomllib
from collections.abc import Callable
from pathlib import Path

import pytest

from kodezart.adapters.in_repo_prompt_registry import default_sets_root
from kodezart.adapters.toml_operation_config import load_operation_config
from kodezart.core.prompt_namespaces import bindings_for
from kodezart.core.prompt_rendering import binding_names
from kodezart.types.domain.prompts import PromptKey
from tests.prompts.test_claude_opus_goldens import V5_SET
from tests.prompts.test_operation_config import raw_example, write_toml
from tests.prompts.test_prompt_wiring import DEFAULT_SET, load_registry

SHIPPED_SETS = (DEFAULT_SET, V5_SET)
PASS_KEYS = (PromptKey.FIRE_PREP_PASS, PromptKey.GROOMING_PASS)

#: The two roster collections a pass enumerates rather than addresses.
ROSTERS = ("teams", "repos")

#: A reference under a roster: the roster name, a dot, and anything. Every
#: one of them is a fixed slot, whether the segment is a mapping key
#: (``teams.primary``) or a decimal position (``repos.0``).
ROSTER_SLOT = re.compile(rf"^({'|'.join(ROSTERS)})\.")

#: Where a pass's scan window starts from: the checkpoint document, or the
#: record destination whose most recent row IS the window boundary. Both
#: are configuration paths; which one a pass uses is the set's choice.
CHECKPOINT_PREFIXES = ("documents.checkpoint.", "records.")

#: Where a pass writes what it did.
DESTINATION_PREFIXES = ("records.", "knowledge.")

#: One edit to the parsed example, applied before it is written back.
Mutation = Callable[[dict[str, object]], None]


def member_files() -> dict[tuple[str, str], str]:
    """Every shipped member file's text, keyed by ``(set name, stem)``."""
    root = default_sets_root()
    return {
        (set_name, path.stem): path.read_text(encoding="utf-8")
        for set_name in SHIPPED_SETS
        for path in sorted((root / set_name).glob("*.md"))
    }


def roster_slots(body: str) -> tuple[str, ...]:
    """Every fixed team or repository slot *body* references."""
    return tuple(
        sorted(name for name in binding_names(body) if ROSTER_SLOT.match(name))
    )


def pass_bodies() -> dict[tuple[str, PromptKey], str]:
    """The RESOLVED body of every shipped pass template.

    Resolved rather than raw, because a set may contribute its mechanisms
    as a fragment: the file is what an author edits, the composed body is
    what a session is sent, and the mechanism rule is about the latter.
    """
    return {
        (set_name, key): load_registry(default_set=set_name).template_for(key).body
        for set_name in SHIPPED_SETS
        for key in PASS_KEYS
    }


def references_under(body: str, prefixes: tuple[str, ...]) -> tuple[str, ...]:
    """Every binding name of *body* rooted at one of *prefixes*."""
    return tuple(
        sorted(name for name in binding_names(body) if name.startswith(prefixes))
    )


# ---------------------------------------------------------------------------
# KOD-150 / KOD-157 — the roster is iterated, never slotted
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("member", sorted(member_files()))
def test_no_shipped_template_names_a_team_or_repository_slot(
    member: tuple[str, str],
) -> None:
    """A literal key or position under either roster is the defect itself."""
    found = roster_slots(member_files()[member])
    assert found == (), f"{member[0]}/{member[1]} names fixed roster slots: {found}"


def test_the_slot_rule_fires_on_the_shape_it_retired() -> None:
    """Non-vacuity: the detector reports the slots the passes used to carry."""
    fixture = "{{teams.primary.name}} {{teams.agent.key}} {{repos.0.checks.1.name}}"
    assert roster_slots(fixture) == (
        "repos.0.checks.1.name",
        "teams.agent.key",
        "teams.primary.name",
    )


@pytest.mark.parametrize("case", [(s, k) for s in SHIPPED_SETS for k in PASS_KEYS])
def test_every_pass_template_iterates_both_rosters(
    case: tuple[str, PromptKey],
) -> None:
    """The other half: absent slots plus absent loops would also pass above."""
    body = pass_bodies()[case]
    for roster in ROSTERS:
        assert f"{{{{#each {roster}}}}}" in body, (
            f"{case[0]}/{case[1].value} does not enumerate {roster}"
        )


# ---------------------------------------------------------------------------
# KOD-155 — every pass carries its window marker and its destination
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("case", [(s, k) for s in SHIPPED_SETS for k in PASS_KEYS])
def test_every_pass_template_addresses_a_checkpoint(
    case: tuple[str, PromptKey],
) -> None:
    """A pass with no configured window marker re-reads the board forever."""
    found = references_under(pass_bodies()[case], CHECKPOINT_PREFIXES)
    assert found, f"{case[0]}/{case[1].value} addresses no checkpoint"


@pytest.mark.parametrize("case", [(s, k) for s in SHIPPED_SETS for k in PASS_KEYS])
def test_every_pass_template_addresses_a_destination(
    case: tuple[str, PromptKey],
) -> None:
    """A pass that records nowhere leaves the next one nothing to read."""
    found = references_under(pass_bodies()[case], DESTINATION_PREFIXES)
    assert found, f"{case[0]}/{case[1].value} addresses no destination"


def test_the_v5_mechanisms_are_declared_once_and_carried_by_no_member_file() -> None:
    """One source, N passes: a member carrying the text is the second copy.

    Counted over the member FILES, because resolution is what puts the
    clause into a body — the same rule the suppression proxy is held to.
    """
    fragments = tomllib.loads(
        (default_sets_root() / V5_SET / "set.toml").read_text(encoding="utf-8"),
    )["fragments"]
    assert isinstance(fragments, dict)
    first_line = str(fragments["pass_mechanisms"]).splitlines()[0]

    carriers = [
        name for (set_name, name), body in member_files().items() if first_line in body
    ]
    assert carriers == []


def test_the_v5_mechanisms_reach_exactly_the_pass_members() -> None:
    """The other half: declared once and composed into nothing is worse."""
    registry = load_registry(default_set=V5_SET)
    fragments = tomllib.loads(
        (default_sets_root() / V5_SET / "set.toml").read_text(encoding="utf-8"),
    )["fragments"]
    assert isinstance(fragments, dict)
    mechanisms = str(fragments["pass_mechanisms"])

    carriers = {
        key for key in PromptKey if mechanisms in registry.template_for(key).body
    }
    assert carriers == set(PASS_KEYS)


def test_the_mechanism_rules_fire_on_a_template_that_carries_neither() -> None:
    """Non-vacuity: a roster-only template satisfies neither rule."""
    fixture = (
        "{{#each teams}}{{this.name}}{{/each}}{{#each repos}}{{this.slug}}{{/each}}"
    )
    assert references_under(fixture, CHECKPOINT_PREFIXES) == ()
    assert references_under(fixture, DESTINATION_PREFIXES) == ()


# ---------------------------------------------------------------------------
# What the rules buy: the renders
# ---------------------------------------------------------------------------


def rendered(config_path: Path, set_name: str, key: PromptKey) -> str:
    """One pass template, rendered against the config at *config_path*."""
    bindings = dict(bindings_for(load_operation_config(config_path)))
    registry = load_registry(default_set=set_name, bindings=bindings)
    return registry.template_for(key).render({"skills_reference": ""})


def written(tmp_path: Path, mutate: Mutation | None = None) -> Path:
    """The annotated example, mutated by *mutate*, written back as TOML.

    No mutation is the control case: the same round trip, so a difference
    between two renders is a difference of configuration and not of the
    serialiser this helper writes through.
    """
    raw = raw_example()
    if mutate is not None:
        mutate(raw)
    return write_toml(tmp_path, raw)


def rename_teams(raw: dict[str, object]) -> None:
    """Re-key every team, so no mapping key the templates once used survives.

    Document containers reference teams BY KEY (KOD-166), so re-keying the
    roster moves those references with it — the declared graph stays
    consistent, which is itself the property a rename exercises.
    """
    teams = raw["teams"]
    assert isinstance(teams, dict)
    raw["teams"] = {f"board_{name}": entry for name, entry in teams.items()}
    documents = raw.get("documents")
    if isinstance(documents, dict):
        for entry in documents.values():
            if isinstance(entry, dict) and "container" in entry:
                entry["container"] = f"board_{entry['container']}"


def three_of_each(raw: dict[str, object]) -> None:
    """A third team and a third repository, explicitly bound to each other."""
    repos = raw["repos"]
    teams = raw["teams"]
    assert isinstance(repos, list)
    assert isinstance(teams, dict)
    third_url = "https://example.invalid/example-org/third-repo"
    repos.append(
        {
            "url": third_url,
            "trunk": "trunk-three",
            "checks": [{"name": "verify", "command": "make verify"}],
        },
    )
    teams["third"] = {
        "name": "Third Team",
        "key": "THR",
        "repository": third_url,
        "visibility": "public",
    }


def without_records_or_knowledge(raw: dict[str, object]) -> None:
    """The M1 deployment: a tracker, and no store or record beside it."""
    for field in ("records", "knowledge", "documents"):
        del raw[field]


@pytest.mark.parametrize("case", [(s, k) for s in SHIPPED_SETS for k in PASS_KEYS])
def test_a_roster_keyed_other_than_primary_still_renders(
    case: tuple[str, PromptKey],
    tmp_path: Path,
) -> None:
    """KOD-150: the mapping key was never a name a template may depend on."""
    output = rendered(written(tmp_path, rename_teams), case[0], case[1])
    assert "{{" not in output
    assert "Example Team" in output


@pytest.mark.parametrize("case", [(s, k) for s in SHIPPED_SETS for k in PASS_KEYS])
def test_a_third_team_and_repository_render_by_name(
    case: tuple[str, PromptKey],
    tmp_path: Path,
) -> None:
    """KOD-157: configuration widens the roster; the template does not move."""
    output = rendered(written(tmp_path, three_of_each), case[0], case[1])
    assert "{{" not in output
    for team in ("Example Team", "example-agent-team", "Third Team"):
        assert team in output, team
    for repo in ("example-repo", "second-repo", "third-repo"):
        assert repo in output, repo
    assert "trunk-three" in output


@pytest.mark.parametrize("key", PASS_KEYS)
def test_a_deployment_with_no_store_renders_the_absence_instruction(
    key: PromptKey,
    tmp_path: Path,
) -> None:
    """M1: no record and no store is a deployment, not a render failure.

    The v5 set is the one that must serve it — the legacy set addresses a
    knowledge document unconditionally and says so.  What is asserted is
    that the absent state produces an INSTRUCTION, never a hole.
    """
    output = rendered(written(tmp_path, without_records_or_knowledge), V5_SET, key)

    assert "{{" not in output
    assert "}}" not in output
    assert "No record destination is configured" in output
    assert "No store is configured beside the tracker" in output
    assert "No checkpoint is configured" in output


@pytest.mark.parametrize("key", PASS_KEYS)
def test_the_configured_deployment_renders_the_present_arm_instead(
    key: PromptKey,
    tmp_path: Path,
) -> None:
    """The pair is mutually exclusive, so neither arm can be vacuous."""
    output = rendered(written(tmp_path), V5_SET, key)

    assert "No record destination is configured" not in output
    assert "Example Run Log" in output

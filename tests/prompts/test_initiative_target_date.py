"""KOD-112 defect 1 — an initiative without a target date is the ordinary case.

``Initiative.target_date`` was a REQUIRED ``date``, so no config for a real
operation was constructable without inventing one.  The pass template then
rendered "steering toward <invented date>" and instructed the session to
state the distance to it as an observation — a commitment manufactured by
the configuration model and asserted onto the tracker.

The test that matters is not that the field is optional; it is that a config
carrying no date LOADS and RENDERS, and that the rendered prompt neither
names a date nor asks for a distance to one.
"""

from pathlib import Path

from kodezart.adapters.toml_operation_config import load_operation_config
from kodezart.core.prompt_namespaces import operation_bindings
from kodezart.core.prompt_rendering import render_template
from kodezart.types.domain.operation import OperationConfig

REPO_ROOT = Path(__file__).resolve().parents[2]
GROOMING = (
    REPO_ROOT
    / "src"
    / "kodezart"
    / "prompts"
    / "sets"
    / "claude-opus"
    / "grooming_pass.md"
)

_CONFIG = """
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

[[repos]]
url = "https://example.invalid/repo"

[[repos.check_commands]]
name = "check"
command = "make check"

[documents.checkpoint]
system = "tracker"
id = "doc-1"

[documents.house_rules]
system = "knowledge"
id = "doc-2"

[records.run_log]
system = "knowledge"
id = "record-1"
append_only = true

[knowledge]
house_rules = "doc-2"

[endpoints]
escalation = "https://example.invalid/escalation"
"""

_DATED = '\n[[initiatives]]\nid = "dated"\ntarget_date = 2026-12-31\n'
_UNDATED = '\n[[initiatives]]\nid = "undated"\n'


def _load(initiatives: str, tmp_path: Path) -> OperationConfig:
    path = tmp_path / "operation.toml"
    path.write_text(_CONFIG + initiatives, encoding="utf-8")
    return load_operation_config(path)


def _render(config: OperationConfig) -> str:
    body = GROOMING.read_text(encoding="utf-8")
    bindings: dict[str, object] = dict(operation_bindings(config))
    bindings["skills_reference"] = ""
    return render_template(body, bindings)


def test_a_config_whose_initiative_has_no_target_date_loads(tmp_path: Path) -> None:
    """The defect in one line: this used to be unconstructable."""
    config = _load(_UNDATED, tmp_path)
    assert config.initiatives[0].target_date is None


def test_the_rendered_pass_says_the_initiative_carries_none(tmp_path: Path) -> None:
    rendered = _render(_load(_UNDATED, tmp_path))
    assert "undated, carrying no target date" in rendered


def test_the_rendered_pass_invents_no_date_for_an_undated_initiative(
    tmp_path: Path,
) -> None:
    """No ISO date may appear anywhere in a prompt rendered from no date."""
    rendered = _render(_load(_UNDATED, tmp_path))
    assert "steering toward" not in rendered


def test_a_dated_initiative_still_renders_its_date(tmp_path: Path) -> None:
    """The paired positive — optionality did not delete the present case."""
    rendered = _render(_load(_DATED, tmp_path))
    assert "dated, steering toward 2026-12-31" in rendered
    assert "carrying no target date" not in rendered


def test_the_two_renderings_are_mutually_exclusive_per_initiative(
    tmp_path: Path,
) -> None:
    """One config, both kinds: each initiative gets exactly one clause."""
    rendered = _render(_load(_DATED + _UNDATED, tmp_path))
    assert "dated, steering toward 2026-12-31" in rendered
    assert "undated, carrying no target date" in rendered
    assert rendered.count("steering toward") == 1
    assert rendered.count("carrying no target date") == 1

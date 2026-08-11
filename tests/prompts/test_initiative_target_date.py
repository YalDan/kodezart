"""KOD-112 defect 1 — an initiative without a target date is the ordinary case.

``Initiative.target_date`` was a REQUIRED ``date``, so no config for a real
operation was constructable without inventing one.  The pass template then
rendered a distance to the invented date — a commitment manufactured by the
configuration model and asserted onto the tracker.

The test that matters is not that the field is optional; it is that a config
carrying no date LOADS and RENDERS, and that the rendered prompt neither
names a date nor asks for a distance to one.  Reshaped to the verbatim
grooming template under KOD-60 R20(a): the deadlines clause is
presence-guarded on the first initiative's target date, so the undated
state renders the pass WITHOUT the clause rather than a paraphrase of it —
the property (no invented date, the present case still rendered) is
unchanged.
"""

import re
from pathlib import Path

from kodezart.adapters.toml_operation_config import load_operation_config
from kodezart.core.prompt_namespaces import operation_bindings
from kodezart.core.prompt_rendering import render_template
from kodezart.types.domain.operation import OperationConfig
from tests.prompts.test_operation_config import raw_example, write_toml

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

ISO_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")

#: The verbatim deadlines clause is guarded on this reference.
CLAUSE = "initiative target"

_DATED = [
    {"id": "dated", "target_date": "2026-12-31"},
    {"id": "second", },
]
_UNDATED = [
    {"id": "undated"},
    {"id": "second"},
]


def _load(initiatives: list[dict[str, object]], tmp_path: Path) -> OperationConfig:
    raw = raw_example()
    raw["initiatives"] = initiatives
    return load_operation_config(write_toml(tmp_path, raw))


def _render(config: OperationConfig) -> str:
    body = GROOMING.read_text(encoding="utf-8")
    bindings: dict[str, object] = dict(operation_bindings(config))
    bindings["skills_reference"] = ""
    return render_template(body, bindings)


def test_a_config_whose_initiative_has_no_target_date_loads(tmp_path: Path) -> None:
    """The defect in one line: this used to be unconstructable."""
    config = _load(_UNDATED, tmp_path)
    assert config.initiatives[0].target_date is None


def test_an_undated_initiative_renders_without_the_deadlines_clause(
    tmp_path: Path,
) -> None:
    """The guarded clause is absent — never a hole, never a paraphrase."""
    rendered = _render(_load(_UNDATED, tmp_path))
    assert rendered
    assert CLAUSE not in rendered


def test_the_rendered_pass_invents_no_date_for_an_undated_initiative(
    tmp_path: Path,
) -> None:
    """No ISO date may appear anywhere in a prompt rendered from no date."""
    rendered = _render(_load(_UNDATED, tmp_path))
    assert not ISO_DATE.search(rendered)


def test_a_dated_initiative_still_renders_its_date(tmp_path: Path) -> None:
    """The paired positive — optionality did not delete the present case."""
    rendered = _render(_load(_DATED, tmp_path))
    assert "against the 2026-12-31 initiative target" in rendered


def test_the_two_renderings_are_mutually_exclusive(tmp_path: Path) -> None:
    """One clause site: present exactly once with a date, absent without."""
    dated = _render(_load(_DATED, tmp_path))
    undated = _render(_load(_UNDATED, tmp_path))
    assert dated.count(CLAUSE) == 1
    assert undated.count(CLAUSE) == 0

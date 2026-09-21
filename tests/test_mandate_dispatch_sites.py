"""One configured phase table answers everything a mandate kind could.

Three code paths behind one lane is the fork this lane ruled against, so
the sources may hold exactly one site that dispatches on a mandate kind:
the table resolved at boot.  Naming a kind member is how a reader selects
behaviour per phase instead of reading the row the table already carries,
so the guard walks the shipped sources and reports every site that does.

The walk is textual and executes nothing, which is what lets it speak for
the whole tree; the controls below inject each construction a member can
hide in, and each way of naming the enum without selecting a member.
"""

import ast
import sys
from pathlib import Path

import pytest

from kodezart.types.domain.organize import MandateKind

SOURCE = Path(__file__).resolve().parents[1] / "src" / "kodezart"
ENUM = MandateKind.__name__
MEMBERS = frozenset(member.name for member in MandateKind)
VALUES = frozenset(member.value for member in MandateKind)
#: Where the enum itself is declared, and so where its table belongs.
TABLE_MODULE = (
    Path(sys.modules[MandateKind.__module__].__file__ or "")
    .resolve()
    .relative_to(SOURCE)
    .as_posix()
)


def _bound_names(tree):
    """Every local name the enum answers to: its declaration and aliases."""
    bound = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == ENUM:
            bound.add(node.name)
        if isinstance(node, ast.ImportFrom):
            bound.update(
                alias.asname or alias.name for alias in node.names if alias.name == ENUM
            )
        if (
            isinstance(node, ast.Assign)
            and isinstance(node.value, ast.Name)
            and node.value.id in bound
        ):
            bound.update(
                target.id for target in node.targets if isinstance(target, ast.Name)
            )
    return bound


def _constants(nodes):
    return {node.value for node in nodes if isinstance(node, ast.Constant)}


def _selects_a_member(node, bound):
    """Whether this expression singles one phase out of the enum."""
    if isinstance(node, ast.Attribute):
        return (
            isinstance(node.value, ast.Name)
            and node.value.id in bound
            and node.attr in MEMBERS
        )
    if isinstance(node, ast.Subscript):
        return (
            isinstance(node.value, ast.Name)
            and node.value.id in bound
            and bool(_constants([node.slice]) & MEMBERS)
        )
    if isinstance(node, ast.Call):
        return (
            isinstance(node.func, ast.Name)
            and node.func.id in bound
            and bool(_constants(node.args) & VALUES)
        )
    return False


def _sites(tree):
    """Label each source location that singles a phase out, once per scope."""
    bound = _bound_names(tree)
    found = set()

    def walk(node, label):
        for child in ast.iter_child_nodes(node):
            here = label
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                here = child.name if label is None else f"{label}.{child.name}"
            elif label is None:
                here = f"line {child.lineno}"
            if _selects_a_member(child, bound):
                found.add(here)
            walk(child, here)

    walk(tree, None)
    return found


def _dispatch_sites(root):
    found = {}
    for path in sorted(root.rglob("*.py")):
        sites = _sites(ast.parse(path.read_text()))
        if sites:
            found[path.relative_to(root).as_posix()] = sorted(sites)
    return found


def test_the_sources_dispatch_on_a_mandate_kind_at_the_phase_table_alone():
    sites = _dispatch_sites(SOURCE)
    assert sum(len(group) for group in sites.values()) == 1, sites
    assert list(sites) == [TABLE_MODULE], sites


def test_each_resolved_row_carries_what_a_phase_branch_used_to_select():
    from kodezart.types.domain.operation import OperationConfig
    from kodezart.types.domain.prompts import PromptKey
    from tests.domain.test_organize import mandate_operation_fields

    fields = mandate_operation_fields()
    # Declaration order carries no authority over the governed sequence.
    fields["organize_mandates"].reverse()
    phases = OperationConfig.model_validate(fields).resolve_organize_mandates()
    assert [
        (
            phase.spec.kind.value,
            phase.role.author_prompt_key,
            phase.role.marks_specification_body,
            phase.role.marks_execution_stage,
            phase.role.runs_under_approval,
            phase.marker_source,
        )
        for phase in phases
    ] == [
        (
            "groom",
            PromptKey.ORGANIZE_AUTHOR,
            False,
            False,
            False,
            "organize_mandates.groom.terminal_marker_key",
        ),
        (
            "ticket",
            PromptKey.ORGANIZE_AUTHOR,
            True,
            False,
            True,
            "organize_mandates.ticket.terminal_marker_key",
        ),
        (
            "criteria",
            PromptKey.ORGANIZE_CRITERIA_AUTHOR,
            False,
            True,
            True,
            "organize_mandates.criteria.terminal_marker_key",
        ),
    ]


@pytest.mark.parametrize(
    "body",
    [
        "def author(kind):\n    return kind is MandateKind.TICKET\n",
        "ROLES = {MandateKind.CRITERIA: 'criteria_author'}\n",
        "def marker(kind):\n"
        "    match kind:\n"
        "        case MandateKind.GROOM:\n"
        "            return 'groomed'\n",
        "def marker(kind):\n    return [MandateKind.GROOM, MandateKind.TICKET]\n",
        "class Phases:\n    first = MandateKind.GROOM\n",
        "def author(kind):\n    return kind == MandateKind('ticket')\n",
        "def author(kind):\n    return kind == MandateKind['TICKET']\n",
        "def outer():\n"
        "    def inner(kind):\n"
        "        return kind is MandateKind.CRITERIA\n"
        "    return inner\n",
        "def author(kind, table={MandateKind.GROOM: 1}):\n    return table[kind]\n",
    ],
)
def test_every_construction_that_singles_a_phase_out_is_reported(body):
    source = f"from kodezart.types.domain.organize import MandateKind\n{body}"
    assert _sites(ast.parse(source))


@pytest.mark.parametrize(
    "alias",
    [
        "from kodezart.types.domain.organize import MandateKind as Phase\n"
        "def author(kind):\n    return kind is Phase.TICKET\n",
        "from kodezart.types.domain.organize import MandateKind\n"
        "Phase = MandateKind\n"
        "def author(kind):\n    return kind is Phase.TICKET\n",
    ],
)
def test_renaming_the_enum_does_not_hide_the_phase_it_singles_out(alias):
    assert _sites(ast.parse(alias))


@pytest.mark.parametrize(
    "body",
    [
        "def author(kind: MandateKind) -> str:\n    return kind.value\n",
        "ADMISSIONS: dict[MandateKind, str] = {}\n",
        "def declared(specs):\n    return [kind for kind in MandateKind]\n",
        "def author(table, spec):\n    return table[spec.kind]\n",
        "def author(kind):\n    return kind is OtherKind.TICKET\n",
        "def marker(kind):\n    return MandateKind(kind.value)\n",
    ],
)
def test_reading_the_enum_without_singling_a_phase_out_is_not_a_site(body):
    source = f"from kodezart.types.domain.organize import MandateKind\n{body}"
    assert _sites(ast.parse(source)) == set()


def test_one_scope_that_branches_twice_is_one_site_and_two_scopes_are_two():
    source = (
        "from kodezart.types.domain.organize import MandateKind\n"
        "def author(kind):\n"
        "    if kind is MandateKind.GROOM:\n"
        "        return 'author'\n"
        "    return kind is MandateKind.CRITERIA\n"
        "def marker(kind):\n"
        "    return kind is MandateKind.TICKET\n"
    )
    assert _sites(ast.parse(source)) == {"author", "marker"}


def test_a_module_level_table_and_a_module_level_branch_are_separate_sites():
    source = (
        "from kodezart.types.domain.organize import MandateKind\n"
        "ROLES = {MandateKind.GROOM: 0, MandateKind.TICKET: 1}\n"
        "FIRST = MandateKind.CRITERIA\n"
    )
    assert len(_sites(ast.parse(source))) == 2


def test_tree_guard_accepts_one_table_and_rejects_a_second_site(tmp_path, monkeypatch):
    table = tmp_path / "table.py"
    table.write_text(
        "from kodezart.types.domain.organize import MandateKind\n"
        "ROLES = {\n"
        "    MandateKind.GROOM: 0,\n"
        "    MandateKind.TICKET: 1,\n"
        "    MandateKind.CRITERIA: 2,\n"
        "}\n"
    )
    assert _dispatch_sites(tmp_path) == {"table.py": ["line 2"]}
    (tmp_path / "worker.py").write_text(
        "from kodezart.types.domain.organize import MandateKind\n"
        "def route(kind):\n    return kind is MandateKind.CRITERIA\n"
    )
    monkeypatch.setattr(sys.modules[__name__], "SOURCE", tmp_path)
    with pytest.raises(AssertionError):
        test_the_sources_dispatch_on_a_mandate_kind_at_the_phase_table_alone()

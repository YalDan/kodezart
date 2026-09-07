"""Ruling identity has one typed mint and no answer-dependent component."""

import ast
from pathlib import Path

import pytest

from kodezart.domain.agent import mint_ruling_id

SOURCE_ROOT = Path(__file__).parents[2] / "src" / "kodezart"


def _construction_sites(source: str) -> tuple[int, ...]:
    tree = ast.parse(source)
    constructors = {"RulingId"}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            constructors.update(
                alias.asname or alias.name
                for alias in node.names
                if alias.name == "RulingId"
            )
    return tuple(
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and (
            (isinstance(node.func, ast.Name)
            and node.func.id in constructors)
            or (isinstance(node.func, ast.Attribute)
            and node.func.attr == "RulingId")
        )
    )


def _bare_ruling_fields(source: str) -> tuple[int, ...]:
    return tuple(
        node.lineno
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.AnnAssign)
        and isinstance(node.target, ast.Name)
        and node.target.id in {"ruling_id", "ruling_ids", "ruling_ref", "ruling_refs"}
        and any(
            isinstance(part, ast.Name) and part.id == "str"
            for part in ast.walk(node.annotation)
        )
    )


def test_one_minting_site_and_no_bare_string_model_addresses() -> None:
    sites = []
    bare = []
    for path in SOURCE_ROOT.rglob("*.py"):
        source = path.read_text()
        sites.extend((path, line) for line in _construction_sites(source))
        if "types" in path.parts:
            bare.extend((path, line) for line in _bare_ruling_fields(source))
    assert len(sites) == 1, sites
    assert sites[0][0] == SOURCE_ROOT / "domain" / "agent.py"
    assert bare == []


@pytest.mark.parametrize(
    "source",
    [
        "RulingId('copy')",
        "from module import RulingId as RuleKey\nRuleKey('copy')",
        "module.RulingId('copy')",
    ],
)
def test_site_guard_detects_aliases_and_qualified_constructors(source: str) -> None:
    assert len(_construction_sites(source)) == 1


@pytest.mark.parametrize("annotation", ["str", "str | None", "tuple[str, ...]"])
def test_address_guard_rejects_bare_strings_inside_containers(annotation: str) -> None:
    assert _bare_ruling_fields(f"class Record:\n    ruling_id: {annotation}")
    assert not _bare_ruling_fields("class Record:\n    ruling_id: RulingId")


def test_exact_pair_is_stable_and_component_boundaries_are_distinct() -> None:
    value = mint_ruling_id(issue_ref="EXT/42", question="Which reading applies?")
    assert value == mint_ruling_id(
        issue_ref="EXT/42", question="Which reading applies?"
    )
    assert mint_ruling_id(issue_ref="a", question="bc") != mint_ruling_id(
        issue_ref="ab", question="c"
    )


@pytest.mark.parametrize(
    "issue,question",
    [
        ("OTHER", "Question"),
        ("ISSUE", "question"),
        ("ISSUE", "Question "),
        ("ISSUE", "Quéstion"),
    ],
)
def test_identity_preserves_each_exact_input(issue: str, question: str) -> None:
    assert mint_ruling_id(issue_ref=issue, question=question) != mint_ruling_id(
        issue_ref="ISSUE", question="Question"
    )


@pytest.mark.parametrize(
    "issue,question",
    [("", "question"), (" \n", "question"), ("issue", ""), ("issue", "\t ")],
)
def test_missing_identity_inputs_refuse(issue: str, question: str) -> None:
    with pytest.raises(ValueError):
        mint_ruling_id(issue_ref=issue, question=question)

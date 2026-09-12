"""Ruling identity has one typed mint and no answer-dependent component."""

from pathlib import Path

import pytest

from kodezart.domain.agent import mint_ruling_id
from tests.identity_guards import construction_sites as _construction_sites
from tests.identity_guards import invalid_ruling_fields as _bare_ruling_fields

SOURCE_ROOT = Path(__file__).parents[2] / "src" / "kodezart"


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


@pytest.mark.parametrize(
    "source",
    [
        'class Record:\n    ruling_id: "str"',
        'class Record:\n    ruling_ids: "tuple[str, ...]"',
        "from builtins import str as Text\nclass Record:\n    ruling_id: Text",
        "import builtins\nclass Record:\n    ruling_ref: builtins.str",
        "import builtins as primitives\nclass Record:\n    ruling_ref: primitives.str",
        'from builtins import str as Text\nclass Record:\n    ruling_id: "Text | None"',
    ],
)
def test_address_guard_rejects_quoted_and_aliased_builtin_annotations(source):
    assert _bare_ruling_fields(source)


@pytest.mark.parametrize(
    "annotation",
    ['"RulingId"', 'tuple["RulingId", ...]', 'Annotated[RulingId, "str"]'],
)
def test_address_guard_keeps_typed_forward_references_and_metadata(annotation):
    assert not _bare_ruling_fields(f"class Record:\n    ruling_id: {annotation}")


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

"""Code backend: shared criterion and ruling identities retain their owners."""

from pathlib import Path

import pytest

from tests.identity_guards import construction_sites, invalid_ruling_fields

SOURCE_ROOT = Path(__file__).parents[2] / "src" / "kodezart"
IDENTITY_OWNERS = {
    "CriterionRef": "domain/fire_spec.py",
    "RulingId": "domain/agent.py",
}


def identity_violations(sources: dict[str, str]) -> tuple[str, ...]:
    failures = []
    for identity, owner in IDENTITY_OWNERS.items():
        sites = [
            (path, line)
            for path, source in sources.items()
            for line in construction_sites(source, identity=identity)
        ]
        if len(sites) != 1 or sites[0][0] != owner:
            failures.append(
                f"{identity}: expected one construction in {owner}; {sites}"
            )
    for path, source in sources.items():
        if path.startswith("types/"):
            for line in invalid_ruling_fields(source):
                failures.append(f"{path}:{line}: ruling address lacks RulingId")
    return tuple(failures)


def test_identity_invariant_uses_the_actual_code_backend():
    sources = {
        path.relative_to(SOURCE_ROOT).as_posix(): path.read_text()
        for path in SOURCE_ROOT.rglob("*.py")
    }
    assert identity_violations(sources) == ()


@pytest.mark.parametrize("identity", IDENTITY_OWNERS)
@pytest.mark.parametrize(
    "extra",
    [
        "{identity}('another')",
        "from somewhere import {identity} as Key\nKey('another')",
        "namespace.{identity}('another')",
        "construct = namespace.{identity}\ncopy = construct\ncopy('another')",
        "from somewhere import {identity} as Key\n"
        "construct: object = Key\nconstruct('another')",
        "def function(value={identity}('another')):\n    return value",
        "@decorate({identity}('another'))\ndef function():\n    pass",
    ],
)
def test_a_second_explicit_construction_fails_the_same_invariant(identity, extra):
    sources = {
        owner: f"{name}('native-key')" for name, owner in IDENTITY_OWNERS.items()
    }
    assert identity_violations(sources) == ()
    sources["another.py"] = extra.format(identity=identity)
    failures = identity_violations(sources)
    assert len(failures) == 1
    assert identity in failures[0] and "another.py" in failures[0]


@pytest.mark.parametrize(
    "annotation",
    [
        "str",
        '"str | None"',
        "tuple[str, ...]",
        "bool",
        "int",
        "object",
        "Any",
        "RulingId | str",
        "Annotated[str, 'RulingId']",
        "Text",
        '"Text"',
        "CycleA",
    ],
)
def test_a_model_cannot_replace_a_ruling_address_with_text_or_another_type(annotation):
    sources = {
        owner: f"{name}('native-key')" for name, owner in IDENTITY_OWNERS.items()
    }
    sources["types/domain/record.py"] = (
        "Text = str\nCycleA = CycleB\nCycleB = CycleA\n"
        f"class Record:\n    ruling_id: {annotation}\n"
    )
    failures = identity_violations(sources)
    assert len(failures) == 1
    assert "types/domain/record.py" in failures[0] and "RulingId" in failures[0]


@pytest.mark.parametrize(
    "annotation",
    [
        "RulingId",
        '"RulingId | None"',
        'tuple["RulingId", ...]',
        "frozenset[RulingId]",
        "Annotated[RulingId, 'str']",
        "Alias",
        '"Alias"',
        "namespace.RulingId",
    ],
)
def test_typed_ruling_addresses_preserve_aliases_and_annotation_metadata(annotation):
    source = (
        "from somewhere import RulingId as Key\nAlias = Key\n"
        f"class Record:\n    ruling_ref: {annotation}\n"
    )
    assert invalid_ruling_fields(source) == ()


def test_identity_mentions_and_annotations_do_not_construct_addresses():
    source = """
from somewhere import CriterionRef
"RulingId('a quotation')"
class Record:
    criterion: CriterionRef
    ruling_id: RulingId
"""
    for identity in IDENTITY_OWNERS:
        assert construction_sites(source, identity=identity) == ()


@pytest.mark.parametrize(
    "replacement",
    ["RulingId = str", "from builtins import str as RulingId"],
)
def test_rebinding_the_identity_name_to_text_does_not_keep_the_address_typed(
    replacement,
):
    assert invalid_ruling_fields(
        f"{replacement}\nclass Record:\n    ruling_id: RulingId"
    )


@pytest.mark.parametrize("identity", IDENTITY_OWNERS)
def test_moving_the_only_construction_to_another_owner_fails(identity):
    sources = {
        owner: f"{name}('native-key')" for name, owner in IDENTITY_OWNERS.items()
    }
    sources["another.py"] = sources.pop(IDENTITY_OWNERS[identity])
    failures = identity_violations(sources)
    assert len(failures) == 1 and identity in failures[0]


@pytest.mark.parametrize("declaration", ["RulingId = str", "RulingId: TypeAlias = str"])
def test_qualified_shadow_cannot_make_text_an_identity(declaration):
    source = (
        f"class Types:\n    {declaration}\n"
        "class BadRecord:\n    ruling_id: Types.RulingId\n"
        "class GoodRecord:\n    ruling_id: RulingId\n"
    )
    assert invalid_ruling_fields(source) == (4,)
    assert (
        invalid_ruling_fields(
            "import somewhere as namespace\nclass Record:\n"
            "    ruling_id: namespace.RulingId"
        )
        == ()
    )


def test_aliasing_a_local_namespace_preserves_its_untyped_address_refusal():
    assert invalid_ruling_fields(
        "class Types:\n    RulingId = str\n"
        "Alias = Types\nclass Record:\n    ruling_id: Alias.RulingId"
    ) == (5,)

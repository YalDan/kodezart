"""Criterion identity is a type, not a string that happens to look like one.

KOD-53/AC-5's identity half — the stable ``AC-n`` the persisted shape and
every downstream consumer key off.

The rule these checks hold is KOD-66 R4's: a criterion identity must not
be assignable from an arbitrary ``str``.  A constrained alias would
validate the format and still be ``str`` to the type checker, so a union
discriminating a criterion identity from another minted identity would
collapse and admit any loose string — a defect review has to catch
instead of the build.

The checks quantify over every model in ``kodezart.types.domain`` rather
than over a list of the fields that carry an identity today.  A roster
would go stale the first time a field is added; a rule cannot.
"""

import ast
import importlib
import pkgutil
from pathlib import Path
from typing import Annotated, NewType, Union, get_args, get_origin

import pytest
from pydantic import BaseModel, Field, ValidationError
from pydantic.fields import FieldInfo

from kodezart.domain.criteria import mint_criterion_id
from kodezart.types.domain.criteria import (
    CRITERION_ID_PATTERN,
    Contradiction,
    CriterionId,
)
from kodezart.types.domain.fire_spec import CriterionRef
from kodezart.types.domain.grading import IterationGrade

_IDENTITY_FIELD_SUFFIXES = ("criterion_id", "criterion_ids")
_MINTING_MODULE = Path("src/kodezart/domain/criteria.py")


def _domain_models() -> list[type[BaseModel]]:
    """Every pydantic model declared under ``kodezart.types.domain``."""
    package = importlib.import_module("kodezart.types.domain")
    models: list[type[BaseModel]] = []
    for info in pkgutil.iter_modules(list(package.__path__)):
        module = importlib.import_module(f"{package.__name__}.{info.name}")
        for value in vars(module).values():
            if (
                isinstance(value, type)
                and issubclass(value, BaseModel)
                and value.__module__ == module.__name__
            ):
                models.append(value)
    return models


def _mentions_identity(
    annotation: object, identities: tuple[object, ...] = (CriterionId, CriterionRef)
) -> bool:
    """Whether an authored or tracker-native identity types the field."""
    if any(annotation is identity for identity in identities):
        return True
    origin = get_origin(annotation)
    if origin is None:
        return False
    if origin is Annotated:
        return _mentions_identity(get_args(annotation)[0], identities)
    if origin is Union or origin is list or origin is tuple or origin is set:
        return any(_mentions_identity(arg, identities) for arg in get_args(annotation))
    return False


def _constrained_by_the_id_pattern(field: FieldInfo) -> bool:
    return any(
        getattr(constraint, "pattern", None) == CRITERION_ID_PATTERN
        for constraint in field.metadata
    )


def _fields() -> list[tuple[str, str, FieldInfo]]:
    return [
        (model.__name__, name, field)
        for model in _domain_models()
        for name, field in model.model_fields.items()
    ]


def test_the_identity_is_a_new_type_over_str() -> None:
    """A distinct type, so a bare `str` is not assignable to it.

    An alias — ``CriterionId = str``, or an ``Annotated[str, ...]`` with
    the pattern on it — passes every runtime check below's siblings make
    and still collapses under a type checker.  This is the one assertion
    that tells the two apart.
    """
    assert isinstance(CriterionId, NewType)
    assert CriterionId.__supertype__ is str


def test_every_field_constrained_by_the_id_pattern_annotates_the_identity() -> None:
    """A field validating the `AC-n` shape carries the type that means it."""
    offenders = [
        f"{model}.{name}"
        for model, name, field in _fields()
        if _constrained_by_the_id_pattern(field)
        and not _mentions_identity(field.annotation, (CriterionId,))
    ]
    assert offenders == []


def test_every_field_named_for_a_criterion_id_annotates_the_identity() -> None:
    """Identity lists too: `str` there is the same collapse, one level in."""
    offenders = [
        f"{model}.{name}"
        for model, name, field in _fields()
        if name.endswith(_IDENTITY_FIELD_SUFFIXES)
        and not _mentions_identity(field.annotation)
    ]
    assert offenders == []


def test_identity_construction_is_authored_mint_or_exact_tracker_key() -> None:
    """Authored IDs are minted; the native reader carries the exact source key."""
    offenders: list[str] = []
    for path in Path("src").rglob("*.py"):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "CriterionId"
                and path
                not in {_MINTING_MODULE, Path("src/kodezart/chains/criteria.py")}
            ):
                offenders.append(f"{path}:{node.lineno}")
    assert offenders == []
    source = Path("src/kodezart/chains/criteria.py").read_text()
    native_calls = [
        node
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "CriterionId"
    ]
    assert len(native_calls) == 1
    assert ast.unparse(native_calls[0]) == "CriterionId(key)"


@pytest.mark.parametrize(
    ("record", "field", "payload"),
    [
        (
            Contradiction,
            "criterionIds",
            {"criterionIds": [" ", "AC-99"], "explanation": "conflict"},
        ),
        (
            IterationGrade,
            "missingIds",
            {
                "results": [
                    {
                        "criterionId": "AC-1",
                        "criterion": "c",
                        "passed": True,
                        "reasoning": "r",
                    }
                ],
                "missingIds": ["\t\n"],
                "dispatchedCount": 1,
                "passedCount": 1,
                "verdict": "accepted",
            },
        ),
    ],
    ids=["contradiction", "iteration-grade"],
)
def test_a_malformed_element_of_an_identity_list_fails_closed(
    record: type[BaseModel],
    field: str,
    payload: dict[str, object],
) -> None:
    """Nonblank identity applies to each element, for either source arm."""
    with pytest.raises(ValidationError) as excinfo:
        record.model_validate(payload)
    locations = [error["loc"] for error in excinfo.value.errors()]
    assert (field, 0) in locations


def test_a_minted_identity_matches_the_scheme() -> None:
    assert mint_criterion_id(1) == "AC-1"
    assert mint_criterion_id(12) == "AC-12"


def test_positions_are_one_based() -> None:
    with pytest.raises(ValueError, match="1-based"):
        mint_criterion_id(0)


def test_tracker_identity_is_distinct_and_loose_strings_remain_rejected():
    assert CriterionRef is not CriterionId
    assert _mentions_identity(CriterionRef)
    assert _mentions_identity(list[CriterionRef])
    assert not _mentions_identity(str)
    assert not _mentions_identity(list[str])


def test_authored_pattern_requires_authored_identity_even_on_native_shape():
    assert _mentions_identity(CriterionId, (CriterionId,))
    assert not _mentions_identity(CriterionRef, (CriterionId,))
    assert not _mentions_identity(
        Annotated[CriterionRef, Field(pattern=CRITERION_ID_PATTERN)],
        (CriterionId,),
    )


@pytest.mark.parametrize("key", ["fire/native-key", "KOD-815", "AC-1"])
def test_shared_identity_lists_preserve_nonblank_native_or_authored_keys(key):
    record = IterationGrade.model_validate(
        {
            "results": [
                {
                    "criterionId": "AC-1",
                    "criterion": "c",
                    "passed": True,
                    "reasoning": "r",
                }
            ],
            "missingIds": [key, "another/key"],
            "dispatchedCount": 3,
            "passedCount": 1,
            "verdict": "rejected",
        }
    )
    assert record.missing_ids == [key, "another/key"]


@pytest.mark.parametrize("key", ["fire/native-key", "KOD-815"])
def test_authored_contradiction_rejects_native_criterion_keys(key):
    with pytest.raises(ValidationError) as excinfo:
        Contradiction.model_validate(
            {"criterionIds": ["AC-1", key], "explanation": "conflict"}
        )
    assert excinfo.value.errors()[0]["loc"] == ("criterionIds", 1)

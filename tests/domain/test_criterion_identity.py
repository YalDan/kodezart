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
from typing import NewType, Union, get_args, get_origin

import pytest
from pydantic import BaseModel
from pydantic.fields import FieldInfo

from kodezart.domain.criteria import mint_criterion_id
from kodezart.types.domain.criteria import CRITERION_ID_PATTERN, CriterionId

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


def _mentions_identity(annotation: object) -> bool:
    """Whether ``CriterionId`` appears anywhere in *annotation*."""
    if annotation is CriterionId:
        return True
    origin = get_origin(annotation)
    if origin is None:
        return False
    if origin is Union or origin is list or origin is tuple or origin is set:
        return any(_mentions_identity(arg) for arg in get_args(annotation))
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
        and not _mentions_identity(field.annotation)
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


def test_the_minting_function_is_the_only_construction_site() -> None:
    """Nothing outside the minting module may call `CriterionId(...)`.

    Two construction sites is two places that know the `AC-n` shape, and
    the second one is how a value that never passed the format check
    acquires the type that says it did.
    """
    offenders: list[str] = []
    for path in Path("src").rglob("*.py"):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "CriterionId"
                and path != _MINTING_MODULE
            ):
                offenders.append(f"{path}:{node.lineno}")
    assert offenders == []


def test_a_minted_identity_matches_the_scheme() -> None:
    assert mint_criterion_id(1) == "AC-1"
    assert mint_criterion_id(12) == "AC-12"


def test_positions_are_one_based() -> None:
    with pytest.raises(ValueError, match="1-based"):
        mint_criterion_id(0)

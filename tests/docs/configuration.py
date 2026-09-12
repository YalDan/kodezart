"""Extracted shared tracker rules."""

from types import UnionType
from typing import Annotated, Union, get_args, get_origin

from pydantic import BaseModel

from kodezart.core.config import AppConfig


def model_types(annotation):
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        yield annotation
    elif get_origin(annotation) is Annotated:
        yield from model_types(get_args(annotation)[0])
    elif get_origin(annotation) in (Union, UnionType):
        for arm in get_args(annotation):
            yield from model_types(arm)


def shipped_config_variables(model=AppConfig, prefix="KODEZART_"):
    """Include JSON container names and each selectable transport arm's fields."""
    names = set()
    for name, field in model.model_fields.items():
        variable = prefix + name.upper()
        names.add(variable)
        for nested in model_types(field.annotation):
            names.update(shipped_config_variables(nested, variable + "__"))
    return names

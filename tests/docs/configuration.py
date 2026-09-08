"""Environment names derived from the shipped nested settings models."""

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


# These names describe the removed flat API, not additional shipped fields.
RETIRED_KNOWLEDGE_VARIABLES = frozenset(
    "KODEZART_KNOWLEDGE_" + suffix
    for suffix in (
        "SESSION_GRANTS",
        "MCP_SERVER_NAME",
        "MCP_CALL_TIMEOUT_SECONDS",
        "MCP_ERROR_DETAIL_LIMIT",
        "MCP_TRANSPORT",
        "MCP_SERVER_URL",
        "MCP_AUTH_HEADER",
        "MCP_AUTH_SCHEME",
        "MCP_TOKEN",
        "MCP_GATEWAY_TOKEN",
        "MCP_INTERACTIVE_AUTH_HOSTS",
        "MCP_TIMEOUT_SECONDS",
        "MCP_SSE_READ_TIMEOUT_SECONDS",
        "MCP_COMMAND",
        "MCP_ARGS",
        "MCP_ENV",
        "MCP_CREDENTIAL_ENV",
        "MCP_STDERR_TAIL_LIMIT",
    )
)

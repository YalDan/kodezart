"""CamelCaseModel — base for all API models."""

from pydantic import BaseModel, ConfigDict
from pydantic.alias_generators import to_camel


class CamelCaseModel(BaseModel):
    """Base Pydantic model for all API-facing types.

    Generates camelCase field aliases, accepts both camelCase and snake_case
    input (``populate_by_name=True``), and rejects unexpected fields
    (``extra=forbid``).
    """

    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        extra="forbid",
    )

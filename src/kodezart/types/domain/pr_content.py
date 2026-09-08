"""The content and reference identities of one observed open pull request."""

from pydantic import ConfigDict, Field

from kodezart.types.base import CamelCaseModel


class PRContent(CamelCaseModel):
    """Editable prose and base, with the immutable identity of its open PR."""

    model_config = ConfigDict(frozen=True)

    url: str = Field(min_length=1)
    number: int = Field(gt=0)
    head_branch: str = Field(min_length=1)
    base_branch: str = Field(min_length=1)
    title: str = Field(min_length=1)
    body: str
